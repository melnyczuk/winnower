"""Internet Archive audio (https://archive.org), no key needed.

Search goes through ``advancedsearch.php``; each hit's file list comes from
``/metadata/{identifier}``. One SourceItem is yielded per track: an original and its
derivatives (IA's FLAC, VBR MP3 and Ogg transcodes) count as one track, analysed once.

File choice per track, best first: a lossless original under the size cap, a lossless
derivative under the cap, a lossy original (e.g. a netlabel's MP3 upload), then a lossy
derivative (marked ``analysed_from: "preview"``).

Only items with a ``licenseurl`` are searched unless ``allow_unknown`` is set. IA
publishes no rate limit; the default here is 30 requests a minute.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import PurePosixPath
from urllib.parse import quote

import requests

from .base import MAX_DOWNLOAD_BYTES, RateLimiter, SourceItem, download, http_session, strip_html
from .local import AUDIO_EXTS, LOSSY_EXTS
from .spec import QUERY, UNLICENSED, Harvest, Source, arg, limits, window

SEARCH = "https://archive.org/advancedsearch.php"
METADATA = "https://archive.org/metadata/{}"
DOWNLOAD = "https://archive.org/download/{}/{}"
DETAILS = "https://archive.org/details/{}"


def parse_length(v) -> float | None:
    """IA lengths are seconds (``"132"``, ``"2306.88"``) or clock time (``"02:12"``)."""
    if v in (None, ""):
        return None
    try:
        parts = [float(p) for p in str(v).split(":")]
    except ValueError:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def _size(f: dict) -> int | None:
    s = f.get("size")
    return int(s) if s and str(s).isdigit() else None


def _ext(name: str) -> str:
    return PurePosixPath(name).suffix.lower()


def _first(v) -> str | None:
    if isinstance(v, list):
        return "; ".join(str(x) for x in v) if v else None
    return v


def _subjects(v) -> list[str]:
    items = v if isinstance(v, list) else str(v or "").split(";")
    return [s.strip() for s in items if s and s.strip()]


def tracks(files: list[dict]) -> dict[str, list[dict]]:
    """Audio files grouped by the original they derive from."""
    out: dict[str, list[dict]] = {}
    for f in files:
        name = f.get("name", "")
        if _ext(name) not in AUDIO_EXTS or f.get("source") == "metadata":
            continue
        key = f.get("original") if f.get("source") == "derivative" else name
        out.setdefault(key or name, []).append(f)
    return out


def choose(group: list[dict], max_bytes: int = MAX_DOWNLOAD_BYTES) -> tuple[dict, str] | None:
    """Pick the file to analyse from one track's files; returns (file, analysed_from)."""

    def fits(f):
        size = _size(f)
        return size is None or size <= max_bytes

    lossless = [f for f in group if _ext(f["name"]) not in LOSSY_EXTS and fits(f)]
    lossy = [f for f in group if _ext(f["name"]) in LOSSY_EXTS and fits(f)]
    for f in lossless:
        if f.get("source") == "original":
            return f, "original"
    if lossless:
        return min(lossless, key=lambda f: _size(f) or 0), "original"
    for f in lossy:
        if f.get("source") == "original":
            return f, "original"
    # Prefer Ogg (no MP3 encoder delay) then the larger, higher-bitrate file.
    lossy.sort(key=lambda f: (_ext(f["name"]) != ".ogg", -(_size(f) or 0)))
    return (lossy[0], "preview") if lossy else None


class InternetArchive:
    provider = "archive"

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

    def _get(self, url: str, params: dict | None = None) -> requests.Response:
        self.limiter.acquire()
        r = self.http.get(url, params=params, timeout=60)
        r.raise_for_status()
        return r

    def query(self, query: str, collection: str | None = None) -> str:
        q = f"({query}) AND mediatype:audio" if query else "mediatype:audio"
        if collection:
            q += f" AND collection:({collection})"
        if not self.allow_unknown:
            q += " AND licenseurl:*"
        return q

    def search(
        self,
        query: str,
        limit: int = 20,
        max_duration: float = 600.0,
        collection: str | None = None,
        rows: int = 50,
    ) -> Iterator[SourceItem]:
        params = {
            "q": self.query(query, collection),
            "fl[]": ["identifier", "title", "creator", "licenseurl"],
            "rows": rows,
            "output": "json",
        }
        page, yielded = 1, 0
        while yielded < limit:
            data = self._get(SEARCH, params | {"page": page}).json()["response"]
            docs = data.get("docs", [])
            for doc in docs:
                meta = self._get(METADATA.format(doc["identifier"])).json()
                for item in self.items(doc, meta, max_duration):
                    yield item
                    yielded += 1
                    if yielded >= limit:
                        return
            if not docs or page * rows >= data.get("numFound", 0):
                return
            page += 1

    def items(self, doc: dict, meta: dict, max_duration: float = 600.0) -> Iterator[SourceItem]:
        """SourceItems for one search hit and its ``/metadata`` response."""
        if not meta.get("files"):
            return
        md = meta.get("metadata", {})
        identifier = md.get("identifier") or doc["identifier"]
        groups = tracks(meta["files"])
        for key, group in groups.items():
            picked = choose(group, self.max_bytes)
            if picked is None:
                continue
            f, origin = picked
            length = next((parse_length(g.get("length")) for g in group if g.get("length")), None)
            if length and length > max_duration:
                continue
            yield self.to_item(identifier, md, doc, key, f, origin, length, len(groups) > 1)

    def to_item(self, identifier, md, doc, key, f, origin, length, multi) -> SourceItem:
        item_title = _first(md.get("title")) or doc.get("title")
        stem = PurePosixPath(key).stem
        title = f.get("title") or (f"{item_title}: {stem}" if multi and item_title else None)
        return SourceItem(
            provider="archive",
            provider_id=f"{identifier}/{key}",
            title=title or item_title or stem,
            author=f.get("creator") or _first(md.get("creator")) or _first(doc.get("creator")),
            page_url=DETAILS.format(identifier),
            licence_raw=_first(md.get("licenseurl")) or doc.get("licenseurl"),
            audio_url=DOWNLOAD.format(identifier, quote(f["name"])),
            tags=_subjects(md.get("subject")),
            description=strip_html(_first(md.get("description"))),
            duration_s=length,
            raw={
                "identifier": identifier,
                "file": f,
                "collection": md.get("collection"),
                "date": md.get("date"),
            },
            analysed_from=origin,
            lossy=_ext(f["name"]) in LOSSY_EXTS,
            allow_unknown=self.allow_unknown,
        )

    def fetch_audio(self, item: SourceItem) -> bytes:
        return download(self.http, item.audio_url, self.max_bytes)


def _open(args, settings) -> Harvest:
    ia = InternetArchive(allow_unknown=args.allow_unknown)
    return Harvest(
        ia.search(args.query, collection=args.collection, **window(args)), ia.fetch_audio
    )


SOURCE = Source(
    "archive",
    "search the Internet Archive (no key)",
    (
        QUERY,
        *limits(),
        UNLICENSED,
        arg("--collection", help="limit to a collection, e.g. netlabels, radio-aporee-maps"),
    ),
    _open,
)
