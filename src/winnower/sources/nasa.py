"""NASA Image and Video Library audio (https://images.nasa.gov), no key needed.

Search with ``media_type=audio``; each hit's ``href`` is an asset manifest listing the
files (``~orig.wav`` or ``~orig.mp3``, plus 128k/64k MP3 and M4A transcodes). File choice:
a lossless original, then a lossy original, then the 128k MP3 (``analysed_from:
"preview"``).

NASA media are generally not copyrighted, but some include third-party material, so the
licence is recorded as ``NASA-Media`` and flagged by default; move it to ``allow`` in your
policy if that suits you. The search API doesn't report durations, so ``--max-duration``
can't skip long recordings (they're analysed in chunks). Default rate: 60 requests a
minute.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import PurePosixPath
from urllib.parse import urlparse

import requests

from .base import RateLimiter, SourceItem, download, http_session, strip_html
from .local import AUDIO_EXTS, LOSSY_EXTS

API = "https://images-api.nasa.gov/search"
DETAILS = "https://images.nasa.gov/details/{}"


def _https(url: str) -> str:
    return "https://" + url.removeprefix("http://") if url.startswith("http://") else url


def _ext(url: str) -> str:
    return PurePosixPath(urlparse(url).path).suffix.lower()


def choose(manifest: list[str]) -> tuple[str, str] | None:
    """Pick the asset to analyse; returns (url, analysed_from)."""
    audio = [_https(u) for u in dict.fromkeys(manifest) if _ext(u) in AUDIO_EXTS]
    orig = [u for u in audio if "~orig." in u]
    for u in orig:
        if _ext(u) not in LOSSY_EXTS:
            return u, "original"
    for u in audio:
        if _ext(u) not in LOSSY_EXTS:
            return u, "original"
    if orig:
        return orig[0], "original"
    # MP3 decodes without ffmpeg; prefer the higher bitrate.
    rest = sorted(audio, key=lambda u: (_ext(u) != ".mp3", "~128k" not in u))
    return (rest[0], "preview") if rest else None


class NasaMedia:
    provider = "nasa"

    def __init__(
        self,
        session: requests.Session | None = None,
        limiter: RateLimiter | None = None,
        allow_unknown: bool = False,
    ):
        self.http = session or http_session()
        self.limiter = limiter or RateLimiter(per_minute=60)
        self.allow_unknown = allow_unknown

    def _get(self, url: str, params: dict | None = None) -> requests.Response:
        self.limiter.acquire()
        r = self.http.get(url, params=params, timeout=30)
        r.raise_for_status()
        return r

    def search(
        self, query: str, limit: int = 20, max_duration: float = 600.0, page_size: int = 50
    ) -> Iterator[SourceItem]:
        params = {"q": query, "media_type": "audio", "page_size": page_size}
        page, yielded = 1, 0
        while yielded < limit:
            coll = self._get(API, params | {"page": page}).json()["collection"]
            for hit in coll.get("items", []):
                if not hit.get("href"):
                    continue
                manifest = self._get(_https(hit["href"])).json()
                item = self.to_item(hit, manifest)
                if item is None:
                    continue
                yield item
                yielded += 1
                if yielded >= limit:
                    return
            if not any(link.get("rel") == "next" for link in coll.get("links", [])):
                return
            page += 1

    @staticmethod
    def to_item(hit: dict, manifest: list[str]) -> SourceItem | None:
        data = (hit.get("data") or [{}])[0]
        picked = choose(manifest)
        if not data.get("nasa_id") or picked is None:
            return None
        url, origin = picked
        author = data.get("photographer") or data.get("secondary_creator")
        if not author:
            author = f"NASA {data['center']}" if data.get("center") else "NASA"
        return SourceItem(
            provider="nasa",
            provider_id=data["nasa_id"],
            title=data.get("title"),
            author=author,
            page_url=DETAILS.format(data["nasa_id"]),
            licence_raw="NASA-Media",
            audio_url=url,
            tags=[k.strip() for k in data.get("keywords") or [] if k and k.strip()],
            description=strip_html(data.get("description")),
            raw={k: data.get(k) for k in ("nasa_id", "center", "date_created", "album")},
            analysed_from=origin,
            lossy=_ext(url) in LOSSY_EXTS,
        )

    def fetch_audio(self, item: SourceItem) -> bytes:
        return download(self.http, item.audio_url)
