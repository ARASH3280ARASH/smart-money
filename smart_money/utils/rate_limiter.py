from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Deque

from utils.logger import get_logger

logger = get_logger(__name__)


class TokenBucketRateLimiter:
    """
    Tracks CU consumption over a rolling window.
    Blocks callers when budget is nearly exhausted.
    """

    def __init__(self, cu_per_second: int = 1000, window_sec: float = 4.0) -> None:
        self.cu_per_window = cu_per_second * window_sec
        self.window_sec = window_sec
        self._usage: Deque[tuple[float, int]] = deque()  # (timestamp, cu)
        self._lock = asyncio.Lock()
        self._total_cu_consumed = 0

    def _evict_old(self, now: float) -> None:
        cutoff = now - self.window_sec
        while self._usage and self._usage[0][0] < cutoff:
            self._usage.popleft()

    def _current_usage(self, now: float) -> int:
        self._evict_old(now)
        return sum(cu for _, cu in self._usage)

    async def acquire(self, cu_cost: int = 1) -> None:
        """Wait until sufficient CU budget is available, then reserve it."""
        async with self._lock:
            while True:
                now = time.monotonic()
                used = self._current_usage(now)
                if used + cu_cost <= self.cu_per_window:
                    self._usage.append((now, cu_cost))
                    self._total_cu_consumed += cu_cost
                    return
                # Budget full – wait briefly and retry
                pressure = (used + cu_cost) / self.cu_per_window
                if pressure > 0.95:
                    wait = 0.5
                elif pressure > 0.80:
                    wait = 0.2
                else:
                    wait = 0.05
                logger.debug(
                    "Rate limit pressure %.0f%% – sleeping %.2fs", pressure * 100, wait
                )
                await asyncio.sleep(wait)

    @property
    def total_cu_consumed(self) -> int:
        return self._total_cu_consumed

    async def current_pressure(self) -> float:
        async with self._lock:
            used = self._current_usage(time.monotonic())
            return used / self.cu_per_window


_limiter: TokenBucketRateLimiter | None = None


def get_rate_limiter() -> TokenBucketRateLimiter:
    global _limiter
    if _limiter is None:
        from config.settings import get_settings

        s = get_settings()
        _limiter = TokenBucketRateLimiter(cu_per_second=s.cu_per_second)
    return _limiter
