import synth
from fakes import FakeResponse, FakeSession, fixture, no_wait
from test_pipeline import SETTINGS, ogg_bytes

from winnower.licences import normalise
from winnower.pipeline import harvest
from winnower.sources.internet_archive import (
    InternetArchive,
    choose,
    parse_length,
    tracks,
)
from winnower.storage import Store

SEARCH = fixture("archive/search.json")
APOREE = fixture("archive/metadata_aporee_73542_85947.json")
DARSHAN = fixture("archive/metadata_fabiokeiner-darshan.json")
META = "https://archive.org/metadata/"


def routes(search=SEARCH):
    docs = search["response"]["docs"]
    r = {"https://archive.org/advancedsearch.php": search}
    for d in docs:
        r[META + d["identifier"]] = APOREE | {
            "metadata": APOREE["metadata"] | {"identifier": d["identifier"]}
        }
    return r


def test_parse_length():
    assert parse_length("132") == 132.0
    assert parse_length("2306.88") == 2306.88
    assert parse_length("02:12") == 132.0
    assert parse_length("1:02:03") == 3723.0
    assert parse_length(None) is None and parse_length("n/a") is None


def test_tracks_group_original_with_derivatives():
    groups = tracks(APOREE["files"])
    assert list(groups) == ["AMBNatrFieldAtNightLapwingCallWarmAir.wav"]
    names = sorted(f["name"].rsplit(".", 1)[1] for f in groups[next(iter(groups))])
    assert names == ["flac", "mp3", "wav"]  # not the png, afpk or metadata


def test_choose_prefers_lossless_original_then_falls_back_by_size():
    group = next(iter(tracks(APOREE["files"]).values()))
    f, origin = choose(group)
    assert f["name"].endswith(".wav") and origin == "original"
    f, origin = choose(group, max_bytes=30_000_000)  # WAV 38 MB too big: FLAC derivative
    assert f["name"].endswith(".flac") and origin == "original"
    f, origin = choose(group, max_bytes=5_000_000)  # only the MP3 fits
    assert f["name"].endswith(".mp3") and origin == "preview"
    assert choose(group, max_bytes=1000) is None

    darshan = next(iter(tracks(DARSHAN["files"]).values()))
    f, origin = choose(darshan)  # 610 MB WAV, 331 MB FLAC: over the 200 MB cap
    assert f["name"] == "Darshan.mp3" and origin == "preview"


def test_lossy_original_upload_counts_as_original():
    files = [
        {"name": "a.mp3", "source": "original", "size": "100"},
        {"name": "a.ogg", "source": "derivative", "original": "a.mp3", "size": "90"},
    ]
    f, origin = choose(tracks(files)["a.mp3"])
    assert f["name"] == "a.mp3" and origin == "original"


def test_search_maps_fields_and_stops_at_limit():
    session = FakeSession(routes())
    ia = InternetArchive(session=session, limiter=no_wait())
    items = list(ia.search("field recording", limit=2, collection="radio-aporee-maps"))
    assert len(items) == 2
    q = session.calls[0][1]["q"]
    assert q == (
        "(field recording) AND mediatype:audio AND collection:(radio-aporee-maps) AND licenseurl:*"
    )
    assert len(session.calls) == 3  # search + two metadata lookups, no third item fetched
    it = items[0]
    assert it.provider == "archive"
    assert it.provider_id == "aporee_73231_85530/AMBNatrFieldAtNightLapwingCallWarmAir.wav"
    assert it.audio_url == (
        "https://archive.org/download/aporee_73231_85530/AMBNatrFieldAtNightLapwingCallWarmAir.wav"
    )
    assert it.page_url == "https://archive.org/details/aporee_73231_85530"
    assert it.title.endswith("lapwing calling at the night field")
    assert it.author == "Pasha Lens" and "field recording" in it.tags
    assert it.duration_s == 132.0 and it.analysed_from == "original" and it.lossy is False
    assert normalise(it.licence_raw) == "PDM"
    assert "<br" not in it.description and "ZOOM H5" in it.description


def test_max_duration_skips_long_tracks():
    ia = InternetArchive(session=FakeSession([]), limiter=no_wait())
    doc = {"identifier": "fabiokeiner-darshan"}
    assert list(ia.items(doc, DARSHAN, max_duration=600)) == []
    (it,) = ia.items(doc, DARSHAN, max_duration=3600)
    assert it.analysed_from == "preview" and it.lossy is True
    assert normalise(it.licence_raw) == "CC-BY-NC-SA-4.0"


def test_allow_unknown_drops_licence_filter_and_marks_items():
    session = FakeSession(routes())
    ia = InternetArchive(session=session, limiter=no_wait(), allow_unknown=True)
    items = list(ia.search("drone", limit=1))
    assert "licenseurl" not in session.calls[0][1]["q"]
    assert items[0].allow_unknown


def test_pagination_stops_when_results_run_out():
    empty = {"response": {"numFound": 0, "docs": []}}
    ia = InternetArchive(session=FakeSession([empty]), limiter=no_wait())
    assert list(ia.search("nothing")) == []


def test_end_to_end_harvest_from_archive_item(tmp_path):
    ia = InternetArchive(session=FakeSession([]), limiter=no_wait())
    (item,) = ia.items({"identifier": "x"}, APOREE)
    ia.http = FakeSession([FakeResponse(ogg_bytes(synth.crackle_drone(12)))])
    store = Store(str(tmp_path))
    counts = harvest([item], ia.fetch_audio, store, SETTINGS)
    assert counts["samples"] >= 1
    sc = store.read_json(next(store.glob("samples/*/*.json")))
    assert sc["audio"]["analysed_from"] == "original"
    assert sc["licence"] == {
        "id": "PDM",
        "url": "https://creativecommons.org/publicdomain/mark/1.0/",
        "decision": "allow",
        "reason": "allowed by policy",
    }
    assert sc["source"]["attribution"].endswith("licensed PDM")
    assert store.exists(f"sources/archive/{item.provider_id}/meta.json")
