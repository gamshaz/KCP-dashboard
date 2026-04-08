from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional

from bloomberg import OptionQuote
from options_pricer import OptionLeg, StructureResult, price_structure
from preferences import OpenRisk, TraderPreferences, VolView
from scenario_engine import SettlementTargetRange
from skew_logic import KinkFlag, SkewAnalysis
from trade_structures import (
    Structure,
    make_call_spread,
    make_condor,
    make_fly,
    make_ladder,
    make_put_spread,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ThetaSign(Enum):
    POSITIVE = "positive"   # receiving theta (short premium)
    NEGATIVE = "negative"   # paying theta (long premium)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class PnLProfile:
    """P&L summary for a candidate over a single settlement target range."""
    min_pnl: float
    max_pnl: float
    mean_pnl: float


@dataclass
class CandidateStructure:
    """A fully evaluated trade candidate for a single expiry.

    pnl_by_range keys are (lower_ticks, upper_ticks) tuples produced by
    range_key(SettlementTargetRange). ranker.py uses the same helper to
    look up profiles per active range.

    NOTE on last_price / implied_vol:
    Bloomberg pull_strike_data returns one OptionQuote per strike (no call/put
    distinction). Prices and vols are therefore shared across put and call
    structures at the same strike. This is adequate for Phase 1 given that
    SR3 options implied vols are equal for calls and puts (put-call parity)
    and mid prices are close. A richer OMON pull distinguishing calls/puts
    is a Phase 2 upgrade.
    """
    expiry: date
    structure: Structure
    net_premium: float          # positive = debit (long premium), negative = credit
    is_long_premium: bool
    delta: float
    gamma: float
    vega: float
    theta_per_day: float
    theta_sign: ThetaSign
    kink_flags: dict[float, KinkFlag]   # strike → flag for each leg
    pnl_by_range: dict[tuple[int, int], PnLProfile]   # range_key → profile
    market_distance_ticks: int          # signed ticks from target strike (informational)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Broken-wing fly: for each primary width, the set of valid upper-wing offsets
_BROKEN_UPPER_BY_WIDTH: dict[int, list[int]] = {
    6:  [8, 10, 12],
    12: [6, 8, 10],
}

_CONDOR_INNER = 3
_CONDOR_OUTER = 9
_CONDOR_BROKEN_OUTER_WIDTHS = [6, 8, 10]   # broken upper outer wings (all ≠ 9)

_LADDER_WIDTHS = [6, 12]

# Hard filter threshold for FLAT mode: |net delta| must be ≤ this value
_FLAT_DELTA_EPSILON = 0.05

# Symmetric strike window around target (each side)
_STRIKE_WINDOW = 5


# ---------------------------------------------------------------------------
# Exported helper — used by ranker.py to look up pnl_by_range
# ---------------------------------------------------------------------------

def range_key(r: SettlementTargetRange, tick: float = 0.005) -> tuple[int, int]:
    """Canonical dict key for a SettlementTargetRange.

    Returns (lower_ticks, upper_ticks) as integers.
    Both trade_builder and ranker use this to ensure consistent lookup.
    """
    return (round(r.lower_bp / tick), round(r.upper_bp / tick))


# ---------------------------------------------------------------------------
# Private helpers — strike universe
# ---------------------------------------------------------------------------

def _find_target_strike(strikes: list[float], target_price: float) -> float:
    """Return the strike in strikes nearest to target_price."""
    return min(strikes, key=lambda k: abs(k - target_price))


def _get_strike_universe(
    all_strikes: list[float],
    target_strike: float,
    window: int = _STRIKE_WINDOW,
    tick: float = 0.005,
) -> list[float]:
    """Return strikes within ±window ticks of target_strike, sorted ascending."""
    radius = window * tick + 1e-9
    return sorted(k for k in all_strikes if abs(k - target_strike) <= radius)


# ---------------------------------------------------------------------------
# Private helpers — structure generation
# ---------------------------------------------------------------------------

def _generate_structures(
    strike_universe: list[float],
    tick: float = 0.005,
) -> list[Structure]:
    """Generate all candidate structures across the strike universe.

    Iterates over every valid centre strike and produces:
      - Symmetric flies (widths 6, 12) — calls and puts
      - Broken flies (broken upper wing) — calls and puts
      - Standard condor (inner=3, outer=9) — calls and puts
      - Broken condors (varied upper outer wing) — calls and puts
      - Ladders (widths 6, 12) — calls and puts
      - Call spreads — all (lower, upper) pairs in universe
      - Put spreads — all (upper, lower) pairs in universe
    """
    structures: list[Structure] = []

    for centre in strike_universe:
        for pc in ("call", "put"):
            # Symmetric flies
            for width in (6, 12):
                structures.append(make_fly(centre, width, pc))

            # Broken flies
            for width, broken_uppers in _BROKEN_UPPER_BY_WIDTH.items():
                for bu in broken_uppers:
                    structures.append(make_fly(centre, width, pc, broken_upper=bu))

            # Standard condor
            structures.append(make_condor(centre, _CONDOR_INNER, _CONDOR_OUTER, pc))

            # Broken condors
            for buo in _CONDOR_BROKEN_OUTER_WIDTHS:
                structures.append(
                    make_condor(centre, _CONDOR_INNER, _CONDOR_OUTER, pc,
                                broken_upper_outer=buo)
                )

            # Ladders
            for width in _LADDER_WIDTHS:
                structures.append(make_ladder(centre, width, pc))

    # Call and put spreads — all pairs in universe
    n = len(strike_universe)
    for i in range(n):
        for j in range(i + 1, n):
            lower_k = strike_universe[i]
            upper_k = strike_universe[j]
            structures.append(make_call_spread(lower_k, upper_k))
            structures.append(make_put_spread(upper_k, lower_k))

    return structures


# ---------------------------------------------------------------------------
# Private helpers — pricing
# ---------------------------------------------------------------------------

def _price_candidate(
    structure: Structure,
    omon_expiry: dict[float, OptionQuote],
    scenario_price: float,
    expiry_years: float,
    tick: float = 0.005,
) -> Optional[tuple[float, StructureResult]]:
    """Price all legs of a structure using OMON market data.

    Returns (net_premium, StructureResult) or None if any leg strike is
    absent from the OMON chain.

    forward = scenario_price (theoretical futures price under scenario).
    """
    option_legs: list[OptionLeg] = []
    for leg in structure.legs:
        k = structure.strike_for(leg)
        quote = omon_expiry.get(k)
        if quote is None:
            return None
        option_legs.append(
            OptionLeg(
                put_call=leg.put_call,
                strike=k,
                quantity=leg.quantity,
                expiry_years=expiry_years,
                implied_vol=quote.implied_vol,
                last_price=quote.last_price,
            )
        )

    result = price_structure(option_legs, forward=scenario_price)
    net_premium = result.net_premium
    return net_premium, result


# ---------------------------------------------------------------------------
# Private helpers — kink flags per leg
# ---------------------------------------------------------------------------

def _build_kink_map(
    structure: Structure,
    kink_flags: dict[float, KinkFlag],
) -> dict[float, KinkFlag]:
    """Return kink flags keyed by absolute strike for each leg of structure."""
    out: dict[float, KinkFlag] = {}
    for leg in structure.legs:
        k = structure.strike_for(leg)
        if k in kink_flags:
            out[k] = kink_flags[k]
    return out


# ---------------------------------------------------------------------------
# Private helpers — P&L over settlement target ranges
# ---------------------------------------------------------------------------

def _evaluate_pnl_ranges(
    structure: Structure,
    net_premium: float,
    ranges: list[SettlementTargetRange],
    scenario_price: float,
    tick: float = 0.005,
) -> dict[tuple[int, int], PnLProfile]:
    """Evaluate P&L at expiry over a 1-tick grid for each settlement target range.

    Grid spans [scenario_price - lower_bp, scenario_price + upper_bp].
    P&L = payoff(terminal) - net_premium.
    """
    profiles: dict[tuple[int, int], PnLProfile] = {}

    for r in ranges:
        key = range_key(r, tick)
        lower = round(scenario_price - r.lower_bp, 5)
        upper = round(scenario_price + r.upper_bp, 5)
        n_steps = round((upper - lower) / tick)
        if n_steps < 0:
            continue

        pnls: list[float] = []
        for i in range(n_steps + 1):
            terminal = round(lower + i * tick, 5)
            pnl = structure.compute_payoff(terminal) - net_premium
            pnls.append(pnl)

        if not pnls:
            continue

        profiles[key] = PnLProfile(
            min_pnl=min(pnls),
            max_pnl=max(pnls),
            mean_pnl=sum(pnls) / len(pnls),
        )

    return profiles


# ---------------------------------------------------------------------------
# Private helpers — preference hard filters
# ---------------------------------------------------------------------------

def _passes_filters(candidate: CandidateStructure, preferences: TraderPreferences) -> bool:
    """Return False if the candidate violates any hard preference filter."""
    if preferences.open_risk == OpenRisk.FLAT:
        if abs(candidate.delta) > _FLAT_DELTA_EPSILON:
            return False
    if preferences.open_risk == OpenRisk.RISK_OFF:
        if candidate.is_long_premium:
            return False
    if preferences.vol_view == VolView.VOL_DOWN:
        if candidate.vega > 0.0:
            return False
    return True


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def build_candidates(
    omon_chain: dict[date, dict[float, OptionQuote]],
    scenario_prices: dict[date, float],
    expiry_years: dict[date, float],
    active_ranges: list[SettlementTargetRange],
    skew_analyses: dict[date, SkewAnalysis],
    preferences: TraderPreferences,
    tick: float = 0.005,
) -> list[CandidateStructure]:
    """Generate, price, and filter all candidate structures across all expiries.

    Args:
        omon_chain:      {expiry: {strike: OptionQuote}} — from bloomberg.pull_strike_data.
        scenario_prices: {expiry: futures_price} — scenario settlement price per expiry.
                         Sourced from rates_engine.get_scenario_curve(); caller extracts
                         the price for each expiry date key.
        expiry_years:    {expiry: float} — time to expiry in years.
                         Caller computes via sofr.year_fraction(val_date, expiry).
        active_ranges:   from scenario_engine.get_active_ranges() — 1 or 3 ranges.
        skew_analyses:   {expiry: SkewAnalysis} — from skew_logic.analyse_skew per expiry.
        preferences:     TraderPreferences — hard filters applied before returning.
        tick:            SR3 tick size (default 0.005).

    Returns:
        Filtered list of CandidateStructure, one per valid structure × expiry combination.
        Sorted: long-premium first, then short-premium; within each group by expiry ascending.
    """
    candidates: list[CandidateStructure] = []

    for expiry, omon_expiry in omon_chain.items():
        scenario_price = scenario_prices.get(expiry)
        exp_years = expiry_years.get(expiry)
        skew = skew_analyses.get(expiry)

        if scenario_price is None or exp_years is None:
            continue

        kink_flags_map = skew.kink_flags if skew is not None else {}
        all_strikes = sorted(omon_expiry.keys())
        target_strike = _find_target_strike(all_strikes, scenario_price)
        strike_universe = _get_strike_universe(all_strikes, target_strike, _STRIKE_WINDOW, tick)

        for structure in _generate_structures(strike_universe, tick):
            priced = _price_candidate(structure, omon_expiry, scenario_price, exp_years, tick)
            if priced is None:
                continue

            net_premium, result = priced
            is_long = net_premium > 0.0
            theta_sign = (
                ThetaSign.POSITIVE if result.theta_per_day > 0.0
                else ThetaSign.NEGATIVE
            )
            kink_map = _build_kink_map(structure, kink_flags_map)
            pnl_by_range = _evaluate_pnl_ranges(
                structure, net_premium, active_ranges, scenario_price, tick
            )
            market_dist = round((structure.centre_strike - target_strike) / tick)

            candidate = CandidateStructure(
                expiry=expiry,
                structure=structure,
                net_premium=net_premium,
                is_long_premium=is_long,
                delta=result.delta,
                gamma=result.gamma,
                vega=result.vega,
                theta_per_day=result.theta_per_day,
                theta_sign=theta_sign,
                kink_flags=kink_map,
                pnl_by_range=pnl_by_range,
                market_distance_ticks=market_dist,
            )

            if _passes_filters(candidate, preferences):
                candidates.append(candidate)

    # Sort: long-premium first, then short-premium; within each group by expiry ascending
    candidates.sort(key=lambda c: (not c.is_long_premium, c.expiry))
    return candidates
