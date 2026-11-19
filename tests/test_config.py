import json
import os

import pytest

from winnower import cli
from winnower.config import load_env_file, load_settings
from winnower.sources import base
from winnower.sources.freesound import Freesound
from winnower.sources.openverse import Openverse


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A clean environment and working directory (no stray .env or winnower.toml)."""
    clean = {k: v for k, v in os.environ.items() if "TOKEN" not in k and "WINNOWER" not in k}
    monkeypatch.setattr(os, "environ", clean)
    monkeypatch.chdir(tmp_path)
    return clean


def test_env_file_parsing_and_precedence(env, tmp_path):
    (tmp_path / ".env").write_text(
        "# keys\n"
        "FREESOUND_TOKEN=abc123  # trailing comment\n"
        "export OPENVERSE_TOKEN='quoted # not a comment'\n"
        'WINNOWER_USER_AGENT="winnower/0.1 (me)"\n'
        "WINNOWER_MIN_INTEREST=0.5\n"
        "not a pair\n"
    )
    env["WINNOWER_MIN_INTEREST"] = "0.7"  # the real environment wins over .env
    read = load_env_file()
    assert read["FREESOUND_TOKEN"] == "abc123"
    s = load_settings()
    assert s.freesound_token == "abc123"
    assert s.openverse_token == "quoted # not a comment"
    assert s.analysis.min_interest == 0.7
    assert base.http_session().headers["User-Agent"] == "winnower/0.1 (me)"


def test_env_file_optional_unless_named(env, tmp_path):
    assert load_env_file() == {}
    with pytest.raises(FileNotFoundError):
        load_env_file(tmp_path / "missing.env")
    assert cli.main(["--env-file", "missing.env", "reindex"]) == 2


def test_cli_token_flags_and_env_file(env, tmp_path, monkeypatch, capsys):
    seen = {}

    def fake_init(cls):
        def init(self, token=None, *a, **kw):
            seen[cls.__name__] = token
            self.token, self.http = token, None

        return init

    monkeypatch.setattr(Freesound, "__init__", fake_init(Freesound))
    monkeypatch.setattr(Freesound, "search", lambda self, *a, **kw: iter(()))
    monkeypatch.setattr(Openverse, "__init__", fake_init(Openverse))
    monkeypatch.setattr(Openverse, "search", lambda self, *a, **kw: iter(()))
    root = ["--root", str(tmp_path / "data")]

    assert cli.main([*root, "harvest", "freesound", "rain"]) == 2
    assert "--token" in capsys.readouterr().err

    assert cli.main([*root, "harvest", "freesound", "rain", "--token", "flag"]) == 0
    assert seen["Freesound"] == "flag"

    (tmp_path / "keys.env").write_text("FREESOUND_TOKEN=fromfile\nOPENVERSE_TOKEN=ov\n")
    assert cli.main(["--env-file", "keys.env", *root, "harvest", "freesound", "rain"]) == 0
    assert seen["Freesound"] == "fromfile"
    argv = ["--env-file", "keys.env", *root, "harvest", "openverse", "rain"]
    assert cli.main(argv) == 0 and seen["Openverse"] == "ov"
    assert cli.main([*argv, "--token", "flag"]) == 0 and seen["Openverse"] == "flag"
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["seen"] == 0


def test_output_format_from_toml_and_env(env, tmp_path):
    assert load_settings().output.format == "flac"
    (tmp_path / "winnower.toml").write_text('[output]\nformat = "mp3"\n')
    assert load_settings().output.format == "mp3"
    env["WINNOWER_OUTPUT_FORMAT"] = "FLAC"
    assert load_settings().output.format == "flac"
    env["WINNOWER_OUTPUT_FORMAT"] = "wav"
    with pytest.raises(ValueError, match="output format"):
        load_settings()


def test_min_duration_from_env_beats_toml(env, tmp_path):
    (tmp_path / "winnower.toml").write_text("[analysis]\nmin_duration_s = 3.0\n")
    assert load_settings().analysis.min_duration_s == 3.0
    env["WINNOWER_MIN_DURATION_S"] = "7.5"
    assert load_settings().analysis.min_duration_s == 7.5
