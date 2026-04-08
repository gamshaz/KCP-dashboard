from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class KinkFlag(Enum):
    SELL_TARGET = "sell_target"   # kinked strike — action depends on kink_mode
    BUY_TARGET  = "buy_target"
    NEUTRAL     = "neutral"


class KinkMode(Enum):
    SELL_CHEAP = "sell_cheap"   # kinked-down → SELL_TARGET (default)
    FADE_KINK  = "fade_kink"    # kinked-down → BUY_TARGET


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class VolPoint:
    """Vol data at a single strike."""
    strike: float
    implied_vol: float
    bid_vol: float
    offer_vol: float


@dataclass
class WingRecommendation:
    """Recommended wing placement for a fly centred at a given strike.

    best_lower_offset and best_upper_offset are in ticks from centre_strike.
    vol_differential is the vol advantage achieved by this placement
    (positive = structure benefits from the skew).
    """
    centre_strike: float
    best_lower_offset: int      # ticks (negative convention, e.g. -6)
    best_upper_offset: int      # ticks (positive convention, e.g. +8)
    vol_differential: float     # implied vol difference favouring the structure


@dataclass
class SkewAnalysis:
    """Full output of skew_logic analysis for one expiry."""
    expiry_date: object                             # date
    vol_ladder: dict[float, VolPoint]               # strike → VolPoint
    kink_flags: dict[float, KinkFlag]               # strike → KinkFlag
    wing_recommendations: dict[int, WingRecommendation]  # width_ticks → recommendation
    kink_threshold: float                           # threshold used in this analysis


# ---------------------------------------------------------------------------
# Kink detection
# ---------------------------------------------------------------------------

def _second_difference(vol_ladder: dict[float, VolPoint], strike: float, tick: float) -> Optional[float]:
    """Discrete second difference at a strike:
        vol[K] - 0.5 * (vol[K - tick] + vol[K + tick])

    Returns None if either neighbour is missing from the ladder.
    Positive result → vol is kinked UP (local vol hump).
    Negative result → vol is kinked DOWN (local vol dip).
    """
    lower = round(strike - tick, 5)
    upper = round(strike + tick, 5)
    if lower not in vol_ladder or upper not in vol_ladder:
        return None
    v_centre = vol_ladder[strike].implied_vol
    v_lower  = vol_ladder[lower].implied_vol
    v_upper  = vol_ladder[upper].implied_vol
    return v_centre - 0.5 * (v_lower + v_upper)


def _compute_kink_flags(
    vol_ladder: dict[float, VolPoint],
    kink_threshold: float,
    kink_mode: KinkMode,
    tick: float = 0.005,
) -> dict[float, KinkFlag]:
    """Assign a KinkFlag to every strike that has two neighbours in the ladder.

    kink_mode controls how a detected kink maps to a trading signal:
      SELL_CHEAP: kinked-down → SELL_TARGET, kinked-up → BUY_TARGET
      FADE_KINK:  kinked-down → BUY_TARGET,  kinked-up → SELL_TARGET
    """
    flags: dict[float, KinkFlag] = {}
    for strike in vol_ladder:
        sd = _second_difference(vol_ladder, strike, tick)
        if sd is None:
            continue
        if sd < -kink_threshold:
            # Vol is locally depressed at this strike
            flags[strike] = (
                KinkFlag.SELL_TARGET if kink_mode == KinkMode.SELL_CHEAP
                else KinkFlag.BUY_TARGET
            )
        elif sd > kink_threshold:
            # Vol is locally elevated at this strike
            flags[strike] = (
                KinkFlag.BUY_TARGET if kink_mode == KinkMode.SELL_CHEAP
                else KinkFlag.SELL_TARGET
            )
        else:
            flags[strike] = KinkFlag.NEUTRAL
    return flags


# ---------------------------------------------------------------------------
# Wing selection
# ---------------------------------------------------------------------------

_WING_WIDTHS = [6, 8, 10, 12]       # candidate wing offsets in ticks
_BROKEN_OFFSETS = [6, 8, 10, 12]    # candidate broken-wing offsets


