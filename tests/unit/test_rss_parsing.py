from datetime import UTC, datetime

import pytest

from culture.collectors.base import CollectorError
from culture.collectors.rss import parse_feed

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Publication</title>
    <item>
      <title>Tokyo workwear moves west</title>
      <link>https://example.com/story?utm_source=rss</link>
      <guid isPermaLink="false">story-123</guid>
      <author>Jane Writer</author>
      <description>Japanese workwear brands are showing up in London.</description>
      <pubDate>Tue, 19 Aug 2026 10:30:00 GMT</pubDate>
      <category>fashion</category>
      <category>workwear</category>
    </item>
    <item>
      <title>Item with almost nothing</title>
      <link>https://example.com/minimal</link>
    </item>
    <item>
      <title>No link — must be skipped</title>
    </item>
  </channel>
</rss>
"""


def test_parse_feed_normalizes_entries():
    items = parse_feed(FEED, "https://example.com/feed")
    assert len(items) == 2

    first = items[0]
    assert first.external_id == "story-123"
    assert first.url == "https://example.com/story?utm_source=rss"  # raw; normalized later
    assert first.title == "Tokyo workwear moves west"
    assert first.author == "Jane Writer"
    assert first.published_at == datetime(2026, 8, 19, 10, 30, tzinfo=UTC)
    assert first.content_type == "article"
    assert first.metadata["feed_tags"] == ["fashion", "workwear"]


def test_parse_feed_tolerates_missing_fields():
    minimal = parse_feed(FEED, "https://example.com/feed")[1]
    assert minimal.title == "Item with almost nothing"
    assert minimal.external_id is None
    assert minimal.author is None
    assert minimal.published_at is None


def test_parse_feed_rejects_garbage():
    with pytest.raises(CollectorError):
        parse_feed("this is not xml at all {}", "https://example.com/feed")
