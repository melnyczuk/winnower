"""Gentle HTTP: every request a source makes goes through one :class:`PoliteSession`.

Per host it:

- spaces requests by ``min_interval_s``, varied by ``jitter`` so they never fall into a
  metronome beat and never go out in a burst;
- slows itself down when the host pushes back (429/503 double the gap; each success
  takes 10% off again, so the pace drifts back once the host is happy);
- retries 429, 502, 503, 504 and dropped connections, waiting for ``Retry-After`` when the
  host gives one, otherwise an exponential backoff with full jitter;
- stops asking a host that wants a longer pause than ``max_backoff_s`` (a daily cap, say):
  :class:`RateLimited` is raised, and so is every later request to that host for a while,
  without touching the network;
- takes a longer breather every few dozen requests to the same host.

It stays honest: the User-Agent is the same identifying one throughout (robots.txt is
still read by the web source). The API quotas in :class:`.base.RateLimiter` still apply
on top of this.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests

from ..config import CrawlSettings

log = logging.getLogger(__name__)

RETRY_STATUS = frozenset({429, 502, 503, 504})
SLOW_DOWN_STATUS = frozenset({429, 503})  # the host is telling us to ease off
MAX_SLOWDOWN = 16.0
RECOVERY = 0.9  # slowdown is multiplied by this after each success


class RateLimited(requests.HTTPError):
    """A host asked for a longer pause than we're willing to wait (or kept refusing)."""


@dataclass
class _Host:
    last: float | None = None  # when the previous request started
    slowdown: float = 1.0
    count: int = 0  # requests since the last breather
    next_breather: int = 0
    blocked_until: float = 0.0


def retry_after(value: str | None, now: datetime | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header: delta-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - (now or datetime.now(UTC))).total_seconds())


class PoliteSession:
    """Wraps a ``requests.Session`` (or anything with ``get``/``head``) with per-host
    pacing, backoff and retries. ``clock``, ``sleep`` and ``rng`` are injectable for tests."""

    def __init__(
        self,
        inner,
        crawl: CrawlSettings | None = None,
        clock=time.monotonic,
        sleep=time.sleep,
        rng: random.Random | None = None,
    ):
        self.inner = inner
        self.crawl = crawl or CrawlSettings()
        self._clock, self._sleep = clock, sleep
        self._rng = rng or random.Random()
        self._hosts: dict[str, _Host] = {}

    @property
    def headers(self):
        return self.inner.headers

    def get(self, url: str, **kw) -> requests.Response:
        return self._request("get", url, **kw)

    def head(self, url: str, **kw) -> requests.Response:
        return self._request("head", url, **kw)

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if close:
            close()

    # -- internals --------------------------------------------------------------------

    def _host(self, name: str) -> _Host:
        h = self._hosts.get(name)
        if h is None:
            h = self._hosts[name] = _Host(next_breather=self._draw_breather())
        return h

    def _draw_breather(self) -> int:
        lo, hi = self.crawl.breather_every
        return self._rng.randint(lo, hi) if hi > 0 else 0

    def _request(self, method: str, url: str, **kw) -> requests.Response:
        name = urlparse(url).netloc
        h = self._host(name)
        c = self.crawl
        for attempt in range(c.max_retries + 1):
            if self._clock() < h.blocked_until:
                wait = h.blocked_until - self._clock()
                raise RateLimited(f"{name} asked us to back off; {wait:.0f}s left")
            self._pace(name, h)
            try:
                r = getattr(self.inner, method)(url, **kw)
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == c.max_retries:
                    raise
                self._backoff(name, attempt, None, f"{type(e).__name__}")
                continue
            if r.status_code not in RETRY_STATUS:
                h.slowdown = max(1.0, h.slowdown * RECOVERY)
                return r
            if r.status_code in SLOW_DOWN_STATUS:
                h.slowdown = min(MAX_SLOWDOWN, h.slowdown * 2)
            asked = retry_after(r.headers.get("Retry-After"))
            if asked is not None and asked > c.max_backoff_s:
                _close(r)
                self._block(name, h, asked)
                raise RateLimited(
                    f"{name} asked us to wait {asked:.0f}s (over max_backoff_s "
                    f"{c.max_backoff_s:.0f}); stopping requests to it",
                    response=r,
                )
            if attempt == c.max_retries:
                if r.status_code == 429:
                    _close(r)
                    self._block(name, h, c.max_backoff_s)
                    raise RateLimited(
                        f"{name} still says 429 after {c.max_retries} retries", response=r
                    )
                return r  # the caller's raise_for_status reports it
            _close(r)
            self._backoff(name, attempt, asked, str(r.status_code))
        raise AssertionError("unreachable")  # pragma: no cover

    def _pace(self, name: str, h: _Host) -> None:
        c = self.crawl
        if h.last is not None and c.min_interval_s > 0:
            gap = c.min_interval_s * h.slowdown * self._rng.uniform(1 - c.jitter, 1 + c.jitter)
            wait = h.last + gap - self._clock()
            if wait > 0:
                self._sleep(wait)
        h.count += 1
        if h.next_breather and h.count > h.next_breather:
            pause = self._rng.uniform(*c.breather_s)
            log.info("pausing %.0fs after %d requests to %s", pause, h.count - 1, name)
            self._sleep(pause)
            h.count = 1
            h.next_breather = self._draw_breather()
        h.last = self._clock()

    def _backoff(self, name: str, attempt: int, asked: float | None, why: str) -> None:
        if asked is None:
            asked = self._rng.uniform(0, min(self.crawl.max_backoff_s, 2.0 * 2**attempt))
        log.info("%s from %s; retrying in %.1fs", why, name, asked)
        self._sleep(asked)

    def _block(self, name: str, h: _Host, seconds: float) -> None:
        h.blocked_until = self._clock() + seconds
        log.warning("%s is rate limiting us; leaving it alone for %.0fs", name, seconds)


def _close(r) -> None:
    close = getattr(r, "close", None)
    if close:
        close()
