"""Latest posts from the public Substack publication, for the homepage.

Stdlib-only (urllib + xml.etree) because this module ships in the Vercel
web bundle, which deliberately excludes the pipeline's heavier deps like
feedparser. Substack's /feed is plain RSS 2.0 — a handful of stable tags.

Fail-silent by design: the homepage must never be slowed down or broken by
Substack being unreachable, so failures return [] (section hidden) and the
last good result is cached per warm instance for CACHE_TTL_SECONDS.
"""

import contextlib
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime

from culture.logging import get_logger

log = get_logger("culture.web.publication")

FETCH_TIMEOUT_SECONDS = 3
CACHE_TTL_SECONDS = 15 * 60

_cache: dict[str, tuple[float, list["PublicationPost"]]] = {}


@dataclass
class PublicationPost:
    title: str
    url: str
    published_at: datetime | None
    excerpt: str


def _parse_feed(xml_text: str, limit: int) -> list[PublicationPost]:
    # Feed URL is operator-configured, and stdlib ElementTree never resolves
    # external entities — but a legitimate RSS feed has no business carrying
    # a DTD in its prolog, so reject one there rather than parsing it. Only
    # the prolog: post bodies inside CDATA legitimately contain HTML (and
    # its DOCTYPE), and an HTML page served where a feed should be (e.g. a
    # redirect to a profile page) is also caught by this prolog check.
    root_pos = xml_text.find("<rss")
    prolog = xml_text[:root_pos] if root_pos != -1 else xml_text
    if "<!DOCTYPE" in prolog or "<!ENTITY" in prolog:
        raise ValueError("feed has a DTD before the root element — refusing to parse")
    root = ET.fromstring(xml_text)
    posts = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or "").strip()
        if not title or not url.startswith("https://"):
            continue
        published_at = None
        if pub := item.findtext("pubDate"):
            with contextlib.suppress(ValueError, TypeError):
                published_at = parsedate_to_datetime(pub)
        excerpt = (item.findtext("description") or "").strip()
        # Substack descriptions are plain-text subtitles, but strip any tags
        # defensively rather than trusting that forever.
        while "<" in excerpt and ">" in excerpt:
            start = excerpt.find("<")
            end = excerpt.find(">", start)
            if end == -1:
                break
            excerpt = excerpt[:start] + excerpt[end + 1 :]
        posts.append(
            PublicationPost(
                title=title, url=url, published_at=published_at, excerpt=excerpt[:220]
            )
        )
        if len(posts) >= limit:
            break
    return posts


def latest_posts(feed_url: str, limit: int = 3) -> list[PublicationPost]:
    """Latest posts from the publication feed, or [] if unset/unreachable."""
    if not feed_url:
        return []
    cached = _cache.get(feed_url)
    if cached and time.monotonic() - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]
    try:
        request = urllib.request.Request(
            feed_url, headers={"User-Agent": "UrbanTasteIntelligence/1.0 (+homepage)"}
        )
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            posts = _parse_feed(response.read().decode("utf-8", errors="replace"), limit)
    except Exception as exc:
        log.warning("publication feed fetch failed (%s) — section hidden", exc)
        # Serve the stale cache if there is one; hide the section otherwise.
        return cached[1] if cached else []
    _cache[feed_url] = (time.monotonic(), posts)
    return posts