def _fly_vol_differential(
    vol_ladder: dict[float, VolPoint],
    centre_strike: float,
    lower_offset: int,
    upper_offset: int,
    tick: float = 0.005,
) -> Optional[float]:
    """Vol differential for a fly centred at centre_strike with given wing offsets.

    For a long fly (long body, short wings), the structure benefits when the
    wings have HIGHER vol than the body — we are selling expensive wings.

    Differential = 0.5 * (vol[lower_wing] + vol[upper_wing]) - vol[body]
    Positive = wings are richer than body → favours selling wings (long fly).
    """
    lower_strike = round(centre_strike + lower_offset * tick, 5)
    upper_strike = round(centre_strike + upper_offset * tick, 5)
    if (lower_strike not in vol_ladder or
            upper_strike not in vol_ladder or
            centre_strike not in vol_ladder):
        return None
    v_lower  = vol_ladder[lower_strike].implied_vol
    v_upper  = vol_ladder[upper_strike].implied_vol
    v_centre = vol_ladder[centre_strike].implied_vol
    return 0.5 * (v_lower + v_upper) - v_centre


def _compute_wing_recommendations(
    vol_ladder: dict[float, VolPoint],
    centre_strike: float,
    tick: float = 0.005,
) -> dict[int, WingRecommendation]:
    """For each primary wing width, find the broken-wing combination that
    maximises the vol differential in favour of a long fly.

    Returns {primary_width_ticks: WingRecommendation}.
    Only widths where at least one valid combination exists are returned.
    """
    recommendations: dict[int, WingRecommendation] = {}

    for primary_width in _WING_WIDTHS:
        best_diff: Optional[float] = None
        best_lower = -primary_width
        best_upper = +primary_width

        # Symmetric baseline
        for lower_off in [-w for w in _BROKEN_OFFSETS]:
            for upper_off in [+w for w in _BROKEN_OFFSETS]:
                diff = _fly_vol_differential(
                    vol_ladder, centre_strike, lower_off, upper_off, tick
                )
                if diff is None:
                    continue
                if best_diff is None or diff > best_diff:
                    best_diff = diff
                    best_lower = lower_off
                    best_upper = upper_off

        if best_diff is not None:
            recommendations[primary_width] = WingRecommendation(
                centre_strike=centre_strike,
                best_lower_offset=best_lower,
                best_upper_offset=best_upper,
                vol_differential=best_diff,
            )

    return recommendations


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def analyse_skew(
    vol_ladder: dict[float, VolPoint],
    expiry_date: object,
    centre_strike: float,
    kink_threshold: float,
    kink_mode: KinkMode,
    tick: float = 0.005,
) -> SkewAnalysis:
    """Full skew analysis for one expiry.

    Args:
        vol_ladder:      {strike: VolPoint} — discrete OMON vol data.
        expiry_date:     date of this expiry (informational).
        centre_strike:   reference strike for wing recommendations (scenario target).
        kink_threshold:  minimum absolute second difference to flag as a kink.
                         Sourced from products.yaml (sofr.kink_threshold).
        kink_mode:       SELL_CHEAP or FADE_KINK — controls trading signal direction.
        tick:            SR3 tick size, default 0.005.

    Returns:
        SkewAnalysis with kink_flags for all strikes and wing_recommendations
        for centre_strike.
    """
    kink_flags = _compute_kink_flags(vol_ladder, kink_threshold, kink_mode, tick)
    wing_recs = _compute_wing_recommendations(vol_ladder, centre_strike, tick)

    return SkewAnalysis(
        expiry_date=expiry_date,
        vol_ladder=vol_ladder,
        kink_flags=kink_flags,
        wing_recommendations=wing_recs,
        kink_threshold=kink_threshold,
    )


def build_vol_ladder(omon_chain: dict) -> dict[float, VolPoint]:
    """Convert raw OMON chain data to a vol ladder for skew analysis.

    Args:
        omon_chain: {strike: OptionQuote} for a single expiry, as returned by
                    bloomberg.pull_strike_data()[expiry].

    OptionQuote does not carry bid/offer vol separately — IVOL_MID is used for
    all three fields until bid/offer vol fields are added to the OMON pull.
    """
    ladder: dict[float, VolPoint] = {}
    for strike, quote in omon_chain.items():
        ladder[strike] = VolPoint(
            strike=strike,
            implied_vol=quote.implied_vol,
            bid_vol=quote.implied_vol,    # placeholder until bid/offer pulled
            offer_vol=quote.implied_vol,
        )
    return ladder
