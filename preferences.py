from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from skew_logic import KinkMode

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class OpenRisk(Enum):
    OPEN_RISK = "open_risk"   # no constraint — all structures eligible
    FLAT      = "flat"        # zero-delta structures only
    RISK_OFF  = "risk_off"    # short-premium structures only (receive theta)


class VolView(Enum):
    VOL_UP   = "vol_up"    # expect vol to rise — favour long vega
    NEUTRAL  = "neutral"   # no vol directional view
    VOL_DOWN = "vol_down"  # expect vol to fall — favour short vega


# ---------------------------------------------------------------------------
# TraderPreferences
# ---------------------------------------------------------------------------

@dataclass
class TraderPreferences:
    """Validated container for trader preference inputs.

    Used by trade_builder.py to hard-filter the candidate trade universe:
      - open_risk == FLAT:     exclude structures with net delta != 0
      - open_risk == RISK_OFF: exclude long-premium structures (net_premium > 0)
      - vol_view == VOL_DOWN:  exclude structures with net positive vega

    theta_propensity and vol_view are also used as multipliers in the Phase 2
    composite scoring upgrade (see KCP_dashboard_plan.md §7.2).

    Fields:
        open_risk:         OPEN_RISK | FLAT | RISK_OFF
        vol_view:          VOL_UP | NEUTRAL | VOL_DOWN
        theta_propensity:  float in [0, 1] — 0 = indifferent to theta,
                           1 = maximise theta income. Phase 1: used as a
                           soft preference signal only (not a hard filter).
        kink_mode:         SELL_CHEAP | FADE_KINK — controls how skew kinks
                           map to trading signals in skew_logic.analyse_skew.
    """
    open_risk: OpenRisk
    vol_view: VolView
    theta_propensity: float   # [0, 1]
    kink_mode: KinkMode

    def __post_init__(self) -> None:
        if not (0.0 <= self.theta_propensity <= 1.0):
            raise ValueError(
                f"theta_propensity must be in [0, 1], got {self.theta_propensity}"
            )


# ---------------------------------------------------------------------------
# Factory — sensible defaults for a new session
# ---------------------------------------------------------------------------

def default_preferences() -> TraderPreferences:
    """Return the default TraderPreferences for a new dashboard session.

    Defaults:
        open_risk        = OPEN_RISK  (no constraints)
        vol_view         = NEUTRAL
        theta_propensity = 0.5        (moderate preference for theta income)
        kink_mode        = SELL_CHEAP (kinked-down vol → sell target)
    """
    return TraderPreferences(
        open_risk=OpenRisk.OPEN_RISK,
        vol_view=VolView.NEUTRAL,
        theta_propensity=0.5,
        kink_mode=KinkMode.SELL_CHEAP,
    )
