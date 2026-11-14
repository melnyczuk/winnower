"""Wikimedia Commons audio (https://commons.wikimedia.org), no key needed.

Uses the MediaWiki API's search generator over the File namespace with
``filetype:audio``, and ``imageinfo`` + ``extmetadata`` for the file URL and licence.
Every Commons file carries a licence (``LicenseUrl``, or a ``LicenseShortName`` such as
"Public domain"). Files are the uploaded originals.

Wikimedia asks API clients to send a User-Agent with contact details: set
``WINNOWER_USER_AGENT`` to e.g. ``"winnower/0.1 (you@example.org)"``. Requests are made
one at a time, at most 60 a minute.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import PurePosixPath
from urllib.parse import urlparse, urlunparse

import requests

from .base import RateLimiter, SourceItem, download, http_session, strip_html
from .local import AUDIO_EXTS, LOSSY_EXTS
from .spec import QUERY, Harvest, Source, limits, window

API = "https://commons.wikimedia.org/w/api.php"
EXTMETA = "LicenseUrl|LicenseShortName|Artist|ImageDescription|ObjectName|Categories"
# Housekeeping categories that say nothing about the sound.
_NOISE = ("license", "self-published", "gfdl", "cc-", "pd-", "uploaded via", "files by")


def _meta(ii: dict, key: str) -> str | None:
    v = (ii.get("extmetadata") or {}).get(key, {}).get("value")
    return str(v) if v not in (None, "") else None


def _categories(raw: str | None) -> list[str]:
    cats = [c.strip() for c in (raw or "").split("|") if c.strip()]
    return [c for c in cats if not any(n in c.lower() for n in _NOISE)]


class WikimediaCommons:
    provider = "wikimedia"

    def __init__(
        self,
        session: requests.Session | None = None,
        limiter: RateLimiter | None = None,
        allow_unknown: bool = False,
    ):
        self.http = session or http_session()
        self.limiter = limiter or RateLimiter(per_minute=60)
        self.allow_unknown = allow_unknown

    def _get(self, params: dict) -> requests.Response:
        self.limiter.acquire()
        r = self.http.get(API, params=params, timeout=30)
        r.raise_for_status()
        return r

    def search(
        self, query: str, limit: int = 20, max_duration: float = 600.0, page_size: int = 50
    ) -> Iterator[SourceItem]:
        params = {
            "action": "query",
            "generator": "search",
            "gsrnamespace": 6,
            "gsrsearch": f"{query} filetype:audio",
            "gsrlimit": min(page_size, 50),
            "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata",
            "iiextmetadatafilter": EXTMETA,
            "format": "json",
            "formatversion": 2,
        }
        cont: dict = {}
        yielded = 0
        while yielded < limit:
            data = self._get(params | cont).json()
            pages = sorted(data.get("query", {}).get("pages", []), key=lambda p: p.get("index", 0))
            for page in pages:
                item = self.to_item(page, self.allow_unknown)
                if item is None or (item.duration_s and item.duration_s > max_duration):
                    continue
                yield item
                yielded += 1
                if yielded >= limit:
                    return
            cont = data.get("continue") or {}
            if not cont:
                return

    @staticmethod
    def to_item(page: dict, allow_unknown: bool = False) -> SourceItem | None:
        infos = page.get("imageinfo") or []
        if not infos or not infos[0].get("url"):
            return None
        ii = infos[0]
        # Drop the utm_* tracking query the API appends.
        url = urlunparse(urlparse(ii["url"])._replace(query=""))
        ext = PurePosixPath(urlparse(url).path).suffix.lower()
        if ext not in AUDIO_EXTS:  # e.g. MIDI
            return None
        name = page.get("title", "").removeprefix("File:")
        return SourceItem(
            provider="wikimedia",
            provider_id=str(page["pageid"]),
            title=strip_html(_meta(ii, "ObjectName")) or PurePosixPath(name).stem or None,
            author=strip_html(_meta(ii, "Artist")),
            page_url=ii.get("descriptionurl"),
            licence_raw=_meta(ii, "LicenseUrl") or _meta(ii, "LicenseShortName"),
            audio_url=url,
            tags=_categories(_meta(ii, "Categories")),
            description=strip_html(_meta(ii, "ImageDescription")),
            duration_s=ii.get("duration"),
            raw={"title": page.get("title"), "mime": ii.get("mime"), "size": ii.get("size")},
            analysed_from="original",
            lossy=ext in LOSSY_EXTS,
            allow_unknown=allow_unknown,
        )

    def fetch_audio(self, item: SourceItem) -> bytes:
        return download(self.http, item.audio_url)


def _open(args, settings) -> Harvest:
    wm = WikimediaCommons()
    return Harvest(wm.search(args.query, **window(args)), wm.fetch_audio)


SOURCE = Source("wikimedia", "search Wikimedia Commons (no key)", (QUERY, *limits()), _open)
