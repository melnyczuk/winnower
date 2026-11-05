"""Deterministic synthetic test signals."""

import numpy as np

SR = 22050


def _t(dur):
    return np.arange(int(dur * SR)) / SR


def sine(dur=12.0, f=220.0):
    return (0.3 * np.sin(2 * np.pi * f * _t(dur)))[None].astype(np.float32)


def hum(dur=12.0):
    t = _t(dur)
    y = sum(0.1 / k * np.sin(2 * np.pi * 50 * k * t) for k in range(1, 8))
    return y[None].astype(np.float32)


def white(dur=12.0, seed=0):
    return (0.2 * np.random.default_rng(seed).standard_normal(int(dur * SR)))[None].astype(
        np.float32
    )


def crackle_drone(dur=12.0, seed=1):
    rng = np.random.default_rng(seed)
    t = _t(dur)
    drone = sum(0.08 * np.sin(2 * np.pi * f * t) for f in (110, 110.7, 165.3, 220.9, 331.0))
    y = drone.copy()
    n = len(t)
    for pos in rng.integers(0, n - 400, size=int(6 * dur)):
        y[pos : pos + 300] += (
            rng.uniform(0.1, 0.5) * rng.standard_normal(300) * np.exp(-np.arange(300) / 40)
        )
    return y[None].astype(np.float32)


def rain(dur=12.0, seed=2):
    rng = np.random.default_rng(seed)
    n = int(dur * SR)
    y = 0.02 * rng.standard_normal(n)
    for pos in rng.integers(0, n - 2000, size=int(25 * dur)):
        L = int(rng.integers(200, 1500))
        drop = rng.standard_normal(L) * np.exp(-np.arange(L) / (L / 5))
        drop = np.convolve(drop, np.ones(int(rng.integers(1, 6))), mode="same")
        y[pos : pos + L] += rng.uniform(0.05, 0.3) * drop
    return y[None].astype(np.float32)


def sweep(dur=12.0, seed=3):
    """Noise through a resonant band rising from 200 Hz to 6 kHz, swelling in level."""
    from scipy.signal import butter, sosfilt

    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(dur * SR))
    out = np.zeros_like(x)
    blocks = 600
    L = len(x) // blocks
    for i in range(blocks):
        fc = 200 * (30 ** (i / blocks))
        sos = butter(2, [fc * 0.8, fc * 1.25], btype="band", fs=SR, output="sos")
        out[i * L : (i + 1) * L] = sosfilt(sos, x[i * L : (i + 1) * L])
    env = np.linspace(0.05, 1.0, len(out))
    return (0.5 * out * env / (np.abs(out).max() + 1e-9))[None].astype(np.float32)


def clicks(dur=12.0, bpm=120):
    y = np.zeros(int(dur * SR))
    step = int(SR * 60 / bpm)
    for pos in range(0, len(y) - 200, step):
        y[pos : pos + 200] += 0.6 * np.exp(-np.arange(200) / 20) * np.sin(np.arange(200))
    return y[None].astype(np.float32)


def stereo(y_left, y_right):
    return np.vstack([y_left[0], y_right[0]]).astype(np.float32)
