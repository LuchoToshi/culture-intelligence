"""Lead images for content items, so reports can show what they talk about.

Three sources, in order of trust:
1. ``metadata_json["image_url"]``: the publisher's own share image (og:image or
   twitter:image), stored by ``backfill_image_urls`` from the article page.
2. YouTube: the video's own thumbnail, derived from the video id.
3. Instagram: the photo the collector stored on disk. Only the local pipeline has
   those files, so the hosted viewer simply shows no image for them.

Nothing here invents an image: an item without one of these has no image.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from html import unescape
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from culture.logging import get_logger
from culture.models.content import ContentItem
from culture.models.source import Source
from culture.utils.dates import now_utc

log = get_logger("culture.images")

_YOUTUBE_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/)([\w-]{11})")
_META_TAG = re.compile(
    r"""<meta[^>]+(?:property|name)=["'](?:og:image(?::secure_url)?|twitter:image(?::src)?)["'][^>]*>""",
    re.IGNORECASE,
)
_CONTENT = re.compile(r"""content=["']([^"']+)["']""", re.IGNORECASE)
# Share images that are a site logo or a generic placeholder, not the article.
_EXPIRING = re.compile(r"(cdninstagram\.com|fbcdn\.net|tiktokcdn)", re.IGNORECASE)
_GENERIC = re.compile(r"(logo|placeholder|default[-_]?share|favicon|apple-touch)", re.IGNORECASE)


def youtube_thumbnail(url: str | None) -> str | None:
    match = _YOUTUBE_ID.search(url or "")
    return f"https://i.ytimg.com/vi/{match.group(1)}/hqdefault.jpg" if match else None


def local_image_path(item: ContentItem) -> Path | None:
    for image in (item.metadata_json or {}).get("images", []) or []:
        path = image.get("path") if isinstance(image, dict) else None
        if path and Path(path).is_file():
            return Path(path)
    return None


def image_url(item: ContentItem) -> str | None:
    """A displayable image URL for an item, or None. Local files go through /media/."""
    meta = item.metadata_json or {}
    if "instagram.com" in (item.url or ""):
        # Instagram CDN links in the scraper metadata expire within days; only the
        # stored photo is reliable.
        local = local_image_path(item)
        return f"/media/{local.name}" if local else None
    if meta.get("image_url") and not _EXPIRING.search(meta["image_url"]):
        return meta["image_url"]
    thumb = youtube_thumbnail(item.url)
    if thumb:
        return thumb
    local = local_image_path(item)
    return f"/media/{local.name}" if local else None


def og_image_from_html(page: str, base_url: str) -> str | None:
    for tag in _META_TAG.findall(page):
        found = _CONTENT.search(tag)
        if not found:
            continue
        src = unescape(found.group(1)).strip()
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            match = re.match(r"https?://[^/]+", base_url)
            src = (match.group(0) if match else "") + src
        if src.startswith("http") and not _GENERIC.search(src):
            return src
    return None


@dataclass
class ImageBackfillStats:
    checked: int = 0
    found: int = 0
    missing: int = 0
    failed: list[str] = field(default_factory=list)


def pending_image_items(session: Session, days: int = 60) -> list[tuple[int, str]]:
    """(item id, url) for recent web and newsletter items that were never checked."""
    since = now_utc() - timedelta(days=days)
    rows = session.execute(
        select(
            ContentItem.id, ContentItem.url, ContentItem.canonical_url, ContentItem.metadata_json
        )
        .join(Source, Source.id == ContentItem.source_id)
        .where(
            Source.platform.in_(["web", "substack", "newsletter"]),
            ContentItem.discovered_at >= since,
        )
    ).all()
    return [
        (item_id, canonical or url)
        for item_id, url, canonical, meta in rows
        if not (meta or {}).get("image_url") and not (meta or {}).get("image_checked")
    ]


MAX_HEAD_BYTES = 400_000


def _public_http_url(url: str) -> bool:
    """Only http(s) to public addresses: feed links are outside input."""
    import ipaddress
    import socket
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except OSError:
        return False
    return all(ipaddress.ip_address(info[4][0]).is_global for info in infos)


def _read_head(client, url: str, max_hops: int = 5) -> tuple[str, str]:
    """GET with every redirect hop checked, reading at most MAX_HEAD_BYTES of HTML."""
    for _ in range(max_hops):
        if not _public_http_url(url):
            raise ValueError("refused non-public url")
        with client.stream("GET", url, follow_redirects=False) as response:
            if response.is_redirect:
                url = str(response.url.join(response.headers["location"]))
                continue
            response.raise_for_status()
            if "html" not in response.headers.get("content-type", "html"):
                raise ValueError("not html")
            data = bytearray()
            for chunk in response.iter_bytes():
                data.extend(chunk)
                if len(data) >= MAX_HEAD_BYTES:
                    break
            text = data[:MAX_HEAD_BYTES].decode(response.encoding or "utf-8", "replace")
            return text, str(response.url)
    raise ValueError("too many redirects")


def fetch_share_images(
    pairs: list[tuple[int, str]], workers: int = 8
) -> list[tuple[int, str | None, str | None]]:
    """Read each page's head and return (item id, image url or None, error or None)."""
    from culture.utils.http import create_client

    def fetch(pair: tuple[int, str]) -> tuple[int, str | None, str | None]:
        item_id, url = pair
        try:
            with create_client() as client:
                body, final_url = _read_head(client, url)
            return item_id, og_image_from_html(body, final_url), None
        except Exception as exc:  # one bad page never stops the run
            return item_id, None, f"{url}: {exc.__class__.__name__}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fetch, pairs))


def store_image_results(
    session: Session, results: list[tuple[int, str | None, str | None]]
) -> ImageBackfillStats:
    stats = ImageBackfillStats(checked=len(results))
    found = {item_id: (url, error) for item_id, url, error in results}
    for item in session.scalars(select(ContentItem).where(ContentItem.id.in_(list(found)))):
        url, error = found[item.id]
        meta = dict(item.metadata_json or {})
        if url:
            meta["image_url"] = url
            stats.found += 1
        else:
            meta["image_checked"] = now_utc().date().isoformat()
            stats.missing += 1
            if error:
                stats.failed.append(error)
        item.metadata_json = meta
    session.flush()
    return stats


def backfill_image_urls(engine, days: int = 60, batch: int = 150) -> ImageBackfillStats:
    """Store the publisher's share image for recent web and newsletter items.

    Reads only page heads (no analysis, no API cost). The database connection is
    not held while pages load: Neon drops idle connections, so the run reads the
    work list, fetches, then writes back in short batches."""
    from culture.database import session_scope

    with session_scope(engine) as session:
        todo = pending_image_items(session, days)
    total = ImageBackfillStats()
    for start in range(0, len(todo), batch):
        results = fetch_share_images(todo[start : start + batch])
        with session_scope(engine) as session:
            stats = store_image_results(session, results)
        total.checked += stats.checked
        total.found += stats.found
        total.missing += stats.missing
        total.failed += stats.failed
    log.info("image backfill: %d checked, %d found", total.checked, total.found)
    return total
