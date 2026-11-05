"""One thin storage layer so a local folder and an S3 bucket are interchangeable.

The storage root is a single setting: ``./data`` or ``file:///data/samples`` during
testing, ``s3://bucket/samples`` later (needs the ``s3`` extra). Every read and write in
the project goes through :class:`Store`, so switching is a copy plus a config change.

Layout (relative to the root)::

    sources/{provider}/{provider_id}/meta.json   API response + licence snapshot
    samples/{id[:2]}/{id}.flac                   the cut region, lossless
    samples/{id[:2]}/{id}.json                   sidecar: the source of truth
    index/samples.jsonl                          derived, rebuildable
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import fsspec


class Store:
    def __init__(self, root: str):
        self.fs, path = fsspec.core.url_to_fs(root)
        self.root = path.rstrip("/")
        if self.is_local:
            self.fs.makedirs(self.root, exist_ok=True)

    def _p(self, key: str) -> str:
        return f"{self.root}/{key.lstrip('/')}"

    def exists(self, key: str) -> bool:
        return self.fs.exists(self._p(key))

    def write_bytes(self, key: str, data: bytes) -> None:
        path = self._p(key)
        parent = path.rsplit("/", 1)[0]
        self.fs.makedirs(parent, exist_ok=True)
        with self.fs.open(path, "wb") as f:
            f.write(data)

    def read_bytes(self, key: str) -> bytes:
        with self.fs.open(self._p(key), "rb") as f:
            return f.read()

    def write_json(self, key: str, obj: Any) -> None:
        self.write_bytes(key, json.dumps(obj, indent=2, ensure_ascii=False).encode())

    def read_json(self, key: str) -> Any:
        return json.loads(self.read_bytes(key))

    def glob(self, pattern: str) -> Iterator[str]:
        prefix = self.root + "/"
        for path in sorted(self.fs.glob(self._p(pattern))):
            yield path[len(prefix) :] if path.startswith(prefix) else path

    @property
    def is_local(self) -> bool:
        protocols = self.fs.protocol if isinstance(self.fs.protocol, tuple) else (self.fs.protocol,)
        return "file" in protocols

    def uri(self, key: str) -> str:
        """A location other libraries understand: a plain path locally, a URI otherwise."""
        return self._p(key) if self.is_local else self.fs.unstrip_protocol(self._p(key))


def sample_key(sample_id: str, ext: str) -> str:
    return f"samples/{sample_id[:2]}/{sample_id}.{ext}"


def source_key(provider: str, provider_id: str, name: str = "meta.json") -> str:
    return f"sources/{provider}/{provider_id}/{name}"
