import synth
from fakes import FakeResponse, FakeSession, fixture, no_wait
from test_pipeline import SETTINGS, ogg_bytes

from winnower.licences import decide
from winnower.pipeline import harvest
from winnower.sources.ccmixter import CcMixter, choose
from winnower.storage import Store

UPLOADS = fixture("ccmixter/query.json")


def test_choose_prefers_lossless_under_cap():
    files = UPLOADS[0]["files"]
    assert choose(files)["file_name"].endswith(".flac")
    assert choose(files, max_bytes=10_000_000)["file_name"].endswith(".mp3")
    stems = {"file_name": "stems.zip", "file_format_info": {"media-type": "archive"}}
    assert choose([stems]) is None


def test_search_paginates_by_offset_and_maps_fields():
    session = FakeSession([UPLOADS, UPLOADS[:1]])
    cc = CcMixter(session=session, limiter=no_wait())
    items = list(cc.search("ambient", tags="chill", page_size=2))
    assert len(items) == 3
    assert session.calls[0][1] == {
        "f": "json",
        "limit": 2,
        "search": "ambient",
        "tags": "chill",
        "offset": 0,
    }
    assert session.calls[1][1]["offset"] == 2 and len(session.calls) == 2

    it = items[0]
    assert it.provider == "ccmixter" and it.provider_id == "71335"
    assert it.title == "SQUARE PEGS in CIRCLES" and it.author == "Stefan Kartenberg"
    assert it.page_url == "https://ccmixter.org/files/JeffSpeed68/71335"
    assert it.audio_url.endswith(".flac") and it.lossy is False
    assert it.analysed_from == "original" and it.duration_s == 251.0
    assert "ambient" in it.tags and "mp3" not in it.tags
    assert decide(it.licence_raw, SETTINGS.licences).id == "CC-BY-NC-4.0"
    assert decide(it.licence_raw, SETTINGS.licences).decision == "flag"


def test_limit_and_max_duration():
    session = FakeSession([UPLOADS])
    cc = CcMixter(session=session, limiter=no_wait())
    assert len(list(cc.search("x", limit=1))) == 1
    cc = CcMixter(session=FakeSession([UPLOADS]), limiter=no_wait())
    assert list(cc.search("x", max_duration=200)) == []  # both are about 4 minutes


def test_end_to_end_harvest_from_ccmixter_item(tmp_path):
    item = CcMixter.to_item(UPLOADS[1])
    cc = CcMixter(session=FakeSession([FakeResponse(ogg_bytes(synth.crackle_drone(12)))]))
    store = Store(str(tmp_path))
    assert harvest([item], cc.fetch_audio, store, SETTINGS)["samples"] >= 1
    sc = store.read_json(next(store.glob("samples/*/*.json")))
    assert sc["licence"]["decision"] == "flag" and sc["audio"]["analysed_from"] == "original"
    assert sc["source"]["attribution"] == (
        '"Living in the past" by Stefan Kartenberg '
        "(https://ccmixter.org/files/JeffSpeed68/71327) licensed CC-BY-NC-4.0"
    )
