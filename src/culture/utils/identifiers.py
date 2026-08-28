"""Canonical identity for a source, so duplicate accounts collide.

Different admins (or the same admin at different times) enter the same
account in different shapes: "@DesfileDiario", "instagram.com/desfilediario/",
a bare handle. `normalize_identifier` collapses all of these to one string,
backing the unique index on `sources.normalized_identifier` (see the
"source admin columns" migration). Web/RSS/podcast/newsletter sources reuse
`culture.utils.urls.normalize_url` — the same link-dedup logic the ingestion
pipeline already relies on, not a second implementation of URL canonicalization.
"""

import re
from urllib.parse import urlsplit

from culture.utils.urls import normalize_url

_HANDLE_DOMAINS = {
    "instagram": ("instagram.com",),
    "tiktok": ("tiktok.com",),
    "x": ("x.com", "twitter.com"),
}
_YOUTUBE_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{20,}$")
_YOUTUBE_CHANNEL_PATH = re.compile(r"/channel/([A-Za-z0-9_-]{10,})")


def _strip_handle(value: str) -> str:
    return value.strip().lstrip("@").rstrip("/").lower()


def _youtube_channel_id(value: str) -> str | None:
    match = _YOUTUBE_CHANNEL_PATH.search(value)
    if match:
        return match.group(1)
    if _YOUTUBE_CHANNEL_ID.fullmatch(value.strip()):
        return value.strip()
    return None


def normalize_identifier(platform: str, value: str) -> str:
    """`value` may be a bare @handle, a profile URL, or (for YouTube) a
    channel URL or a raw channel ID. Returns a canonical string such that
    every equivalent way of writing the same account normalizes identically.
    Empty input normalizes to "" (never matches anything, including itself,
    at the DB level — see the nullable unique index)."""
    value = value.strip()
    if not value:
        return ""

    if platform in _HANDLE_DOMAINS:
        lowered = value.lower()
        looks_like_url = (
            "://" in value
            or lowered.startswith("www.")
            or any(domain in lowered for domain in _HANDLE_DOMAINS[platform])
        )
        if looks_like_url:
            url = value if "://" in value else f"https://{value}"
            handle = urlsplit(url).path.strip("/").split("/")[0]
        else:
            handle = value
        return f"{platform}:{_strip_handle(handle)}"

    if platform == "youtube":
        channel_id = _youtube_channel_id(value)
        if channel_id:
            return f"youtube:{channel_id}"
        return f"youtube:{normalize_url(value)}"

    # podcast / web / rss / newsletter / substack / reddit / other: the feed
    # or profile URL itself.
    return normalize_url(value)
