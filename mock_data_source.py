"""mock_data_source.py — Offline stub for running the dashboard without Bloomberg.

Returns realistic but synthetic market data so the full UI can be exercised
without blpapi or a Bloomberg terminal.

Usage: set USE_MOCK=1 in the environment, or pass --mock flag.
app.py checks this automatically.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from bloomberg import OptionQuote, ProbabilityDistribution
from data.base import DataSource
from utils.cache import Cache
from utils import date_utils

# ---------------------------------------------------------------------------
# Synthetic market levels
# ---------------------------------------------------------------------------

_SOFR_FIXING = 5.33          # current overnight SOFR (%)
_SOFR_FFR_SPREAD = 0.05      # 5 bp
_FLAT_RATE = _SOFR_FIXING    # hold-everything scenario

# Realistic implied vol for SR3 short-dated options (~80–120 bps normal vol)
_BASE_VOL = 0.012            # lognormal proxy; ~80 bp normal vol at 95.0

# Strike grid: SR3 futures prices around 95.00 (rate ~5.00%)
_BASE_PRICE = 95.000
_STRIKE_SPACING = 0.0625


# ---------------------------------------------------------------------------
# MockDataSource
# ---------------------------------------------------------------------------

class MockDataSource(DataSource):
    """Implements DataSource with fully synthetic data. No network calls."""

    def __init__(self) -> None:
        self._cache = Cache()
        self._last_wirp_pull:    Optional[datetime] = None
        self._last_futures_pull: Optional[datetime] = None
        self._last_omon_pull:    Optional[datetime] = None
        self._omon_pulled_for:   Optional[str] = None

    # ------------------------------------------------------------------
    # DataSource interface
    # ------------------------------------------------------------------

    def pull_startup_data(
        self, fomc_dates: list[date], contracts: list[str]
    ) -> tuple[dict, dict]:
        wirp  = self._make_wirp(fomc_dates)
        fut   = self._make_futures(contracts)
        self._cache.set("wirp", wirp)
        self._cache.set("futures_prices", fut)
        self._last_wirp_pull    = datetime.now()
        self._last_futures_pull = datetime.now()
        return wirp, fut

    def pull_option_chain(self, underlying: str) -> list[str]:
        # Return synthetic ticker strings — not used for anything except
        # being passed straight back to pull_strike_data.
        return [f"MOCK_{underlying}_{i}" for i in range(20)]

    def pull_strike_data(
        self, tickers: list[str], target_strike: float
    ) -> dict[date, dict[float, OptionQuote]]:
        chain = self._make_omon(target_strike)
        self._cache.set("omon_raw", chain)
        self._last_omon_pull = datetime.now()
        return chain

    def get_cached_wirp(self) -> dict | None:
        return self._cache.get("wirp")

    def get_cached_futures(self) -> dict | None:
        return self._cache.get("futures_prices")

    def get_cached_omon(self) -> dict | None:
        return self._cache.get("omon_raw")

    def is_omon_stale(self, current_scenario_id: str) -> bool:
        if not self._cache.has("omon_raw"):
            return False
        return self._omon_pulled_for != current_scenario_id

    def get_pull_timestamps(self) -> dict:
        return {
            "last_wirp_pull":    self._last_wirp_pull,
            "last_futures_pull": self._last_futures_pull,
            "last_omon_pull":    self._last_omon_pull,
            "omon_pulled_for":   self._omon_pulled_for,
        }

    # ------------------------------------------------------------------
    # Synthetic data generators
    # ------------------------------------------------------------------

    def _make_wirp(
        self, fomc_dates: list[date]
    ) -> dict[date, ProbabilityDistribution]:
        """70% cut at each meeting — simple, visually interesting WIRP curve."""
        result: dict[date, ProbabilityDistribution] = {}
        p_cut = 0.70
        for meeting in fomc_dates:
            result[meeting] = ProbabilityDistribution(
                meeting_date=meeting,
                outcomes={0: 1 - p_cut, -25: p_cut},
                expected_change_bp=-25 * p_cut,
            )
        return result

    def _make_futures(self, contracts: list[str]) -> dict[str, float]:
        """Gentle downward-sloping SR3 curve (easing cycle priced in)."""
        prices: dict[str, float] = {}
        step = 0.05          # ~10bp per contract step — visible slope
        for i, code in enumerate(contracts):
            prices[code] = round(_BASE_PRICE + i * step, 3)
        return prices

    def _make_omon(
        self, target_strike: float
    ) -> dict[date, dict[float, OptionQuote]]:
        """Synthetic OMON chain: ±5 strikes × 3 serial expiries with vol smile."""
        today = date.today()
        # Three serial expiry dates: roughly 1, 2, 3 months out
        expiries = [
            date_utils.get_imm_expiry(today.year, today.month + i)
            if today.month + i <= 12
            else date_utils.get_imm_expiry(today.year + 1, (today.month + i) % 12 or 12)
            for i in range(1, 4)
        ]

        chain: dict[date, dict[float, OptionQuote]] = {}
        for expiry in expiries:
            strikes: dict[float, OptionQuote] = {}
            for offset in range(-5, 6):
                k = round(target_strike + offset * _STRIKE_SPACING, 4)
                # Smile: wings slightly richer than body
                smile_bump = abs(offset) * 0.0005
                vol = _BASE_VOL + smile_bump
                # Approximate Black-76 intrinsic as proxy for last_price
                atm_dist = abs(k - target_strike)
                last_price = max(0.002, round(0.015 - atm_dist * 0.3, 4))
                # Delta: rough approximation (call-like)
                delta = max(0.05, min(0.95, 0.5 - (k - target_strike) * 4))
                strikes[k] = OptionQuote(
                    strike=k,
                    expiry=expiry,
                    implied_vol=vol,
                    last_price=last_price,
                    delta=delta,
                    underlying="SR3H Comdty",
                )
            chain[expiry] = strikes

        return chain
