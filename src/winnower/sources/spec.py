"""How a source plugs into the command line.

Each adapter module ends with a ``SOURCE``: a :class:`Source` naming the subcommand,
declaring its arguments as data, and giving a function from parsed arguments and
settings to a :class:`Harvest` (the items to process and how to fetch each one).
``sources.registry.SOURCES`` lists the active ones; the CLI is built from that list.
"""

from __future__ import annotations

from argparse import Namespace
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..config import Settings
from .base import SourceItem

Fetch = Callable[[SourceItem], bytes | str]


@dataclass(frozen=True)
class Arg:
    """One ``argparse`` argument, as data: ``Arg(("--depth",), {"type": int})``."""

    flags: tuple[str, ...]
    options: dict[str, Any]


def arg(*flags: str, **options: Any) -> Arg:
    return Arg(flags, options)


@dataclass(frozen=True)
class Harvest:
    items: Iterable[SourceItem]
    fetch: Fetch


@dataclass(frozen=True)
class Source:
    name: str
    help: str
    args: tuple[Arg, ...]
    open: Callable[[Namespace, Settings], Harvest]


class SourceError(Exception):
    """A source can't start (missing key, nothing to read). ``code`` is the exit status."""

    def __init__(self, message: str, code: int = 2):
        super().__init__(message)
        self.code = code


# Arguments several sources share.
QUERY = arg("query")


def limits(limit: int | None = 20) -> tuple[Arg, ...]:
    return (
        arg("--limit", type=int, default=limit, help="max source files to process"),
        arg("--max-duration", type=float, default=600.0, help="skip longer files (s)"),
    )


UNLICENSED = arg(
    "--include-unlicensed",
    dest="allow_unknown",
    action="store_true",
    help="keep audio with no stated licence, flagged web-unknown (rights reserved)",
)
PATH = arg("path", type=Path)


def window(args: Namespace) -> dict[str, Any]:
    """The shared ``limit`` / ``max_duration`` keywords every ``search()`` takes."""
    return {"limit": args.limit, "max_duration": args.max_duration}
