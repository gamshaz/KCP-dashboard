from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Optional

from bloomberg import ProbabilityDistribution
from utils import date_utils

# ---------------------------------------------------------------------------
# Settlement price compounding
# ---------------------------------------------------------------------------

def compute_settlement_price(
    contract_start: date,
    contract_end: date,
    rate_path: dict[date, float],
) -> float:
    """Compute the theoretical SR3 futures settlement price for a contract period.

    SR3 settles to the compounded SOFR rate over the contract's 3-month window.
    The rate_path is a dict of {meeting_date: cumulative_sofr_rate_pct} covering
    all FOMC meetings. Between meetings the rate is flat.

    Algorithm:
      1. Collect all meeting dates that fall within (contract_start, contract_end].
      2. Build a list of segment boundaries: contract_start, meeting dates, contract_end.
      3. For each segment, apply the rate in force at its start (flat within segment).
      4. Geometric compound across all segments: product of (1 + r_i * days_i / 360).
      5. Annualise the compounded factor and convert to price: 100 - rate.

    Args:
        contract_start: First calendar day of the contract period (inclusive).
        contract_end:   Last calendar day of the contract period (inclusive).
        rate_path:      {meeting_date: sofr_rate_pct} — cumulative rate in force
                        AFTER each meeting. Rates before the first meeting are the
                        current SOFR fixing, passed as a sentinel at date.min or
                        as the rate at contract_start.

    Returns:
        Futures price (100 - annualised compounded rate).
    """
    # Meetings strictly inside the contract window
    meetings_in_window = sorted(
        d for d in rate_path if contract_start < d <= contract_end
    )

    # Segment boundaries
    boundaries = [contract_start] + meetings_in_window + [contract_end]

    # Rate in force before the first meeting in the window —
    # find the latest meeting on or before contract_start
    rate_before_window = _rate_at(rate_path, contract_start)

    compound = 1.0
    for i in range(len(boundaries) - 1):
        seg_start = boundaries[i]
        seg_end = boundaries[i + 1]
        days = (seg_end - seg_start).days
        if days <= 0:
            continue

        # Rate in force during this segment = rate after the meeting at seg_start
        # (or the pre-window rate for the first segment)
        if seg_start == contract_start:
            rate_pct = rate_before_window
        else:
            rate_pct = rate_path[seg_start]

        compound *= 1.0 + rate_pct / 100.0 * days / 360.0

    total_days = (contract_end - contract_start).days
    if total_days <= 0:
        return 100.0

    annualised_rate = (compound - 1.0) * 360.0 / total_days * 100.0
    return 100.0 - annualised_rate


def _rate_at(rate_path: dict[date, float], as_of: date) -> float:
    """Return the rate in force as of a given date — the rate set by the most
    recent meeting on or before as_of. Returns 0.0 if no prior meeting exists."""
    prior = [d for d in rate_path if d <= as_of]
    if not prior:
        return 0.0
    return rate_path[max(prior)]


# ---------------------------------------------------------------------------
# SR3 contract period boundaries
# ---------------------------------------------------------------------------

def _contract_period(contract_code: str, val_date: date) -> tuple[date, date]:
    """Return (period_start, period_end) calendar dates for an SR3 contract.

    SR3 contracts cover a 3-month window. The contract expires on its IMM date,
    and the settlement period runs from the prior IMM date (exclusive) to the
    expiry IMM date (inclusive).

    Month-letter to month mapping: H=3, M=6, U=9, Z=12.
    _N suffix means N years beyond the nearest future instance.
    """
    _MONTH_CODES = {'H': 3, 'M': 6, 'U': 9, 'Z': 12}

    rest = contract_code[3:]          # strip "SR3"
    parts = rest.split("_")
    month_letter = parts[0]
    offset_years = int(parts[1]) if len(parts) > 1 else 0
    quarter_month = _MONTH_CODES[month_letter]

    year = val_date.year
    if date_utils.get_imm_expiry(year, quarter_month) < val_date:
        year += 1
    year += offset_years

    expiry = date_utils.get_imm_expiry(year, quarter_month)

    # Period start = IMM expiry of the prior quarter
    prev_quarter_month = quarter_month - 3
    prev_year = year
    if prev_quarter_month <= 0:
        prev_quarter_month += 12
        prev_year -= 1
    period_start = date_utils.get_imm_expiry(prev_year, prev_quarter_month)

    return period_start, expiry


# ---------------------------------------------------------------------------
# RatesEngine
# ---------------------------------------------------------------------------

