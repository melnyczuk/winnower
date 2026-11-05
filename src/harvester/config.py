"""Settings from harvester.toml, overridable with HARVESTER_* / FREESOUND_TOKEN env vars."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_ALLOW = ("CC0-1.0", "PDM", "CC-BY-3.0", "CC-BY-4.0")
DEFAULT_FLAG = (
    "CC-BY-NC-3.0",
    "CC-BY-NC-4.0",
    "CC-BY-SA-3.0",
    "CC-BY-SA-4.0",
    "Sampling+",
)


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


@dataclass(frozen=True)
class Settings:
    storage_root: str = "./data"
    freesound_token: str | None = None
    licences: LicencePolicy = field(default_factory=LicencePolicy)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    """Read settings from a TOML file (default ./harvester.toml if present), then env vars."""
    data: dict = {}
    candidate = Path(path) if path else Path("harvester.toml")
    if candidate.exists():
        data = tomllib.loads(candidate.read_text())
    elif path:
        raise FileNotFoundError(candidate)

    lic = data.get("licences", {})
    ana = data.get("analysis", {})
    return Settings(
        storage_root=os.environ.get("HARVESTER_STORAGE_ROOT", data.get("storage_root", "./data")),
        freesound_token=os.environ.get("FREESOUND_TOKEN", data.get("freesound_token")),
        licences=LicencePolicy(
            allow=tuple(lic.get("allow", DEFAULT_ALLOW)),
            flag=tuple(lic.get("flag", DEFAULT_FLAG)),
        ),
        analysis=AnalysisSettings(
            sample_rate=int(ana.get("sample_rate", 44100)),
            max_regions_per_file=int(ana.get("max_regions_per_file", 4)),
            min_interest=float(
                os.environ.get("HARVESTER_MIN_INTEREST", ana.get("min_interest", 0.35))
            ),
            chunk_s=float(ana.get("chunk_s", 600.0)),
        ),
    )
