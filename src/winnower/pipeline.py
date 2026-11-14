"""Glue: source item -> licence gate -> analysis -> cut regions -> FLAC + sidecars."""

from __future__ import annotations

import hashlib
import io
import itertools
import logging
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import reduce
from typing import Literal

import librosa
import numpy as np
import soundfile as sf
from mutagen.flac import FLAC

from . import PIPELINE_VERSION
from .analysis import (
    GridSpec,
    Key,
    Region,
    audio_duration,
    compute_features,
    estimate_key,
    find_regions,
    load_audio,
)
from .config import AnalysisSettings, Settings
from .licences import LicenceInfo, attribution, decide
from .naming import sample_path
from .sources.base import SourceItem
from .storage import Store, source_key

log = logging.getLogger(__name__)
FADE_S = 0.01
XFADE_S = 0.08  # longest baked loop crossfade; shorter at fast tempos (a 32nd note)


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _crossfade(head: np.ndarray, tail: np.ndarray) -> np.ndarray:
    """Fade ``tail`` out over ``head`` fading in. Linear when the two are alike (no dip),
    equal-power when they aren't (no hole in the level)."""
    t = (np.arange(head.shape[1], dtype=np.float32) + 0.5) / head.shape[1]
    a, b = head.mean(axis=0), tail.mean(axis=0)
    r = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else 0.0
    fade_in, fade_out = (t, 1 - t) if r > 0.5 else (np.sin(t * np.pi / 2), np.cos(t * np.pi / 2))
    return head * fade_in + tail * fade_out


