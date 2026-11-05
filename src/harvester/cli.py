"""Command line: ``harvester harvest freesound "drone"``, ``harvester analyse *.wav``, ..."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from . import index
from .config import load_settings
from .pipeline import analyse_local, harvest
from .storage import Store


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="harvester", description=__doc__)
    ap.add_argument("--config", help="path to harvester.toml (default ./harvester.toml if present)")
    ap.add_argument("--root", help="override storage_root (local path or s3:// URI)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("harvest", help="search a source, analyse results, keep good regions")
    h.add_argument("source", choices=["freesound"])
    h.add_argument("query")
    h.add_argument("--limit", type=int, default=20, help="max source files to process")
    h.add_argument("--max-duration", type=float, default=600.0, help="skip longer files (s)")

    a = sub.add_parser("analyse", help="analyse your own audio files")
    a.add_argument("paths", nargs="+", type=Path)

    sub.add_parser("reindex", help="rebuild the index from sidecars")

    ls = sub.add_parser("list", help="list indexed samples")
    ls.add_argument("--min-interest", type=float, default=0.0)
    ls.add_argument("--type", choices=["texture_loop", "texture", "gesture", "one_shot"])
    ls.add_argument("--licence", action="append", help="licence id to include (repeatable)")
    ls.add_argument("--commercial", action="store_true", help="only allow-listed licences")
    ls.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s"
    )
    settings = load_settings(args.config)
    store = Store(args.root or settings.storage_root)

    if args.cmd == "harvest":
        from .sources.freesound import Freesound

        if not settings.freesound_token:
            print("Set FREESOUND_TOKEN or freesound_token in harvester.toml", file=sys.stderr)
            return 2
        fs = Freesound(settings.freesound_token)
        items = fs.search(args.query, limit=args.limit, max_duration=args.max_duration)
        counts = harvest(items, fs.fetch_audio, store, settings)
        n = index.rebuild(store)
        print(json.dumps(counts | {"indexed": n}))
    elif args.cmd == "analyse":
        samples = analyse_local(args.paths, store, settings)
        n = index.rebuild(store)
        for s in samples:
            sc, r = s["scores"], s["region"]
            print(
                f"{s['file']}  {r['type']:12s} {r['start_s']:7.2f}-{r['end_s']:7.2f}s  "
                f"interest={sc['interest']:.2f}"
            )
        print(f"{len(samples)} samples written; {n} in index", file=sys.stderr)
    elif args.cmd == "reindex":
        print(index.rebuild(store))
    elif args.cmd == "list":
        rows = index.load(store)
        rows = [r for r in rows if (r["interest"] or 0) >= args.min_interest]
        if args.type:
            rows = [r for r in rows if r["type"] == args.type]
        if args.licence:
            rows = [r for r in rows if r["licence"] in args.licence]
        if args.commercial:
            rows = [r for r in rows if r["licence_decision"] in ("allow", "local")]
        rows.sort(key=lambda r: r["rank"] or 0, reverse=True)
        for r in rows:
            if args.json:
                print(json.dumps(r))
            else:
                print(
                    f"{r['rank']:.2f}  {r['type']:12s} {r['duration_s']:6.1f}s  "
                    f"{r['licence'] or '-':13s} {store.uri(r['file'])}  {r['title'] or ''}"
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
