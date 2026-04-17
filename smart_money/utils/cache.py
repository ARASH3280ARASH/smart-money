from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional, Tuple

from cachetools import TTLCache


class AsyncTTLCache:
    """Thread-safe TTL cache for async contexts."""

    def __init__(self, maxsize: int = 2048, ttl: float = 30.0) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl)
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        async with self._lock:
            return self._cache.get(key)

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._cache[key] = value

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        async with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        return len(self._cache)


# Shared caches keyed by TTL bucket
_wallet_cache = AsyncTTLCache(maxsize=1000, ttl=30)
_price_cache = AsyncTTLCache(maxsize=2000, ttl=10)
_metrics_cache = AsyncTTLCache(maxsize=500, ttl=300)
_token_cache = AsyncTTLCache(maxsize=2000, ttl=60)


def get_wallet_cache() -> AsyncTTLCache:
    return _wallet_cache


def get_price_cache() -> AsyncTTLCache:
    return _price_cache


def get_metrics_cache() -> AsyncTTLCache:
    return _metrics_cache


def get_token_cache() -> AsyncTTLCache:
    return _token_cache
