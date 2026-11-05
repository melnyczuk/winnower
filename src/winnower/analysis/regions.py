"""Propose candidate regions, score them, and pick a diverse few per file."""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

from .features import Features, compute_features
from .scoring import Scores, score_region
from .segment import segment

MIN_REGION_S = 2.0
MAX_REGION_S = 20.0
ONE_SHOT_MAX_S = 4.0
EDGE_S = 0.6  # trimmed from each side of a segment: boundaries and file edges are smeared


@dataclass
class Region:
    type: str  # texture_loop | texture | gesture | one_shot
    start_s: float
    end_s: float
    scores: Scores
    rank: float
    summary: np.ndarray  # timbre vector used for diversity

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def loop(self) -> tuple[float, float] | None:
        if self.type != "texture_loop":
            return None
        return 0.0, min(self.duration_s, self.scores.loop_end_s)


def _windows(a: float, b: float) -> list[tuple[float, float]]:
    """Split a segment into candidate windows no longer than MAX_REGION_S."""
    length = b - a
    if length < MIN_REGION_S:
        return []
    if length <= MAX_REGION_S:
        return [(a, b)]
    win = min(MAX_REGION_S, max(8.0, length / 4))
    hop = win / 2
    out, t = [], a
    while t + win <= b + 1e-6:
        out.append((t, t + win))
        t += hop
    return out


def _one_shots(f: Features) -> list[tuple[float, float]]:
    """Onset to decay below -30 dB of its peak (or ONE_SHOT_MAX_S), with quiet before."""
    onsets = librosa.onset.onset_detect(
        onset_envelope=f.flux, sr=f.sr, hop_length=f.hop, units="frames"
    )
    rms_db = librosa.amplitude_to_db(f.rms + 1e-10)
    out = []
    pre = int(0.1 * f.fps)
    for o in onsets:
        if o < pre:
            continue
        peak_i = o + int(np.argmax(rms_db[o : o + int(0.2 * f.fps) + 1]))
        peak = rms_db[peak_i]
        if peak - rms_db[o - pre : o].mean() < 18:  # needs a clear attack out of quiet
            continue
        end_lim = min(f.n_frames, peak_i + int(ONE_SHOT_MAX_S * f.fps))
        below = np.nonzero(rms_db[peak_i:end_lim] < peak - 30)[0]
        end = peak_i + (below[0] if below.size else end_lim - peak_i)
        if (end - o) / f.fps >= 0.15:
            out.append((max(0.0, (o - 2) / f.fps), end / f.fps))
    return out


def _classify(sc: Scores, one_shot: bool) -> str:
    if one_shot:
        return "one_shot"
    if sc.trend >= 0.7:
        return "gesture"
    if sc.stationarity >= 0.45 and sc.loopability >= 0.6:
        return "texture_loop"
    return "texture"


def _rank(sc: Scores, kind: str) -> float:
    r = sc.interest * sc.quality * (1.0 - 0.6 * sc.rhythmicity)
    if kind == "texture_loop":
        r *= 0.85 + 0.15 * sc.loopability
    return float(r)


def _summary(f: Features, a: float, b: float) -> np.ndarray:
    s = slice(f.frame(a), max(f.frame(a) + 1, f.frame(b)))
    v = np.concatenate([f.mfcc[1:13, s].mean(axis=1) / 10.0, [np.log2(f.centroid[s].mean() + 1)]])
    return v


def find_regions(
    y: np.ndarray,
    sr: int,
    max_regions: int = 4,
    min_interest: float = 0.35,
    features: Features | None = None,
) -> list[Region]:
    """Segment, score and select up to ``max_regions`` diverse, interesting regions."""
    f = features or compute_features(y, sr)
    candidates: list[tuple[float, float, bool]] = []
    for a, b in segment(f):
        candidates += [(x, z, False) for x, z in _windows(a + EDGE_S, b - EDGE_S)]
    candidates += [(a, b, True) for a, b in _one_shots(f)]

    regions: list[Region] = []
    for a, b, shot in candidates:
        sc = score_region(y, f, a, b)
        if sc.quality == 0.0 or sc.interest < min_interest:
            continue
        kind = _classify(sc, shot)
        end = b
        if kind == "texture_loop":
            end = a + sc.loop_end_s
        regions.append(Region(kind, a, end, sc, _rank(sc, kind), _summary(f, a, end)))
    return _select(regions, max_regions)


def _select(regions: list[Region], k: int, lam: float = 0.7) -> list[Region]:
    """Maximal marginal relevance: high rank, unlike what's already chosen, no overlaps."""
    pool = sorted(regions, key=lambda r: r.rank, reverse=True)
    chosen: list[Region] = []
    while pool and len(chosen) < k:

        def mmr(r: Region) -> float:
            if not chosen:
                return r.rank
            dist = min(float(np.linalg.norm(r.summary - c.summary)) for c in chosen)
            return lam * r.rank + (1 - lam) * min(1.0, dist / 2.0)

        best = max(pool, key=mmr)
        pool.remove(best)
        if any(best.start_s < c.end_s and c.start_s < best.end_s for c in chosen):
            continue
        chosen.append(best)
    return sorted(chosen, key=lambda r: r.start_s)
