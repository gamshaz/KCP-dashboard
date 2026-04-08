from datetime import date

from products.base import Product
from utils import date_utils

# ---------------------------------------------------------------------------
# Serial month mapping (hardcoded per §4.13)
# Maps quarterly month → the three serial months that expire on or before it.
# June options on September underlying excluded — not traded in practice.
# ---------------------------------------------------------------------------

SERIAL_MONTHS: dict[int, list[int]] = {
    3:  [1, 2, 3],   # March quarterly  → Jan, Feb, Mar serials
    6:  [4, 5, 6],   # June quarterly   → Apr, May, Jun serials
    9:  [7, 8, 9],   # Sep  quarterly   → Jul, Aug, Sep serials
    12: [10, 11, 12], # Dec  quarterly  → Oct, Nov, Dec serials
}

# Bloomberg month-letter → calendar month number
_MONTH_CODES: dict[str, int] = {
    'H': 3,
    'M': 6,
    'U': 9,
    'Z': 12,
}


def _parse_quarterly_code(code: str) -> tuple[int, int]:
    """Parse a quarterly contract code (e.g. SR3H, SR3M_1) into (year, quarter_month).

    The optional _N suffix means N contract-years beyond the base instance.
    Base instance: the nearest future quarterly expiry for that month letter.
    """
    rest = code[3:]              # strip root "SR3"
    parts = rest.split("_")
    month_letter = parts[0]
    offset_years = int(parts[1]) if len(parts) > 1 else 0

    quarter_month = _MONTH_CODES[month_letter]

    today = date.today()
    year = today.year
    if date_utils.get_imm_expiry(year, quarter_month) < today:
        year += 1

    year += offset_years
    return year, quarter_month


# ---------------------------------------------------------------------------
# SOFR product
# ---------------------------------------------------------------------------

class SOFR(Product):
    """3-month SOFR futures (SR3). Phase 1 scope only."""

    # --- Contract specs ---------------------------------------------------

    def get_contract_code(self) -> str:
        return "SR3"

    def get_tick_size(self) -> float:
        return 0.005          # half a basis point

    def get_tick_value(self) -> float:
        return 12.50          # USD per tick

    def get_contract_size(self) -> float:
        return 1_000_000.0    # USD notional

    def get_day_count_convention(self) -> str:
        return "Actual/360"

    # --- Expiry -----------------------------------------------------------

    def get_expiry(self, year: int, month: int) -> date:
        """IMM expiry (third Wednesday, CME-holiday adjusted) for the given year and month."""
        return date_utils.get_imm_expiry(year, month)

    def get_serial_expiries(self, quarterly_code: str) -> list[date]:
        """Return the three serial IMM expiry dates associated with a quarterly contract code.

        Example: SR3U (Sep quarterly) → Jul, Aug, Sep IMM expiries of the same year.
        """
        year, quarter_month = _parse_quarterly_code(quarterly_code)
        serial_months = SERIAL_MONTHS[quarter_month]
        return [date_utils.get_imm_expiry(year, m) for m in serial_months]

    # --- Year fraction (Actual/360 over CME business days) ----------------

    def year_fraction(self, date_a: date, date_b: date) -> float:
        """Business days from date_a (excl) to date_b (incl), divided by 360.
        Used by options_pricer.py for time-to-expiry in Black-76."""
        raw_days = date_utils.business_days_between(date_a, date_b)
        return raw_days / 360.0
