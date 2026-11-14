"""Key estimation: mean chroma against the Krumhansl-Schmuckler key profiles.

Noise, crackle and most percussion have no key; they come back as ``None`` rather than a
confident guess. A drone or a held chord gets its tonic.
"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
MIN_CORRELATION = 0.6  # best profile match needed to name a key
MIN_CONTRAST = 0.35  # (max - min) / max of the mean chroma; flat chroma means no pitch


@dataclass(frozen=True)
class Key:
    tonic: str  # C, C#, ... B
    mode: str  # major | minor
    confidence: float  # correlation with the key profile, 0..1

    @property
    def short(self) -> str:
        """``Am``, ``F#``: the form used in file names and DAWs."""
        return self.tonic + ("m" if self.mode == "minor" else "")

    @property
    def name(self) -> str:
        return f"{self.tonic} {self.mode}"

    @property
    def camelot(self) -> str:
        return camelot(NOTES.index(self.tonic), self.mode)


def camelot(pc: int, mode: str) -> str:
    """Camelot wheel position: C major is 8B, A minor (same notes) is 8A; one step round
    the wheel is a fifth."""
    major_pc = pc if mode == "major" else (pc + 3) % 12
    number = (7 * major_pc + 8) % 12 or 12
    return f"{number}{'B' if mode == 'major' else 'A'}"


def compatible(code: str) -> set[str]:
    """Camelot codes that mix harmonically with ``code``: itself, its relative major or
    minor, and a fifth either side."""
    number, letter = int(code[:-1]), code[-1]
    other = "A" if letter == "B" else "B"
    step = [(number + d - 1) % 12 + 1 for d in (-1, 1)]
    return {code, f"{number}{other}"} | {f"{n}{letter}" for n in step}


def estimate_key(y: np.ndarray, sr: int) -> Key | None:
    mono = y.mean(axis=0)
    if mono.size < sr // 2 or np.abs(mono).max() < 1e-4:
        return None
    chroma = librosa.feature.chroma_cqt(y=mono, sr=sr, hop_length=2048).mean(axis=1)
    if chroma.max() <= 0 or (chroma.max() - chroma.min()) / chroma.max() < MIN_CONTRAST:
        return None
    best: tuple[float, int, str] = (-1.0, 0, "major")
    for mode, profile in (("major", MAJOR), ("minor", MINOR)):
        for pc in range(12):
            r = float(np.corrcoef(chroma, np.roll(profile, pc))[0, 1])
            best = max(best, (r, pc, mode))
    r, pc, mode = best
    if r < MIN_CORRELATION:
        return None
    return Key(NOTES[pc], mode, round(r, 4))
