import synth
from fakes import FakeResponse, FakeSession, fixture, no_wait
from test_pipeline import SETTINGS, ogg_bytes

from winnower.licences import normalise
from winnower.pipeline import harvest
from winnower.sources.web import PageParser, WebPages
from winnower.storage import Store

SITE = "https://label.example"
RELEASE = f"{SITE}/releases/dust/"


def routes(**extra):
    r = {
        f"{SITE}/robots.txt": fixture("web/robots.txt"),
        RELEASE: fixture("web/release.html"),
        f"{SITE}/releases/dust/notes.html": fixture("web/notes.html"),
        f"{SITE}/releases/dust/deeper.html": fixture("web/footer_only.html"),
        f"{SITE}/private/outtakes.html": fixture("web/unlicensed.html"),
        f"{SITE}/style.css": FakeResponse("body{}", headers={"Content-Type": "text/css"}),
    }
    return r | extra


def web(**kw):
    session = FakeSession(routes())
    return WebPages(session=session, limiter=no_wait, **kw), session


def parse(name):
    p = PageParser()
    p.feed(fixture(name))
    return p


def test_licence_detection_order():
    assert parse("web/release.html").licence() == (
        "https://creativecommons.org/licenses/by-sa/4.0/",
        "rel",
    )
    assert parse("web/meta_only.html").licence() == (
        "https://creativecommons.org/licenses/by-nc/4.0/",
        "meta",
    )
    assert parse("web/notes.html").licence() == (
        "https://creativecommons.org/publicdomain/zero/1.0/",
        "jsonld",
    )
    assert parse("web/footer_only.html").licence() == (
        "https://creativecommons.org/licenses/by/3.0/",
        "cc-link",
    )
    assert parse("web/unlicensed.html").licence() == (None, None)


def test_seed_page_audio_links_with_robots_and_dedup():
    w, session = web()
    items = list(w.search([RELEASE]))
    urls = [i.audio_url for i in items]
    assert urls == [
        f"{SITE}/releases/dust/audio/01-gravel.ogg",
        # 02-wind.flac is disallowed by robots.txt; the MP3 isn't
        f"{SITE}/releases/dust/audio/02-wind.mp3",
        f"{SITE}/releases/dust/audio/03-pylons.wav",  # the ?dl=1 duplicate is dropped
        "https://elsewhere.example/other.mp3",  # robots.txt there is a 404: allowed
    ]
    gravel, wind, pylons, _ = items
    assert pylons.title == "Pylons (WAV)" and gravel.title == "01-gravel"
    assert gravel.author == "Field Unit" and gravel.page_url == RELEASE
    assert all(normalise(i.licence_raw) == "CC-BY-SA-4.0" for i in items)
    assert gravel.raw == {"page_title": "Dust Tapes — Rust Belt Netlabel", "licence_source": "rel"}
    assert gravel.lossy is True and pylons.lossy is False and pylons.analysed_from == "original"
    assert len(gravel.provider_id) == 16 and gravel.provider == "web"
    # depth 0: no other pages fetched
    pages = [c[0] for c in session.calls if not c[0].endswith("robots.txt")]
    assert pages == [RELEASE]


def test_depth_follows_same_host_links_and_respects_robots():
    w, session = web(depth=2)
    items = list(w.search([RELEASE], limit=50))
    fetched = [c[0] for c in session.calls]
    assert f"{SITE}/releases/dust/notes.html" in fetched
    assert f"{SITE}/releases/dust/deeper.html" in fetched
    assert f"{SITE}/private/outtakes.html" not in fetched  # robots.txt
    assert "https://elsewhere.example/page.html" not in fetched  # other host
    by_url = {i.audio_url: i for i in items}
    bonus = by_url[f"{SITE}/releases/dust/audio/bonus.mp3"]
    assert normalise(bonus.licence_raw) == "CC0-1.0" and bonus.raw["licence_source"] == "jsonld"
    loop = by_url[f"{SITE}/releases/dust/loop.aiff"]
    assert normalise(loop.licence_raw) == "CC-BY-3.0"


def test_max_pages_and_limit():
    w, session = web(depth=2, max_pages=1)
    assert len(list(w.search([RELEASE], limit=50))) == 4
    w, _ = web()
    assert len(list(w.search([RELEASE], limit=2))) == 2


def test_disallowed_seed_and_licence_override():
    w, _ = web()
    assert list(w.search([f"{SITE}/private/outtakes.html"])) == []
    w, _ = web(licence="CC0")
    items = list(w.search([RELEASE]))
    assert {i.licence_raw for i in items} == {"CC0"}
    assert items[0].raw["licence_source"] == "cli"


def test_unlicensed_page_kept_only_when_allowed(tmp_path):
    page = f"{SITE}/mine.html"
    audio = ogg_bytes(synth.rain(12))
    for allow, expected in ((False, "denied"), (True, "done")):
        session = FakeSession(
            routes(**{page: fixture("web/unlicensed.html"), f"{SITE}/files/creek.wav": audio})
        )
        w = WebPages(session=session, limiter=no_wait, allow_unknown=allow)
        store = Store(str(tmp_path / str(allow)))
        (item,) = w.search([page])
        assert item.licence_raw is None
        harvest([item], w.fetch_audio, store, SETTINGS)
        meta = store.read_json(f"sources/web/{item.provider_id}/meta.json")
        assert meta["status"] == expected
    sc = store.read_json(next(store.glob("samples/*/*.json")))
    assert sc["licence"]["id"] == "web-unknown" and sc["licence"]["decision"] == "flag"
    assert sc["source"]["attribution"] == f'"Creek" ({page})'


def test_robots_forbidden_means_disallow_all():
    session = FakeSession({f"{SITE}/robots.txt": FakeResponse("", status=403)})
    w = WebPages(session=session, limiter=no_wait)
    assert not w.allowed(RELEASE)
