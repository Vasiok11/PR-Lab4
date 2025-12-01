from __future__ import annotations

import asyncio
from typing import Dict, Optional


class KeyValueStore:

    def __init__(self) -> None:
        self._data: Dict[str, Dict[str, int | str]] = {}
        self._version = 0
        self._lock = asyncio.Lock()

    async def write(self, key: str, value: str) -> int:
        """Leader-only write that assigns the next global version."""
        async with self._lock:
            self._version += 1
            version = self._version
            self._data[key] = {"value": value, "version": version}
            return version

    async def apply_replica(self, key: str, value: str, version: int) -> int:
        """Apply a replicated write, preserving monotonically increasing versions."""
        async with self._lock:
            current = self._data.get(key)
            if current and current["version"] >= version:
                return current["version"]
            self._data[key] = {"value": value, "version": version}
            if version > self._version:
                self._version = version
            return version

    async def read(self, key: str) -> Optional[Dict[str, int | str]]:
        async with self._lock:
            value = self._data.get(key)
            if value is None:
                return None
            return dict(value)

    async def dump(self) -> Dict[str, Dict[str, int | str]]:
        async with self._lock:
            return {k: dict(v) for k, v in self._data.items()}

    async def reset(self) -> None:
        async with self._lock:
            self._data.clear()
            self._version = 0
