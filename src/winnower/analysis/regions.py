"""Propose candidate regions, fit each to a beat grid, score them, and pick a diverse few
per file."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np

from .features import Features, compute_features
from .groove import Grid, GridSpec, Pulse, grid_for, length_preference, pulse, snap_to_onset
from .scoring import Scores, rhythmicity, score_region, trend
from .segment import segment

EDGE_S = 0.6  # trimmed from each side of a segment: boundaries and file edges are smeared
WINDOW_OVER_TARGET = 1.3  # candidate windows leave the grid room to choose start and length
GESTURE_TREND = 0.7


@dataclass
class Region:
    type: str  # loop | gesture
    start_s: float
    end_s: float
    scores: Scores
    rank: float
    summary: np.ndarray  # timbre vector used for diversity
    grid: Grid

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def loop(self) -> tuple[float, float] | None:
        """Loops are baked so the whole file loops."""
        return (0.0, self.duration_s) if self.type == "loop" else None


def _windows(a: float, b: float, spec: GridSpec) -> list[tuple[float, float]]:
    """Split a segment into overlapping candidate windows a little longer than the target."""
    length = b - a
    if length < spec.min_s:
        return []
    if length <= spec.max_s:
        return [(a, b)]
    win = min(spec.max_s, spec.target_s * WINDOW_OVER_TARGET)
    hop = win / 2
    out, t = [], a
    while t + win <= b + 1e-6:
        out.append((t, t + win))
        t += hop
    return out


def _classify(sc: Scores) -> str:
    """A gesture moves one way (a swell, a rising sweep) and isn't looped; everything else
    is a bar-length loop. Its ``seam`` says how invisible the wrap is."""
    return "gesture" if sc.trend >= GESTURE_TREND else "loop"


def _rank(sc: Scores, kind: str, grid: Grid, spec: GridSpec) -> float:
    r = sc.interest * sc.quality * (0.85 + 0.15 * length_preference(grid.length_s, spec.target_s))
    if kind == "loop":
        r *= 0.85 + 0.15 * grid.seam
    return float(r)


def _summary(f: Features, a: float, b: float) -> np.ndarray:
    s = slice(f.frame(a), max(f.frame(a) + 1, f.frame(b)))
    v = np.concatenate([f.mfcc[1:13, s].mean(axis=1) / 10.0, [np.log2(f.centroid[s].mean() + 1)]])
    return v


def _grid(
    y: np.ndarray, f: Features, a: float, b: float, spec: GridSpec, beat: Pulse
) -> Grid | None:
    """Fit a window to a beat grid. Gestures are fitted for length alone (they don't loop);
    detected downbeats are moved onto the attack, sample-accurately."""
    s = slice(f.frame(a), max(f.frame(a) + 1, f.frame(b)))
    loop = trend(f, s) < GESTURE_TREND
    grid = grid_for(f, a, b, rhythmicity(f, s), spec, loop, beat)
    if grid is not None and grid.source == "detected":
        grid = replace(grid, start_s=snap_to_onset(y, f.sr, grid.start_s))
    return grid


def find_regions(
    y: np.ndarray,
    sr: int,
    max_regions: int = 4,
    min_interest: float = 0.35,
    features: Features | None = None,
    spec: GridSpec | None = None,
) -> list[Region]:
    """Segment, fit to beat grids, score and select up to ``max_regions`` diverse,
    interesting regions."""
    f = features or compute_features(y, sr)
    spec = spec or GridSpec()
    windows = [w for a, b in segment(f) for w in _windows(a + EDGE_S, b - EDGE_S, spec)]
    beat = pulse(f, spec)

    regions: list[Region] = []
    for a, b in windows:
        grid = _grid(y, f, a, b, spec, beat)
        if grid is None or grid.length_s < spec.min_s:
            continue
        sc = replace(score_region(y, f, grid.start_s, grid.end_s), loopability=grid.seam)
        if sc.quality == 0.0 or sc.interest < min_interest:
            continue
        kind = _classify(sc)
        summary = _summary(f, grid.start_s, grid.end_s)
        rank = _rank(sc, kind, grid, spec)
        regions.append(Region(kind, grid.start_s, grid.end_s, sc, rank, summary, grid))
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
