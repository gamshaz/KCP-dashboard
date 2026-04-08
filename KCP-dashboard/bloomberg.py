from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from data.base import DataSource
from utils.cache import Cache

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class BloombergConnectionError(Exception):
    """Raised when the blpapi session cannot be started or a service cannot be opened."""

class BloombergTimeoutError(Exception):
    """Raised when a Bloomberg request times out."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ProbabilityDistribution:
    meeting_date: date
    outcomes: dict[int, float]   # e.g. {0: 0.65, -25: 0.35}
    expected_change_bp: float    # negative for cuts, e.g. -17.5 for 70% priced cut


@dataclass
class OptionQuote:
    strike: float
    expiry: date
    implied_vol: float
    last_price: float
    delta: float
    underlying: str


# ---------------------------------------------------------------------------
# Session factory (spec §4.10)
# ---------------------------------------------------------------------------

def _create_session():
    import blpapi
    opts = blpapi.SessionOptions()
    opts.setServerHost("localhost")
    opts.setServerPort(8194)
    session = blpapi.Session(opts)
    if not session.start():
        raise BloombergConnectionError("Bloomberg session failed to start")
    if not session.openService("//blp/refdata"):
        raise BloombergConnectionError("Failed to open //blp/refdata")
    return session


# ---------------------------------------------------------------------------
# Low-level blpapi helpers
# ---------------------------------------------------------------------------

_TIMEOUT_MS = 30_000  # 30 seconds


def _bdp(session, securities: list[str], fields: list[str]) -> dict[str, dict[str, object]]:
    """Synchronous BDP — reference data for a list of securities and fields.
    Returns {security: {field: value}}. Missing fields logged and returned as None."""
    import blpapi

    service = session.getService("//blp/refdata")
    request = service.createRequest("ReferenceDataRequest")
    for sec in securities:
        request.getElement("securities").appendValue(sec)
    for fld in fields:
        request.getElement("fields").appendValue(fld)

    session.sendRequest(request)

    results: dict[str, dict[str, object]] = {
        sec: {fld: None for fld in fields} for sec in securities
    }

    done = False
    while not done:
        ev = session.nextEvent(_TIMEOUT_MS)
        if ev.eventType() == blpapi.Event.TIMEOUT:
            raise BloombergTimeoutError("BDP request timed out")

        for msg in ev:
            if msg.hasElement("responseError"):
                logger.error("BDP response error: %s",
                             msg.getElement("responseError").getElementAsString("message"))
                continue

            if not msg.hasElement("securityData"):
                continue

            sec_data_array = msg.getElement("securityData")
            for i in range(sec_data_array.numValues()):
                sec_data = sec_data_array.getValueAsElement(i)
                ticker = sec_data.getElementAsString("security")

                if sec_data.hasElement("securityError"):
                    logger.error("Security error for %s: %s", ticker,
                                 sec_data.getElement("securityError").getElementAsString("message"))
                    continue

                field_data = sec_data.getElement("fieldData")
                for fld in fields:
                    if field_data.hasElement(fld):
                        try:
                            results[ticker][fld] = field_data.getElement(fld).getValue()
                        except Exception as exc:
                            logger.error("Error reading field %s for %s: %s", fld, ticker, exc)
                    else:
                        logger.warning("Field %s not returned for %s", fld, ticker)

        if ev.eventType() == blpapi.Event.RESPONSE:
            done = True

    return results


def _bds(session, security: str, field_name: str) -> list[object]:
    """Synchronous BDS — bulk/array data for a single security and field.
    Returns a list of blpapi element rows."""
    import blpapi

    service = session.getService("//blp/refdata")
    request = service.createRequest("ReferenceDataRequest")
    request.getElement("securities").appendValue(security)
    request.getElement("fields").appendValue(field_name)

    session.sendRequest(request)

    rows: list[object] = []
    done = False

    while not done:
        ev = session.nextEvent(_TIMEOUT_MS)
        if ev.eventType() == blpapi.Event.TIMEOUT:
            raise BloombergTimeoutError(f"BDS request timed out for {security}/{field_name}")

        for msg in ev:
            if not msg.hasElement("securityData"):
                continue

            sec_data = msg.getElement("securityData").getValueAsElement(0)
            if sec_data.hasElement("securityError"):
                logger.error("BDS security error for %s: %s", security,
                             sec_data.getElement("securityError").getElementAsString("message"))
                continue

            field_data = sec_data.getElement("fieldData")
            if field_data.hasElement(field_name):
                bulk = field_data.getElement(field_name)
                for i in range(bulk.numValues()):
                    rows.append(bulk.getValueAsElement(i))

        if ev.eventType() == blpapi.Event.RESPONSE:
            done = True

    return rows


# ---------------------------------------------------------------------------
# FF contract → calendar month helper
# ---------------------------------------------------------------------------

def _ff_month_start(code: str, today: date) -> Optional[date]:
    """Map FFn to the first day of its calendar month.
    FF1 = current month, FF2 = next month, etc."""
    try:
        n = int(code[2:])
    except ValueError:
        return None
    total_months = today.month + n - 1
    year = today.year + (total_months - 1) // 12
    month = (total_months - 1) % 12 + 1
    return date(year, month, 1)


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class BloombergDataSource(DataSource):

    def __init__(self, ff_contracts: list[str], sr3_contracts: list[str]):
        self._session = None
        self._ff_contracts: list[str] = ff_contracts
        self._sr3_contracts: list[str] = sr3_contracts
        self._cache = Cache()
        self._last_wirp_pull: Optional[datetime] = None
        self._last_futures_pull: Optional[datetime] = None
        self._last_omon_pull: Optional[datetime] = None
        self._omon_pulled_for: Optional[str] = None

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def initialise_session(self) -> None:
        """Start the blpapi session. Called once at startup via st.cache_resource."""
        self._session = _create_session()

    # ------------------------------------------------------------------
    # DataSource interface
    # ------------------------------------------------------------------

    def pull_startup_data(self, fomc_dates: list[date], contracts: list[str]) -> tuple[dict, dict]:
        """Pull WIRP and live futures on startup. Returns (wirp_data, futures_prices)."""
        wirp_data = self._pull_wirp(fomc_dates)
        futures_prices = self._pull_futures(contracts)
        return wirp_data, futures_prices

    def pull_option_chain(self, underlying: str) -> list[str]:
        """Stage 1 OMON — return option tickers for underlying via CHAIN_TICKERS BDS."""
        rows = _bds(self._session, underlying, "CHAIN_TICKERS")
        tickers = []
        for row in rows:
            try:
                tickers.append(row.getElementAsString("Ticker"))
            except Exception as exc:
                logger.warning("Could not read ticker from CHAIN_TICKERS row: %s", exc)
        return tickers

    def pull_strike_data(
        self, tickers: list[str], target_strike: float
    ) -> dict[date, dict[float, OptionQuote]]:
        """Stage 2 OMON — BDP across tickers, filtered to ±5 strikes (±31.25bp) of target_strike.
        Returns {expiry: {strike: OptionQuote}}."""
        STRIKE_RADIUS = 5 * 0.0625  # 31.25bp

        bbg_fields = [
            "IVOL_MID",
            "PX_LAST",
            "DELTA_MID",
            "STRIKE_PX",
            "OPT_EXPIRE_DT",
            "OPT_UNDERLYING_TICKER",
        ]
        raw = _bdp(self._session, tickers, bbg_fields)

        result: dict[date, dict[float, OptionQuote]] = {}

        for ticker, fields in raw.items():
            strike = fields.get("STRIKE_PX")
            if strike is None:
                continue
            if abs(float(strike) - target_strike) > STRIKE_RADIUS:
                continue

            expiry_val = fields.get("OPT_EXPIRE_DT")
            if expiry_val is None:
                continue

            # blpapi returns dates as blpapi.Datetime objects
            if hasattr(expiry_val, "year"):
                expiry = date(expiry_val.year, expiry_val.month, expiry_val.day)
            else:
                expiry = expiry_val

            quote = OptionQuote(
                strike=float(strike),
                expiry=expiry,
                implied_vol=float(fields.get("IVOL_MID") or 0.0),
                last_price=float(fields.get("PX_LAST") or 0.0),
                delta=float(fields.get("DELTA_MID") or 0.0),
                underlying=str(fields.get("OPT_UNDERLYING_TICKER") or ""),
            )
            result.setdefault(expiry, {})[quote.strike] = quote

        self._cache.set("omon_raw", result)
        self._last_omon_pull = datetime.now()
        return result

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
            "last_wirp_pull": self._last_wirp_pull,
            "last_futures_pull": self._last_futures_pull,
            "last_omon_pull": self._last_omon_pull,
            "omon_pulled_for": self._omon_pulled_for,
        }

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def refresh_all(
        self,
        fomc_dates: list[date],
        contracts: list[str],
        target_strike: float,
        current_scenario_id: str,
    ) -> None:
        """Re-pull WIRP and futures unconditionally.
        OMON is not re-pulled here — triggered via explicit Pull Options flow."""
        self._pull_wirp(fomc_dates)
        self._pull_futures(contracts)

    # ------------------------------------------------------------------
    # Private pull methods
    # ------------------------------------------------------------------

    def _pull_wirp(self, fomc_dates: list[date]) -> dict[date, ProbabilityDistribution]:
        """Pull FF1-FF24 PX_LAST and compute meeting-implied probability distributions.

        WIRP arithmetic (single-move-or-hold, Phase 1):
          front_rate        = 100 - FF_price[meeting month]
          back_rate         = 100 - FF_price[month after meeting]
          expected_change_bp = (back_rate - front_rate) * 100
                               negative → cut priced, positive → hike priced
          p_move            = min(1, abs(expected_change_bp) / 25)
          outcomes          = {0: 1-p_move, -25: p_move}  if cut
                            = {0: 1-p_move, +25: p_move}  if hike
        """
        today = date.today()
        ff_tickers = [f"{c} Comdty" for c in self._ff_contracts]
        raw = _bdp(self._session, ff_tickers, ["PX_LAST"])

        # Map month_start -> implied rate (%)
        ff_rates: dict[date, float] = {}
        for code, ticker in zip(self._ff_contracts, ff_tickers):
            px = raw[ticker].get("PX_LAST")
            if px is None:
                continue
            month_start = _ff_month_start(code, today)
            if month_start is not None:
                ff_rates[month_start] = 100.0 - float(px)

        wirp_data: dict[date, ProbabilityDistribution] = {}

        for meeting in fomc_dates:
            front_month = date(meeting.year, meeting.month, 1)
            next_month_num = meeting.month + 1
            back_year = meeting.year + (next_month_num - 1) // 12
            back_month = (next_month_num - 1) % 12 + 1
            back_month_start = date(back_year, back_month, 1)

            front_rate = ff_rates.get(front_month)
            back_rate = ff_rates.get(back_month_start)

            if front_rate is None or back_rate is None:
                logger.warning("Missing FF prices for FOMC meeting %s — skipping", meeting)
                continue

            expected_change_bp = (back_rate - front_rate) * 100  # signed: negative=cut, positive=hike
            p_move = min(1.0, abs(expected_change_bp) / 25.0)
            p_hold = 1.0 - p_move

            if expected_change_bp < 0:
                outcomes = {0: p_hold, -25: p_move}
            elif expected_change_bp > 0:
                outcomes = {0: p_hold, +25: p_move}
            else:
                outcomes = {0: 1.0}

            wirp_data[meeting] = ProbabilityDistribution(
                meeting_date=meeting,
                outcomes=outcomes,
                expected_change_bp=expected_change_bp,
            )

        self._cache.set("wirp", wirp_data)
        self._last_wirp_pull = datetime.now()
        logger.info("WIRP pull complete — %d meetings", len(wirp_data))
        return wirp_data

    def _pull_futures(self, contracts: list[str]) -> dict[str, float]:
        """Pull SR3 contracts via BDP PX_LAST. Returns {contract_code: price}."""
        tickers = [f"{c} Comdty" for c in contracts]
        raw = _bdp(self._session, tickers, ["PX_LAST"])

        futures_prices: dict[str, float] = {}
        for code, ticker in zip(contracts, tickers):
            px = raw[ticker].get("PX_LAST")
            if px is not None:
                futures_prices[code] = float(px)
            else:
                logger.warning("No PX_LAST returned for %s", ticker)

        self._cache.set("futures_prices", futures_prices)
        self._last_futures_pull = datetime.now()
        logger.info("Futures pull complete — %d contracts", len(futures_prices))
        return futures_prices
