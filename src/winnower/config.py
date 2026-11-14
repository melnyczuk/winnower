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


@dataclass(frozen=True)
class Settings:
    storage_root: str = "./data"
    freesound_token: str | None = None
    openverse_token: str | None = None
    licences: LicencePolicy = field(default_factory=LicencePolicy)
    analysis: AnalysisSettings = field(default_factory=AnalysisSettings)


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
        ),
    )
