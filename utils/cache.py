from __future__ import annotations

from datetime import datetime


class Cache:

    def __init__(self):
        self._store: dict[str, object] = {}
        self._timestamps: dict[str, datetime] = {}

    def set(self, key: str, value: object) -> None:
        self._store[key] = value
        self._timestamps[key] = datetime.now()

    def get(self, key: str) -> object | None:
        return self._store.get(key, None)

    def invalidate(self, key: str) -> None:
        self._store.pop(key, None)
        self._timestamps.pop(key, None)

    def invalidate_all(self) -> None:
        self._store.clear()
        self._timestamps.clear()

    def get_timestamp(self, key: str) -> datetime | None:
        return self._timestamps.get(key, None)

    def has(self, key: str) -> bool:
        return key in self._store