def _cut(y: np.ndarray, sr: int, r: Region) -> tuple[np.ndarray, int]:
    """The region as exactly ``beats`` beats of audio, and its baked crossfade in samples.

    A loop is baked so the file itself wraps seamlessly: the audio just after the loop end
    is crossfaded into the loop's first few ms (or, near the end of the chunk, the audio
    just before the start into its last few ms). Other regions get short edge fades.
    """
    a = int(round(r.start_s * sr))
    n = min(int(round(r.grid.length_s * sr)), y.shape[1] - a)
    seg = y[:, a : a + n].copy()
    if r.type == "loop":
        x = min(int(XFADE_S * sr), int(7.5 / r.grid.bpm * sr), n // 4)
        if a + n + x <= y.shape[1]:
            seg[:, :x] = _crossfade(seg[:, :x], y[:, a + n : a + n + x])
            return seg, x
        if a >= x:
            seg[:, n - x :] = _crossfade(y[:, a - x : a], seg[:, n - x :])
            return seg, x
    m = min(int(FADE_S * sr), seg.shape[1] // 4)
    if m > 0:
        ramp = np.linspace(0.0, 1.0, m, dtype=np.float32)
        seg[:, :m] *= ramp
        seg[:, -m:] *= ramp[::-1]
    return seg, 0


def _flac(seg: np.ndarray, sr: int, subtype: str) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, seg.T, sr, format="FLAC", subtype=subtype)
    return buf.getvalue()


def _tagged(data: bytes, sidecar: dict) -> bytes:
    """Vorbis comments DAWs and players read: tempo, key, title, and the attribution, so a
    CC BY credit travels with the file."""
    buf = io.BytesIO(data)
    tags = FLAC(buf)
    src, region, key = sidecar["source"], sidecar["region"], sidecar["key"]
    tags["BPM"] = str(region["bpm"])
    if key:
        tags["INITIALKEY"] = key["short"]
    tags["TITLE"] = f"{src.get('title') or 'untitled'} ({region['type']})"
    if src.get("author"):
        tags["ARTIST"] = src["author"]
    tags["COPYRIGHT"] = src.get("attribution") or ""
    tags["COMMENT"] = f"winnower {sidecar['id']}; source {src.get('page_url') or ''}"
    buf.seek(0)
    tags.save(buf)
    return buf.getvalue()


def _key(k: Key | None) -> dict | None:
    if k is None:
        return None
    return {
        "tonic": k.tonic,
        "mode": k.mode,
        "name": k.name,
        "short": k.short,
        "camelot": k.camelot,
        "confidence": k.confidence,
    }


def grid_spec(a: AnalysisSettings) -> GridSpec:
    return GridSpec(a.min_duration_s, a.target_duration_s, a.max_duration_s, a.bpm_min, a.bpm_max)


def _descriptors(y: np.ndarray, sr: int) -> dict:
    """Summary statistics (mean, std, slope per second) of frame descriptors for one region."""
    f = compute_features(y, sr)
    t = np.arange(f.n_frames) / f.fps

    def stats(x: np.ndarray, digits: int = 4) -> dict:
        slope = float(np.polyfit(t, x, 1)[0]) if x.size > 2 and x.std() > 0 else 0.0
        return {
            "mean": round(float(x.mean()), digits),
            "std": round(float(x.std()), digits),
            "slope": round(slope, digits),
        }

    mono = y.mean(axis=0)
    peak = float(np.abs(y).max()) + 1e-12
    return {
        "rms_db": stats(librosa.amplitude_to_db(f.rms + 1e-10), 2),
        "centroid_hz": stats(f.centroid, 1),
        "bandwidth_hz": stats(f.bandwidth, 1),
        "rolloff95_hz": stats(f.rolloff, 1),
        "flatness": stats(f.flatness),
        "flux": stats(f.flux),
        "contrast_db": [round(float(v), 2) for v in f.contrast.mean(axis=1)],
        "mfcc": [round(float(v), 3) for v in f.mfcc.mean(axis=1)],
        "zcr": round(float(librosa.feature.zero_crossing_rate(mono).mean()), 4),
        "peak_dbfs": round(20 * np.log10(peak), 2),
    }


def process_audio(
    audio: bytes | str,
    store: Store,
    settings: Settings,
    source: dict,
    licence: LicenceInfo | None,
    analysed_from: str,
    lossy: bool = False,
) -> list[dict]:
    """Analyse one file and write a FLAC + sidecar for each selected region.

    Files longer than ``settings.analysis.chunk_s`` are read and analysed chunk by chunk
    (each chunk can yield up to ``max_regions_per_file``), so a 70-minute album side or DJ
    mix needs no more memory than a 10-minute track.
    """
    total = audio_duration(audio)
    # Lossy sources gain nothing from 24-bit; keep 24-bit for lossless ones.
    subtype = "PCM_16" if lossy or analysed_from == "preview" else "PCM_24"
    a = settings.analysis
    loaded = (  # lazy: one chunk in memory at a time
        (offset, *load_audio(audio, a.sample_rate, offset=offset, duration=dur))
        for offset, dur in chunks(total, a.chunk_s)
    )
    return [
        _write_region(store, _prepare(y, sr, r, offset, subtype, source, licence, analysed_from))
        for offset, y, sr in loaded
        for r in find_regions(
            y,
            sr,
            max_regions=a.max_regions_per_file,
            min_interest=a.min_interest,
            spec=grid_spec(a),
        )
    ]


def chunks(total: float, size: float) -> Iterator[tuple[float, float]]:
    """``(offset, duration)`` windows covering ``total`` seconds in steps of ``size``. A
    trailing sliver under 2 s is dropped (too short to analyse)."""
    offsets = itertools.takewhile(lambda o: o < total - 1e-3, itertools.count(0.0, size))
    windows = ((o, min(size, total - o)) for o in offsets)
    return (w for w in windows if w[0] == 0 or w[1] >= 2.0)


@dataclass(frozen=True)
class _Region:
    """A cut region ready to write: its FLAC bytes and sidecar."""

    flac: bytes
    sidecar: dict


def _prepare(y, sr, r: Region, offset, subtype, source, licence, analysed_from) -> _Region:
    seg, xfade = _cut(y, sr, r)
    data = _flac(seg, sr, subtype)
    sid = hashlib.sha256(data).hexdigest()[:24]
    loop, g = r.loop, r.grid
    sidecar = {
        "id": sid,
        "file": None,  # set below, once the sidecar can name it
        "source": source,
        "licence": None
        if licence is None
        else {
            "id": licence.id,
            "url": licence.url,
            "decision": licence.decision,
            "reason": licence.reason,
        },
        "region": {
            "type": r.type,
            "start_s": round(offset + r.start_s, 3),
            "end_s": round(offset + r.end_s, 3),
            "bpm": g.bpm,
            "beats": g.beats,
            "bars": g.bars,
            "tempo_source": g.source,
            "tempo_confidence": g.confidence,
            "seam": round(g.seam, 4),
            "loop_start_s": None if loop is None else round(loop[0], 3),
            "loop_end_s": None if loop is None else round(seg.shape[1] / sr, 3),
            "crossfade_ms": round(1000 * xfade / sr, 1) if loop else None,
        },
        "key": _key(estimate_key(seg, sr)),
        "audio": {
            "sr": sr,
            "channels": int(seg.shape[0]),
            "subtype": subtype,
            "duration_s": round(seg.shape[1] / sr, 3),
            "analysed_from": analysed_from,
        },
        "descriptors": _descriptors(seg, sr),
        "scores": r.scores.to_dict() | {"rank": round(r.rank, 4)},
        "tags": list(source.get("tags", [])),
        "embeddings": {},
        "caption": None,
        "pipeline": {"version": PIPELINE_VERSION, "created_at": _now()},
    }
    sidecar["file"] = sample_path(sidecar, "flac")
    return _Region(_tagged(data, sidecar), sidecar)


def _write_region(store: Store, region: _Region) -> dict:
    store.write_bytes(region.sidecar["file"], region.flac)
    store.write_json(sample_path(region.sidecar, "json"), region.sidecar)
    return region.sidecar


LOCAL_UNKNOWN = LicenceInfo(
    "local-unknown", None, "flag", "your own file, rights unknown: clear before release"
)


def _unknown(item: SourceItem, url: str | None) -> LicenceInfo:
    if item.provider == "local":
        return LOCAL_UNKNOWN
    return LicenceInfo(
        "web-unknown",
        url,
        "flag",
        "no licence found: rights reserved by default, clear before release",
    )


def licence_for(item: SourceItem, settings: Settings) -> LicenceInfo:
    """The gate's decision for one item. Items without a recognisable licence are denied
    unless ``item.allow_unknown`` is set, in which case they're kept and flagged
    ``web-unknown`` (``local-unknown`` for local files). Recognised denials (ND and so on)
    are never overridden."""
    lic = decide(item.licence_raw, settings.licences)
    return _unknown(item, lic.url) if lic.id is None and item.allow_unknown else lic


def lossiness(item: SourceItem, origin: str, lossy: Callable | None = None) -> bool:
    """An explicit ``lossy(item)``, else the item's own ``lossy``, else previews are."""
    if lossy:
        return lossy(item)
    return item.lossy if item.lossy is not None else origin == "preview"


Status = Literal["skipped_existing", "denied", "error", "no_regions", "done"]


@dataclass(frozen=True)
class Outcome:
    status: Status
    samples: int = 0


def harvest_one(
    item: SourceItem,
    fetch,
    store: Store,
    settings: Settings,
    analysed_from: str | None = None,
    lossy: Callable | None = None,
) -> Outcome:
    """Gate, analyse and store one item, recording it under ``sources/``."""
    meta_key = source_key(item.provider, item.provider_id)
    if store.exists(meta_key):
        return Outcome("skipped_existing")
    lic = licence_for(item, settings)
    source = _source_record(item, lic)
    if lic.decision == "deny":
        store.write_json(meta_key, source | {"status": "denied", "reason": lic.reason})
        return Outcome("denied")
    origin = analysed_from or item.analysed_from
    try:
        samples = process_audio(
            fetch(item), store, settings, source, lic, origin, lossiness(item, origin, lossy)
        )
    except Exception as e:  # one bad file must not stop a harvest
        log.warning("failed %s/%s: %s", item.provider, item.provider_id, e)
        return Outcome("error")
    store.write_json(
        meta_key,
        source | {"status": "done", "samples": [s["id"] for s in samples], "raw": item.raw},
    )
    return Outcome("done" if samples else "no_regions", len(samples))


COUNTS = ("seen", "denied", "skipped_existing", "no_regions", "samples", "errors")
_COUNTER: dict[Status, str] = {
    "skipped_existing": "skipped_existing",
    "denied": "denied",
    "error": "errors",
    "no_regions": "no_regions",
}


def _increments(o: Outcome) -> dict[str, int]:
    counter = _COUNTER.get(o.status)
    return {"seen": 1, "samples": o.samples} | ({counter: 1} if counter else {})


def _add(counts: dict[str, int], inc: dict[str, int]) -> dict[str, int]:
    return {k: v + inc.get(k, 0) for k, v in counts.items()}


def tally(outcomes: Iterable[Outcome]) -> dict[str, int]:
    """Fold outcomes into ``seen``/``denied``/``skipped_existing``/... counts."""
    return reduce(_add, map(_increments, outcomes), dict.fromkeys(COUNTS, 0))


def harvest(
    items: Iterable[SourceItem],
    fetch,
    store: Store,
    settings: Settings,
    analysed_from: str | None = None,
    lossy: Callable | None = None,
) -> dict[str, int]:
    """Run a stream of SourceItems through the pipeline. Returns counts.

    ``fetch(item)`` returns bytes or a local path. ``analysed_from`` overrides each item's
    own value; ``lossy(item)`` overrides each item's ``lossy`` (see :func:`lossiness`).
    Items are processed one at a time as the stream yields them.
    """
    return tally(harvest_one(it, fetch, store, settings, analysed_from, lossy) for it in items)


def _source_record(item: SourceItem, lic: LicenceInfo) -> dict:
    return {
        "provider": item.provider,
        "provider_id": item.provider_id,
        "title": item.title,
        "author": item.author,
        "page_url": item.page_url,
        "audio_url": item.audio_url,
        "tags": item.tags,
        "attribution": attribution(item.title, item.author, item.page_url, lic.id),
        **(
            {"path": item.raw.get("path"), "file_tags": item.raw.get("file_tags")}
            if item.provider == "local"
            else {}
        ),
        "retrieved_at": _now(),
    }
