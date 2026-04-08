from datetime import date, timedelta

# ---------------------------------------------------------------------------
# CME holiday calendar — computed algorithmically, cached per year
# ---------------------------------------------------------------------------

def _easter(year: int) -> date:
    """Anonymous Gregorian computus — returns Easter Sunday."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """nth occurrence of weekday (0=Mon … 6=Sun) in the given month."""
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """Last occurrence of weekday in the given month."""
    next_month_first = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = next_month_first - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset)


def _weekend_adjust(d: date) -> date:
    """CME observance rule: Saturday → Friday, Sunday → Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _cme_holidays(year: int) -> frozenset:
    good_friday = _easter(year) - timedelta(days=2)
    return frozenset({
        _weekend_adjust(date(year, 1, 1)),      # New Year's Day
        _nth_weekday(year, 1, 0, 3),            # MLK Day       (3rd Mon Jan)
        _nth_weekday(year, 2, 0, 3),            # Presidents'   (3rd Mon Feb)
        good_friday,                             # Good Friday
        _last_weekday(year, 5, 0),              # Memorial Day  (last Mon May)
        _weekend_adjust(date(year, 6, 19)),     # Juneteenth
        _weekend_adjust(date(year, 7, 4)),      # Independence Day
        _nth_weekday(year, 9, 0, 1),            # Labor Day     (1st Mon Sep)
        _nth_weekday(year, 11, 3, 4),           # Thanksgiving  (4th Thu Nov)
        _weekend_adjust(date(year, 12, 25)),    # Christmas Day
    })


_holiday_cache: dict[int, frozenset] = {}


def _holidays(year: int) -> frozenset:
    if year not in _holiday_cache:
        _holiday_cache[year] = _cme_holidays(year)
    return _holiday_cache[year]


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def is_good_business_day(d: date) -> bool:
    """True if d is a CME business day (not weekend, not CME holiday)."""
    if d.weekday() >= 5:
        return False
    return d not in _holidays(d.year)


def get_imm_expiry(year: int, month: int) -> date:
    """Third Wednesday of the given month, rolled back if it falls on a CME holiday."""
    third_wed = _nth_weekday(year, month, 2, 3)  # weekday 2 = Wednesday
    while not is_good_business_day(third_wed):
        third_wed -= timedelta(days=1)
    return third_wed


def get_contract_expiries(n: int, from_date: date) -> list[date]:
    """Return the next n monthly IMM expiry dates strictly after from_date."""
    expiries = []
    year, month = from_date.year, from_date.month
    while len(expiries) < n:
        candidate = get_imm_expiry(year, month)
        if candidate > from_date:
            expiries.append(candidate)
        month += 1
        if month > 12:
            month = 1
            year += 1
    return expiries


def business_days_between(date_a: date, date_b: date) -> int:
    """CME business day count from date_a (exclusive) to date_b (inclusive).
    Returns 0 if date_b <= date_a."""
    if date_b <= date_a:
        return 0
    count = 0
    current = date_a + timedelta(days=1)
    while current <= date_b:
        if is_good_business_day(current):
            count += 1
        current += timedelta(days=1)
    return count


# ---------------------------------------------------------------------------
# FOMC meeting dates — hardcoded 2-year rolling window (2025–2027)
# Can be replaced with a BBG pull in a later phase.
# ---------------------------------------------------------------------------

_FOMC_DATES: list[date] = [
    # 2025
    date(2025, 1, 29),
    date(2025, 3, 19),
    date(2025, 5, 7),
    date(2025, 6, 18),
    date(2025, 7, 30),
    date(2025, 9, 17),
    date(2025, 10, 29),
    date(2025, 12, 10),
    # 2026
    date(2026, 1, 28),
    date(2026, 3, 19),
    date(2026, 5, 7),
    date(2026, 6, 18),
    date(2026, 7, 30),
    date(2026, 9, 17),
    date(2026, 10, 29),
    date(2026, 12, 10),
    # 2027
    date(2027, 1, 27),
    date(2027, 3, 17),
    date(2027, 5, 5),
    date(2027, 6, 16),
    date(2027, 7, 28),
    date(2027, 9, 15),
    date(2027, 10, 27),
    date(2027, 12, 8),
]


def get_fomc_dates(from_date: date, to_date: date) -> list[date]:
    """Return FOMC meeting dates within [from_date, to_date] inclusive."""
    return [d for d in _FOMC_DATES if from_date <= d <= to_date]
