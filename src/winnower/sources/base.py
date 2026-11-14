"""Common types and HTTP helpers for source adapters.

Each adapter turns an upstream API (Freesound, Openverse, Internet Archive, Wikimedia
Commons, NASA, ccMixter, a manifest file, plain web pages) into a stream of
:class:`SourceItem` and knows how to fetch the audio for one item. Adapters take an
injectable ``session`` and ``limiter`` so tests can feed them recorded responses.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

USER_AGENT = os.environ.get("WINNOWER_USER_AGENT", "winnower/0.1 (+sample research tool)")
MAX_DOWNLOAD_BYTES = 200_000_000


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
    analysed_from: str = "preview"  # "preview" (a transcode) or "original"
    lossy: bool | None = None  # None: infer from analysed_from
    allow_unknown: bool = False  # keep, flagged, if no licence is recognised


class RateLimiter:
    """Token bucket: at most ``per_minute`` calls a minute and ``per_day`` calls in total.

    ``per_minute`` may be fractional (5/60 is five an hour); ``burst`` is the bucket size,
    by default one minute's worth (at least one call).
    """

    def __init__(
        self,
        per_minute: float,
        per_day: int | None = None,
        clock=time.monotonic,
        sleep=time.sleep,
        burst: float | None = None,
    ):
        self.capacity = float(burst if burst is not None else max(1.0, per_minute))
        self.tokens = self.capacity
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


class TooLarge(RuntimeError):
    pass


def http_session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def download(http, url: str, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    """Stream ``url`` into memory, refusing anything over ``max_bytes`` (a Pi has little RAM)."""
    r = http.get(url, stream=True, timeout=60)
    r.raise_for_status()
    size = r.headers.get("Content-Length")
    if size and size.isdigit() and int(size) > max_bytes:
        r.close()
        raise TooLarge(f"{url} is {int(size)} bytes, over the {max_bytes} byte cap")
    buf = bytearray()
    for chunk in r.iter_content(chunk_size=1 << 16):
        buf += chunk
        if len(buf) > max_bytes:
            r.close()
            raise TooLarge(f"{url} is over the {max_bytes} byte cap")
    return bytes(buf)
