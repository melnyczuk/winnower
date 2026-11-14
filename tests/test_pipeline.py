import io
import json

import numpy as np
import pytest
import soundfile as sf
import synth
from fakes import FakeResponse, FakeSession
from mutagen.flac import FLAC

from winnower import index
from winnower.config import AnalysisSettings, Settings
from winnower.pipeline import harvest
from winnower.sources.base import (
    DailyQuotaExceeded,
    RateLimiter,
    SourceItem,
    TooLarge,
    download,
)
from winnower.sources.freesound import Freesound
from winnower.storage import Store


def ogg_bytes(y):
    buf = io.BytesIO()
    sf.write(buf, y.T, synth.SR, format="OGG", subtype="VORBIS")
    return buf.getvalue()


def item(pid, licence):
    return SourceItem(
        "freesound",
        pid,
        f"t{pid}",
        "someone",
        f"https://freesound.org/s/{pid}/",
        licence,
        f"https://cdn.example/{pid}.ogg",
        tags=["field-recording"],
    )


SETTINGS = Settings(analysis=AnalysisSettings(sample_rate=synth.SR))


def test_harvest_writes_samples_sidecars_and_index(tmp_path):
    store = Store(str(tmp_path))
    audio = {
        "1": ogg_bytes(np.concatenate([synth.rain(12)[0], synth.crackle_drone(12)[0]])[None]),
        "2": ogg_bytes(synth.hum(12)),
        "3": ogg_bytes(synth.rain(12)),
    }
    items = [
        item("1", "https://creativecommons.org/licenses/by/4.0/"),
        item("2", "http://creativecommons.org/publicdomain/zero/1.0/"),
        item("3", "https://creativecommons.org/licenses/by-nd/4.0/"),
    ]
    counts = harvest(items, lambda it: audio[it.provider_id], store, SETTINGS)
    assert counts["seen"] == 3 and counts["denied"] == 1 and counts["errors"] == 0
    assert counts["samples"] >= 1
    assert counts["no_regions"] == 1  # the hum

    sidecars = [store.read_json(k) for k in store.glob("samples/*/*.json")]
    assert len(sidecars) == counts["samples"]
    sc = sidecars[0]
    assert sc["licence"]["id"] == "CC-BY-4.0"
    assert sc["source"]["attribution"].startswith('"t1" by someone')
    assert sc["audio"]["analysed_from"] == "preview"
    assert sc["scores"]["interest"] >= SETTINGS.analysis.min_interest
    assert store.exists(sc["file"])
    y, sr = sf.read(io.BytesIO(store.read_bytes(sc["file"])))
    assert sr == synth.SR and abs(len(y) / sr - sc["audio"]["duration_s"]) < 0.01
    region = sc["region"]
    assert len(y) == round(region["beats"] * 60 / region["bpm"] * sr)
    assert sc["audio"]["duration_s"] >= 2.0
    assert sc["file"].endswith(f"_{sc['id'][:6]}.flac")
    assert sc["file"].split("/")[1] == (
        f"{sc['key']['camelot']} {sc['key']['name']}" if sc["key"] else "no key"
    )
    tags = FLAC(io.BytesIO(store.read_bytes(sc["file"])))
    assert tags["BPM"] == [str(region["bpm"])] and tags["COPYRIGHT"][0].startswith('"t1"')

    denied = store.read_json("sources/freesound/3/meta.json")
    assert denied["status"] == "denied"

    assert index.rebuild(store) == len(sidecars)
    rows = index.load(store)
    assert {r["id"] for r in rows} == {s["id"] for s in sidecars}

    # Re-running skips sources already processed.
    again = harvest(items, lambda it: audio[it.provider_id], store, SETTINGS)
    assert again["skipped_existing"] == 3


def hit(i):
    return {
        "id": i,
        "name": f"s{i}",
        "username": "u",
        "license": "https://creativecommons.org/licenses/by/4.0/",
        "url": f"https://freesound.org/s/{i}/",
        "tags": ["drone"],
        "duration": 10.0,
        "previews": {"preview-hq-ogg": f"https://cdn.freesound.org/{i}.ogg"},
    }


def test_freesound_search_paginates_and_maps_fields():
    session = FakeSession(
        [
            {
                "results": [hit(1), hit(2), {"id": 3, "previews": {}}],
                "next": "https://freesound.org/apiv2/search/text/?page=2",
            },
            {"results": [hit(4), hit(5)], "next": None},
        ]
    )
    fs = Freesound("tok", session=session, limiter=RateLimiter(60, 2000, sleep=lambda s: None))
    items = list(fs.search("drone", limit=3))
    assert [i.provider_id for i in items] == ["1", "2", "4"]
    assert items[0].audio_url.endswith("1.ogg") and items[0].licence_raw.endswith("by/4.0/")
    assert session.calls[0][2]["Authorization"] == "Token tok"
    assert session.calls[0][1]["query"] == "drone"
    assert session.calls[1][1] is None


