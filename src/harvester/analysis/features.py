"""Frame-level features shared by segmentation and scoring."""

from __future__ import annotations

import io
import subprocess
from dataclasses import dataclass

import librosa
import numpy as np
import soundfile as sf

HOP = 512
N_FFT = 2048
N_MELS = 64
N_MFCC = 20


def _src(data: bytes | str):
    return io.BytesIO(data) if isinstance(data, bytes | bytearray) else data


def _ffmpeg_input(data: bytes | str) -> tuple[list[str], bytes | None]:
    if isinstance(data, str):
        return ["-i", data], None
    return ["-i", "pipe:0"], bytes(data)


def _ffprobe_duration(data: bytes | str) -> float:
    args, stdin = _ffmpeg_input(data)
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", *args[1:]],
        input=stdin,
        capture_output=True,
        check=True,
    )
    return float(out.stdout.strip())


def _ffmpeg_load(data: bytes | str, sr: int, offset: float, duration: float | None) -> np.ndarray:
    """Decode anything ffmpeg can (AAC/M4A, ALAC, Opus, ...) to float32 (channels, samples)."""
    args, stdin = _ffmpeg_input(data)
    cmd = ["ffmpeg", "-v", "error", "-nostdin" if stdin is None else "-hide_banner"]
    cmd += ["-ss", str(offset)] + (["-t", str(duration)] if duration is not None else [])
    cmd += args + ["-ac", "2", "-ar", str(sr), "-f", "f32le", "pipe:1"]
    out = subprocess.run(cmd, input=stdin, capture_output=True, check=True)
    y = np.frombuffer(out.stdout, dtype=np.float32).reshape(-1, 2).T
    if np.allclose(y[0], y[1]):
        y = y[:1]
    return y


def audio_duration(data: bytes | str) -> float:
    """Duration in seconds, without decoding the whole file where possible."""
    try:
        info = sf.info(_src(data))
        return info.frames / info.samplerate
    except Exception:
        return _ffprobe_duration(data)


def load_audio(
    data: bytes | str, sr: int, offset: float = 0.0, duration: float | None = None
) -> tuple[np.ndarray, int]:
    """Decode to float32 (channels, samples) at ``sr``. Accepts bytes or a path.

    ``offset``/``duration`` (seconds) read just part of a file, so long recordings can be
    analysed in chunks with bounded memory. libsndfile handles WAV/AIFF/FLAC/OGG/MP3;
    anything else goes through the ffmpeg command-line tool.
    """
    try:
        with sf.SoundFile(_src(data)) as f:
            file_sr = f.samplerate
            f.seek(int(offset * file_sr))
            frames = -1 if duration is None else int(duration * file_sr)
            y = f.read(frames, dtype="float32", always_2d=True).T
    except (sf.LibsndfileError, RuntimeError):
        y, file_sr = _ffmpeg_load(data, sr, offset, duration), sr
    if y.shape[0] > 2:
        y = y[:2]
    if file_sr != sr:
        y = librosa.resample(y, orig_sr=file_sr, target_sr=sr)
    return np.ascontiguousarray(y, dtype=np.float32), sr


@dataclass
class Features:
    sr: int
    hop: int
    rms: np.ndarray  # (T,)
    centroid: np.ndarray  # (T,) Hz
    bandwidth: np.ndarray  # (T,) Hz
    flatness: np.ndarray  # (T,) 0..1
    rolloff: np.ndarray  # (T,) Hz, 95%
    flux: np.ndarray  # (T,) onset strength
    mfcc: np.ndarray  # (N_MFCC, T)
    contrast: np.ndarray  # (7, T)
    logmel: np.ndarray  # (N_MELS, T) dB
    stereo_corr: np.ndarray | None  # (T,) L/R correlation per frame, None if mono
    spectrum_db: np.ndarray  # (N_FFT//2+1,) long-term average spectrum

    @property
    def fps(self) -> float:
        return self.sr / self.hop

    @property
    def n_frames(self) -> int:
        return self.rms.shape[0]

    def frame(self, t: float) -> int:
        return int(round(t * self.fps))


def compute_features(y: np.ndarray, sr: int) -> Features:
    mono = y.mean(axis=0)
    S = np.abs(librosa.stft(mono, n_fft=N_FFT, hop_length=HOP))
    power = S**2
    mel = librosa.feature.melspectrogram(S=power, sr=sr, n_mels=N_MELS)
    logmel = librosa.power_to_db(mel, ref=1.0, top_db=None)
    T = S.shape[1]

    def fit(a: np.ndarray) -> np.ndarray:
        a = np.asarray(a).reshape(-1) if a.ndim == 1 or a.shape[0] == 1 else a
        return a[..., :T]

    stereo_corr = None
    if y.shape[0] == 2:
        L = librosa.util.frame(np.pad(y[0], N_FFT // 2), frame_length=N_FFT, hop_length=HOP)[:, :T]
        R = librosa.util.frame(np.pad(y[1], N_FFT // 2), frame_length=N_FFT, hop_length=HOP)[:, :T]
        L = L - L.mean(axis=0)
        R = R - R.mean(axis=0)
        denom = np.sqrt((L**2).sum(0) * (R**2).sum(0)) + 1e-12
        stereo_corr = (L * R).sum(0) / denom

    return Features(
        sr=sr,
        hop=HOP,
        rms=fit(librosa.feature.rms(S=S, frame_length=N_FFT)[0]),
        centroid=fit(librosa.feature.spectral_centroid(S=S, sr=sr)[0]),
        bandwidth=fit(librosa.feature.spectral_bandwidth(S=S, sr=sr)[0]),
        flatness=fit(librosa.feature.spectral_flatness(S=S)[0]),
        rolloff=fit(librosa.feature.spectral_rolloff(S=S, sr=sr, roll_percent=0.95)[0]),
        flux=fit(librosa.onset.onset_strength(S=logmel, sr=sr)),
        mfcc=fit(librosa.feature.mfcc(S=logmel, n_mfcc=N_MFCC)),
        contrast=fit(librosa.feature.spectral_contrast(S=S, sr=sr)),
        logmel=logmel,
        stereo_corr=stereo_corr,
        spectrum_db=librosa.amplitude_to_db(S.mean(axis=1) + 1e-10, ref=1.0),
    )


def block_matrix(f: Features, seconds: float = 0.25) -> np.ndarray:
    """Timbre feature sequence downsampled to blocks: (blocks, dims).

    Dimensions are scaled to comparable absolute units (roughly "1 = a clearly audible
    change") rather than z-scored, so a uniform file stays uniform instead of having its
    noise amplified into fake boundaries.
    """
    X = np.vstack(
        [
            f.mfcc[1:13] / 10.0,
            f.contrast / 10.0,
            np.log2(f.centroid + 1)[None],
            f.flatness[None] * 5.0,
            librosa.amplitude_to_db(f.rms + 1e-10)[None] / 6.0,
        ]
    )
    step = max(1, int(round(seconds * f.fps)))
    n = X.shape[1] // step
    if n == 0:
        return X.mean(axis=1, keepdims=True).T
    B = X[:, : n * step].reshape(X.shape[0], n, step).mean(axis=2).T
    return B - B.mean(axis=0)
