"""Beat grids, so every phrase is a whole number of bars and loops on the beat.

Pulsed material gets its own tempo and downbeat (``detected``). What counts as pulsed is
decided over the whole recording: music keeps its tempo from one window to the next, while
rain or crackle gives a different estimate every time. Arrhythmic material has no
pulse to find, so the sound picks its own loop length, the one whose end flows best back
into its start, and that length is read as bars at a whole-number tempo (``fitted``). Either
way a region becomes ``beats`` beats at ``bpm``, starting on a real or imagined downbeat.
"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

from .features import Features

BEATS = (4, 8, 16, 32)  # 1, 2, 4 and 8 bars of 4/4
DETECT_MIN = 0.35  # rhythmicity above which a short file's pulse is trusted
PULSE_WINDOW_S = 8.0  # local tempo estimates are made over windows this long
STEADY_MIN = 0.6  # share of windows that must agree with the overall tempo
CLARITY_MIN = 1.5  # beat-comb onset energy over mean onset energy; noise sits near 1.1
SEAM_S = 0.3  # spectra this long either side of the start and the end are compared
START_SEARCH_S = 3.0  # fitted grids may start this far into a candidate window
LENGTH_SIGMA = 0.5  # width (in ln units) of the preference for lengths near the target


@dataclass(frozen=True)
class GridSpec:
    min_s: float = 2.0
    target_s: float = 10.0
    max_s: float = 24.0
    bpm_min: int = 70
    bpm_max: int = 150


@dataclass(frozen=True)
class Grid:
    bpm: int
    beats: int
    start_s: float
    source: str  # detected | fitted
    confidence: float  # pulse strength for detected grids, 0 for fitted ones
    seam: float  # 0..1: how well the end flows back into the start

    @property
    def length_s(self) -> float:
        return self.beats * 60.0 / self.bpm

    @property
    def end_s(self) -> float:
        return self.start_s + self.length_s

    @property
    def bars(self) -> int:
        return self.beats // 4


@dataclass(frozen=True)
class Pulse:
    """A recording's overall tempo, and how far to trust it."""

    bpm: int
    steadiness: float  # share of windows whose own tempo agrees with ``bpm``
    clarity: float  # median over windows of beat-comb energy / mean onset energy
    windows: int

    @property
    def steady(self) -> bool:
        return self.windows >= 3 and self.steadiness >= STEADY_MIN and self.clarity >= CLARITY_MIN


def length_preference(length_s: float, target_s: float) -> float:
    """1 at the target length, falling off smoothly (in log time) either side."""
    return float(np.exp(-0.5 * (np.log(length_s / target_s) / LENGTH_SIGMA) ** 2))


def _rolling_mean(M: np.ndarray, w: int) -> np.ndarray:
    """``R[:, i] = M[:, i : i + w].mean(axis=1)``."""
    c = np.cumsum(np.pad(M, ((0, 0), (1, 0))), axis=1, dtype=np.float64)
    return ((c[:, w:] - c[:, :-w]) / w).astype(np.float32)


