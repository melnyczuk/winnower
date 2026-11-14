"""Audio linked from ordinary web pages (netlabel release pages, artists' sites).

Give it seed page URLs. Each page is fetched (robots.txt is checked first, for pages and
for audio files), and ``<audio src>``, ``<source src>`` and ``<a href>`` links ending in
an audio extension are collected. With ``depth >= 1`` same-host links are followed, up
to ``max_pages`` pages in all.

The page's licence applies to every file found on it, detected in this order:

1. ``<a rel="license">`` or ``<link rel="license">``
2. ``<meta name="license">`` or a schema.org ``license`` in JSON-LD
3. any creativecommons.org licence link on the page

``licence`` overrides detection. Pages with no licence give items with no licence, which
the gate denies unless ``allow_unknown`` is set (then they're kept as ``web-unknown``:
all rights reserved by default). Requests are rate limited per host, one a second.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib import robotparser
from urllib.parse import urldefrag, urljoin, urlparse

import requests

from .base import RateLimiter, SourceItem, download, http_session, user_agent
from .local import AUDIO_EXTS, LOSSY_EXTS
from .spec import UNLICENSED, Harvest, Source, arg, limits, window

log = logging.getLogger(__name__)
_CC_LICENCE = ("creativecommons.org/licenses/", "creativecommons.org/publicdomain/")


def _ext(url: str) -> str:
    return PurePosixPath(urlparse(url).path).suffix.lower()


def _jsonld_licence(obj) -> str | None:
    if isinstance(obj, list):
        return next((x for x in map(_jsonld_licence, obj) if x), None)
    if not isinstance(obj, dict):
        return None
    lic = obj.get("license")
    if isinstance(lic, str):
        return lic
    if isinstance(lic, dict):
        return lic.get("@id") or lic.get("url") or lic.get("name")
    if isinstance(lic, list) and lic:
        return _jsonld_licence({"license": lic[0]})
    return next((x for x in map(_jsonld_licence, obj.values()) if x), None)


class PageParser(HTMLParser):
    """Collects audio links, page links, title, author and licence hints from one page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.audio: list[tuple[str, str | None, str]] = []  # (href, link text, tag)
        self.links: list[str] = []
        self.title = ""
        self.author: str | None = None
        self.rel_licence: str | None = None
        self.meta_licence: str | None = None
        self.jsonld_licence: str | None = None
        self.cc_link: str | None = None
        self._in: str | None = None
        self._text: list[str] = []
        self._anchor: str | None = None
        self._script: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: v or "" for k, v in attrs}
        rel = a.get("rel", "").lower().split()
        if tag == "title":
            self._in = "title"
        elif tag in ("audio", "source") and a.get("src"):
            self.audio.append((a["src"], None, tag))
        elif tag == "a" and a.get("href"):
            href = a["href"]
            if "license" in rel and not self.rel_licence:
                self.rel_licence = href
            if any(c in href for c in _CC_LICENCE) and not self.cc_link:
                self.cc_link = href
            if _ext(href) in AUDIO_EXTS:
                self._anchor, self._text = href, []
            else:
                self.links.append(href)
        elif tag == "link" and "license" in rel and a.get("href") and not self.rel_licence:
            self.rel_licence = a["href"]
        elif tag == "meta":
            name = a.get("name", "").lower()
            if name in ("license", "dc.rights", "dcterms.license") and not self.meta_licence:
                self.meta_licence = a.get("content") or None
            elif name == "author":
                self.author = a.get("content") or None
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in, self._script = "jsonld", []

    def handle_endtag(self, tag):
        if tag == "title" and self._in == "title":
            self._in = None
        elif tag == "script" and self._in == "jsonld":
            self._in = None
            try:
                found = _jsonld_licence(json.loads("".join(self._script)))
            except ValueError:
                found = None
            self.jsonld_licence = self.jsonld_licence or found
        elif tag == "a" and self._anchor:
            text = " ".join("".join(self._text).split()) or None
            self.audio.append((self._anchor, text, "a"))
            self._anchor = None

    def handle_data(self, data):
        if self._in == "title":
            self.title += data
        elif self._in == "jsonld":
            self._script.append(data)
        if self._anchor:
            self._text.append(data)

    def licence(self) -> tuple[str | None, str | None]:
        """(licence, how it was found)."""
        for how, value in (
            ("rel", self.rel_licence),
            ("meta", self.meta_licence),
            ("jsonld", self.jsonld_licence),
            ("cc-link", self.cc_link),
        ):
            if value:
                return value, how
        return None, None


