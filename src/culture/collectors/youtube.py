import feedparser
import httpx

from culture.collectors.base import CollectorError
from culture.logging import get_logger
from culture.models.content import ContentType
from culture.models.source import Source
from culture.schemas.collector import RawContentItem
from culture.utils.dates import from_struct_time
from culture.utils.http import get_with_retries

log = get_logger("culture.collectors.youtube")


def parse_youtube_feed(content: bytes | str, feed_url: str) -> list[RawContentItem]:
    """Parse a YouTube channel Atom feed into normalized video items."""
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise CollectorError(
            f"YouTube feed unparseable: {feed_url}: {parsed.get('bozo_exception')}"
        )

    items: list[RawContentItem] = []
    for entry in parsed.entries:
        video_id = entry.get("yt_videoid")
        url = entry.get("link")
        if not video_id or not url:
            log.warning("feed entry without video id/link skipped in %s", feed_url)
            continue
        metadata: dict = {"channel_id": entry.get("yt_channelid")}
        stats = entry.get("media_statistics") or {}
        if stats.get("views"):
            metadata["views_at_ingest"] = stats["views"]
        items.append(
            RawContentItem(
                external_id=video_id,
                url=url,
                title=(entry.get("title") or "").strip() or None,
                author=(entry.get("author") or "").strip() or None,
                description=(entry.get("summary") or "").strip() or None,
                published_at=from_struct_time(
                    entry.get("published_parsed") or entry.get("updated_parsed")
                ),
                content_type=ContentType.VIDEO,
                metadata=metadata,
            )
        )
    return items


class YouTubeCollector:
    """Collects videos from a channel's public Atom feed."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def fetch(self, source: Source) -> list[RawContentItem]:
        if not source.feed_url:
            raise CollectorError(f"Source {source.name!r} has no feed_url configured")
        try:
            response = get_with_retries(self.client, source.feed_url)
        except httpx.HTTPError as exc:
            raise CollectorError(f"YouTube feed fetch failed for {source.name!r}: {exc}") from exc
        return parse_youtube_feed(response.content, source.feed_url)
