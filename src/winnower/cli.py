"""Command line: ``winnower harvest freesound "drone"``, ``winnower harvest local ~/Music``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import requests

from . import index
from .config import load_env_file, load_settings
from .pipeline import harvest
from .sources.base import DailyQuotaExceeded
from .storage import Store


def _common(p: argparse.ArgumentParser, unlicensed: bool = False) -> None:
    p.add_argument("--limit", type=int, default=20, help="max source files to process")
    p.add_argument("--max-duration", type=float, default=600.0, help="skip longer files (s)")
    if unlicensed:
        p.add_argument(
            "--include-unlicensed",
            action="store_true",
            help="keep audio with no stated licence, flagged web-unknown (rights reserved)",
        )


def _run(items, fetch, store: Store, settings, **kw) -> int:
    """Harvest, rebuild the index and print counts. A failed search or a used-up quota
    stops the run cleanly; whatever was harvested before that is kept."""
    stopped: list[str] = []

    def guarded():
        try:
            yield from items
        except DailyQuotaExceeded as e:
            stopped.append(str(e))
        except requests.RequestException as e:
            stopped.append(f"search failed: {e}")

    counts = harvest(guarded(), fetch, store, settings, **kw)
    if stopped:
        print(f"Stopped early: {stopped[0]}", file=sys.stderr)
        counts["stopped"] = stopped[0]
    print(json.dumps(counts | {"indexed": index.rebuild(store)}))
    return 1 if stopped and not counts["seen"] else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="winnower", description=__doc__)
    ap.add_argument("--config", help="path to winnower.toml (default ./winnower.toml if present)")
    ap.add_argument(
        "--env-file", help="KEY=value file of env vars, e.g. API keys (default ./.env if present)"
    )
    ap.add_argument("--root", help="override storage_root (local path or s3:// URI)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("harvest", help="analyse audio from a source and keep good regions")
    hs = h.add_subparsers(dest="source", required=True)
    hf = hs.add_parser("freesound", help="search Freesound (needs an API key)")
    hf.add_argument("query")
    _common(hf)
    hf.add_argument("--token", help="Freesound API key (default: FREESOUND_TOKEN)")
    hl = hs.add_parser("local", help="files or folders on disk, e.g. albums you've downloaded")
    hl.add_argument("paths", nargs="+", type=Path)
    hl.add_argument(
        "--licence",
        help="licence URL or id for these files (e.g. a CC netlabel album); "
        "default: kept but flagged as rights unknown",
    )
    hl.add_argument("--no-recursive", action="store_true", help="don't descend into subfolders")
    ho = hs.add_parser("openverse", help="search Openverse (no key; OPENVERSE_TOKEN optional)")
    ho.add_argument("query")
    _common(ho)
    ho.add_argument("--token", help="Openverse token for higher limits (default: OPENVERSE_TOKEN)")
    ho.add_argument("--license", dest="licence", help="Openverse licence codes, e.g. by,cc0")
    ho.add_argument(
        "--license-type", dest="licence_type", help="commercial, modification, all-cc, ..."
    )
    ho.add_argument(
        "--include-freesound",
        action="store_true",
        help="keep Freesound results (skipped by default: use `harvest freesound`)",
    )
    ha = hs.add_parser("archive", help="search the Internet Archive (no key)")
    ha.add_argument("query")
    _common(ha, unlicensed=True)
    ha.add_argument("--collection", help="limit to a collection, e.g. netlabels, radio-aporee-maps")
    hw = hs.add_parser("wikimedia", help="search Wikimedia Commons (no key)")
    hw.add_argument("query")
    _common(hw)
    hn = hs.add_parser("nasa", help="search the NASA Image and Video Library (no key)")
    hn.add_argument("query")
    _common(hn)
    hc = hs.add_parser("ccmixter", help="search ccMixter (no key)")
    hc.add_argument("query", nargs="?", help="text to search for")
    _common(hc)
    hc.add_argument("--tags", help="comma-separated ccMixter tags, e.g. ambient,field_recording")
    hm = hs.add_parser("manifest", help="a CSV/JSONL list of files or URLs (datasets)")
    hm.add_argument("path", type=Path)
    _common(hm, unlicensed=True)
    hm.set_defaults(limit=None)  # a manifest is a deliberate list: all rows by default
    hm.add_argument("--provider", help="provider name (default: the manifest's file name)")
    hwb = hs.add_parser("web", help="audio linked from web pages (respects robots.txt)")
    hwb.add_argument("urls", nargs="+", help="seed page URLs")
    _common(hwb, unlicensed=True)
    hwb.add_argument("--depth", type=int, default=0, help="follow same-host links this deep")
    hwb.add_argument("--max-pages", type=int, default=50, help="stop after this many pages")
    hwb.add_argument("--licence", help="licence URL or id for everything found (skips detection)")

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
    try:
        load_env_file(args.env_file)
    except FileNotFoundError as e:
        print(f"No such env file: {e}", file=sys.stderr)
        return 2
    settings = load_settings(args.config)
    store = Store(args.root or settings.storage_root)

    if args.cmd == "harvest":
        return _harvest(args, store, settings)
    if args.cmd == "reindex":
        print(index.rebuild(store))
    elif args.cmd == "list":
        rows = index.load(store)
        rows = [r for r in rows if (r["interest"] or 0) >= args.min_interest]
        if args.type:
            rows = [r for r in rows if r["type"] == args.type]
        if args.licence:
            rows = [r for r in rows if r["licence"] in args.licence]
        if args.commercial:
            rows = [r for r in rows if r["licence_decision"] == "allow"]
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


def _harvest(args, store: Store, settings) -> int:
    if args.source == "freesound":
        from .sources.freesound import Freesound

        token = args.token or settings.freesound_token
        if not token:
            print(
                "Give --token, or set FREESOUND_TOKEN (environment or .env) "
                "or freesound_token in winnower.toml",
                file=sys.stderr,
            )
            return 2
        fs = Freesound(token)
        items = fs.search(args.query, limit=args.limit, max_duration=args.max_duration)
        return _run(items, fs.fetch_audio, store, settings)
    if args.source == "local":
        from .sources import local

        files = list(local.iter_audio_files(args.paths, recursive=not args.no_recursive))
        if not files:
            print("No audio files found", file=sys.stderr)
            return 1

        def items():
            for i, f in enumerate(files, 1):
                print(f"[{i}/{len(files)}] {f}", file=sys.stderr)
                yield local.to_item(f, args.licence)

        return _run(items(), lambda it: it.audio_url, store, settings)
    if args.source == "openverse":
        from .sources.openverse import Openverse

        ov = Openverse(
            args.token or settings.openverse_token, include_freesound=args.include_freesound
        )
        items = ov.search(
            args.query,
            limit=args.limit,
            max_duration=args.max_duration,
            licence=args.licence,
            licence_type=args.licence_type,
        )
        return _run(items, ov.fetch_audio, store, settings)
    if args.source == "archive":
        from .sources.internet_archive import InternetArchive

        ia = InternetArchive(allow_unknown=args.include_unlicensed)
        items = ia.search(
            args.query,
            limit=args.limit,
            max_duration=args.max_duration,
            collection=args.collection,
        )
        return _run(items, ia.fetch_audio, store, settings)
    if args.source == "wikimedia":
        from .sources.wikimedia import WikimediaCommons

        wm = WikimediaCommons()
        items = wm.search(args.query, limit=args.limit, max_duration=args.max_duration)
        return _run(items, wm.fetch_audio, store, settings)
    if args.source == "nasa":
        from .sources.nasa import NasaMedia

        nasa = NasaMedia()
        items = nasa.search(args.query, limit=args.limit, max_duration=args.max_duration)
        return _run(items, nasa.fetch_audio, store, settings)
    if args.source == "ccmixter":
        from .sources.ccmixter import CcMixter

        if not args.query and not args.tags:
            print("Give a search query or --tags", file=sys.stderr)
            return 2
        cc = CcMixter()
        items = cc.search(
            args.query, limit=args.limit, max_duration=args.max_duration, tags=args.tags
        )
        return _run(items, cc.fetch_audio, store, settings)
    if args.source == "manifest":
        from .sources.manifest import Manifest

        if not args.path.exists():
            print(f"No such manifest: {args.path}", file=sys.stderr)
            return 2
        m = Manifest(args.path, provider=args.provider, allow_unknown=args.include_unlicensed)
        items = m.search(limit=args.limit, max_duration=args.max_duration)
        return _run(items, m.fetch_audio, store, settings)
    if args.source == "web":
        from .sources.web import WebPages

        web = WebPages(
            depth=args.depth,
            max_pages=args.max_pages,
            allow_unknown=args.include_unlicensed,
            licence=args.licence,
        )
        items = web.search(args.urls, limit=args.limit, max_duration=args.max_duration)
        return _run(items, web.fetch_audio, store, settings)
    raise AssertionError(args.source)


if __name__ == "__main__":
    raise SystemExit(main())