class WebPages:
    provider = "web"

    def __init__(
        self,
        depth: int = 0,
        max_pages: int = 50,
        session: requests.Session | None = None,
        limiter=None,
        allow_unknown: bool = False,
        licence: str | None = None,
    ):
        self.depth = depth
        self.max_pages = max_pages
        self.http = session or http_session()
        # One limiter per host; ``limiter`` (a factory) lets tests skip the waiting.
        self._new_limiter = limiter or (lambda: RateLimiter(per_minute=60))
        self._limiters: dict[str, RateLimiter] = {}
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self.allow_unknown = allow_unknown
        self.licence = licence

    def _limit(self, url: str) -> None:
        host = urlparse(url).netloc
        if host not in self._limiters:
            self._limiters[host] = self._new_limiter()
        self._limiters[host].acquire()

    def allowed(self, url: str) -> bool:
        u = urlparse(url)
        root = f"{u.scheme}://{u.netloc}"
        rp = self._robots.get(root)
        if rp is None:
            rp = robotparser.RobotFileParser(root + "/robots.txt")
            self._limit(url)
            try:
                r = self.http.get(root + "/robots.txt", timeout=30)
                status = r.status_code
            except requests.RequestException:
                r, status = None, 404
            if status in (401, 403):
                rp.disallow_all = True
            elif status >= 400 or r is None:
                rp.allow_all = True
            else:
                rp.parse(r.text.splitlines())
            self._robots[root] = rp
        return rp.can_fetch(user_agent(), url)

    def _looks_like_audio(self, url: str) -> bool:
        """A HEAD check for plain links: ``/wiki/File:Rain.wav`` is an HTML page."""
        self._limit(url)
        try:
            r = self.http.head(url, allow_redirects=True, timeout=30)
        except requests.RequestException:
            return True  # let the download decide
        if r.status_code >= 400:
            return r.status_code in (405, 501)  # HEAD not supported
        return "html" not in r.headers.get("Content-Type", "")

    def _page(self, url: str) -> PageParser | None:
        self._limit(url)
        r = self.http.get(url, timeout=30)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "text/html")
        if "html" not in ctype:
            return None
        p = PageParser()
        p.feed(r.text)
        p.close()
        return p

    def search(
        self, seeds: list[str], limit: int = 20, max_duration: float = 600.0
    ) -> Iterator[SourceItem]:
        """Items for audio linked from ``seeds`` (and, with depth, same-host pages)."""
        queue = deque((urldefrag(s)[0], 0) for s in seeds)
        seen_pages: set[str] = set()
        seen_audio: set[str] = set()
        yielded = 0
        while queue and len(seen_pages) < self.max_pages:
            url, d = queue.popleft()
            if url in seen_pages:
                continue
            seen_pages.add(url)
            if not self.allowed(url):
                log.info("robots.txt disallows %s", url)
                continue
            try:
                page = self._page(url)
            except requests.RequestException as e:
                if d == 0:
                    raise
                log.warning("skipped %s: %s", url, e)
                continue
            if page is None:
                continue
            for item in self.items(url, page):
                key = urlparse(item.audio_url)._replace(query="", fragment="").geturl()
                if key in seen_audio or not self.allowed(item.audio_url):
                    continue
                if item.raw["tag"] == "a" and not self._looks_like_audio(item.audio_url):
                    continue
                seen_audio.add(key)
                yield item
                yielded += 1
                if yielded >= limit:
                    return
            if d < self.depth:
                host = urlparse(url).netloc
                for href in page.links:
                    nxt = urldefrag(urljoin(url, href))[0]
                    u = urlparse(nxt)
                    if u.scheme in ("http", "https") and u.netloc == host:
                        queue.append((nxt, d + 1))

    def items(self, page_url: str, page: PageParser) -> Iterator[SourceItem]:
        found, how = page.licence()
        licence = self.licence or (urljoin(page_url, found) if found else None)
        how = "cli" if self.licence else how
        page_title = " ".join(page.title.split()) or None
        for href, text, tag in page.audio:
            audio = urldefrag(urljoin(page_url, href))[0]
            if urlparse(audio).scheme not in ("http", "https") or _ext(audio) not in AUDIO_EXTS:
                continue
            yield SourceItem(
                provider="web",
                provider_id=hashlib.sha1(audio.encode()).hexdigest()[:16],
                title=text or PurePosixPath(urlparse(audio).path).stem,
                author=page.author,
                page_url=page_url,
                licence_raw=licence,
                audio_url=audio,
                raw={"page_title": page_title, "licence_source": how, "tag": tag},
                analysed_from="original",
                lossy=_ext(audio) in LOSSY_EXTS,
                allow_unknown=self.allow_unknown,
            )

    def fetch_audio(self, item: SourceItem) -> bytes:
        self._limit(item.audio_url)
        return download(self.http, item.audio_url)


def _open(args, settings) -> Harvest:
    web = WebPages(
        depth=args.depth,
        max_pages=args.max_pages,
        allow_unknown=args.allow_unknown,
        licence=args.licence,
    )
    return Harvest(web.search(args.urls, **window(args)), web.fetch_audio)


SOURCE = Source(
    "web",
    "audio linked from web pages (respects robots.txt)",
    (
        arg("urls", nargs="+", help="seed page URLs"),
        *limits(),
        UNLICENSED,
        arg("--depth", type=int, default=0, help="follow same-host links this deep"),
        arg("--max-pages", type=int, default=50, help="stop after this many pages"),
        arg("--licence", help="licence URL or id for everything found (skips detection)"),
    ),
    _open,
)
