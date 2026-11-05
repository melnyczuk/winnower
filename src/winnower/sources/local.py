"""Local files and folders (albums you've downloaded, your own recordings).

Each file becomes a SourceItem with provider ``local``. Its id is derived from the path,
size and modification time, so re-running over the same folder skips files already
processed, and a re-tagged or replaced file is picked up again.

Rights: a downloaded album is almost always "all rights reserved". Unless you pass a
licence for the folder (e.g. a CC netlabel release), samples are kept but flagged
``local-unknown`` so `winnower list --commercial` leaves them out.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Iterator
from pathlib import Path

from .base import SourceItem

AUDIO_EXTS = {
    ".wav",
    ".wave",
    ".aif",
    ".aiff",
    ".flac",
    ".mp3",
    ".ogg",
    ".oga",
    ".opus",
    ".m4a",
    ".aac",
    ".wv",
    ".alac",
}
LOSSY_EXTS = {".mp3", ".ogg", ".oga", ".opus", ".m4a", ".aac"}


def iter_audio_files(paths: Iterable[Path], recursive: bool = True) -> Iterator[Path]:
    for p in paths:
        p = p.expanduser()
        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            for f in sorted(p.glob(pattern)):
                if f.is_file() and f.suffix.lower() in AUDIO_EXTS and not f.name.startswith("."):
                    yield f
        elif p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            yield p


def file_id(path: Path) -> str:
    st = path.stat()
    key = f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def read_tags(path: Path) -> dict:
    """Artist/album/title/etc. via mutagen if installed; {} otherwise or on failure."""
    try:
        import mutagen  # type: ignore
    except ImportError:
        return {}
    try:
        m = mutagen.File(path, easy=True)
    except Exception:
        return {}
    if not m or not m.tags:
        return {}
    keys = ("artist", "albumartist", "album", "title", "tracknumber", "date", "genre")
    out = {}
    for k in keys:
        v = m.tags.get(k)
        if v:
            out[k] = v[0] if len(v) == 1 else list(v)
    return out


def to_item(path: Path, licence: str | None = None) -> SourceItem:
    tags = read_tags(path)
    genre = tags.get("genre")
    return SourceItem(
        provider="local",
        provider_id=file_id(path),
        title=tags.get("title") or path.stem,
        author=tags.get("artist") or tags.get("albumartist"),
        page_url=None,
        licence_raw=licence,
        audio_url=str(path.resolve()),
        tags=[genre] if isinstance(genre, str) else list(genre or []),
        raw={"path": str(path.resolve()), "file_tags": tags},
    )


def is_lossy(path: str) -> bool:
    return Path(path).suffix.lower() in LOSSY_EXTS
