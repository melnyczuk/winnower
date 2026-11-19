"""Openverse audio search (https://api.openverse.org), no key needed.

Openverse indexes openly licensed audio from Jamendo, Wikimedia Commons, Freesound,
ccMixter and others, and links to the original file. Anonymous use is limited to 20
results a page, 5 requests an hour in bursts and 100 a day (the API's defaults); a token
from ``/v1/auth_tokens/register`` (env ``OPENVERSE_TOKEN``) raises that to 50 a page,
100 a minute and 10,000 a day. Results stop at a depth of 240 either way.

Freesound results are skipped by default: the Freesound adapter covers them with better
previews and its own quota.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import PurePosixPath
from urllib.parse import urlparse

import requests

from .base import RateLimiter, SourceItem, download, http_session
from .local import LOSSY_EXTS
from .spec import QUERY, Harvest, Source, arg, limits, window

API = "https://api.openverse.org/v1"
MAX_DEPTH = 240


def _licence(hit: dict) -> str | None:
    if hit.get("license_url"):
        return hit["license_url"]
    code, ver = (hit.get("license") or "").lower(), hit.get("license_version") or ""
    if not code:
        return None
    if code in ("cc0", "pdm"):
        return code
    if "sampling" in code:
        return code
    return f"CC {code.upper()} {ver}".strip()


def _lossy(filetype: str | None, url: str) -> bool:
    ext = f".{filetype.lower()}" if filetype else PurePosixPath(urlparse(url).path).suffix.lower()
    # Unknown types are treated as lossy; that only means 16-bit output.
    return ext in LOSSY_EXTS or ext == ""


class Openverse:
    provider = "openverse"

    def __init__(
        self,
        token: str | None = None,
        session: requests.Session | None = None,
        limiter: RateLimiter | None = None,
        allow_unknown: bool = False,
        include_freesound: bool = False,
    ):
        self.token = token
        self.http = session or http_session()
        if limiter is None:
            limiter = (
                RateLimiter(per_minute=100, per_day=10_000)
                if token
                else RateLimiter(per_minute=5 / 60, per_day=100, burst=5)
            )
        self.limiter = limiter
        self.allow_unknown = allow_unknown
        self.include_freesound = include_freesound

    def _get(self, url: str, params: dict | None = None) -> requests.Response:
        self.limiter.acquire()
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else None
        r = self.http.get(url, params=params, headers=headers, timeout=30)
        r.raise_for_status()
        return r

    def search(
        self,
        query: str,
        limit: int = 20,
        max_duration: float = 600.0,
        licence: str | None = None,
        licence_type: str | None = None,
        page_size: int | None = None,
    ) -> Iterator[SourceItem]:
        """``licence`` (e.g. ``by,cc0``) and ``licence_type`` (``commercial``,
        ``modification``) pass straight to the API; by default everything comes back and the
        licence gate decides."""
        page_size = page_size or (50 if self.token else 20)
        params = {"q": query, "page_size": page_size}
        if licence:
            params["license"] = licence
        if licence_type:
            params["license_type"] = licence_type
        page, yielded = 1, 0
        while yielded < limit and page * page_size <= MAX_DEPTH:
            data = self._get(f"{API}/audio/", params | {"page": page}).json()
            for hit in data.get("results", []):
                item = self.to_item(hit, self.allow_unknown)
                if item is None:
                    continue
                if item.raw.get("source") == "freesound" and not self.include_freesound:
                    continue
                if item.duration_s and item.duration_s > max_duration:
                    continue
                yield item
                yielded += 1
                if yielded >= limit:
                    return
            if page >= (data.get("page_count") or 0):
                return
            page += 1

    @staticmethod
    def to_item(hit: dict, allow_unknown: bool = False) -> SourceItem | None:
        url = hit.get("url")
        if not url:
            return None
        duration = hit.get("duration")
        return SourceItem(
            provider="openverse",
            provider_id=str(hit["id"]),
            title=hit.get("title"),
            author=hit.get("creator"),
            page_url=hit.get("foreign_landing_url"),
            licence_raw=_licence(hit),
            audio_url=url,
            tags=[t["name"] for t in hit.get("tags") or [] if t.get("name")],
            duration_s=duration / 1000 if duration else None,  # Openverse gives milliseconds
            raw=hit,
            analysed_from="original",
            lossy=_lossy(hit.get("filetype"), url),
            allow_unknown=allow_unknown,
        )

    def fetch_audio(self, item: SourceItem) -> bytes:
        # The file lives on the upstream host; no Openverse token goes with it.
        return download(self.http, item.audio_url)


def _open(args, settings) -> Harvest:
    ov = Openverse(
        args.token or settings.openverse_token,
        include_freesound=args.include_freesound,
        session=http_session(settings.crawl),
    )
    items = ov.search(
        args.query, licence=args.licence, licence_type=args.licence_type, **window(args)
    )
    return Harvest(items, ov.fetch_audio)


SOURCE = Source(
    "openverse",
    "search Openverse (no key; OPENVERSE_TOKEN optional)",
    (
        QUERY,
        *limits(),
        arg("--token", help="Openverse token for higher limits (default: OPENVERSE_TOKEN)"),
        arg("--license", dest="licence", help="Openverse licence codes, e.g. by,cc0"),
        arg("--license-type", dest="licence_type", help="commercial, modification, all-cc, ..."),
        arg(
            "--include-freesound",
            action="store_true",
            help="keep Freesound results (skipped by default: use `harvest freesound`)",
        ),
    ),
    _open,
)
