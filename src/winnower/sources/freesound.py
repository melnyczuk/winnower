"""Freesound API v2 adapter.

Search and HQ previews need only an API key (https://freesound.org/apiv2/apply).
Downloading lossless originals needs OAuth2 and is not implemented yet; v0 analyses and
cuts from the high-quality OGG preview and records that in the sidecar.
Default limits: 60 requests/minute, 2,000/day.
"""

from __future__ import annotations

from collections.abc import Iterator

import requests

from .base import RateLimiter, SourceItem, download, user_agent
from .spec import QUERY, Harvest, Source, SourceError, arg, limits, window

API = "https://freesound.org/apiv2"
FIELDS = "id,name,username,license,url,previews,tags,description,duration"


class Freesound:
    provider = "freesound"

    def __init__(
        self,
        token: str,
        session: requests.Session | None = None,
        limiter: RateLimiter | None = None,
    ):
        if not token:
            raise ValueError("Freesound needs an API token (FREESOUND_TOKEN)")
        self.token = token
        self.http = session or requests.Session()
        self.http.headers.setdefault("User-Agent", user_agent())
        self.limiter = limiter or RateLimiter(per_minute=60, per_day=2000)

    def _get(self, url: str, params: dict | None = None) -> requests.Response:
        self.limiter.acquire()
        r = self.http.get(
            url, params=params, headers={"Authorization": f"Token {self.token}"}, timeout=30
        )
        r.raise_for_status()
        return r

    def search(
        self,
        query: str,
        limit: int = 20,
        min_duration: float = 3.0,
        max_duration: float = 600.0,
        page_size: int = 50,
    ) -> Iterator[SourceItem]:
        params = {
            "query": query,
            "filter": f"duration:[{min_duration} TO {max_duration}]",
            "fields": FIELDS,
            "page_size": min(page_size, 150),
        }
        url: str | None = f"{API}/search/text/"
        yielded = 0
        while url and yielded < limit:
            data = self._get(url, params).json()
            params = None  # the `next` URL already carries them
            for hit in data.get("results", []):
                item = self.to_item(hit)
                if item is None:
                    continue
                yield item
                yielded += 1
                if yielded >= limit:
                    return
            url = data.get("next")

    @staticmethod
    def to_item(hit: dict) -> SourceItem | None:
        previews = hit.get("previews") or {}
        audio = previews.get("preview-hq-ogg") or previews.get("preview-hq-mp3")
        if not audio:
            return None
        return SourceItem(
            provider="freesound",
            provider_id=str(hit["id"]),
            title=hit.get("name"),
            author=hit.get("username"),
            page_url=hit.get("url"),
            licence_raw=hit.get("license"),
            audio_url=audio,
            tags=list(hit.get("tags") or []),
            description=hit.get("description"),
            duration_s=hit.get("duration"),
            raw=hit,
        )

    def fetch_audio(self, item: SourceItem) -> bytes:
        # Previews are plain file downloads from the CDN, not API endpoint calls.
        return download(self.http, item.audio_url)


def _open(args, settings) -> Harvest:
    token = args.token or settings.freesound_token
    if not token:
        raise SourceError(
            "Give --token, or set FREESOUND_TOKEN (environment or .env) "
            "or freesound_token in winnower.toml"
        )
    fs = Freesound(token)
    return Harvest(fs.search(args.query, **window(args)), fs.fetch_audio)


SOURCE = Source(
    "freesound",
    "search Freesound (needs an API key)",
    (QUERY, *limits(), arg("--token", help="Freesound API key (default: FREESOUND_TOKEN)")),
    _open,
)
