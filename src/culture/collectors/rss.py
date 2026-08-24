import feedparser
import httpx

from culture.collectors.base import CollectorError
from culture.logging import get_logger
from culture.models.content import ContentType
from culture.models.source import Source
from culture.schemas.collector import RawContentItem
from culture.utils.dates import from_struct_time
from culture.utils.http import get_with_retries

log = get_logger("culture.collectors.rss")


def parse_feed(content: bytes | str, feed_url: str) -> list[RawContentItem]:
    """Parse RSS/Atom bytes into normalized items. Pure function, easy to test."""
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise CollectorError(f"Feed unparseable: {feed_url}: {parsed.get('bozo_exception')}")

    items: list[RawContentItem] = []
    for entry in parsed.entries:
        url = entry.get("link")
        if not url:
            log.warning("feed entry without link skipped in %s", feed_url)
            continue
        published = from_struct_time(entry.get("published_parsed") or entry.get("updated_parsed"))
        tags = [t.get("term") for t in entry.get("tags", []) if t.get("term")]
        items.append(
            RawContentItem(
                external_id=entry.get("id") or None,
                url=url,
                title=(entry.get("title") or "").strip() or None,
                author=(entry.get("author") or "").strip() or None,
                description=(entry.get("summary") or "").strip() or None,
                published_at=published,
                content_type=ContentType.ARTICLE,
                metadata={"feed_tags": tags} if tags else {},
            )
        )
    return items


class RSSCollector:
    """Collects articles from any source with a verified RSS/Atom feed."""

    def __init__(self, client: httpx.Client) -> None:
        self.client = client

    def fetch(self, source: Source) -> list[RawContentItem]:
        if not source.feed_url:
            raise CollectorError(f"Source {source.name!r} has no feed_url configured")
        try:
            response = get_with_retries(self.client, source.feed_url)
        except httpx.HTTPError as exc:
            raise CollectorError(f"Feed fetch failed for {source.name!r}: {exc}") from exc
        return parse_feed(response.content, source.feed_url)
