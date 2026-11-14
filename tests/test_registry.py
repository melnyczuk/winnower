import json

import pytest
import synth
from test_pipeline import ogg_bytes

from winnower import cli
from winnower.sources.base import SourceItem
from winnower.sources.registry import SOURCES
from winnower.sources.spec import QUERY, Harvest, Source, SourceError, limits, window


def test_every_registered_source_is_a_subcommand():
    names = [s.name for s in SOURCES]
    assert len(names) == len(set(names))
    sub = cli.parser()._subparsers._group_actions[0].choices["harvest"]
    assert list(sub._subparsers._group_actions[0].choices) == names


def fake_source(seen):
    def open_(args, settings):
        if args.query == "nothing":
            raise SourceError("nothing to do", code=3)
        seen.update(query=args.query, **window(args))
        item = SourceItem("fake", "1", "t", "a", None, "CC0", "mem://1", analysed_from="original")
        return Harvest([item], lambda it: ogg_bytes(synth.rain(12)))

    return Source("fake", "a stand-in source", (QUERY, *limits(limit=5)), open_)


def test_sources_can_be_swapped_without_touching_the_cli(tmp_path, capsys):
    seen = {}
    sources = (fake_source(seen),)
    argv = ["--root", str(tmp_path), "harvest", "fake", "rain", "--max-duration", "30"]
    assert cli.main(argv, sources=sources) == 0
    assert seen == {"query": "rain", "limit": 5, "max_duration": 30.0}
    counts = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert counts["seen"] == 1 and counts["samples"] >= 1

    assert cli.main(["--root", str(tmp_path), "harvest", "fake", "nothing"], sources=sources) == 3
    assert capsys.readouterr().err.strip() == "nothing to do"

    with pytest.raises(SystemExit):  # the real sources aren't there
        cli.main(["--root", str(tmp_path), "harvest", "freesound", "rain"], sources=sources)
