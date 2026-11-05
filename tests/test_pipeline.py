import io
import json

import numpy as np
import pytest
import soundfile as sf
import synth

from harvester import index
from harvester.config import AnalysisSettings, Settings
from harvester.pipeline import harvest
from harvester.sources.base import DailyQuotaExceeded, RateLimiter, SourceItem
from harvester.sources.freesound import Freesound
from harvester.storage import Store


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

    denied = store.read_json("sources/freesound/3/meta.json")
    assert denied["status"] == "denied"

    assert index.rebuild(store) == len(sidecars)
    rows = index.load(store)
    assert {r["id"] for r in rows} == {s["id"] for s in sidecars}

    # Re-running skips sources already processed.
    again = harvest(items, lambda it: audio[it.provider_id], store, SETTINGS)
    assert again["skipped_existing"] == 3


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers))
        return FakeResponse(self.pages[len(self.calls) - 1])


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
