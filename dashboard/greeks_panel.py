"""dashboard/greeks_panel.py — Greeks summary card, time decay table, scenario P&L chart.

Rendered as @st.fragment (requires Streamlit >= 1.37.0).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from options_pricer import OptionLeg, StructureResult, price_structure
from products.sofr import SOFR
from scenario_engine import SettlementTargetRange
from trade_builder import CandidateStructure
from trade_structures import Structure
from utils import date_utils

_TICK = 0.005
_sofr = SOFR()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

@st.fragment
def render_greeks_panel() -> None:
    """Render the Greeks and P&L panel (right column of dashboard)."""
    candidate: Optional[CandidateStructure] = st.session_state.get("selected_candidate")

    if candidate is None:
        st.info("Select a trade from the centre panel to see Greeks and P&L analysis.")
        return

    repriced = _get_repriced(candidate)

    col_left, col_right = st.columns([1, 1])

    with col_left:
        _render_greeks_card(candidate, repriced)
        st.divider()
        _render_time_decay_table(candidate)

    with col_right:
        _render_scenario_pnl_chart(candidate, repriced)


# ---------------------------------------------------------------------------
# Repricing helper (shared across all sections)
# ---------------------------------------------------------------------------

def _get_repriced(candidate: CandidateStructure) -> Optional[StructureResult]:
    """Build a StructureResult for the selected candidate respecting any strike overrides.

    Vol and market price per strike are fixed to OMON values. If any leg's
    (potentially overridden) strike has no OMON quote, falls back to the
    nearest available quote.
    """
    s = candidate.structure
    omon_chain = st.session_state.get("omon_chain", {})
    omon_expiry: dict = omon_chain.get(candidate.expiry, {})
    val_date: date = st.session_state.val_date_manager.get_val_date()
    expiry_years = _sofr.year_fraction(val_date, candidate.expiry)
    scenario_prices: dict = st.session_state.get("_scenario_prices_by_expiry", {})
    forward = scenario_prices.get(candidate.expiry, s.centre_strike)
    px_override = st.session_state.get("underlying_price_override")
    strike_overrides: dict[int, float] = st.session_state.get("_strike_overrides", {})

    option_legs: list[OptionLeg] = []
    for i, leg in enumerate(s.legs):
        k = strike_overrides.get(i, s.strike_for(leg))
        quote = omon_expiry.get(k) or _nearest_quote(omon_expiry, k)
        if quote is None:
            return None
        option_legs.append(OptionLeg(
            put_call=leg.put_call,
            strike=k,
            quantity=leg.quantity,
            expiry_years=expiry_years,
            implied_vol=quote.implied_vol,
            last_price=quote.last_price,
        ))

    if not option_legs:
        return None

    return price_structure(option_legs, forward, underlying_price_override=px_override)


# ---------------------------------------------------------------------------
# Greeks summary card
# ---------------------------------------------------------------------------

def _render_greeks_card(
    candidate: CandidateStructure,
    repriced: Optional[StructureResult],
) -> None:
    """Four-metric card: Δ, Γ, ν, Θ with directional colour indicators."""
    delta = repriced.delta if repriced else candidate.delta
    gamma = repriced.gamma if repriced else candidate.gamma
    vega  = repriced.vega  if repriced else candidate.vega
    theta = repriced.theta_per_day if repriced else candidate.theta_per_day

    st.caption("Greeks (structure net)")
    c1, c2 = st.columns(2)
    c3, c4 = st.columns(2)

    # st.metric delta parameter: positive → green arrow up, negative → red arrow down
    with c1:
        st.metric("Delta (Δ)", f"{delta:+.4f}",
                  delta=delta, delta_color="normal")
    with c2:
        st.metric("Gamma (Γ)", f"{gamma:+.5f}",
                  delta=gamma, delta_color="normal")
    with c3:
        # Vega: positive = long vol (favourable if vol_view == VOL_UP)
        st.metric("Vega (ν)", f"{vega:+.5f}",
                  delta=vega, delta_color="normal")
    with c4:
        # Theta: positive = receiving (trading sign — credit/short premium)
        st.metric("Theta/day (Θ)", f"{theta:+.5f}",
                  delta=theta, delta_color="normal")

    net_prem = repriced.net_premium if repriced else candidate.net_premium
    prem_label = f"Net premium: {net_prem:+.4f} pts  ({'Long' if net_prem > 0 else 'Short'})"
    st.caption(prem_label)


# ---------------------------------------------------------------------------
# Time decay table
# ---------------------------------------------------------------------------

def _render_time_decay_table(candidate: CandidateStructure) -> None:
    """Daily theta and cumulative theta from val_date to expiry.

    Vol, underlying price, and strikes are held constant at OMON values.
    Theta is shown in ticks (divide price-point theta by 0.005).
    """
    st.caption("Time Decay")

    # Cache key: candidate identity + strike overrides + val_date
    val_date: date = st.session_state.val_date_manager.get_val_date()
    strike_overrides = st.session_state.get("_strike_overrides", {})
    cache_key = (id(candidate), tuple(sorted(strike_overrides.items())), val_date)
    cached_key = st.session_state.get("_decay_cache_key")
    if cached_key == cache_key:
        df = st.session_state.get("_decay_table_df", pd.DataFrame())
        st.dataframe(df, hide_index=True, use_container_width=True, height=300)
        return

    # Build legs at fixed vol / price
    s = candidate.structure
    omon_chain = st.session_state.get("omon_chain", {})
    omon_expiry: dict = omon_chain.get(candidate.expiry, {})
    scenario_prices: dict = st.session_state.get("_scenario_prices_by_expiry", {})
    forward = scenario_prices.get(candidate.expiry, s.centre_strike)

    base_legs: list[tuple] = []
    for i, leg in enumerate(s.legs):
        k = strike_overrides.get(i, s.strike_for(leg))
        quote = omon_expiry.get(k) or _nearest_quote(omon_expiry, k)
        if quote is None:
            st.caption("OMON data unavailable for time decay.")
            return
        base_legs.append((leg.put_call, k, leg.quantity, quote.implied_vol, quote.last_price))

    biz_days = _enum_business_days(val_date, candidate.expiry)

    rows = []
    cumulative_ticks = 0.0
    for d in biz_days:
        T = _sofr.year_fraction(d, candidate.expiry)
        option_legs = [
            OptionLeg(put_call=pc, strike=k, quantity=qty,
                      expiry_years=max(T, 0.0),
                      implied_vol=vol, last_price=lp)
            for pc, k, qty, vol, lp in base_legs
        ]
        result = price_structure(option_legs, forward)
        theta_ticks = result.theta_per_day / _TICK
        cumulative_ticks += theta_ticks
        rows.append({
            "Date":              d.isoformat(),
            "Days to Exp":       (candidate.expiry - d).days,
            "Theta (ticks/day)": round(theta_ticks, 3),
            "Cum. Theta (ticks)":round(cumulative_ticks, 3),
        })

    df = pd.DataFrame(rows)
    st.session_state._decay_cache_key = cache_key
    st.session_state._decay_table_df = df
    st.dataframe(df, hide_index=True, use_container_width=True, height=300)


# ---------------------------------------------------------------------------
# Scenario P&L chart — three series
# ---------------------------------------------------------------------------

def _render_scenario_pnl_chart(
    candidate: CandidateStructure,
    repriced: Optional[StructureResult],
) -> None:
    """Three-series P&L chart: expiry payoff, live (val_date), midpoint date.

    X-axis: settlement price grid.
    Each series shows net P&L = theoretical structure value - net_premium_paid.

    Series:
      1. P&L at expiry       — intrinsic payoff only (no time value)
      2. P&L at val_date     — full Black-76 at current vol and current T
      3. P&L at midpoint date— Black-76 at T/2, vol held constant

    Vertical dashed line at scenario midpoint. Shaded active range(s).
    """
    s = candidate.structure
    omon_chain = st.session_state.get("omon_chain", {})
    omon_expiry: dict = omon_chain.get(candidate.expiry, {})
    val_date: date = st.session_state.val_date_manager.get_val_date()
    scenario_prices: dict = st.session_state.get("_scenario_prices_by_expiry", {})
    scenario_midpoint = scenario_prices.get(candidate.expiry, s.centre_strike)
    active_ranges: list[SettlementTargetRange] = st.session_state.get("_active_ranges", [])
    strike_overrides: dict[int, float] = st.session_state.get("_strike_overrides", {})
    px_override = st.session_state.get("underlying_price_override")

    net_premium = repriced.net_premium if repriced else candidate.net_premium

    # Build (put_call, strike, quantity, implied_vol, last_price) for each leg
    leg_specs: list[tuple] = []
    for i, leg in enumerate(s.legs):
        k = strike_overrides.get(i, s.strike_for(leg))
        quote = omon_expiry.get(k) or _nearest_quote(omon_expiry, k)
        if quote is None:
            st.caption("Cannot build P&L chart — OMON data missing.")
            return
        leg_specs.append((leg.put_call, k, leg.quantity, quote.implied_vol, quote.last_price))

    # Grid domain: widest range + 10-tick buffer on each side
    max_lower = max((r.lower_bp for r in active_ranges), default=6 * _TICK)
    max_upper = max((r.upper_bp for r in active_ranges), default=6 * _TICK)
    buffer = 10 * _TICK
    x_lo = scenario_midpoint - max_lower - buffer
    x_hi = scenario_midpoint + max_upper + buffer
    n_steps = round((x_hi - x_lo) / _TICK)
    xs = [round(x_lo + i * _TICK, 5) for i in range(n_steps + 1)]

    # --- Series 1: P&L at expiry (intrinsic) ---
    ys_expiry = [s.compute_payoff(x) - net_premium for x in xs]

    # --- Series 2: P&L at val_date ---
    T_val = _sofr.year_fraction(val_date, candidate.expiry)
    ys_live = _pnl_series(leg_specs, xs, T_val, net_premium, px_override)

    # --- Series 3: P&L at midpoint date ---
    days_remaining = (candidate.expiry - val_date).days
    mid_date = val_date + timedelta(days=max(days_remaining // 2, 0))
    T_mid = _sofr.year_fraction(mid_date, candidate.expiry)
    ys_mid = _pnl_series(leg_specs, xs, T_mid, net_premium, px_override)

    # Build figure
    fig = go.Figure()

    # Shaded active ranges
    for r in active_ranges:
        fig.add_vrect(
            x0=scenario_midpoint - r.lower_bp,
            x1=scenario_midpoint + r.upper_bp,
            fillcolor="steelblue", opacity=0.1, line_width=0,
        )

    # Three P&L series
    fig.add_trace(go.Scatter(
        x=xs, y=ys_live,
        mode="lines", name="Live (val date)",
        line=dict(color="steelblue", width=2, dash="dot"),
    ))
    fig.add_trace(go.Scatter(
        x=xs, y=ys_mid,
        mode="lines", name="Midpoint date",
        line=dict(color="mediumpurple", width=2, dash="dash"),
    ))
    fig.add_trace(go.Scatter(
        x=xs, y=ys_expiry,
        mode="lines", name="At expiry",
        line=dict(color="white", width=2),
    ))

    # Zero line and midpoint marker
    fig.add_hline(y=0, line=dict(color="grey", dash="dash", width=1))
    fig.add_vline(
        x=scenario_midpoint,
        line=dict(color="orange", dash="dash", width=1),
        annotation_text="Scenario mid",
        annotation_position="top right",
    )

    fig.update_layout(
        height=380,
        margin=dict(l=0, r=0, t=24, b=0),
        title=dict(text="Scenario P&L", font=dict(size=13)),
        xaxis_title="Settlement Price",
        yaxis_title="P&L (price pts)",
        legend=dict(orientation="h", y=-0.18),
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _pnl_series(
    leg_specs: list[tuple],
    xs: list[float],
    expiry_years: float,
    net_premium: float,
    px_override: Optional[float],
) -> list[float]:
    """Compute theoretical P&L for each settlement price x.

    For each x: treat x as the forward price (underlying). Strikes and vols fixed.
    P&L = net_theoretical_value - net_premium_paid.
    """
    ys: list[float] = []
    for x in xs:
        option_legs = [
            OptionLeg(put_call=pc, strike=k, quantity=qty,
                      expiry_years=max(expiry_years, 0.0),
                      implied_vol=vol, last_price=lp)
            for pc, k, qty, vol, lp in leg_specs
        ]
        result = price_structure(option_legs, forward=x, underlying_price_override=px_override)
        ys.append(result.net_theoretical_value - net_premium)
    return ys


def _enum_business_days(start: date, end: date) -> list[date]:
    """Return all CME business days in [start, end] inclusive.

    Uses date_utils.business_days_between: if business_days_between(d-1day, d) == 1,
    then d is a business day.
    """
    result: list[date] = []
    d = start
    one_day = timedelta(days=1)
    while d <= end:
        if date_utils.business_days_between(d - one_day, d) == 1:
            result.append(d)
        d += one_day
    return result


def _nearest_quote(omon_expiry: dict, target_strike: float):
    """Return the OptionQuote for the strike nearest to target_strike."""
    if not omon_expiry:
        return None
    return min(omon_expiry.values(), key=lambda q: abs(q.strike - target_strike))
