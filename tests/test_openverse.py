import synth
from fakes import FakeResponse, FakeSession, fixture, no_wait
from test_pipeline import SETTINGS, ogg_bytes

from winnower.config import LicencePolicy
from winnower.licences import decide, normalise
from winnower.pipeline import harvest
from winnower.sources.openverse import Openverse
from winnower.storage import Store

PAGES = [fixture("openverse/search_page1.json"), fixture("openverse/search_page2.json")]


def test_search_paginates_skips_freesound_and_maps_fields():
    session = FakeSession(PAGES)
    ov = Openverse(session=session, limiter=no_wait())
    items = list(ov.search("rain", limit=10))
    assert [i.title for i in items] == [
        "Wish You Were Here",
        "File:Rain on a tin roof.wav",
        "Thunder",
    ]
    assert session.calls[0][1] == {"q": "rain", "page_size": 20, "page": 1}
    assert session.calls[1][1]["page"] == 2 and len(session.calls) == 2
    assert session.calls[0][2] is None  # anonymous: no Authorization header

    jam, wm, pdm = items
    assert jam.provider == "openverse" and jam.provider_id == "8624ba61-57f1-4f98-8a85-ece206c319cf"
    assert jam.audio_url == "https://mp3d.jamendo.com/download/track/1214935/mp32"
    assert jam.page_url == "https://www.jamendo.com/track/1214935"
    assert jam.duration_s == 270.0 and jam.tags == ["vocal", "guitar"]
    assert jam.analysed_from == "original" and jam.lossy is True
    assert jam.raw["source"] == "jamendo"
    assert normalise(jam.licence_raw) == "CC-BY-NC-SA-3.0"
    assert wm.licence_raw == "CC BY-SA 2.0" and wm.lossy is False  # no license_url: short name
    assert normalise(pdm.licence_raw) == "PDM" and pdm.lossy is False


def test_search_stops_at_limit_and_honours_options():
    session = FakeSession(PAGES)
    ov = Openverse(token="t", session=session, limiter=no_wait(), include_freesound=True)
    items = list(ov.search("rain", limit=2, licence="by,cc0", licence_type="commercial"))
    assert [i.raw["source"] for i in items] == ["jamendo", "freesound"]
    assert len(session.calls) == 1
    url, params, headers = session.calls[0]
    assert url == "https://api.openverse.org/v1/audio/"
    assert params["license"] == "by,cc0" and params["license_type"] == "commercial"
    assert params["page_size"] == 50 and headers == {"Authorization": "Bearer t"}


def test_max_duration_filters_client_side():
    ov = Openverse(session=FakeSession(PAGES), limiter=no_wait())
    assert [i.title for i in ov.search("rain", max_duration=60)] == [
        "File:Rain on a tin roof.wav",
        "Thunder",  # no duration given: kept
    ]


def test_licence_decisions_for_openverse_items():
    p = LicencePolicy()
    ov = Openverse(session=FakeSession(PAGES), limiter=no_wait())
    decisions = [decide(i.licence_raw, p).decision for i in ov.search("rain")]
    assert decisions == ["flag", "flag", "allow"]


def test_end_to_end_harvest_from_openverse_item(tmp_path):
    hit = fixture("openverse/search_page2.json")["results"][0]
    item = Openverse.to_item(hit)
    audio = synth.rain(12)
    ov = Openverse(session=FakeSession([FakeResponse(ogg_bytes(audio))]), limiter=no_wait())
    store = Store(str(tmp_path))
    counts = harvest([item], ov.fetch_audio, store, SETTINGS)
    assert counts["samples"] >= 1 and counts["errors"] == 0
    sc = store.read_json(next(store.glob("samples/*/*.json")))
    assert sc["audio"]["analysed_from"] == "original" and sc["audio"]["subtype"] == "PCM_24"
    assert sc["licence"]["id"] == "PDM" and sc["licence"]["decision"] == "allow"
    assert sc["source"]["attribution"] == (
        '"Thunder" by Someone (https://commons.wikimedia.org/w/index.php?curid=3536953) '
        "licensed PDM"
    )
    assert harvest([item], ov.fetch_audio, store, SETTINGS)["skipped_existing"] == 1


def test_cli_stops_cleanly_when_search_fails(tmp_path, capsys, monkeypatch):
    import json

    import requests

    from winnower import cli

    def failing(self, *a, **kw):
        raise requests.HTTPError("403 Client Error: Forbidden")
        yield

    monkeypatch.setattr(Openverse, "search", failing)
    assert cli.main(["--root", str(tmp_path), "harvest", "openverse", "rain"]) == 1
    out = capsys.readouterr()
    assert "Stopped early: search failed: 403" in out.err and "Traceback" not in out.err
    assert json.loads(out.out)["seen"] == 0
