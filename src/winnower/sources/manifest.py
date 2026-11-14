"""A CSV or JSONL manifest of audio files: how datasets get in.

FSD50K, Clotho, the FMA dump and similar datasets ship as folders of audio plus a
metadata table. Write (or convert to) a small manifest with these columns, one row per
file, and harvest it:

    url | path   where the audio is: an http(s) URL, or a file path (relative paths are
                 relative to the manifest)
    licence      licence URL or name (``license`` works too)
    title, author, page_url, description
    tags         comma-separated in CSV, a list in JSONL
    duration     seconds, optional; used for ``--max-duration``
    id           stable id; default: a hash of the url or path

The provider name is the manifest's file name (``fsd50k.csv`` → ``fsd50k``) unless
given. Rows without a recognised licence are denied unless ``allow_unknown`` is set.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import re
from collections.abc import Iterator
from pathlib import Path

import requests

from .base import RateLimiter, SourceItem, download, http_session
from .local import LOSSY_EXTS

log = logging.getLogger(__name__)


def provider_name(path: Path) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", path.stem.lower()).strip("-") or "manifest"


def _rows(path: Path) -> Iterator[dict]:
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        with path.open() as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
    else:
        with path.open(newline="") as f:
            yield from csv.DictReader(f)


def _tags(v) -> list[str]:
    if isinstance(v, list):
        return [str(t).strip() for t in v if str(t).strip()]
    return [t.strip() for t in re.split(r"[,;]", v or "") if t.strip()]


def _float(v) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


class Manifest:
    def __init__(
        self,
        path: str | Path,
        provider: str | None = None,
        session: requests.Session | None = None,
        limiter: RateLimiter | None = None,
        allow_unknown: bool = False,
    ):
        self.path = Path(path).expanduser()
        self.provider = provider or provider_name(self.path)
        self.http = session or http_session()
        self.limiter = limiter or RateLimiter(per_minute=60)
        self.allow_unknown = allow_unknown

    def search(self, limit: int | None = None, max_duration: float = 600.0):
        yielded = 0
        for n, row in enumerate(_rows(self.path), 1):
            item = self.to_item(row)
            if item is None:
                log.warning("%s row %d: no url or path, skipped", self.path.name, n)
                continue
            if item.duration_s and item.duration_s > max_duration:
                continue
            yield item
            yielded += 1
            if limit is not None and yielded >= limit:
                return

    def to_item(self, row: dict) -> SourceItem | None:
        row = {k.strip().lower(): v for k, v in row.items() if k}
        url, path = (row.get("url") or "").strip(), (row.get("path") or "").strip()
        if not url and not path:
            return None
        raw = {"row": row}
        if path:
            p = Path(path).expanduser()
            location = str((p if p.is_absolute() else self.path.parent / p).resolve())
            raw["path"] = location
        else:
            location = url
        pid = str(row.get("id") or "").strip()
        pid = pid or hashlib.sha1((url or path).encode()).hexdigest()[:16]
        return SourceItem(
            provider=self.provider,
            provider_id=pid,
            title=row.get("title") or Path(url or path).stem or None,
            author=row.get("author") or None,
            page_url=row.get("page_url") or None,
            licence_raw=row.get("licence") or row.get("license") or None,
            audio_url=location,
            tags=_tags(row.get("tags")),
            description=row.get("description") or None,
            duration_s=_float(row.get("duration")),
            raw=raw,
            analysed_from="original",
            lossy=Path(location.split("?")[0]).suffix.lower() in LOSSY_EXTS,
            allow_unknown=self.allow_unknown,
        )

    def fetch_audio(self, item: SourceItem) -> bytes | str:
        if item.raw.get("path"):
            return item.raw["path"]  # local file: read in place, like `harvest local`
        self.limiter.acquire()
        return download(self.http, item.audio_url)


def iter_manifest(path: str | Path, **kw) -> Iterator[SourceItem]:
    return Manifest(path, **kw).search()
