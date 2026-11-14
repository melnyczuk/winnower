"""The sources the CLI offers, in ``winnower harvest --help`` order.

To add a source, write a module that ends with a ``SOURCE`` (see :mod:`.spec`) and list
it here; to drop one, delete its line.
"""

from __future__ import annotations

from . import (
    ccmixter,
    freesound,
    internet_archive,
    local,
    manifest,
    nasa,
    openverse,
    web,
    wikimedia,
)
from .spec import Source

SOURCES: tuple[Source, ...] = (
    freesound.SOURCE,
    openverse.SOURCE,
    internet_archive.SOURCE,
    wikimedia.SOURCE,
    nasa.SOURCE,
    ccmixter.SOURCE,
    manifest.SOURCE,
    web.SOURCE,
    local.SOURCE,
)
