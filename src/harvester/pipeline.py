"""Glue: source item -> licence gate -> analysis -> cut regions -> FLAC + sidecars."""

from __future__ import annotations

import hashlib
import io
import logging
from datetime import UTC, datetime

import librosa
import numpy as np
import soundfile as sf

from . import PIPELINE_VERSION
from .analysis import Region, audio_duration, compute_features, find_regions, load_audio
from .config import Settings
from .licences import LicenceInfo, attribution, decide
from .sources.base import SourceItem
from .storage import Store, sample_key, source_key

log = logging.getLogger(__name__)
FADE_S = 0.01


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _cut(y: np.ndarray, sr: int, r: Region) -> np.ndarray:
    a, b = int(r.start_s * sr), int(r.end_s * sr)
    seg = y[:, a:b].copy()
    n = min(int(FADE_S * sr), seg.shape[1] // 4)
    if n > 0 and r.type != "texture_loop":
        ramp = np.linspace(0.0, 1.0, n, dtype=np.float32)
        seg[:, :n] *= ramp
        seg[:, -n:] *= ramp[::-1]
    return seg


def _flac(seg: np.ndarray, sr: int, subtype: str) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, seg.T, sr, format="FLAC", subtype=subtype)
    return buf.getvalue()


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
    sr = settings.analysis.sample_rate
    chunk = settings.analysis.chunk_s
    total = audio_duration(audio)
    # Lossy sources gain nothing from 24-bit; keep 24-bit for lossless ones.
    subtype = "PCM_16" if lossy or analysed_from == "preview" else "PCM_24"
    out = []
    offset = 0.0
    while offset < total - 1e-3:
        dur = min(chunk, total - offset)
        if dur < 2.0 and offset > 0:
            break
        y, sr = load_audio(audio, sr, offset=offset, duration=dur)
        regions = find_regions(
            y,
            sr,
            max_regions=settings.analysis.max_regions_per_file,
            min_interest=settings.analysis.min_interest,
        )
        for r in regions:
            out.append(
                _write_region(y, sr, r, offset, subtype, store, source, licence, analysed_from)
            )
        offset += chunk
    return out


def _write_region(y, sr, r: Region, offset, subtype, store, source, licence, analysed_from) -> dict:
    seg = _cut(y, sr, r)
    data = _flac(seg, sr, subtype)
    sid = hashlib.sha256(data).hexdigest()[:24]
    store.write_bytes(sample_key(sid, "flac"), data)
    loop = r.loop
    sidecar = {
        "id": sid,
        "file": sample_key(sid, "flac"),
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
            "loop_start_s": None if loop is None else round(loop[0], 3),
            "loop_end_s": None if loop is None else round(loop[1], 3),
            "crossfade_ms": 120 if loop else None,
        },
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
    store.write_json(sample_key(sid, "json"), sidecar)
    return sidecar


LOCAL_UNKNOWN = LicenceInfo(
    "local-unknown", None, "flag", "your own file, rights unknown: clear before release"
)


def harvest(
    items, fetch, store: Store, settings: Settings, analysed_from: str = "preview", lossy=None
) -> dict:
    """Run a stream of SourceItems through the pipeline. Returns counts.

    ``fetch(item)`` returns bytes or a local path. ``lossy(item)`` says whether the
    source is lossy (default: previews are, originals aren't).
    """
    counts = {
        "seen": 0,
        "denied": 0,
        "skipped_existing": 0,
        "no_regions": 0,
        "samples": 0,
        "errors": 0,
    }
    for item in items:
        counts["seen"] += 1
        meta_key = source_key(item.provider, item.provider_id)
        if store.exists(meta_key):
            counts["skipped_existing"] += 1
            continue
        lic = decide(item.licence_raw, settings.licences)
        if item.provider == "local" and item.licence_raw is None:
            lic = LOCAL_UNKNOWN
        source = _source_record(item, lic)
        if lic.decision == "deny":
            counts["denied"] += 1
            store.write_json(meta_key, source | {"status": "denied", "reason": lic.reason})
            continue
        try:
            samples = process_audio(
                fetch(item),
                store,
                settings,
                source,
                lic,
                analysed_from,
                lossy=lossy(item) if lossy else analysed_from == "preview",
            )
        except Exception as e:  # one bad file must not stop a harvest
            log.warning("failed %s/%s: %s", item.provider, item.provider_id, e)
            counts["errors"] += 1
            continue
        store.write_json(
            meta_key,
            source | {"status": "done", "samples": [s["id"] for s in samples], "raw": item.raw},
        )
        counts["samples"] += len(samples)
        counts["no_regions"] += 0 if samples else 1
    return counts


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
