"""Derived index, rebuilt from sidecars (the sidecars are the source of truth).

v0 writes ``index/samples.jsonl`` (one flat row per sample) which needs nothing extra.
With the ``index`` extra installed it also writes a LanceDB table next to it, ready for
CLAP vectors when embeddings land.
"""

from __future__ import annotations

import json

from .storage import Store

INDEX_KEY = "index/samples.jsonl"


def flatten(sc: dict) -> dict:
    lic = sc.get("licence") or {}
    src = sc.get("source") or {}
    reg = sc.get("region") or {}
    scores = sc.get("scores") or {}
    desc = sc.get("descriptors") or {}
    return {
        "id": sc["id"],
        "file": sc["file"],
        "provider": src.get("provider"),
        "provider_id": src.get("provider_id"),
        "title": src.get("title"),
        "author": src.get("author"),
        "page_url": src.get("page_url"),
        "attribution": src.get("attribution"),
        "licence": lic.get("id"),
        "licence_decision": lic.get("decision", "local"),
        "type": reg.get("type"),
        "duration_s": (sc.get("audio") or {}).get("duration_s"),
        "interest": scores.get("interest"),
        "stationarity": scores.get("stationarity"),
        "loopability": scores.get("loopability"),
        "rhythmicity": scores.get("rhythmicity"),
        "rank": scores.get("rank"),
        "centroid_hz": (desc.get("centroid_hz") or {}).get("mean"),
        "flatness": (desc.get("flatness") or {}).get("mean"),
        "tags": sc.get("tags") or [],
    }


def rebuild(store: Store) -> int:
    rows = [flatten(store.read_json(k)) for k in store.glob("samples/*/*.json")]
    store.write_bytes(INDEX_KEY, "".join(json.dumps(r) + "\n" for r in rows).encode())
    try:
        import lancedb  # type: ignore
    except ImportError:
        return len(rows)
    if rows:
        db = lancedb.connect(store.uri("index/lance"))
        db.create_table("samples", data=rows, mode="overwrite")
    return len(rows)


def load(store: Store) -> list[dict]:
    if not store.exists(INDEX_KEY):
        return []
    return [json.loads(line) for line in store.read_bytes(INDEX_KEY).decode().splitlines() if line]
