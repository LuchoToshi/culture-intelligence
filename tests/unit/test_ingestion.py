from datetime import UTC, datetime

import pytest

from culture.collectors.base import CollectorError
from culture.models.content import ContentItem, ContentType
from culture.models.source import Source
from culture.schemas.collector import RawContentItem
from culture.services.ingestion import IngestionService

ARTICLE_HTML = """
<html><head>
  <title>Tokyo workwear moves west</title>
  <link rel="canonical" href="https://example.com/story" />
</head><body><article>
  <h1>Tokyo workwear moves west</h1>
  <p>{}</p>
</article></body></html>
""".format("Japanese workwear labels are opening London stockists. " * 20)


class FakeCollector:
    """Returns a fixed batch of items; can be told to fail."""

    def __init__(self, items=None, fail=False):
        self.items = items or []
        self.fail = fail

    def fetch(self, source):
        if self.fail:
            raise CollectorError(f"boom: {source.name}")
        return list(self.items)


def make_raw(url="https://example.com/story?utm_source=rss", **overrides):
    defaults = dict(
        external_id="story-123",
        url=url,
        title="Tokyo workwear moves west",
        author="Jane Writer",
        description="Japanese workwear brands in London.",
        published_at=datetime(2026, 8, 19, 10, 30, tzinfo=UTC),
        content_type=ContentType.ARTICLE,
    )
    defaults.update(overrides)
    return RawContentItem(**defaults)


def make_web_source(session, name="Test Publication", feed_url="https://example.com/feed"):
    source = Source(name=name, platform="web", feed_url=feed_url, active=True, tier="core")
    session.add(source)
    session.commit()
    return source


def make_service(session, collector, pages=None):
    pages = pages if pages is not None else {}

    def fetch_page(url):
        if url in pages:
            result = pages[url]
            if isinstance(result, Exception):
                raise result
            return result
        return ARTICLE_HTML

    return IngestionService(
        session, collectors={"web": collector}, page_fetcher=fetch_page
    )


def test_ingestion_stores_new_article_with_extraction(session):
    source = make_web_source(session)
    service = make_service(session, FakeCollector([make_raw()]))
    stats = service.ingest()

    assert stats.total_new_articles == 1
    assert stats.total_extracted == 1
    item = session.query(ContentItem).one()
    assert item.url == "https://example.com/story"  # normalized, tracking stripped
    assert item.canonical_url == "https://example.com/story"
    assert item.extraction_status == "success"
    assert "Japanese workwear labels" in item.raw_text
    assert item.content_hash is not None
    assert item.processing_status == "ready"
    assert item.metadata_json["original_url"] == "https://example.com/story?utm_source=rss"
    assert source.last_successful_check_at is not None


def test_second_ingestion_creates_zero_duplicates(session):
    make_web_source(session)
    collector = FakeCollector([make_raw()])
    service = make_service(session, collector)

    first = service.ingest()
    assert first.total_new_articles == 1

    second = service.ingest()
    assert second.total_new_articles == 0
    assert second.total_duplicates == 1
    assert session.query(ContentItem).count() == 1


def test_same_story_different_tracking_params_is_duplicate(session):
    make_web_source(session)
    service = make_service(session, FakeCollector([make_raw()]))
    service.ingest()

    # Same story arrives later with different tracking params and no guid.
    variant = make_raw(url="https://example.com/story?utm_source=newsletter", external_id=None)
    service2 = make_service(session, FakeCollector([variant]))
    stats = service2.ingest()

    assert stats.total_duplicates == 1
    assert session.query(ContentItem).count() == 1


def test_failed_extraction_preserves_metadata(session):
    make_web_source(session)
    raw = make_raw(url="https://example.com/broken", external_id="broken-1")
    import httpx

    service = make_service(
        session,
        FakeCollector([raw]),
        pages={"https://example.com/broken": httpx.ConnectError("refused")},
    )
    stats = service.ingest()

    assert stats.total_new_articles == 1
    assert stats.total_extraction_failed == 1
    item = session.query(ContentItem).one()
    assert item.title == "Tokyo workwear moves west"
    assert item.raw_text is None
    assert item.extraction_status == "failed"
    assert item.processing_status == "ready"
    assert "page fetch failed" in item.metadata_json["extraction_error"]


def test_unextractable_html_is_partial_or_failed_but_stored(session):
    make_web_source(session)
    raw = make_raw(url="https://example.com/empty", external_id="empty-1")
    service = make_service(
        session,
        FakeCollector([raw]),
        pages={"https://example.com/empty": "<html><body></body></html>"},
    )
    stats = service.ingest()

    assert stats.total_new_articles == 1
    assert stats.total_extraction_failed == 1
    assert session.query(ContentItem).one().extraction_status == "failed"


def test_one_failing_source_does_not_stop_others(session):
    bad = make_web_source(session, name="Broken Source", feed_url="https://bad.example/feed")
    good = make_web_source(session, name="Good Source", feed_url="https://good.example/feed")

    class PerSourceCollector:
        def fetch(self, source):
            if source.name == "Broken Source":
                raise CollectorError("feed unreachable")
            return [make_raw()]

    service = make_service(session, PerSourceCollector())
    stats = service.ingest()

    assert stats.checked == 2
    assert stats.successful == 1
    assert len(stats.failed) == 1
    assert stats.failed[0].source_name == "Broken Source"
    assert "feed unreachable" in stats.failed[0].error
    assert stats.total_new_articles == 1
    assert bad.last_checked_at is not None
    assert bad.last_successful_check_at is None
    assert good.last_successful_check_at is not None


def test_sources_without_feed_or_collector_are_skipped_visibly(session):
    no_feed = make_web_source(session, name="No Feed", feed_url=None)
    session.add(Source(name="Instagram Thing", platform="instagram", active=True))
    session.commit()

    service = make_service(session, FakeCollector([]))
    stats = service.ingest()

    names = {s.source_name: s.skipped_reason for s in stats.skipped}
    assert "No Feed" in names
    assert "Instagram Thing" in names
    assert stats.checked == 0


def test_inactive_sources_are_not_ingested_in_bulk_run(session):
    source = make_web_source(session, name="Dormant")
    source.active = False
    session.commit()
    service = make_service(session, FakeCollector([make_raw()]))
    stats = service.ingest()
    assert stats.sources == []


def test_named_ingest_of_unknown_source_raises(session):
    service = make_service(session, FakeCollector([]))
    with pytest.raises(ValueError, match="Nope"):
        service.ingest(source_name="Nope")
