from abc import ABC, abstractmethod
from datetime import date


class DataSource(ABC):

    @abstractmethod
    def pull_startup_data(self, fomc_dates: list[date], contracts: list[str]) -> tuple[dict, dict]:
        """Pull WIRP probabilities and live futures prices. Returns (wirp_data, futures_prices)."""

    @abstractmethod
    def pull_option_chain(self, underlying: str) -> list[str]:
        """Return list of option tickers for the given underlying futures contract."""

    @abstractmethod
    def pull_strike_data(self, tickers: list[str], target_strike: float) -> dict:
        """Pull OptionQuote data for tickers within ±5 strikes of target_strike."""

    @abstractmethod
    def get_cached_wirp(self) -> dict | None:
        """Return cached WIRP data or None if not yet pulled."""

    @abstractmethod
    def get_cached_futures(self) -> dict | None:
        """Return cached futures prices or None if not yet pulled."""

    @abstractmethod
    def get_cached_omon(self) -> dict | None:
        """Return cached OMON chain or None if not yet pulled."""

    @abstractmethod
    def is_omon_stale(self, current_scenario_id: str) -> bool:
        """Return True if cached OMON data was pulled for a different scenario."""

    @abstractmethod
    def get_pull_timestamps(self) -> dict:
        """Return dict of last pull timestamps keyed by data type."""
