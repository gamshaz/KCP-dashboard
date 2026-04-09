"""dashboard/trade_panel.py — Ranked candidate list, range tabs, and structure detail.

Rendered as @st.fragment (requires Streamlit >= 1.37.0).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from options_pricer import LegResult, OptionLeg, StructureResult, price_structure
from preferences import TraderPreferences, default_preferences
from products.sofr import SOFR
from ranker import RankedCandidate, RankedList, rank_candidates
from rates_engine import _contract_period
from scenario_engine import (
    SettlementTargetRange,
    assemble_rate_path,
    get_active_ranges,
)
from skew_logic import KinkFlag, SkewAnalysis, analyse_skew, build_vol_ladder
from trade_builder import CandidateStructure, build_candidates
from trade_structures import Structure

_TICK = 0.005
_sofr = SOFR()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

@st.fragment
def render_trade_panel() -> None:
    """Render the ranked trade candidate panel (centre column of dashboard)."""
    # Stale OMON warning
    if st.session_state.get("omon_stale", False):
        st.warning("⚠ Options data is stale — re-pull for the current scenario.")

    scenario = st.session_state.get("active_scenario")
    omon_chain = st.session_state.get("omon_chain")

    if scenario is None:
        st.info("Select a scenario in the left panel.")
        return
    if omon_chain is None:
        st.info("Pull options data to see trade recommendations.")
        return

    ranked_lists = _get_ranked_lists(scenario, omon_chain)
    if not ranked_lists:
        st.info("No candidates generated — check preferences and OMON data.")
        return

    # Range tabs
    tab_labels = [
        f"±{rl.range_lower_ticks}t" if rl.range_lower_ticks == rl.range_upper_ticks
        else f"↓{rl.range_lower_ticks}t / ↑{rl.range_upper_ticks}t"
        for rl in ranked_lists
    ]
    tabs = st.tabs(tab_labels)

    for tab, ranked_list in zip(tabs, ranked_lists):
        with tab:
            _render_ranked_table(ranked_list)

    # Structure detail expands below tabs when a candidate is selected
    sel = st.session_state.get("selected_candidate")
    if sel is not None:
        st.divider()
        _render_structure_detail(sel)


# ---------------------------------------------------------------------------
# Candidate computation
# ---------------------------------------------------------------------------

def _get_ranked_lists(scenario, omon_chain) -> list[RankedList]:
    """Compute ranked lists from OMON data and scenario. Cached in session state."""
    prefs: TraderPreferences = st.session_state.get("preferences") or default_preferences()
    cache_key = (id(omon_chain), scenario.name, id(prefs))
    if st.session_state.get("_trade_cache_key") == cache_key:
        return st.session_state.get("_ranked_lists", [])

    re = st.session_state.get("rates_engine")
    if re is None:
        return []

    wirp_data = st.session_state.get("wirp_data", {})
    scenario_path = assemble_rate_path(scenario, wirp_data)
    scenario_curve = re.get_scenario_curve(scenario_path)  # {contract_code: price}

    val_date: date = st.session_state.val_date_manager.get_val_date()
    contracts: list[str] = st.session_state.get("sr3_contracts", [])

    # Map expiry date → scenario price and expiry_years
    scenario_prices: dict[date, float] = {}
    expiry_years: dict[date, float] = {}
    for expiry in omon_chain:
        for code in contracts:
            try:
                _, code_expiry = _contract_period(code, val_date)
                if code_expiry == expiry:
                    price = scenario_curve.get(code)
                    if price is not None:
                        scenario_prices[expiry] = price
                    break
            except Exception:
                pass
        expiry_years[expiry] = _sofr.year_fraction(val_date, expiry)

    # Active ranges (using scenario.name as the contract key per scenario_panel convention)
    explicit_ranges = st.session_state.get("explicit_ranges", {})
    ref_price = next(iter(scenario_prices.values()), 95.0) if scenario_prices else 95.0
    active_ranges = get_active_ranges(scenario.name, ref_price, explicit_ranges)

    # Skew analyses per expiry
    skew_analyses: dict[date, SkewAnalysis] = {}
    for expiry, chain_expiry in omon_chain.items():
        try:
            ladder = build_vol_ladder(chain_expiry)
            skew_analyses[expiry] = analyse_skew(ladder, prefs.kink_mode)
        except Exception:
            pass

    # Build and rank
    candidates = build_candidates(
        omon_chain=omon_chain,
        scenario_prices=scenario_prices,
        expiry_years=expiry_years,
        active_ranges=active_ranges,
        skew_analyses=skew_analyses,
        preferences=prefs,
    )
    ranked = rank_candidates(candidates, active_ranges)

    # Cache results and supporting data for detail view
    st.session_state._trade_cache_key = cache_key
    st.session_state._ranked_lists = ranked
    st.session_state._scenario_prices_by_expiry = scenario_prices
    st.session_state._active_ranges = active_ranges

    return ranked


# ---------------------------------------------------------------------------
# Ranked table
# ---------------------------------------------------------------------------

def _render_ranked_table(ranked_list: RankedList) -> None:
    """Display the ranked candidate table. Row click selects for detail view."""
    all_rc: list[RankedCandidate] = ranked_list.long_premium + ranked_list.short_premium
    if not all_rc:
        st.caption("No candidates in this range.")
        return

    rows = []
    for rc in all_rc:
        c = rc.candidate
        s = c.structure
        rows.append({
            "Expiry":    _fmt_expiry(c.expiry),
            "Structure": f"{s.structure_type.value} @{s.centre_strike:.3f}",
            "Type":      "Long" if c.is_long_premium else "Short",
            "R:R":       f"{rc.rr_score:.2f}x" if rc.rr_score is not None else "—",
            "Theta":     "▲" if rc.theta_sign.value == "positive" else "▼",
            "Dist(t)":   rc.market_distance_ticks,
            "Min P&L":   f"{rc.pnl_profile.min_pnl:+.4f}",
            "Max P&L":   f"{rc.pnl_profile.max_pnl:+.4f}",
            "Mean P&L":  f"{rc.pnl_profile.mean_pnl:+.4f}",
        })

    df = pd.DataFrame(rows)
    event = st.dataframe(
        df,
        use_container_width=True,
        hide_index=True,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Expiry":    st.column_config.TextColumn("Expiry", width="small"),
            "Structure": st.column_config.TextColumn("Structure"),
            "Type":      st.column_config.TextColumn("Type", width="small"),
            "R:R":       st.column_config.TextColumn("R:R", width="small"),
            "Theta":     st.column_config.TextColumn("Θ", width="small"),
            "Dist(t)":   st.column_config.NumberColumn("Dist(t)", width="small"),
            "Min P&L":   st.column_config.TextColumn("Min P&L"),
            "Max P&L":   st.column_config.TextColumn("Max P&L"),
            "Mean P&L":  st.column_config.TextColumn("Mean P&L"),
        },
    )

    selected_rows = event.selection.rows if hasattr(event, "selection") else []
    if selected_rows:
        idx = selected_rows[0]
        if 0 <= idx < len(all_rc):
            new_sel = all_rc[idx].candidate
            if st.session_state.get("selected_candidate") is not new_sel:
                st.session_state.selected_candidate = new_sel
                st.session_state._strike_overrides = {}
                st.rerun()


# ---------------------------------------------------------------------------
# Structure detail view
# ---------------------------------------------------------------------------

def _render_structure_detail(candidate: CandidateStructure) -> None:
    """Five-section expandable detail for a selected candidate."""
    s = candidate.structure
    omon_chain = st.session_state.get("omon_chain", {})
    omon_expiry = omon_chain.get(candidate.expiry, {})
    scenario_prices = st.session_state.get("_scenario_prices_by_expiry", {})
    active_ranges: list[SettlementTargetRange] = st.session_state.get("_active_ranges", [])
    val_date = st.session_state.val_date_manager.get_val_date()
    expiry_years = _sofr.year_fraction(val_date, candidate.expiry)
    forward = scenario_prices.get(candidate.expiry, s.centre_strike)
    px_override = st.session_state.get("underlying_price_override")

    st.subheader(
        f"{s.structure_type.value.replace('_', ' ').title()}  ·  "
        f"{_fmt_expiry(candidate.expiry)}  ·  @{s.centre_strike:.3f}",
        divider=False,
    )

    if st.button("✕ Clear selection", key="btn_clear_sel"):
        st.session_state.selected_candidate = None
        st.session_state._strike_overrides = {}
        st.rerun()

    # --- Section 1: Leg breakdown (editable strikes) -----------------------
    st.caption("Legs")
    strike_overrides: dict[int, float] = st.session_state.get("_strike_overrides", {})
    leg_rows = []
    for i, leg in enumerate(s.legs):
        orig_strike = s.strike_for(leg)
        quote = omon_expiry.get(orig_strike)
        leg_rows.append({
            "Leg":        i + 1,
            "Put/Call":   leg.put_call.capitalize(),
            "Strike":     strike_overrides.get(i, orig_strike),
            "Qty":        leg.quantity,
            "Mkt Price":  quote.last_price if quote else float("nan"),
            "Delta":      quote.delta if quote else float("nan"),
        })

    leg_df = pd.DataFrame(leg_rows)
    edited_legs = st.data_editor(
        leg_df,
        column_config={
            "Leg":       st.column_config.NumberColumn("Leg", disabled=True),
            "Put/Call":  st.column_config.TextColumn("Put/Call", disabled=True),
            "Strike":    st.column_config.NumberColumn("Strike", format="%.4f", step=0.0625),
            "Qty":       st.column_config.NumberColumn("Qty", disabled=True),
            "Mkt Price": st.column_config.NumberColumn("Mkt Price", format="%.4f", disabled=True),
            "Delta":     st.column_config.NumberColumn("Delta", format="%.3f", disabled=True),
        },
        hide_index=True,
        use_container_width=True,
        key="_leg_editor",
    )

    # Detect strike changes and update overrides
    new_overrides: dict[int, float] = {}
    for i, leg in enumerate(s.legs):
        edited_k = float(edited_legs.loc[i, "Strike"])
        orig_k = s.strike_for(leg)
        if abs(edited_k - orig_k) > 1e-6:
            new_overrides[i] = edited_k
    if new_overrides != strike_overrides:
        st.session_state._strike_overrides = new_overrides
        st.rerun()

    # Build repriced OptionLegs
    option_legs = _build_option_legs(s, omon_expiry, expiry_years, new_overrides)
    if option_legs:
        repriced = price_structure(option_legs, forward, underlying_price_override=px_override)
    else:
        repriced = None

    # --- Section 2: Structure summary -------------------------------------
    st.caption("Summary")
    c1, c2, c3, c4 = st.columns(4)
    net_prem = repriced.net_premium if repriced else candidate.net_premium
    delta_val = repriced.delta if repriced else candidate.delta
    vega_val = repriced.vega if repriced else candidate.vega
    theta_val = repriced.theta_per_day if repriced else candidate.theta_per_day

    c1.metric("Net Premium", f"{net_prem:+.4f}")
    c2.metric("Delta", f"{delta_val:+.4f}")
    c3.metric("Vega", f"{vega_val:+.5f}")
    c4.metric("Theta/day", f"{theta_val:+.5f}")

    # --- Section 3: P&L chart ---------------------------------------------
    st.caption("P&L at Expiry")
    _render_pnl_chart(s, net_prem, forward, active_ranges, candidate.expiry)

    # --- Section 4: Per-leg Greeks table ----------------------------------
    st.caption("Greeks per Leg")
    if repriced:
        greeks_rows = []
        for i, (leg, lr) in enumerate(zip(s.legs, repriced.legs)):
            k = strike_overrides.get(i, s.strike_for(leg))
            greeks_rows.append({
                "Leg":   i + 1,
                "Strike": f"{k:.4f}",
                "Δ":      f"{lr.delta:+.4f}",
                "Γ":      f"{lr.gamma:+.5f}",
                "ν":      f"{lr.vega:+.5f}",
                "Θ/day":  f"{lr.theta_per_day:+.5f}",
            })
        # Totals row
        greeks_rows.append({
            "Leg":    "Total",
            "Strike": "—",
            "Δ":      f"{repriced.delta:+.4f}",
            "Γ":      f"{repriced.gamma:+.5f}",
            "ν":      f"{repriced.vega:+.5f}",
            "Θ/day":  f"{repriced.theta_per_day:+.5f}",
        })
        st.dataframe(pd.DataFrame(greeks_rows), hide_index=True, use_container_width=True)

    # --- Section 5: Kink flags --------------------------------------------
    if candidate.kink_flags:
        st.caption("Kink Signals")
        kink_rows = [
            {"Strike": f"{k:.4f}", "Signal": _fmt_kink(flag)}
            for k, flag in sorted(candidate.kink_flags.items())
        ]
        st.dataframe(pd.DataFrame(kink_rows), hide_index=True, use_container_width=True)


def _render_pnl_chart(
    s: Structure,
    net_premium: float,
    scenario_midpoint: float,
    active_ranges: list[SettlementTargetRange],
    expiry: date,
) -> None:
    """P&L at expiry chart with shaded settlement range and midpoint dashed line."""
    if not active_ranges:
        return

    # Chart domain: widest range ± 5 ticks buffer
    max_lower = max(r.lower_bp for r in active_ranges)
    max_upper = max(r.upper_bp for r in active_ranges)
    buffer = 5 * _TICK
    x_lo = scenario_midpoint - max_lower - buffer
    x_hi = scenario_midpoint + max_upper + buffer
    n_points = round((x_hi - x_lo) / _TICK)
    xs = [round(x_lo + i * _TICK, 5) for i in range(n_points + 1)]
    ys = [s.compute_payoff(x) - net_premium for x in xs]

    fig = go.Figure()

    # Shaded ranges
    for r in active_ranges:
        lo = scenario_midpoint - r.lower_bp
        hi = scenario_midpoint + r.upper_bp
        fig.add_vrect(
            x0=lo, x1=hi,
            fillcolor="steelblue", opacity=0.1,
            line_width=0,
        )

    # P&L line
    fig.add_trace(go.Scatter(
        x=xs, y=ys,
        mode="lines", name="P&L at expiry",
        line=dict(color="white", width=2),
    ))

    # Zero line
    fig.add_hline(y=0, line=dict(color="grey", dash="dash", width=1))

    # Midpoint vertical
    fig.add_vline(
        x=scenario_midpoint,
        line=dict(color="orange", dash="dash", width=1),
        annotation_text="Midpoint",
        annotation_position="top",
    )

    fig.update_layout(
        height=220,
        margin=dict(l=0, r=0, t=8, b=0),
        xaxis_title="Settlement Price",
        yaxis_title="P&L",
        showlegend=False,
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_option_legs(
    s: Structure,
    omon_expiry: dict,
    expiry_years: float,
    strike_overrides: dict[int, float],
) -> list[OptionLeg]:
    """Build OptionLeg list from a Structure, using OMON quotes for vol and price."""
    legs = []
    for i, leg in enumerate(s.legs):
        k = strike_overrides.get(i, s.strike_for(leg))
        # Look up nearest available strike in OMON for vol/price
        quote = omon_expiry.get(k) or _nearest_quote(omon_expiry, k)
        if quote is None:
            return []
        legs.append(OptionLeg(
            put_call=leg.put_call,
            strike=k,
            quantity=leg.quantity,
            expiry_years=expiry_years,
            implied_vol=quote.implied_vol,
            last_price=quote.last_price,
        ))
    return legs


def _nearest_quote(omon_expiry: dict, target_strike: float):
    """Return the OptionQuote for the strike nearest to target_strike."""
    if not omon_expiry:
        return None
    return min(omon_expiry.values(), key=lambda q: abs(q.strike - target_strike))


def _fmt_expiry(d: date) -> str:
    return d.strftime("%b-%y")


def _fmt_kink(flag: KinkFlag) -> str:
    return flag.value.replace("_", " ").title()
