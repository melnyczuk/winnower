"""Scores for a candidate region: quality, stationarity, loopability, rhythmicity and
aural interest.

"Stationary" here means stable *statistics* over ~1 s blocks; "interest" demands
variation at the fine, frame level. Rain or crackle passes both; mains hum, room tone and
plain noise are stationary but dull and score low on interest.

All scores are 0..1. The weights in :func:`interest` are hand-set starting points; the
plan is to replace them with per-taste-profile models learned from your ratings.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import librosa
import numpy as np

from .features import Features


@dataclass
class InterestParts:
    motion: float
    modulation: float
    richness: float
    micro_events: float
    diversity: float
    stereo: float | None


@dataclass
class Scores:
    quality: float
    stationarity: float
    loopability: float
    loop_end_s: float  # best loop end, relative to region start
    rhythmicity: float
    trend: float
    interest: float
    interest_parts: InterestParts

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("loop_end_s")
        return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()} | {
            "interest_parts": {
                k: (None if v is None else round(v, 4))
                for k, v in asdict(self.interest_parts).items()
            }
        }


def _ramp(x: float, lo: float, hi: float) -> float:
    """0 below lo, 1 above hi, linear between."""
    return float(np.clip((x - lo) / (hi - lo), 0.0, 1.0))


def _bump(x: float, lo: float, peak_lo: float, peak_hi: float, hi: float) -> float:
    """Inverted U: 0 outside (lo, hi), 1 on [peak_lo, peak_hi]."""
    if x <= peak_lo:
        return _ramp(x, lo, peak_lo)
    if x <= peak_hi:
        return 1.0
    return 1.0 - _ramp(x, peak_hi, hi)


def _blocks(a: np.ndarray, size: int) -> np.ndarray:
    """Split the last axis into whole blocks: (..., n_blocks, size)."""
    n = a.shape[-1] // size
    return a[..., : n * size].reshape(*a.shape[:-1], n, size)


def quality(y: np.ndarray, f: Features, s: slice) -> float:
    rms_db = librosa.amplitude_to_db(f.rms[s] + 1e-10)
    if rms_db.max() < -50:
        return 0.0
    clipped = float(np.mean(np.abs(y) >= 0.999))
    q = 1.0 - _ramp(clipped, 0.0005, 0.01)
    # A brick-wall cliff in the upper spectrum suggests a low-bitrate lossy source.
    # Natural dark sounds roll off gradually and are not penalised.
    q *= 1.0 - 0.3 * lossy_cliff(f, s)
    return float(q)


def lossy_cliff(f: Features, s: slice) -> float:
    """0..1 likelihood of an encoder low-pass: a sudden >25 dB drop across 3 upper mel bands."""
    spec = np.mean(f.logmel[:, s], axis=1)
    half = spec.shape[0] // 2
    upper = spec[half:]
    if upper.size < 4 or spec.max() - spec.min() < 20:
        return 0.0
    drops = upper[:-3] - upper[3:]
    return _ramp(float(drops.max()), 25.0, 40.0)


def stationarity(f: Features, s: slice) -> float:
    """1 when ~2 s block means of timbre barely change across the region."""
    size = max(1, int(2 * f.fps))
    X = np.vstack(
        [
            f.mfcc[1:13, s] / 10.0,
            np.log2(f.centroid[s] + 1)[None],
            f.flatness[s][None] * 5.0,
            librosa.amplitude_to_db(f.rms[s] + 1e-10)[None] / 6.0,
        ]
    )
    B = _blocks(X, size).mean(axis=-1)  # (dims, n_blocks)
    if B.shape[1] < 2:
        return 0.0
    spread = float(np.mean(B.std(axis=1)))
    return float(np.exp(-spread / 0.4))


def loop_point(f: Features, s: slice, min_loop_s: float = 2.0) -> tuple[float, float]:
    """Find the end point whose spectrum best matches the start. Returns (score, end_s)."""
    M = f.logmel[:, s]
    T = M.shape[1]
    w = max(2, int(0.3 * f.fps))
    lo = int(min_loop_s * f.fps)
    if T < lo + w:
        return 0.0, T / f.fps
    head = M[:, :w].mean(axis=1)
    best, best_t = -1.0, T
    start = max(lo, int(0.6 * T))
    for t in range(start, T - w + 1, max(1, w // 4)):
        tail = M[:, t : t + w].mean(axis=1)
        d = float(np.mean(np.abs(head - tail)))  # mean dB difference
        sim = float(np.exp(-d / 3.0))
        if sim > best:
            best, best_t = sim, t + w // 2
    return best, best_t / f.fps


def rhythmicity(f: Features, s: slice) -> float:
    """Strength of periodic pulses between 30 and 240 BPM (0 = arrhythmic)."""
    env = f.flux[s] - f.flux[s].mean()
    if env.size < int(4 * f.fps) or np.allclose(env, 0):
        return 0.0
    ac = librosa.autocorrelate(env)
    ac = ac / (ac[0] + 1e-12)
    lo, hi = int(0.25 * f.fps), min(int(2.0 * f.fps), ac.size - 1)
    if hi <= lo:
        return 0.0
    # Scale by how pronounced the onset envelope is, so faint estimator noise in a static
    # tone can't look like a pulse.
    strength = _ramp(float(f.flux[s].std()), 0.1, 0.6)
    return float(np.clip(ac[lo:hi].max(), 0.0, 1.0)) * strength


def trend(f: Features, s: slice) -> float:
    """How strongly loudness or brightness moves one way across the region (a gesture)."""
    t = np.arange(f.rms[s].size)
    if t.size < 8:
        return 0.0
    out = 0.0
    for series in (librosa.amplitude_to_db(f.rms[s] + 1e-10), np.log2(f.centroid[s] + 1)):
        if series.std() < 1e-6:
            continue
        out = max(out, abs(float(np.corrcoef(t, series)[0, 1])))
    return out


def interest(f: Features, s: slice) -> tuple[float, InterestParts]:
    size = max(2, int(0.5 * f.fps))

    # Motion: frame-level variation *within* half-second blocks (loudness and brightness).
    # Inverted U so that chaotic bursts don't win.
    rms_db = librosa.amplitude_to_db(f.rms[s] + 1e-10)
    cen = np.log2(f.centroid[s] + 1)
    within = float(np.median(_blocks(rms_db, size).std(axis=-1))) / 6.0 + float(
        np.median(_blocks(cen, size).std(axis=-1))
    )
    motion = _bump(within, 0.08, 0.35, 2.0, 4.0)

    # Modulation: 0.5-20 Hz fluctuation of mel-band envelopes (beating, flutter, grain).
    # Median over 2 s blocks, so one transient at a boundary can't fake it.
    mod_size = int(2 * f.fps)
    Mb = _blocks(f.logmel[:, s], mod_size)  # (bands, n_blocks, mod_size)
    if Mb.shape[1] >= 1:
        Mb = Mb - Mb.mean(axis=2, keepdims=True)
        spec = np.abs(np.fft.rfft(Mb, axis=2)) ** 2
        freqs = np.fft.rfftfreq(mod_size, d=1.0 / f.fps)
        band = (freqs >= 0.5) & (freqs <= 20.0)
        per_block = np.sqrt(spec[:, :, band].sum(axis=2) / mod_size**2 * 2).mean(axis=0)
        mod_db = float(np.median(per_block))
    else:
        mod_db = 0.0
    modulation = _ramp(mod_db, 0.6, 3.0)

    # Richness: penalise a near-empty spectrum (sine-like) and featureless noise.
    S = np.mean(f.logmel[:, s], axis=1)
    smooth = np.convolve(S, np.ones(7) / 7, mode="same")
    peaks = int(np.sum((S[1:-1] > S[:-2]) & (S[1:-1] > S[2:]) & (S[1:-1] - smooth[1:-1] > 3.0)))
    alive = float(np.mean(S > S.max() - 40))
    flat = float(np.mean(f.flatness[s]))
    richness = 0.5 * _ramp(peaks, 1, 6) + 0.5 * _ramp(alive, 0.15, 0.5)
    richness *= 1.0 - 0.7 * _ramp(flat, 0.3, 0.6)

    # Micro-events: small onset peaks per second (crackle, drips, grains).
    flux = f.flux[s]
    med = np.median(flux)
    mad = np.median(np.abs(flux - med)) + 1e-9
    thresh = med + max(6 * mad, 1.0)  # at least a 1 dB mean rise
    ev = int(np.sum((flux[1:-1] > thresh) & (flux[1:-1] >= flux[:-2]) & (flux[1:-1] >= flux[2:])))
    rate = ev / max(1e-6, flux.size / f.fps)
    micro_events = _ramp(rate, 0.3, 4.0)

    # Diversity: low when nearly every pair of (lightly smoothed) frames sounds the same.
    # Euclidean in scaled MFCC units, so tiny random wobble in plain noise doesn't count.
    k = max(1, int(0.1 * f.fps))
    C = f.mfcc[1:13, s] / 10.0
    if C.shape[1] >= k:
        C = np.apply_along_axis(lambda r: np.convolve(r, np.ones(k) / k, mode="valid"), 1, C)
    if C.shape[1] > 200:
        C = C[:, np.linspace(0, C.shape[1] - 1, 200).astype(int)]
    sq = (C**2).sum(axis=0)
    d2 = np.maximum(sq[:, None] + sq[None, :] - 2 * C.T @ C, 0.0)
    n = d2.shape[0]
    off = np.sqrt(d2[~np.eye(n, dtype=bool)]) if n > 1 else np.zeros(1)
    diversity = _ramp(float(np.median(off)), 0.3, 1.5)  # median: robust to a stray transient

    # Stereo movement: how much the L/R correlation varies over time.
    stereo = None
    if f.stereo_corr is not None:
        stereo = _ramp(float(np.std(f.stereo_corr[s])), 0.03, 0.25)

    parts = InterestParts(motion, modulation, richness, micro_events, diversity, stereo)
    weights = {
        "motion": 0.25,
        "modulation": 0.2,
        "richness": 0.1,
        "micro_events": 0.2,
        "diversity": 0.25,
        "stereo": 0.1,
    }
    total, wsum = 0.0, 0.0
    for k, w in weights.items():
        v = getattr(parts, k)
        if v is None:
            continue
        total += w * v
        wsum += w
    score = total / wsum
    # Hard veto: nothing moving at all means not interesting, whatever the spectrum.
    if max(motion, modulation, micro_events, diversity) < 0.1:
        score *= 0.3
    return float(score), parts


def score_region(y: np.ndarray, f: Features, start_s: float, end_s: float) -> Scores:
    s = slice(f.frame(start_s), max(f.frame(start_s) + 1, f.frame(end_s)))
    seg = y[:, int(start_s * f.sr) : int(end_s * f.sr)]
    loop, loop_end = loop_point(f, s)
    inter, parts = interest(f, s)
    return Scores(
        quality=quality(seg, f, s),
        stationarity=stationarity(f, s),
        loopability=loop,
        loop_end_s=loop_end,
        rhythmicity=rhythmicity(f, s),
        trend=trend(f, s),
        interest=inter,
        interest_parts=parts,
    )
