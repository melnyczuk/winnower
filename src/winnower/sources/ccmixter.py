"""ccMixter (https://ccmixter.org), no key needed.

The query API (``/api/query?f=json``) searches uploads by text and tags; each upload
lists its files (often an MP3 plus a FLAC or WAV, sometimes a ZIP of stems). The best
audio file is used: FLAC/WAV under the size cap, else MP3. Every upload carries a
Creative Commons ``license_url``. Default rate: 30 requests a minute.

The server echoes the whole JSON body into an ``X-JSON`` response header, and Python's
HTTP client rejects header lines over 64 KB, so pages are small (5 uploads) and halved
when one still comes back too big.
"""

from __future__ import annotations

from collections.abc import Iterator

import requests

from .base import MAX_DOWNLOAD_BYTES, RateLimiter, SourceItem, download, http_session
from .internet_archive import parse_length
from .local import AUDIO_EXTS, LOSSY_EXTS
from .spec import Harvest, Source, SourceError, arg, limits, window

API = "https://ccmixter.org/api/query"


def _ext(f: dict) -> str:
    info = f.get("file_format_info") or {}
    ext = info.get("default-ext") or f.get("file_name", "").rsplit(".", 1)[-1]
    return f".{ext.lower()}"


def choose(files: list[dict], max_bytes: int = MAX_DOWNLOAD_BYTES) -> dict | None:
    def ok(f):
        info = f.get("file_format_info") or {}
        size = str(f.get("file_rawsize") or "")
        fits = not size.isdigit() or int(size) <= max_bytes
        return info.get("media-type") == "audio" and _ext(f) in AUDIO_EXTS and fits

    audio = [f for f in files if ok(f) and f.get("download_url")]
    lossless = [f for f in audio if _ext(f) not in LOSSY_EXTS]
    return (lossless or audio or [None])[0]


class CcMixter:
    provider = "ccmixter"

    def __init__(
        self,
        session: requests.Session | None = None,
        limiter: RateLimiter | None = None,
        allow_unknown: bool = False,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
    ):
        self.http = session or http_session()
        self.limiter = limiter or RateLimiter(per_minute=30)
        self.allow_unknown = allow_unknown
        self.max_bytes = max_bytes

    def _get(self, params: dict) -> requests.Response:
        self.limiter.acquire()
        r = self.http.get(API, params=params, timeout=30)
        r.raise_for_status()
        return r

    def search(
        self,
        query: str | None = None,
        limit: int = 20,
        max_duration: float = 600.0,
        tags: str | None = None,
        page_size: int = 5,
    ) -> Iterator[SourceItem]:
        params: dict = {"f": "json"}
        if query:
            params["search"] = query
        if tags:
            params["tags"] = tags
        offset, yielded = 0, 0
        while yielded < limit:
            try:
                uploads = self._get(params | {"offset": offset, "limit": page_size}).json()
            except requests.ConnectionError:
                if page_size == 1:
                    raise
                page_size = max(1, page_size // 2)  # the X-JSON header was too long
                continue
            uploads = uploads or []
            for up in uploads:
                item = self.to_item(up, self.max_bytes, self.allow_unknown)
                if item is None or (item.duration_s and item.duration_s > max_duration):
                    continue
                yield item
                yielded += 1
                if yielded >= limit:
                    return
            if len(uploads) < page_size:
                return
            offset += page_size

    @staticmethod
    def to_item(
        up: dict, max_bytes: int = MAX_DOWNLOAD_BYTES, allow_unknown: bool = False
    ) -> SourceItem | None:
        f = choose(up.get("files") or [], max_bytes)
        if f is None:
            return None
        extra = up.get("upload_extra") or {}
        usertags = extra.get("usertags", "") if isinstance(extra, dict) else ""
        info = f.get("file_format_info") or {}
        return SourceItem(
            provider="ccmixter",
            provider_id=str(up["upload_id"]),
            title=up.get("upload_name"),
            author=up.get("user_real_name") or up.get("user_name"),
            page_url=up.get("file_page_url"),
            licence_raw=up.get("license_url"),
            audio_url=f["download_url"],
            tags=[t for t in usertags.split(",") if t],
            description=up.get("upload_description_plain") or None,
            duration_s=parse_length(info.get("ps")),
            raw={
                "user_name": up.get("user_name"),
                "license_name": up.get("license_name"),
                "file": {k: f.get(k) for k in ("file_id", "file_name", "file_rawsize")},
                "bpm": extra.get("bpm") if isinstance(extra, dict) else None,
            },
            analysed_from="original",
            lossy=_ext(f) in LOSSY_EXTS,
            allow_unknown=allow_unknown,
        )

    def fetch_audio(self, item: SourceItem) -> bytes:
        return download(self.http, item.audio_url, self.max_bytes)


def _open(args, settings) -> Harvest:
    if not args.query and not args.tags:
        raise SourceError("Give a search query or --tags")
    cc = CcMixter(session=http_session(settings.crawl))
    return Harvest(cc.search(args.query, tags=args.tags, **window(args)), cc.fetch_audio)


SOURCE = Source(
    "ccmixter",
    "search ccMixter (no key)",
    (
        arg("query", nargs="?", help="text to search for"),
        *limits(),
        arg("--tags", help="comma-separated ccMixter tags, e.g. ambient,field_recording"),
    ),
    _open,
)
