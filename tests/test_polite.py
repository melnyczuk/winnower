import random
from datetime import UTC, datetime

import pytest
import requests
from fakes import FakeResponse, FakeSession

from winnower.config import CrawlSettings, load_settings
from winnower.sources.polite import PoliteSession, RateLimited, retry_after

A = "https://a.example/x"
B = "https://b.example/y"


class Clock:
    """A fake monotonic clock that sleeping advances."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def polite(pages, clock=None, **crawl):
    crawl = {"breather_every": (0, 0), **crawl}
    clock = clock or Clock()
    inner = FakeSession(pages)
    s = PoliteSession(inner, CrawlSettings(**crawl), clock, clock.sleep, random.Random(1))
    return s, inner, clock


def test_same_host_requests_are_spaced_irregularly():
    s, _, clock = polite({A: "ok"}, min_interval_s=2.0, jitter=0.5)
    for _ in range(6):
        s.get(A)
    gaps = clock.sleeps
    assert len(gaps) == 5  # the first request doesn't wait
    assert all(1.0 <= g <= 3.0 for g in gaps)
    assert len({round(g, 3) for g in gaps}) == 5  # no metronome


def test_different_hosts_do_not_wait_on_each_other():
    s, _, clock = polite({A: "ok", B: "ok"}, min_interval_s=2.0)
    s.get(A)
    s.get(B)
    assert clock.sleeps == []


def test_time_already_spent_counts_towards_the_gap():
    s, _, clock = polite({A: "ok"}, min_interval_s=2.0, jitter=0.0)
    s.get(A)
    clock.now += 5  # e.g. analysing the last download
    s.get(A)
    assert clock.sleeps == []


def test_429_with_retry_after_waits_then_succeeds():
    pages = [FakeResponse("", status=429, headers={"Retry-After": "7"}), "ok"]
    s, inner, clock = polite(pages, min_interval_s=0)
    r = s.get(A, timeout=5)
    assert r.status_code == 200
    assert clock.sleeps == [7.0]
    assert len(inner.calls) == 2


def test_503_backs_off_and_slows_the_host_then_recovers():
    pages = [FakeResponse("", status=503)] * 2 + ["ok"] * 20
    s, _, clock = polite(pages, min_interval_s=1.0, jitter=0.0)
    s.get(A)
    host = s._hosts["a.example"]
    # two backoffs (within 2s, then 4s) and the doubled gaps: nothing longer than 4s
    assert clock.sleeps and max(clock.sleeps) <= 4.0
    assert host.slowdown == pytest.approx(4 * 0.9)
    for _ in range(19):
        s.get(A)
    assert host.slowdown == 1.0


def test_dropped_connections_are_retried():
    class Flaky(FakeSession):
        def get(self, url, **kw):
            self.calls.append(url)
            if len(self.calls) == 1:
                raise requests.ConnectionError("reset")
            return FakeResponse("ok")

    clock = Clock()
    s = PoliteSession(
        Flaky({}), CrawlSettings(min_interval_s=0, breather_every=(0, 0)), clock, clock.sleep
    )
    assert s.get(A).text == "ok"


def test_long_retry_after_blocks_the_host_without_more_requests():
    pages = [FakeResponse("", status=429, headers={"Retry-After": "86400"}), "ok"]
    s, inner, clock = polite(pages, max_backoff_s=300)
    with pytest.raises(RateLimited):
        s.get(A)
    with pytest.raises(RateLimited):
        s.get(A)
    assert len(inner.calls) == 1
    assert isinstance(RateLimited("x"), requests.RequestException)  # stops a search cleanly


def test_persistent_429_gives_up_and_blocks():
    pages = [FakeResponse("", status=429)] * 3
    s, inner, _ = polite(pages, max_retries=2, min_interval_s=0)
    with pytest.raises(RateLimited):
        s.get(A)
    assert len(inner.calls) == 3


def test_persistent_502_is_left_to_raise_for_status():
    pages = [FakeResponse("", status=502)] * 3
    s, _, _ = polite(pages, max_retries=2, min_interval_s=0)
    r = s.get(A)
    assert r.status_code == 502
    with pytest.raises(requests.HTTPError):
        r.raise_for_status()


def test_breather_after_the_drawn_number_of_requests():
    s, _, clock = polite(
        {A: "ok"}, min_interval_s=0, breather_every=(3, 3), breather_s=(10.0, 20.0)
    )
    for _ in range(7):
        s.get(A)
    assert len(clock.sleeps) == 2  # before requests 4 and 7
    assert all(10.0 <= p <= 20.0 for p in clock.sleeps)


def test_passes_arguments_and_headers_through():
    s, inner, _ = polite({A: "ok"})
    s.headers["User-Agent"] = "winnower/test"
    s.get(A, params={"q": 1}, headers={"X": "y"}, timeout=3, stream=True)
    assert inner.calls == [(A, {"q": 1}, {"X": "y"})]
    assert inner.headers["User-Agent"] == "winnower/test"


def test_retry_after_parsing():
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert retry_after("12") == 12.0
    assert retry_after("Thu, 01 Jan 2026 12:00:30 GMT", now) == 30.0
    assert retry_after("Thu, 01 Jan 2026 11:00:00 GMT", now) == 0.0
    assert retry_after("soon") is None
    assert retry_after(None) is None


def test_crawl_settings_from_toml(tmp_path, monkeypatch):
    monkeypatch.delenv("WINNOWER_MIN_INTERVAL_S", raising=False)
    (tmp_path / "w.toml").write_text(
        "[crawl]\nmin_interval_s = 3\nbreather_every = [10, 20]\nbreather_s = 5\n"
    )
    c = load_settings(tmp_path / "w.toml").crawl
    assert c.min_interval_s == 3.0
    assert c.breather_every == (10, 20)
    assert c.breather_s == (5.0, 5.0)
    assert c.max_retries == CrawlSettings().max_retries
    with pytest.raises(ValueError):
        CrawlSettings(jitter=1.5)
