"""Settings from winnower.toml, overridable with env vars (or a .env file).

Precedence, highest first: CLI flags, environment variables, ``.env``, ``winnower.toml``.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# Entries without a version match every version (CC-BY covers CC-BY-2.5 and CC-BY-4.0).
DEFAULT_ALLOW = ("CC0-1.0", "PDM", "CC-BY")
DEFAULT_FLAG = ("CC-BY-NC", "CC-BY-SA", "CC-BY-NC-SA", "Sampling+", "NASA-Media")


@dataclass(frozen=True)
class LicencePolicy:
    allow: tuple[str, ...] = DEFAULT_ALLOW
    flag: tuple[str, ...] = DEFAULT_FLAG


@dataclass(frozen=True)
class AnalysisSettings:
    sample_rate: int = 44100
    max_regions_per_file: int = 4
    min_interest: float = 0.35
    chunk_s: float = 600.0  # long files are analysed in chunks of this length
    min_duration_s: float = 2.0  # no phrase shorter than this
    target_duration_s: float = 10.0  # phrase length aimed at
    max_duration_s: float = 24.0
    bpm_min: int = 70  # tempos are folded (doubled or halved) into this range
    bpm_max: int = 150


OUTPUT_FORMATS = ("flac", "mp3")


@dataclass(frozen=True)
class OutputSettings:
    # "flac" (lossless, loops seamlessly) or "mp3" (320k CBR, ~3-4x smaller; encoder
    # padding can put a gap at the loop point in DAWs that ignore the gapless header).
    format: str = "flac"

    def __post_init__(self):
        if self.format not in OUTPUT_FORMATS:
            raise ValueError(f"output format must be one of {OUTPUT_FORMATS}: {self.format!r}")


@dataclass(frozen=True)
class CrawlSettings:
    """How gently every HTTP request is paced, per host (see ``sources.polite``)."""

    min_interval_s: float = 1.5  # base gap between requests to one host
    jitter: float = 0.5  # each gap varies by up to this fraction either way
    max_retries: int = 4  # on 429, 5xx gateway errors and dropped connections
    max_backoff_s: float = 300.0  # a longer Retry-After stops instead of waiting
    breather_every: tuple[int, int] = (25, 60)  # requests to a host between long pauses
    breather_s: tuple[float, float] = (10.0, 40.0)  # length of a long pause

    def __post_init__(self):
        if not 0 <= self.jitter < 1:
            raise ValueError(f"crawl jitter must be in [0, 1): {self.jitter}")
        for name in ("breather_every", "breather_s"):
            lo, hi = getattr(self, name)
            if lo > hi or lo < 0:
                raise ValueError(f"crawl {name} must be [low, high]: {getattr(self, name)}")


@dataclass(frozen=True)
class Settings:
    storage_root: str = "./data"
    freesound_token: str | None = None
    openverse_token: str | None = None
    licences: LicencePolicy = field(default_factory=LicencePolicy)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    crawl: CrawlSettings = field(default_factory=CrawlSettings)


def load_env_file(path: str | os.PathLike | None = None) -> dict[str, str]:
    """Load ``KEY=value`` lines from a .env file (default ./.env if present) into the
    environment. Variables already set in the environment win. Returns what was read."""
    candidate = Path(path) if path else Path(".env")
    if not candidate.exists():
        if path:
            raise FileNotFoundError(candidate)
        return {}
    values = {}
    for line in candidate.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.removeprefix("export ").partition("=")
        if not sep:
            continue
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].strip()  # trailing comment
        values[key] = value
        os.environ.setdefault(key, value)
    return values


def _pair(v, kind):
    """``[low, high]`` from TOML, or a single number for both."""
    lo, hi = (v, v) if isinstance(v, int | float) else v
    return kind(lo), kind(hi)


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    """Read settings from a TOML file (default ./winnower.toml if present), then env vars."""
    data: dict = {}
    candidate = Path(path) if path else Path("winnower.toml")
    if candidate.exists():
        data = tomllib.loads(candidate.read_text())
    elif path:
        raise FileNotFoundError(candidate)

    lic = data.get("licences", {})
    ana = data.get("analysis", {})
    out = data.get("output", {})
    crawl = data.get("crawl", {})
    dc = CrawlSettings()
    return Settings(
        storage_root=os.environ.get("WINNOWER_STORAGE_ROOT", data.get("storage_root", "./data")),
        freesound_token=os.environ.get("FREESOUND_TOKEN", data.get("freesound_token")),
        openverse_token=os.environ.get("OPENVERSE_TOKEN", data.get("openverse_token")),
        licences=LicencePolicy(
            allow=tuple(lic.get("allow", DEFAULT_ALLOW)),
            flag=tuple(lic.get("flag", DEFAULT_FLAG)),
        ),
        analysis=AnalysisSettings(
            sample_rate=int(ana.get("sample_rate", 44100)),
            max_regions_per_file=int(ana.get("max_regions_per_file", 4)),
            min_interest=float(
                os.environ.get("WINNOWER_MIN_INTEREST", ana.get("min_interest", 0.35))
            ),
            chunk_s=float(ana.get("chunk_s", 600.0)),
            min_duration_s=float(
                os.environ.get("WINNOWER_MIN_DURATION_S", ana.get("min_duration_s", 2.0))
            ),
            target_duration_s=float(ana.get("target_duration_s", 10.0)),
            max_duration_s=float(ana.get("max_duration_s", 24.0)),
            bpm_min=int(ana.get("bpm_min", 70)),
            bpm_max=int(ana.get("bpm_max", 150)),
        ),
        output=OutputSettings(
            format=os.environ.get("WINNOWER_OUTPUT_FORMAT", out.get("format", "flac")).lower(),
        ),
        crawl=CrawlSettings(
            min_interval_s=float(
                os.environ.get(
                    "WINNOWER_MIN_INTERVAL_S", crawl.get("min_interval_s", dc.min_interval_s)
                )
            ),
            jitter=float(crawl.get("jitter", dc.jitter)),
            max_retries=int(crawl.get("max_retries", dc.max_retries)),
            max_backoff_s=float(crawl.get("max_backoff_s", dc.max_backoff_s)),
            breather_every=_pair(crawl.get("breather_every", dc.breather_every), int),
            breather_s=_pair(crawl.get("breather_s", dc.breather_s), float),
        ),
    )
