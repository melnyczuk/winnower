"""Common types for source adapters (Freesound now; Internet Archive, Wikimedia later)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceItem:
    provider: str
    provider_id: str
    title: str | None
    author: str | None
    page_url: str | None
    licence_raw: str | None
    audio_url: str  # what we analyse (a preview is fine)
    tags: list[str] = field(default_factory=list)
    description: str | None = None
    duration_s: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class RateLimiter:
    """Token bucket: at most ``per_minute`` calls a minute and ``per_day`` calls in total."""

    def __init__(
        self, per_minute: int, per_day: int | None = None, clock=time.monotonic, sleep=time.sleep
    ):
        self.capacity = per_minute
        self.tokens = float(per_minute)
        self.rate = per_minute / 60.0
        self.per_day = per_day
        self.used = 0
        self._clock, self._sleep = clock, sleep
        self._last = clock()

    def acquire(self) -> None:
        if self.per_day is not None and self.used >= self.per_day:
            raise DailyQuotaExceeded(f"daily quota of {self.per_day} requests used")
        now = self._clock()
        self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
        self._last = now
        if self.tokens < 1:
            wait = (1 - self.tokens) / self.rate
            self._sleep(wait)
            self.tokens = 1
            self._last = self._clock()
        self.tokens -= 1
        self.used += 1


class DailyQuotaExceeded(RuntimeError):
    pass