def test_rate_limiter_waits_and_enforces_daily_quota():
    now = [0.0]
    slept = []

    def sleep(sec):
        slept.append(sec)
        now[0] += sec

    rl = RateLimiter(per_minute=2, per_day=3, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        rl.acquire()
    assert slept and abs(slept[0] - 30.0) < 1e-6
    with pytest.raises(DailyQuotaExceeded):
        rl.acquire()


def test_storage_round_trip(tmp_path):
    store = Store(f"file://{tmp_path}/root")
    store.write_json("a/b/c.json", {"x": 1})
    assert store.read_json("a/b/c.json") == {"x": 1}
    assert list(store.glob("a/*/*.json")) == ["a/b/c.json"]
    assert json.loads((tmp_path / "root/a/b/c.json").read_text()) == {"x": 1}


def test_rate_limiter_fractional_rate_with_burst():
    now = [0.0]
    slept = []

    def sleep(sec):
        slept.append(sec)
        now[0] += sec

    rl = RateLimiter(per_minute=5 / 60, burst=5, clock=lambda: now[0], sleep=sleep)
    for _ in range(6):
        rl.acquire()
    assert len(slept) == 1 and abs(slept[0] - 720.0) < 1e-6  # one call per 12 minutes


def test_download_streams_and_enforces_cap():
    big = FakeResponse(b"x" * 1000)
    assert download(FakeSession([big]), "https://x/a.wav", max_bytes=2000) == b"x" * 1000
    with pytest.raises(TooLarge):
        download(FakeSession([FakeResponse(b"x" * 1000)]), "https://x/a.wav", max_bytes=500)
    declared = FakeResponse(b"", headers={"Content-Length": "999999"})
    with pytest.raises(TooLarge):
        download(FakeSession([declared]), "https://x/a.wav", max_bytes=500)


def test_unknown_licence_kept_and_flagged_when_allowed(tmp_path):
    store = Store(str(tmp_path))
    audio = ogg_bytes(synth.rain(12))
    keep = item("k", None)
    keep.allow_unknown = True
    drop = item("d", "All rights reserved")
    nd = item("n", "https://creativecommons.org/licenses/by-nd/4.0/")
    nd.allow_unknown = True
    counts = harvest([keep, drop, nd], lambda it: audio, store, SETTINGS)
    assert counts["denied"] == 2 and counts["samples"] >= 1
    sidecars = [store.read_json(k) for k in store.glob("samples/*/*.json")]
    assert sidecars and all(s["licence"]["id"] == "web-unknown" for s in sidecars)
    assert all(s["licence"]["decision"] == "flag" for s in sidecars)
    assert "licensed" not in sidecars[0]["source"]["attribution"]
    assert store.read_json("sources/freesound/n/meta.json")["status"] == "denied"


def test_per_item_analysed_from_and_lossy_reach_the_sidecar(tmp_path):
    store = Store(str(tmp_path))
    it = item("o", "https://creativecommons.org/licenses/by/2.5/")
    it.analysed_from, it.lossy = "original", False
    harvest([it], lambda _: ogg_bytes(synth.rain(12)), store, SETTINGS)
    sidecars = [store.read_json(k) for k in store.glob("samples/*/*.json")]
    assert sidecars
    assert sidecars[0]["audio"]["analysed_from"] == "original"
    assert sidecars[0]["audio"]["subtype"] == "PCM_24"
    assert sidecars[0]["licence"]["id"] == "CC-BY-2.5"
    assert sidecars[0]["licence"]["decision"] == "allow"


def test_chunks_cover_the_file_and_drop_a_short_tail():
    from winnower.pipeline import chunks

    assert list(chunks(25.0, 10.0)) == [(0.0, 10.0), (10.0, 10.0), (20.0, 5.0)]
    assert list(chunks(21.0, 10.0)) == [(0.0, 10.0), (10.0, 10.0)]  # 1 s tail dropped
    assert list(chunks(1.5, 10.0)) == [(0.0, 1.5)]  # a short file is still analysed
    assert list(chunks(0.0, 10.0)) == []


def test_tally_folds_outcomes_into_counts():
    from winnower.pipeline import Outcome, tally

    outcomes = [
        Outcome("done", 3),
        Outcome("no_regions"),
        Outcome("denied"),
        Outcome("skipped_existing"),
        Outcome("error"),
        Outcome("done", 2),
    ]
    assert tally(outcomes) == {
        "seen": 6,
        "denied": 1,
        "skipped_existing": 1,
        "no_regions": 1,
        "samples": 5,
        "errors": 1,
    }
    assert tally([]) == dict.fromkeys(tally([]), 0) and tally(iter(outcomes))["seen"] == 6


def test_licence_for_and_lossiness_are_pure_decisions():
    from winnower.pipeline import LOCAL_UNKNOWN, licence_for, lossiness

    it = item("x", None)
    assert licence_for(it, SETTINGS).decision == "deny"
    it.allow_unknown = True
    assert licence_for(it, SETTINGS).id == "web-unknown"
    it.provider = "local"
    assert licence_for(it, SETTINGS) is LOCAL_UNKNOWN

    assert lossiness(it, "preview") is True and lossiness(it, "original") is False
    it.lossy = True
    assert lossiness(it, "original") is True
    assert lossiness(it, "original", lossy=lambda _: False) is False
