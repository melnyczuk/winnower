import synth
from fakes import FakeResponse, FakeSession, fixture, no_wait
from test_pipeline import SETTINGS, ogg_bytes

from winnower.config import LicencePolicy
from winnower.pipeline import harvest
from winnower.sources.nasa import NasaMedia, choose
from winnower.storage import Store

SEARCH = fixture("nasa/search.json")
WAV = fixture("nasa/manifest_wav.json")
WAV2 = fixture("nasa/manifest_wav2.json")
MP3 = fixture("nasa/manifest_mp3.json")
API = "https://images-api.nasa.gov/search"


def test_choose_prefers_original_wav_then_original_mp3_then_transcode():
    url, origin = choose(WAV)
    assert url.endswith("~orig.wav") and url.startswith("https://") and origin == "original"
    url, origin = choose(MP3)
    assert url.endswith("~orig.mp3") and origin == "original"
    url, origin = choose([u for u in MP3 if "~orig" not in u])
    assert url.endswith("~128k.mp3") and origin == "preview"
    assert choose(["https://x/metadata.json"]) is None


def test_search_fetches_manifests_and_maps_fields():
    hrefs = [h["href"] for h in SEARCH["collection"]["items"]]
    last = SEARCH | {"collection": SEARCH["collection"] | {"links": []}}
    session = FakeSession([SEARCH, WAV, WAV2, MP3, last])
    nasa = NasaMedia(session=session, limiter=no_wait())
    items = list(nasa.search("sounds", limit=3))
    assert [c[0] for c in session.calls] == [API, *hrefs]
    assert session.calls[0][1] == {"q": "sounds", "media_type": "audio", "page_size": 50, "page": 1}

    first, _, podcast = items
    assert first.provider == "nasa"
    assert first.provider_id.startswith("KSC-20250226-AU-ILW01-0004")
    assert first.title.startswith("SpaceX CLPS IM-2") and first.author == "NASA/Isaac Watson"
    assert first.page_url == f"https://images.nasa.gov/details/{first.provider_id}"
    assert first.licence_raw == "NASA-Media" and "KSC" in first.tags
    assert first.analysed_from == "original" and first.lossy is False
    assert podcast.author == "NASA JSC" and podcast.lossy is True


def test_search_follows_next_link_until_limit():
    page = SEARCH["collection"]
    one = {"collection": page | {"items": page["items"][:1]}}
    session = FakeSession([one, WAV, one | {"collection": one["collection"] | {"links": []}}, WAV])
    items = list(NasaMedia(session=session, limiter=no_wait()).search("x", limit=5))
    assert len(items) == 2 and session.calls[2][1]["page"] == 2


def test_end_to_end_harvest_from_nasa_item_is_flagged(tmp_path):
    item = NasaMedia.to_item(SEARCH["collection"]["items"][0], WAV)
    nasa = NasaMedia(session=FakeSession([FakeResponse(ogg_bytes(synth.rain(12)))]))
    store = Store(str(tmp_path))
    assert harvest([item], nasa.fetch_audio, store, SETTINGS)["samples"] >= 1
    sc = store.read_json(next(store.glob("samples/*/*.json")))
    assert sc["licence"]["id"] == "NASA-Media" and sc["licence"]["decision"] == "flag"
    assert "government" in sc["licence"]["reason"]
    assert sc["audio"]["analysed_from"] == "original"

    allow = SETTINGS.__class__(
        analysis=SETTINGS.analysis,
        licences=LicencePolicy(allow=("NASA-Media",), flag=()),
    )
    other = Store(str(tmp_path / "b"))
    nasa.http = FakeSession([FakeResponse(ogg_bytes(synth.rain(12)))])
    harvest([item], nasa.fetch_audio, other, allow)
    assert other.read_json(next(other.glob("samples/*/*.json")))["licence"]["decision"] == "allow"