class RatesEngine:
    """Builds WIRP base curve and scenario curves as theoretical SR3 futures prices.

    All outputs are in price terms (100 - rate) to match CME convention.

    Inputs:
        wirp_data           — {meeting_date: ProbabilityDistribution} from bloomberg.py
        live_futures_prices — {contract_code: price} from bloomberg.py
        current_sofr_fixing — today's effective SOFR rate in percent (e.g. 4.33)
        sofr_ffr_spread_bp  — spread to add to FFR path to get SOFR path, in bp
        contracts           — list of SR3 contract codes to build curves for
        val_date            — pricing date; defaults to today
    """

    def __init__(
        self,
        wirp_data: dict[date, ProbabilityDistribution],
        live_futures_prices: dict[str, float],
        current_sofr_fixing: float,
        sofr_ffr_spread_bp: float,
        contracts: list[str],
        val_date: Optional[date] = None,
    ):
        self._wirp_data = wirp_data
        self._live_prices = live_futures_prices
        self._sofr_fixing = current_sofr_fixing
        self._spread_bp = sofr_ffr_spread_bp
        self._contracts = contracts
        self._val_date = val_date or date.today()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_wirp_curve(self) -> dict[str, float]:
        """Theoretical SR3 prices derived from the WIRP-implied FFR path."""
        rate_path = self._build_wirp_rate_path()
        return self._build_curve(rate_path)

    def get_scenario_curve(self, scenario_path: dict[date, int]) -> dict[str, float]:
        """Theoretical SR3 prices derived from a scenario rate path.

        Args:
            scenario_path — {meeting_date: bp_change} as assembled by scenario_engine.py.
                            bp_change is the change at that meeting (e.g. -25, 0, +25).
        """
        rate_path = self._build_scenario_rate_path(scenario_path)
        return self._build_curve(rate_path)

    def get_live_curve(self) -> dict[str, float]:
        """Raw live futures prices from BBG, passed through unchanged."""
        return dict(self._live_prices)

    def get_rich_cheap(self, scenario_path: dict[date, int]) -> dict[str, float]:
        """Price tick difference between scenario curve and live curve per contract.

        Positive = scenario price above live (contract is cheap vs scenario).
        Negative = scenario price below live (contract is rich vs scenario).
        Tick size for SR3 is 0.005 (half a bp, $12.50).
        """
        scenario = self.get_scenario_curve(scenario_path)
        live = self.get_live_curve()
        tick = 0.005
        result = {}
        for code in self._contracts:
            if code in scenario and code in live:
                diff_price = scenario[code] - live[code]
                result[code] = round(diff_price / tick)
        return result

    # ------------------------------------------------------------------
    # Rate path builders
    # ------------------------------------------------------------------

    def _build_wirp_rate_path(self) -> dict[date, float]:
        """Build {meeting_date: cumulative_sofr_rate_pct} from WIRP probabilities.

        For each meeting:
            expected_change_bp = sum(p * bp_change for bp_change, p in outcomes.items())
        Cumulated from current_sofr_fixing. SOFR-FFR spread applied at the end.
        """
        meetings = sorted(self._wirp_data.keys())
        rate_path: dict[date, float] = {}

        # Start from the current SOFR fixing (already in SOFR terms)
        current_rate = self._sofr_fixing

        for meeting in meetings:
            dist = self._wirp_data[meeting]
            expected_change_bp = sum(
                bp * p for bp, p in dist.outcomes.items()
            )
            current_rate += expected_change_bp / 100.0
            rate_path[meeting] = current_rate

        return rate_path

    def _build_scenario_rate_path(self, scenario_path: dict[date, int]) -> dict[date, float]:
        """Build {meeting_date: cumulative_sofr_rate_pct} from a scenario path.

        scenario_path: {meeting_date: bp_change} — explicit bp change per meeting.
        Meetings not in scenario_path inherit the WIRP-implied expected change.
        Cumulated from current_sofr_fixing.
        """
        # Build the WIRP-implied expected changes as fallback
        wirp_expected: dict[date, float] = {}
        for meeting, dist in self._wirp_data.items():
            wirp_expected[meeting] = sum(bp * p for bp, p in dist.outcomes.items())

        all_meetings = sorted(
            set(list(self._wirp_data.keys()) + list(scenario_path.keys()))
        )

        rate_path: dict[date, float] = {}
        current_rate = self._sofr_fixing

        for meeting in all_meetings:
            if meeting in scenario_path:
                change_bp = float(scenario_path[meeting])
            else:
                change_bp = wirp_expected.get(meeting, 0.0)
            current_rate += change_bp / 100.0
            rate_path[meeting] = current_rate

        return rate_path

    # ------------------------------------------------------------------
    # Curve builder
    # ------------------------------------------------------------------

    def _build_curve(self, rate_path: dict[date, float]) -> dict[str, float]:
        """Convert a rate path to theoretical futures prices for all contracts."""
        curve: dict[str, float] = {}
        for code in self._contracts:
            try:
                period_start, period_end = _contract_period(code, self._val_date)
                price = compute_settlement_price(period_start, period_end, rate_path)
                curve[code] = round(price, 5)
            except Exception:
                pass  # contract code not parseable — skip silently
        return curve