def _best(
    f: Features,
    starts: np.ndarray,
    shapes: list[tuple[int, int]],
    b: float,
    spec: GridSpec,
    loop: bool = True,
) -> tuple[float, int, int, float] | None:
    """Best (start_s, beats, bpm, seam) among ``starts`` (seconds) and (beats, bpm) shapes
    that end by ``b``. The seam compares the spectrum around the start with the spectrum
    around the end, so the wrap sounds like the recording carrying on. With ``loop`` off
    (gestures) the seam is ignored and only the length counts."""
    w = max(2, int(round(SEAM_S * f.fps)))
    h = w // 2
    R = _rolling_mean(f.logmel, w)  # R[:, t - h] is centred on frame t
    lengths = np.array([beats * 60.0 / bpm for beats, bpm in shapes])
    s_fr = np.clip(np.round(starts * f.fps).astype(int) - h, 0, R.shape[1] - 1)
    e_s = starts[:, None] + lengths[None, :]
    e_fr = np.round(e_s * f.fps).astype(int) - h
    ok = (e_s <= b + 1e-6) & (e_fr < R.shape[1])
    if not ok.any():
        return None
    e_fr = np.clip(e_fr, 0, R.shape[1] - 1)
    d = np.abs(R[:, s_fr][:, :, None] - R[:, e_fr]).mean(axis=0)  # mean dB difference
    seam = np.exp(-d / 3.0)
    pref = np.array([length_preference(x, spec.target_s) for x in lengths])
    # Tie-break between equal lengths (8 beats at 75 = 16 at 150): favour mid tempos.
    mid = (spec.bpm_min + spec.bpm_max) / 2
    tie = np.array([1e-4 * abs(bpm - mid) / mid for _, bpm in shapes])
    fit = seam * (0.5 + 0.5 * pref[None, :]) if loop else np.broadcast_to(pref, seam.shape)
    score = np.where(ok, fit - tie[None, :], -np.inf)
    i, j = np.unravel_index(int(np.argmax(score)), score.shape)
    beats, bpm = shapes[j]
    return float(starts[i]), beats, bpm, float(seam[i, j])


def _fold(bpm: float, spec: GridSpec) -> int:
    """Double or halve a tempo into the allowed range."""
    while bpm < spec.bpm_min and bpm > 0:
        bpm *= 2
    while bpm > spec.bpm_max:
        bpm /= 2
    return int(round(min(max(bpm, spec.bpm_min), spec.bpm_max)))


def _comb(env: np.ndarray, beat: float, offsets: np.ndarray) -> np.ndarray:
    """Onset energy at ``offset + k * beat`` frames, summed over k, for each offset."""
    pos = offsets[:, None] + beat * np.arange(int(env.size / beat) + 1)[None, :]
    idx = np.round(pos).astype(int)
    return np.where(idx < env.size, env[np.minimum(idx, env.size - 1)], 0.0).sum(axis=1)


def _comb_fit(env: np.ndarray, fps: float, bpm: int) -> float:
    """Mean onset energy on the best-phased beat comb at ``bpm``."""
    beat = 60.0 / bpm * fps
    return float(_comb(env, beat, np.arange(int(beat))).max() / (env.size / beat))


def _refine(env: np.ndarray, fps: float, bpm: int, spec: GridSpec) -> int:
    """The whole-number tempo near ``bpm`` whose beat comb lines up best with the onsets.
    The estimator's lag resolution is coarse (several BPM at 120), which would make an
    8-bar loop drift off the beat."""
    lo, hi = max(spec.bpm_min, bpm - 5), min(spec.bpm_max, bpm + 5)
    return max(range(lo, hi + 1), key=lambda t: _comb_fit(env, fps, t))


def _tempo(env: np.ndarray, f: Features, spec: GridSpec) -> int:
    tempo = librosa.feature.tempo(onset_envelope=env, sr=f.sr, hop_length=f.hop, start_bpm=100)
    return _refine(env, f.fps, _fold(float(np.atleast_1d(tempo)[0]), spec), spec)


def _same_tempo(a: int, b: int) -> bool:
    """Equal within 2% (or 2 BPM), allowing for double- and half-time readings."""
    tol = max(2.0, 0.02 * b)
    return min(abs(a - b), abs(2 * a - b), abs(a / 2 - b)) <= tol


def pulse(f: Features, spec: GridSpec) -> Pulse:
    """Overall tempo of a recording, checked window by window."""
    bpm = _tempo(f.flux, f, spec)
    n, hop = int(PULSE_WINDOW_S * f.fps), int(PULSE_WINDOW_S / 2 * f.fps)
    agree, clarity = [], []
    for i in range(0, f.n_frames - n + 1, hop):
        env = f.flux[i : i + n]
        local = _tempo(env, f, spec)
        agree.append(_same_tempo(local, bpm))
        clarity.append(_comb_fit(env, f.fps, local) / (env.mean() + 1e-9))
    if not agree:
        return Pulse(bpm, 0.0, 0.0, 0)
    return Pulse(bpm, float(np.mean(agree)), float(np.median(clarity)), len(agree))


