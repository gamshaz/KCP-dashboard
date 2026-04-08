from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from scenario_engine import SettlementTargetRange
from trade_builder import CandidateStructure, PnLProfile, ThetaSign, range_key

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class RankedCandidate:
    """A candidate with its ranking metadata for one settlement target range.

    rr_score: max_pnl / abs(net_premium). Set only for long-premium structures.
              None for short-premium (N/A).
    theta_sign: display column — POSITIVE (receiving) or NEGATIVE (paying).
    market_distance_ticks: signed ticks from target strike — informational, not sorted on.
    pnl_profile: P&L summary for this specific range.
    """
    candidate: CandidateStructure
    rr_score: Optional[float]       # None for short-premium structures
    theta_sign: ThetaSign
    market_distance_ticks: int
    pnl_profile: PnLProfile


@dataclass
class RankedList:
    """Ranked candidates for one active settlement target range.

    long_premium:  sorted by rr_score descending.
    short_premium: sorted by mean_pnl descending.
    """
    range_lower_ticks: int
    range_upper_ticks: int
    is_default: bool
    long_premium: list[RankedCandidate]
    short_premium: list[RankedCandidate]


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def rank_candidates(
    candidates: list[CandidateStructure],
    active_ranges: list[SettlementTargetRange],
    tick: float = 0.005,
) -> list[RankedList]:
    """Rank filtered candidates. Returns one RankedList per active range.

    Ranking rules (Phase 1):
      Long-premium:  sorted by R:R descending  (max_pnl / abs(net_premium))
      Short-premium: sorted by mean_pnl descending

    Candidates missing a pnl_by_range entry for a given range are excluded
    from that range's list (possible if the range is very narrow and the
    grid produced no points).

    Args:
        candidates:    output of trade_builder.build_candidates — already filtered.
        active_ranges: same list passed to build_candidates.
        tick:          SR3 tick size (must match build_candidates call).
    """
    ranked: list[RankedList] = []

    for r in active_ranges:
        key = range_key(r, tick)
        lower_ticks, upper_ticks = key

        long_ranked: list[RankedCandidate] = []
        short_ranked: list[RankedCandidate] = []

        for c in candidates:
            profile = c.pnl_by_range.get(key)
            if profile is None:
                continue

            if c.is_long_premium:
                rr = (
                    profile.max_pnl / abs(c.net_premium)
                    if abs(c.net_premium) > 1e-10
                    else None
                )
                long_ranked.append(
                    RankedCandidate(
                        candidate=c,
                        rr_score=rr,
                        theta_sign=c.theta_sign,
                        market_distance_ticks=c.market_distance_ticks,
                        pnl_profile=profile,
                    )
                )
            else:
                short_ranked.append(
                    RankedCandidate(
                        candidate=c,
                        rr_score=None,
                        theta_sign=c.theta_sign,
                        market_distance_ticks=c.market_distance_ticks,
                        pnl_profile=profile,
                    )
                )

        # Sort
        long_ranked.sort(
            key=lambda rc: rc.rr_score if rc.rr_score is not None else -float("inf"),
            reverse=True,
        )
        short_ranked.sort(key=lambda rc: rc.pnl_profile.mean_pnl, reverse=True)

        ranked.append(
            RankedList(
                range_lower_ticks=lower_ticks,
                range_upper_ticks=upper_ticks,
                is_default=r.is_default,
                long_premium=long_ranked,
                short_premium=short_ranked,
            )
        )

    return ranked
