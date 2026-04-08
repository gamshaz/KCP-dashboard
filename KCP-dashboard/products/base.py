from abc import ABC, abstractmethod
from datetime import date


class Product(ABC):

    @abstractmethod
    def get_contract_code(self) -> str: ...

    @abstractmethod
    def get_expiry(self, year: int, month: int) -> date: ...

    @abstractmethod
    def get_serial_expiries(self, quarterly_code: str) -> list[date]: ...

    @abstractmethod
    def year_fraction(self, date_a: date, date_b: date) -> float: ...

    @abstractmethod
    def get_tick_size(self) -> float: ...

    @abstractmethod
    def get_tick_value(self) -> float: ...

    @abstractmethod
    def get_contract_size(self) -> float: ...

    @abstractmethod
    def get_day_count_convention(self) -> str: ...