def _downbeats(f: Features, a: float, b: float, bpm: int) -> np.ndarray:
    """Downbeat times in [a, b): the bar phase whose beats, and especially whose first
    beats, carry the most onset energy."""
    env = f.flux[f.frame(a) : f.frame(b)]
    beat = 60.0 / bpm * f.fps  # frames per beat
    bar = 4 * beat
    phases = np.arange(int(bar))
    phase = int(np.argmax(_comb(env, beat, phases) + _comb(env, bar, phases)))
    return a + (phase + bar * np.arange(int((env.size - phase) / bar) + 1)) / f.fps


def detect(
    f: Features,
    a: float,
    b: float,
    confidence: float,
    spec: GridSpec,
    loop: bool = True,
    near_bpm: int | None = None,
) -> Grid | None:
    """A grid from the recording's own pulse, starting on a downbeat. ``near_bpm`` (the
    recording's overall tempo) is refined locally, following a drifting live tempo."""
    env = f.flux[f.frame(a) : f.frame(b)]
    if env.size < int(4 * f.fps):
        return None
    bpm = _refine(env, f.fps, near_bpm, spec) if near_bpm else _tempo(env, f, spec)
    shapes = [(n, bpm) for n in BEATS if spec.min_s <= n * 60.0 / bpm <= spec.max_s]
    starts = _downbeats(f, a, b, bpm)
    best = _best(f, starts, shapes, b, spec, loop) if shapes and starts.size else None
    if best is None:
        return None
    start, beats, bpm, seam = best
    return Grid(bpm, beats, start, "detected", round(confidence, 4), seam)


def fit(f: Features, a: float, b: float, spec: GridSpec, loop: bool = True) -> Grid | None:
    """A grid for sound with no pulse: the loop length with the best seam, read as a whole
    number of bars at a whole-number tempo."""
    longest = min(spec.max_s, b - a)
    shapes = [
        (n, bpm)
        for n in BEATS
        for bpm in range(spec.bpm_min, spec.bpm_max + 1)
        if spec.min_s <= n * 60.0 / bpm <= longest
    ]
    if not shapes:
        return None
    step = max(1, int(round(0.05 * f.fps))) / f.fps
    room = min(START_SEARCH_S, b - a - spec.min_s)
    starts = a + np.arange(0.0, room + 1e-9, step)
    best = _best(f, starts, shapes, b, spec, loop)
    if best is None:
        return None
    start, beats, bpm, seam = best
    return Grid(bpm, beats, start, "fitted", 0.0, seam)


def grid_for(
    f: Features,
    a: float,
    b: float,
    rhythm: float,
    spec: GridSpec,
    loop: bool = True,
    beat: Pulse | None = None,
) -> Grid | None:
    """A detected grid when the recording has a steady pulse (or, when it's too short to
    tell, the window is clearly rhythmic), otherwise a fitted one."""
    g = None
    if beat is not None and beat.steady:
        g = detect(f, a, b, beat.steadiness, spec, loop, near_bpm=beat.bpm)
    elif (beat is None or beat.windows < 3) and rhythm >= DETECT_MIN:
        g = detect(f, a, b, rhythm, spec, loop)
    return g or fit(f, a, b, spec, loop)


def snap_to_onset(y: np.ndarray, sr: int, t: float, back_s: float = 0.06) -> float:
    """Move a beat time from frame resolution to just before the attack it marks: the
    steepest rise in 1 ms energy within ``back_s`` before (or 20 ms after) ``t``, less
    2 ms. Frame-level onsets land 10-20 ms late, which would clip every downbeat."""
    mono = y.mean(axis=0)
    a = max(0, int((t - back_s) * sr))
    b = min(mono.size, int((t + 0.02) * sr))
    n = max(1, sr // 1000)
    blocks = (b - a) // n
    if blocks < 3:
        return t
    e = 10 * np.log10((mono[a : a + blocks * n].reshape(blocks, n) ** 2).mean(axis=1) + 1e-12)
    rise = int(np.argmax(np.diff(e))) + 1
    return max(0.0, (a + rise * n) / sr - 0.002)
