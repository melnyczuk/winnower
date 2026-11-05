import json
import shutil
import subprocess

import numpy as np
import pytest
import soundfile as sf
import synth

from winnower import cli, index
from winnower.analysis.features import audio_duration, load_audio
from winnower.config import AnalysisSettings, Settings
from winnower.pipeline import harvest, process_audio
from winnower.sources import local
from winnower.storage import Store


def write(path, y, fmt=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(path, y.T, synth.SR, format=fmt)


def album(tmp_path):
    root = tmp_path / "Music" / "Some Artist" / "Some Album"
    write(
        root / "01 rain.flac", np.concatenate([synth.rain(12)[0], synth.crackle_drone(12)[0]])[None]
    )
    write(root / "02 hum.wav", synth.hum(12))
    write(root / "disc 2" / "01 crackle.wav", synth.crackle_drone(12))
    (root / "cover.jpg").write_bytes(b"not audio")
    (root / ".hidden.wav").write_bytes(b"junk")
    import mutagen.flac

    tags = mutagen.flac.FLAC(root / "01 rain.flac")
    tags["artist"], tags["album"], tags["title"], tags["genre"] = (
        "Some Artist",
        "Some Album",
        "Rain",
        "Ambient",
    )
    tags.save()
    return tmp_path / "Music"


def test_iter_audio_files_recurses_and_filters(tmp_path):
    root = album(tmp_path)
    names = [p.name for p in local.iter_audio_files([root])]
    assert names == ["01 rain.flac", "02 hum.wav", "01 crackle.wav"]
    shallow = list(local.iter_audio_files([root / "Some Artist" / "Some Album"], recursive=False))
    assert len(shallow) == 2


def test_item_reads_tags(tmp_path):
    root = album(tmp_path)
    f = next(local.iter_audio_files([root]))
    it = local.to_item(f)
    assert it.title == "Rain" and it.author == "Some Artist" and it.tags == ["Ambient"]
    assert it.raw["file_tags"]["album"] == "Some Album"
    assert local.to_item(f).provider_id == it.provider_id


def test_cli_harvest_local_flags_unknown_rights_and_skips_on_rerun(tmp_path, capsys):
    root = album(tmp_path)
    data = tmp_path / "data"
    args = ["--root", str(data), "harvest", "local", str(root)]
    assert cli.main(args) == 0
    counts = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert counts["seen"] == 3 and counts["errors"] == 0 and counts["samples"] >= 2

    store = Store(str(data))
    sidecars = [store.read_json(k) for k in store.glob("samples/*/*.json")]
    assert all(s["licence"]["id"] == "local-unknown" for s in sidecars)
    assert all(s["licence"]["decision"] == "flag" for s in sidecars)
    rain = [s for s in sidecars if s["source"]["title"] == "Rain"]
    assert rain and rain[0]["source"]["file_tags"]["album"] == "Some Album"
    assert rain[0]["audio"]["subtype"] == "PCM_24"  # lossless source keeps 24-bit
    assert "licensed" not in rain[0]["source"]["attribution"]

    assert cli.main(["--root", str(data), "list", "--commercial"]) == 0
    assert capsys.readouterr().out == ""

    assert cli.main(args) == 0
    again = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert again["skipped_existing"] == 3 and again["samples"] == 0


def test_licence_option_applies_to_local_files(tmp_path):
    root = album(tmp_path)
    store = Store(str(tmp_path / "data"))
    items = [
        local.to_item(f, "https://creativecommons.org/publicdomain/zero/1.0/")
        for f in local.iter_audio_files([root])
    ]
    harvest(items, lambda it: it.audio_url, store, Settings(), analysed_from="original")
    index.rebuild(store)
    rows = index.load(store)
    assert rows and all(
        r["licence"] == "CC0-1.0" and r["licence_decision"] == "allow" for r in rows
    )


def test_long_files_are_processed_in_chunks(tmp_path):
    y = np.concatenate(
        [synth.hum(15)[0], synth.rain(15)[0], synth.hum(15)[0], synth.crackle_drone(15)[0]]
    )[None]
    path = tmp_path / "long.wav"
    write(path, y)
    settings = Settings(analysis=AnalysisSettings(sample_rate=synth.SR, chunk_s=30.0))
    store = Store(str(tmp_path / "data"))
    out = process_audio(str(path), store, settings, {"tags": []}, None, "original")
    starts = sorted(s["region"]["start_s"] for s in out)
    assert any(15 <= t < 30 for t in starts), starts  # rain, first chunk
    assert any(t >= 45 for t in starts), starts  # crackle, second chunk, absolute time


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_m4a_decodes_via_ffmpeg_in_chunks(tmp_path):
    wav = tmp_path / "in.wav"
    write(wav, synth.rain(10))
    m4a = tmp_path / "in.m4a"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-c:a", "aac", str(m4a)], check=True)
    assert abs(audio_duration(str(m4a)) - 10.0) < 0.1
    y, sr = load_audio(str(m4a), synth.SR, offset=2.0, duration=3.0)
    assert sr == synth.SR and abs(y.shape[1] / sr - 3.0) < 0.05
