"""dashboard/scenario_panel.py — Scenario selection, curve chart, and data controls.

Rendered as @st.fragment (requires Streamlit >= 1.37.0). Internal widget interactions
rerun only this panel. Scenario selection and Pull Options trigger st.rerun(scope="app")
so all three panels refresh simultaneously.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from products.sofr import SOFR
from rates_engine import RatesEngine, _contract_period
from scenario_engine import (
    Scenario,
    SettlementTargetRange,
    ValDateManager,
    assemble_rate_path,
    build_custom_scenario,
    clear_explicit_range,
    delete_scenario,
    get_active_ranges,
    get_default_ranges,
    load_saved_scenarios,
    load_templates,
    save_scenario,
    set_explicit_range,
)
from utils import date_utils

# ---------------------------------------------------------------------------
# Module-level constants — stateless
# ---------------------------------------------------------------------------

_TICK = 0.005
_OUTCOME_OPTS = ["", "0bp", "-25bp", "-50bp", "+25bp"]
_STRIKE_SPACING = 0.0625   # SR3 strike grid
_sofr = SOFR()


# ---------------------------------------------------------------------------
# Main panel entry point
# ---------------------------------------------------------------------------

@st.fragment
def render_scenario_panel() -> None:
    """Render the full scenario panel (left column of dashboard)."""
    _render_curve_chart()
    st.divider()
    _render_scenario_selector()
    if st.session_state.get("_show_custom_builder", False):
        _render_custom_builder()
    st.divider()
    _render_overrides()
    if st.session_state.get("active_scenario") is not None:
        st.divider()
        _render_settlement_ranges()
        st.divider()
        _render_pull_options()


# ---------------------------------------------------------------------------
# Curve chart
# ---------------------------------------------------------------------------

def _render_curve_chart() -> None:
    """Three-series Plotly chart: WIRP (orange), scenario (blue), live (grey dots)."""
    re: Optional[RatesEngine] = st.session_state.get("rates_engine")
    if re is None:
        st.info("Waiting for Bloomberg data…")
        return

    val_date: date = st.session_state.val_date_manager.get_val_date()
    contracts: list[str] = st.session_state.get("sr3_contracts", [])

    # Build x-axis: map contract codes to expiry dates, sort chronologically
    expiry_by_code: dict[str, date] = {}
    for code in contracts:
        try:
            _, expiry = _contract_period(code, val_date)
            expiry_by_code[code] = expiry
        except Exception:
            pass

    sorted_codes = sorted(expiry_by_code, key=lambda c: expiry_by_code[c])
    x_dates = [expiry_by_code[c].isoformat() for c in sorted_codes]

    wirp_curve = re.get_wirp_curve()
    live_curve = re.get_live_curve()

    fig = go.Figure()

    # WIRP curve — orange line
    wirp_y = [wirp_curve.get(c) for c in sorted_codes]
    fig.add_trace(go.Scatter(
        x=x_dates, y=wirp_y,
        mode="lines+markers", name="WIRP",
        line=dict(color="orange", width=2),
        marker=dict(size=5),
        connectgaps=True,
    ))

    # Scenario curve — blue line (only when scenario active)
    scenario: Optional[Scenario] = st.session_state.get("active_scenario")
    if scenario is not None:
        wirp_data = st.session_state.get("wirp_data", {})
        scenario_path = assemble_rate_path(scenario, wirp_data)
        scenario_curve = re.get_scenario_curve(scenario_path)
        sc_y = [scenario_curve.get(c) for c in sorted_codes]
        fig.add_trace(go.Scatter(
            x=x_dates, y=sc_y,
            mode="lines+markers", name=scenario.name,
            line=dict(color="steelblue", width=2),
            marker=dict(size=5),
            connectgaps=True,
        ))

    # Live futures — grey dots
    live_y = [live_curve.get(c) for c in sorted_codes]
    fig.add_trace(go.Scatter(
        x=x_dates, y=live_y,
        mode="markers", name="Live",
        marker=dict(color="grey", size=7, symbol="circle"),
    ))

    fig.update_layout(
        height=260,
        margin=dict(l=0, r=0, t=24, b=0),
        legend=dict(orientation="h", y=-0.2),
        xaxis_title=None,
        yaxis_title="Price",
        hovermode="x unified",
        title=dict(text="SR3 Futures Curve", font=dict(size=13)),
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Scenario selector
# ---------------------------------------------------------------------------

def _render_scenario_selector() -> None:
    """Dropdown of template + saved scenarios. 'New custom scenario' at bottom."""
    wirp_data = st.session_state.get("wirp_data", {})
    templates = load_templates(wirp_data)
    saved = load_saved_scenarios()

    # Build option list with prefixed labels
    options: list[str] = []
    label_to_scenario: dict[str, Scenario] = {}
    for s in templates:
        label = f"[Template]  {s.name}"
        options.append(label)
        label_to_scenario[label] = s
    for s in saved:
        label = f"[Saved]  {s.name}"
        options.append(label)
        label_to_scenario[label] = s
    options.append("— New custom scenario —")

    # Determine current selection index to preserve it across reruns
    active: Optional[Scenario] = st.session_state.get("active_scenario")
    current_index = 0
    if active is not None:
        for i, label in enumerate(options):
            if active.name in label:
                current_index = i
                break

    selected = st.selectbox(
        "Scenario",
        options,
        index=current_index,
        key="_scenario_selectbox",
    )

    if selected == "— New custom scenario —":
        st.session_state._show_custom_builder = True
        st.session_state.active_scenario = None
        st.rerun(scope="app")

    elif selected in label_to_scenario:
        chosen = label_to_scenario[selected]
        prev = st.session_state.get("active_scenario")
        if prev is None or prev.name != chosen.name:
            st.session_state.active_scenario = chosen
            # Mark OMON as stale if data source already has a pull
            ds = st.session_state.get("data_source")
            if ds is not None and ds.is_omon_stale(chosen.name):
                st.session_state.omon_stale = True
            st.rerun(scope="app")

    # Delete button for saved scenarios
    if active is not None and active.is_custom:
        if st.button("Delete saved scenario", key="btn_delete_scenario"):
            delete_scenario(active.name)
            st.session_state.active_scenario = None
            st.rerun(scope="app")


# ---------------------------------------------------------------------------
# Custom scenario builder
# ---------------------------------------------------------------------------

def _render_custom_builder() -> None:
    """Expander with per-meeting override table and Save / Activate controls."""
    with st.expander("New custom scenario", expanded=True):
        val_date = st.session_state.val_date_manager.get_val_date()
        wirp_data = st.session_state.get("wirp_data", {})

        # 2-year FOMC meeting window
        from datetime import timedelta
        window_end = date(val_date.year + 2, val_date.month, val_date.day)
        meetings = date_utils.get_fomc_dates(val_date, window_end)

        rows = []
        for m in meetings:
            dist = wirp_data.get(m)
            wirp_str = _fmt_bp(dist.expected_change_bp) if dist is not None else "—"
            rows.append({"Meeting": m.isoformat(), "WIRP": wirp_str, "Override": ""})

        df = pd.DataFrame(rows)
        edited = st.data_editor(
            df,
            column_config={
                "Meeting": st.column_config.TextColumn("Meeting", disabled=True),
                "WIRP":    st.column_config.TextColumn("WIRP (implied)", disabled=True),
                "Override": st.column_config.SelectboxColumn(
                    "Override", options=_OUTCOME_OPTS, required=False
                ),
            },
            hide_index=True,
            use_container_width=True,
            key="_custom_builder_table",
        )

        name_input = st.text_input("Scenario name", key="_custom_scenario_name")
        col_save, col_activate, col_cancel = st.columns([1, 1, 1])

        with col_save:
            if st.button("Save", key="btn_save_custom", disabled=not name_input.strip()):
                scenario = _build_scenario_from_editor(edited, meetings, name_input.strip())
                save_scenario(scenario)
                st.session_state._show_custom_builder = False
                st.success(f"Saved '{name_input.strip()}'")
                st.rerun(scope="app")

        with col_activate:
            if st.button("Activate", key="btn_activate_custom", disabled=not name_input.strip()):
                scenario = _build_scenario_from_editor(edited, meetings, name_input.strip())
                st.session_state.active_scenario = scenario
                st.session_state._show_custom_builder = False
                st.rerun(scope="app")

        with col_cancel:
            if st.button("Cancel", key="btn_cancel_custom"):
                st.session_state._show_custom_builder = False
                st.rerun()


def _build_scenario_from_editor(
    df: pd.DataFrame,
    meetings: list[date],
    name: str,
) -> Scenario:
    """Convert the data_editor DataFrame to a Scenario object."""
    explicit: dict[date, int] = {}
    for _, row in df.iterrows():
        override = row["Override"]
        if override and override.strip():
            bp = int(override.replace("bp", "").strip())
            meeting = date.fromisoformat(row["Meeting"])
            explicit[meeting] = bp
    return build_custom_scenario(name, explicit)


# ---------------------------------------------------------------------------
# Parameter overrides
# ---------------------------------------------------------------------------

def _render_overrides() -> None:
    """Three independent override inputs: val_date, SOFR-FFR spread, underlying price."""
    st.caption("Parameter Overrides")

    vm: ValDateManager = st.session_state.val_date_manager
    any_override = (
        vm.is_overridden
        or st.session_state.get("sofr_ffr_spread_override") is not None
        or st.session_state.get("underlying_price_override") is not None
    )
    if any_override:
        st.warning("⚠ Override active — using non-live inputs", icon=None)

    col1, col2, col3 = st.columns(3)

    # Val date
    with col1:
        st.caption("Val date")
        new_val_date = st.date_input(
            "val_date",
            value=vm.get_val_date(),
            label_visibility="collapsed",
            key="_val_date_input",
        )
        if new_val_date != vm.get_val_date():
            vm.set_val_date(new_val_date)
            st.rerun(scope="app")
        if vm.is_overridden:
            if st.button("Reset to today", key="btn_reset_valdate"):
                vm.reset_val_date()
                st.rerun(scope="app")

    # SOFR-FFR spread
    with col2:
        st.caption("SOFR-FFR spread (bp)")
        active: Optional[Scenario] = st.session_state.get("active_scenario")
        default_spread = active.sofr_ffr_spread_bp if active is not None else 5.0
        spread_override = st.session_state.get("sofr_ffr_spread_override")
        spread_val = st.number_input(
            "spread",
            value=float(spread_override) if spread_override is not None else float(default_spread),
            step=1.0, format="%.1f",
            min_value=-50.0, max_value=100.0,
            label_visibility="collapsed",
            key="_spread_input",
        )
        if spread_override is None and spread_val != default_spread:
            st.session_state.sofr_ffr_spread_override = spread_val
            st.rerun(scope="app")
        elif spread_override is not None and spread_val != spread_override:
            st.session_state.sofr_ffr_spread_override = spread_val
            st.rerun(scope="app")
        if spread_override is not None:
            if st.button("Reset spread", key="btn_reset_spread"):
                st.session_state.sofr_ffr_spread_override = None
                st.rerun(scope="app")

    # Underlying price
    with col3:
        st.caption("Underlying price")
        px_override = st.session_state.get("underlying_price_override")
        re: Optional[RatesEngine] = st.session_state.get("rates_engine")
        live_curve = re.get_live_curve() if re is not None else {}
        active = st.session_state.get("active_scenario")
        # Default: first contract live price as reference
        contracts = st.session_state.get("sr3_contracts", [])
        default_px = live_curve.get(contracts[0], 95.0) if contracts else 95.0

        px_val = st.number_input(
            "price",
            value=float(px_override) if px_override is not None else float(default_px),
            step=_TICK, format="%.4f",
            label_visibility="collapsed",
            key="_price_input",
        )
        if px_override is None and abs(px_val - default_px) > 1e-6:
            st.session_state.underlying_price_override = px_val
            st.rerun(scope="app")
        elif px_override is not None and abs(px_val - px_override) > 1e-6:
            st.session_state.underlying_price_override = px_val
            st.rerun(scope="app")
        if px_override is not None:
            if st.button("Reset price", key="btn_reset_price"):
                st.session_state.underlying_price_override = None
                st.rerun(scope="app")


# ---------------------------------------------------------------------------
# Settlement target range
# ---------------------------------------------------------------------------

def _render_settlement_ranges() -> None:
    """Show default ±6/12/18 tick bands or explicit custom range inputs."""
    st.caption("Settlement Target Range")

    scenario: Scenario = st.session_state.active_scenario
    explicit_ranges: dict = st.session_state.get("explicit_ranges", {})
    contract_key = scenario.name   # keyed by scenario name for explicit range

    if contract_key in explicit_ranges:
        # Explicit range mode
        r = explicit_ranges[contract_key]
        lower_ticks = round(r.lower_bp / _TICK)
        upper_ticks = round(r.upper_bp / _TICK)
        col1, col2 = st.columns(2)
        with col1:
            new_lower = st.number_input(
                "Lower bound (ticks below midpoint)",
                value=lower_ticks, min_value=1, max_value=100, step=1,
                key="_range_lower",
            )
        with col2:
            new_upper = st.number_input(
                "Upper bound (ticks above midpoint)",
                value=upper_ticks, min_value=1, max_value=100, step=1,
                key="_range_upper",
            )
        if new_lower != lower_ticks or new_upper != upper_ticks:
            explicit_ranges[contract_key] = set_explicit_range(
                contract_key, new_lower * _TICK, new_upper * _TICK
            )
            st.session_state.explicit_ranges = explicit_ranges
            st.rerun(scope="app")
        if st.button("Reset to defaults (±6 / ±12 / ±18)", key="btn_reset_range"):
            clear_explicit_range(explicit_ranges, contract_key)
            st.session_state.explicit_ranges = explicit_ranges
            st.rerun(scope="app")
    else:
        # Default display mode
        st.write("Default bands: ±6 ticks | ±12 ticks | ±18 ticks")
        if st.button("Set custom range", key="btn_set_custom_range"):
            explicit_ranges[contract_key] = set_explicit_range(
                contract_key, 6 * _TICK, 6 * _TICK
            )
            st.session_state.explicit_ranges = explicit_ranges
            st.rerun(scope="app")


# ---------------------------------------------------------------------------
# Pull Options button
# ---------------------------------------------------------------------------

def _render_pull_options() -> None:
    """Pull Options button — triggers Stage 1 + Stage 2 OMON pull."""
    scenario: Scenario = st.session_state.active_scenario
    ds = st.session_state.get("data_source")

    # Stale warning
    if st.session_state.get("omon_stale", False):
        st.warning("Options data is stale — pulled for a different scenario.")

    # Timestamp line
    last_pull: Optional[datetime] = st.session_state.get("last_omon_pull")
    pulled_for: Optional[str] = st.session_state.get("omon_pulled_for")
    if last_pull is not None:
        ts_str = last_pull.strftime("%H:%M:%S")
        label_colour = ":orange" if st.session_state.get("omon_stale") else ""
        st.caption(f"Options: {label_colour}[{ts_str} ({pulled_for})]")

    # Pull button
    btn_label = f"Pull Options for {scenario.name}"
    if st.button(btn_label, key="btn_pull_options", disabled=(ds is None)):
        with st.spinner("Pulling OMON data…"):
            _execute_pull_options(scenario, ds)
        st.rerun(scope="app")


def _execute_pull_options(scenario: Scenario, ds) -> None:
    """Perform the two-stage OMON pull and update session state."""
    re: Optional[RatesEngine] = st.session_state.get("rates_engine")
    contracts: list[str] = st.session_state.get("sr3_contracts", [])
    wirp_data = st.session_state.get("wirp_data", {})

    if re is None or not contracts:
        st.error("Rates engine not initialised — refresh data first.")
        return

    # Find nearest quarterly contract for option chain pull
    quarterly = _nearest_quarterly(contracts, st.session_state.val_date_manager.get_val_date())
    if quarterly is None:
        st.error("No valid SR3 contract found.")
        return

    # Scenario settlement price for this contract → use as target strike
    scenario_path = assemble_rate_path(scenario, wirp_data)
    scenario_curve = re.get_scenario_curve(scenario_path)
    raw_price = scenario_curve.get(quarterly)
    if raw_price is None:
        st.error(f"Scenario price not available for {quarterly}.")
        return
    # Snap to nearest valid SR3 strike (0.0625 spacing)
    target_strike = round(raw_price / _STRIKE_SPACING) * _STRIKE_SPACING

    # Stage 1: pull option chain tickers
    tickers = ds.pull_option_chain(f"{quarterly} Comdty")
    if not tickers:
        st.error("No option tickers returned from Bloomberg.")
        return

    # Stage 2: pull strike data filtered to ±5 strikes around target
    omon_chain = ds.pull_strike_data(tickers, target_strike)

    # Update session state
    ds._omon_pulled_for = scenario.name
    st.session_state.omon_stale = False
    st.session_state.last_omon_pull = datetime.now()
    st.session_state.omon_pulled_for = scenario.name
    st.session_state.omon_chain = omon_chain


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _fmt_bp(bp_float: float) -> str:
    """Format a float bp value as '+25bp', '-25bp', '0bp'."""
    rounded = round(bp_float)
    if rounded == 0:
        return "0bp"
    return f"{rounded:+d}bp"


def _nearest_quarterly(contracts: list[str], val_date: date) -> Optional[str]:
    """Return the front quarterly SR3 contract (nearest expiry after val_date)."""
    quarterly = [c for c in contracts if "_" not in c]   # exclude SR3H_1 etc.
    candidates = []
    for code in quarterly:
        try:
            _, expiry = _contract_period(code, val_date)
            if expiry >= val_date:
                candidates.append((expiry, code))
        except Exception:
            pass
    if not candidates:
        return None
    return min(candidates, key=lambda x: x[0])[1]
