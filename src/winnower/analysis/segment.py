"""Timbral change-point segmentation (no beat tracking).

Uses ruptures' PELT with an L2 cost on a block-downsampled timbre sequence. L2 avoids
building a self-similarity matrix, so memory stays linear in file length.
"""

from __future__ import annotations

import numpy as np
import ruptures as rpt

from .features import Features, block_matrix

BLOCK_S = 0.25


def segment(
    f: Features, min_segment_s: float = 2.0, penalty: float = 3.0
) -> list[tuple[float, float]]:
    """Return (start_s, end_s) segments covering the file.

    ``penalty`` is per feature dimension; higher means fewer, longer segments.
    """
    X = block_matrix(f, BLOCK_S)
    n, d = X.shape
    duration = f.n_frames / f.fps
    min_size = max(2, int(min_segment_s / BLOCK_S))
    if n < 2 * min_size:
        return [(0.0, duration)]
    algo = rpt.Pelt(model="l2", min_size=min_size, jump=1).fit(X)
    bkps = algo.predict(pen=penalty * d * np.log(n))
    edges = [0.0] + [min(duration, b * BLOCK_S) for b in bkps]
    edges[-1] = duration
    return [(a, b) for a, b in zip(edges[:-1], edges[1:], strict=True) if b - a > 0]
