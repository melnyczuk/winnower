import synth
from fakes import FakeResponse, FakeSession, fixture, no_wait
from test_pipeline import SETTINGS, ogg_bytes

from winnower.config import LicencePolicy
from winnower.licences import decide
from winnower.pipeline import harvest
from winnower.sources.wikimedia import WikimediaCommons
from winnower.storage import Store

PAGE1 = fixture("wikimedia/search_page1.json")
PAGE2 = fixture("wikimedia/search_page2.json")
LAST = PAGE2 | {"continue": None}


def test_search_follows_continue_and_maps_fields():
    session = FakeSession([PAGE1, LAST])
    wm = WikimediaCommons(session=session, limiter=no_wait())
    items = list(wm.search("thunder", limit=10, max_duration=10_000))
    assert len(items) == 6 and len(session.calls) == 2
    p = session.calls[0][1]
    assert p["gsrsearch"] == "thunder filetype:audio" and p["gsrnamespace"] == 6
    assert "gsroffset" not in p and session.calls[1][1]["gsroffset"] == 3

    assert [i.title for i in items[:3]] == [
        "Thunder 01",
        "Thunder",
        "Nosferatu thunderclap - Richard Humphries",
    ]  # search ranking (index), not response order
    thunder = items[1]
    assert thunder.provider == "wikimedia" and thunder.provider_id.isdigit()
    assert thunder.title == "Thunder" and thunder.author == "Subhashish Panigrahi"
    assert thunder.audio_url == "https://upload.wikimedia.org/wikipedia/commons/1/1b/Thunder.wav"
    assert thunder.page_url == "https://commons.wikimedia.org/wiki/File:Thunder.wav"
    assert thunder.licence_raw == "https://creativecommons.org/licenses/by-sa/4.0"
    assert thunder.analysed_from == "original" and thunder.lossy is False
    assert abs(thunder.duration_s - 35.06) < 0.01
    assert thunder.tags == ["Audio files from Karnataka", "Audio files of thunder"]
    assert items[0].author == "Amuzujoe" and items[0].lossy is True  # Artist was a link

    assert [i.title for i in items[3:]] == ["Jer-Apollon", "En-us-apollo", "Ia-apollon"]
    pd = items[3]
    assert pd.licence_raw == "Public domain" and pd.description.startswith("Prononciâtion")
    assert "<a" not in pd.description


def test_licence_decisions_and_duration_filter():
    wm = WikimediaCommons(session=FakeSession([PAGE1, LAST]), limiter=no_wait())
    items = list(wm.search("x", max_duration=600))
    assert "Ia-apollon" not in [i.title for i in items]  # 834 s
    p = LicencePolicy()
    assert [decide(i.licence_raw, p).decision for i in items] == [
        "flag",
        "flag",
        "allow",
        "allow",
        "flag",
    ]


def test_limit_stops_without_fetching_more():
    session = FakeSession([PAGE1, LAST])
    wm = WikimediaCommons(session=session, limiter=no_wait())
    assert len(list(wm.search("x", limit=2))) == 2 and len(session.calls) == 1


def test_end_to_end_harvest_from_wikimedia_item(tmp_path):
    page = next(p for p in PAGE2["query"]["pages"] if p["pageid"] == 3083459)
    item = WikimediaCommons.to_item(page)
    wm = WikimediaCommons(session=FakeSession([FakeResponse(ogg_bytes(synth.rain(12)))]))
    store = Store(str(tmp_path))
    assert harvest([item], wm.fetch_audio, store, SETTINGS)["samples"] >= 1
    sc = store.read_json(next(store.glob("samples/*/*.json")))
    assert sc["licence"]["id"] == "PDM" and sc["licence"]["decision"] == "allow"
    assert sc["audio"]["analysed_from"] == "original" and sc["audio"]["subtype"] == "PCM_16"
    assert sc["source"]["attribution"] == (
        '"Jer-Apollon" by Man vyi (https://commons.wikimedia.org/wiki/File:Jer-Apollon.ogg) '
        "licensed PDM"
    )
