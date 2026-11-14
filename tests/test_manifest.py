import json

import numpy as np
import soundfile as sf
import synth
from fakes import FakeResponse, FakeSession, no_wait

from winnower import cli
from winnower.sources.manifest import Manifest, iter_manifest
from winnower.storage import Store


def write_dataset(tmp_path):
    audio = tmp_path / "ds" / "audio"
    audio.mkdir(parents=True)
    sf.write(audio / "1.wav", synth.rain(12).T, synth.SR)
    sf.write(audio / "2.wav", synth.crackle_drone(12).T, synth.SR)
    sf.write(audio / "3.wav", np.concatenate([synth.rain(6), synth.hum(6)], axis=1).T, synth.SR)
    csv = tmp_path / "ds" / "FSD50K eval.csv"
    csv.write_text(
        "id,path,licence,title,author,tags,duration\n"
        '1,audio/1.wav,http://creativecommons.org/licenses/by/3.0/,Rain,a,"rain,water",12\n'
        "2,audio/2.wav,https://creativecommons.org/licenses/by-nd/4.0/,Crackle,b,fire,12\n"
        "3,audio/3.wav,,Mystery,c,,12\n"
        ",,,empty row,,,\n"
    )
    return csv


def test_csv_rows_map_to_items(tmp_path):
    csv = write_dataset(tmp_path)
    items = list(iter_manifest(csv))
    assert [i.provider_id for i in items] == ["1", "2", "3"]
    rain = items[0]
    assert rain.provider == "fsd50k-eval"
    assert rain.audio_url == str((tmp_path / "ds/audio/1.wav").resolve())
    assert rain.raw["path"] == rain.audio_url
    assert rain.tags == ["rain", "water"] and rain.duration_s == 12.0
    assert rain.analysed_from == "original" and rain.lossy is False
    assert items[2].licence_raw is None and not items[2].allow_unknown
    assert len(list(iter_manifest(csv, provider="fsd"))) == 3
    assert next(iter_manifest(csv, provider="fsd")).provider == "fsd"


def test_jsonl_urls_hash_ids_and_download(tmp_path):
    jl = tmp_path / "clips.jsonl"
    rows = [
        {"url": "https://example.org/a.ogg", "license": "CC0", "tags": ["x", "y"]},
        {"url": "https://example.org/b.flac", "licence": "CC BY 4.0", "duration": 900},
    ]
    jl.write_text("\n".join(json.dumps(r) for r in rows) + "\n\n")
    session = FakeSession([FakeResponse(b"OggS...")])
    m = Manifest(jl, session=session, limiter=no_wait())
    items = list(m.search())
    assert len(items) == 1  # b is over the default 600 s max duration
    a = items[0]
    assert len(a.provider_id) == 16 and a.tags == ["x", "y"] and a.lossy is True
    assert a.title == "a" and a.licence_raw == "CC0"
    assert m.fetch_audio(a) == b"OggS..." and session.calls[0][0] == "https://example.org/a.ogg"
    assert list(m.search(max_duration=1000))[1].provider_id != a.provider_id


def test_cli_harvest_manifest_gate_and_unlicensed(tmp_path, capsys):
    csv = write_dataset(tmp_path)
    data = tmp_path / "data"
    assert cli.main(["--root", str(data), "harvest", "manifest", str(csv)]) == 0
    counts = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert counts["seen"] == 3 and counts["denied"] == 2 and counts["samples"] >= 1

    store = Store(str(data))
    assert store.read_json("sources/fsd50k-eval/2/meta.json")["status"] == "denied"
    lic = {store.read_json(k)["licence"]["id"] for k in store.glob("samples/*/*.json")}
    assert lic == {"CC-BY-3.0"}

    # --include-unlicensed keeps the unlicensed row on a fresh store; ND stays denied.
    other = tmp_path / "other"
    args = ["--root", str(other), "harvest", "manifest", str(csv), "--include-unlicensed"]
    assert cli.main(args) == 0
    counts = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert counts["denied"] == 1
    store = Store(str(other))
    meta = store.read_json("sources/fsd50k-eval/3/meta.json")
    assert meta["status"] == "done"
    for k in store.glob("samples/*/*.json"):
        sc = store.read_json(k)
        if sc["source"]["title"] == "Mystery":
            assert sc["licence"]["id"] == "web-unknown"

    assert cli.main(["--root", str(other), "list", "--commercial", "--json"]) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert rows and all(r["licence"] == "CC-BY-3.0" for r in rows)

    assert cli.main(args) == 0
    again = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert again["skipped_existing"] == 3
