"""Command line: ``winnower harvest freesound "drone"``, ``winnower harvest local ~/Music``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Iterable, Iterator

import requests

from . import index
from .analysis.tonal import compatible
from .config import Settings, load_env_file, load_settings
from .pipeline import harvest
from .sources.base import DailyQuotaExceeded, SourceItem
from .sources.registry import SOURCES
from .sources.spec import Harvest, Source, SourceError
from .storage import Store

Command = Callable[[argparse.Namespace, Store, Settings], int]


class _Stoppable:
    """Iterates ``items`` until a search fails or a quota runs out, then ends quietly
    and keeps the reason in ``stopped``."""

    def __init__(self, items: Iterable[SourceItem]):
        self.items = items
        self.stopped: str | None = None

    def __iter__(self) -> Iterator[SourceItem]:
        try:
            yield from self.items
        except DailyQuotaExceeded as e:
            self.stopped = str(e)
        except requests.RequestException as e:
            self.stopped = f"search failed: {e}"


def _harvest(args: argparse.Namespace, store: Store, settings: Settings) -> int:
    """Open the chosen source, harvest it, rebuild the index and print counts. A failed
    search or a used-up quota stops the run cleanly; what was harvested is kept."""
    source: Source = args.source
    try:
        h: Harvest = source.open(args, settings)
    except SourceError as e:
        print(e, file=sys.stderr)
        return e.code
    items = _Stoppable(h.items)
    counts = harvest(items, h.fetch, store, settings)
    stopped = {"stopped": items.stopped} if items.stopped else {}
    if items.stopped:
        print(f"Stopped early: {items.stopped}", file=sys.stderr)
    print(json.dumps(counts | stopped | {"indexed": index.rebuild(store)}))
    return 1 if items.stopped and not counts["seen"] else 0


def _reindex(args: argparse.Namespace, store: Store, settings: Settings) -> int:
    print(index.rebuild(store))
    return 0


def bpm_range(text: str) -> tuple[float, float]:
    """``96`` or ``90-110``."""
    lo, _, hi = text.partition("-")
    try:
        return float(lo), float(hi or lo)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a tempo or range: {text}") from None


def _camelots(args: argparse.Namespace) -> set[str] | None:
    codes = set(args.camelot or [])
    if not codes:
        return None
    return set().union(*map(compatible, codes)) if args.compatible else codes


def select(rows: Iterable[dict], args: argparse.Namespace) -> list[dict]:
    """Rows matching the ``list`` filters, best first."""
    camelots = _camelots(args)
    tests = [
        lambda r: (r["interest"] or 0) >= args.min_interest,
        lambda r: not args.type or r["type"] == args.type,
        lambda r: not args.licence or r["licence"] in args.licence,
        lambda r: not args.commercial or r["licence_decision"] == "allow",
        lambda r: not args.key or r.get("key") in args.key,
        lambda r: camelots is None or r.get("camelot") in camelots,
        lambda r: not args.bpm or args.bpm[0] <= (r.get("bpm") or -1) <= args.bpm[1],
        lambda r: (r["duration_s"] or 0) >= args.min_length,
        lambda r: (r.get("seam") or 0) >= args.min_seam,
    ]
    kept = (r for r in rows if all(t(r) for t in tests))
    return sorted(kept, key=lambda r: r["rank"] or 0, reverse=True)


def _row_text(r: dict, store: Store) -> str:
    key = f"{r.get('camelot') or '':3s} {r.get('key') or '-':4s}"
    tempo = f"{r['bpm']:3d}bpm {r['bars']}bar" if r.get("bpm") else "-".ljust(11)
    return (
        f"{r['rank']:.2f}  {r['type']:8s} {key} {tempo:11s} {r['duration_s']:5.1f}s  "
        f"{r['licence'] or '-':13s} {store.uri(r['file'])}"
    )


def _list(args: argparse.Namespace, store: Store, settings: Settings) -> int:
    show = json.dumps if args.json else (lambda r: _row_text(r, store))
    for r in select(index.load(store), args):
        print(show(r))
    return 0


def parser(sources: Iterable[Source] = SOURCES) -> argparse.ArgumentParser:
    """The argument parser, with one ``harvest`` subcommand per source."""
    ap = argparse.ArgumentParser(prog="winnower", description=__doc__)
    ap.add_argument("--config", help="path to winnower.toml (default ./winnower.toml if present)")
    ap.add_argument(
        "--env-file", help="KEY=value file of env vars, e.g. API keys (default ./.env if present)"
    )
    ap.add_argument("--root", help="override storage_root (local path or s3:// URI)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("harvest", help="analyse audio from a source and keep good regions")
    hs = h.add_subparsers(dest="source_name", required=True)
    for source in sources:
        p = hs.add_parser(source.name, help=source.help)
        for a in source.args:
            p.add_argument(*a.flags, **a.options)
        p.set_defaults(command=_harvest, source=source)

    sub.add_parser("reindex", help="rebuild the index from sidecars").set_defaults(command=_reindex)

    ls = sub.add_parser("list", help="list indexed samples")
    ls.add_argument("--min-interest", type=float, default=0.0)
    ls.add_argument("--type", choices=["loop", "gesture"])
    ls.add_argument("--min-seam", type=float, default=0.0, help="0..1: how invisible the wrap is")
    ls.add_argument("--key", action="append", help="key such as Am or F# (repeatable)")
    ls.add_argument("--camelot", action="append", help="Camelot code such as 8A (repeatable)")
    ls.add_argument(
        "--compatible", action="store_true", help="with --camelot: also harmonic neighbours"
    )
    ls.add_argument("--bpm", type=bpm_range, help="tempo or range, e.g. 96 or 90-110")
    ls.add_argument("--min-length", type=float, default=0.0, help="seconds")
    ls.add_argument("--licence", action="append", help="licence id to include (repeatable)")
    ls.add_argument("--commercial", action="store_true", help="only allow-listed licences")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(command=_list)
    return ap


def main(argv: list[str] | None = None, sources: Iterable[Source] = SOURCES) -> int:
    args = parser(sources).parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    try:
        load_env_file(args.env_file)
    except FileNotFoundError as e:
        print(f"No such env file: {e}", file=sys.stderr)
        return 2
    settings = load_settings(args.config)
    command: Command = args.command
    return command(args, Store(args.root or settings.storage_root), settings)


if __name__ == "__main__":
    raise SystemExit(main())
