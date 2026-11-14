"""Recorded-response fakes for source adapter tests."""

import json
from pathlib import Path

import requests

from winnower.sources.base import RateLimiter

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    path = FIXTURES / name
    return json.loads(path.read_text()) if path.suffix == ".json" else path.read_text()


def no_wait():
    return RateLimiter(60, sleep=lambda s: None)


class FakeResponse:
    def __init__(self, data, status=200, headers=None):
        self._data = data
        self.status_code = status
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        return json.loads(self._data) if isinstance(self._data, str | bytes) else self._data

    @property
    def text(self):
        if isinstance(self._data, bytes):
            return self._data.decode()
        return self._data if isinstance(self._data, str) else json.dumps(self._data)

    @property
    def content(self):
        return self._data if isinstance(self._data, bytes) else self.text.encode()

    def iter_content(self, chunk_size=1):
        data = self.content
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    def close(self):
        pass


class FakeSession:
    """Answers GETs in order from ``pages``, or by URL from ``routes`` when given a dict.

    A page that is already a FakeResponse is returned as is (for status codes or headers).
    """

    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, headers=None, timeout=None, stream=False):
        self.calls.append((url, params, headers))
        if isinstance(self.pages, dict):
            page = self.pages.get(url)
            if page is None:
                page = FakeResponse("", status=404)
        else:
            page = self.pages[len(self.calls) - 1]
        return page if isinstance(page, FakeResponse) else FakeResponse(page)

    def head(self, url, allow_redirects=False, timeout=None):
        """Audio unless the URL is routed to an HTML page (dict routes only)."""
        self.calls.append((url, "HEAD", None))
        page = self.pages.get(url) if isinstance(self.pages, dict) else None
        if isinstance(page, str) and "<html" in page.lower():
            return FakeResponse("", headers={"Content-Type": "text/html; charset=utf-8"})
        return FakeResponse("", headers={"Content-Type": "audio/wav"})
