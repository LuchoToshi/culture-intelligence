from datetime import UTC, datetime

from culture.collectors.youtube import parse_youtube_feed
from culture.extraction.youtube import TranscriptResult, VideoEnrichment, VideoMetadata
from culture.models.content import ContentItem, ContentType, TranscriptStatus
from culture.models.source import Source
from culture.schemas.collector import RawContentItem
from culture.services.ingestion import IngestionService

YT_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Test Channel</title>
  <entry>
    <id>yt:video:abc123XYZ00</id>
    <yt:videoId>abc123XYZ00</yt:videoId>
    <yt:channelId>UCtest12345</yt:channelId>
    <title>How London dresses now</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=abc123XYZ00"/>
    <author><name>Test Creator</name></author>
    <published>2026-08-20T10:00:00+00:00</published>
    <media:group>
      <media:description>A look at London menswear this season.</media:description>
      <media:community>
        <media:statistics views="12345"/>
      </media:community>
    </media:group>
  </entry>
  <entry>
    <id>yt:video:missing-link</id>
    <title>Entry without video id — skipped</title>
  </entry>
</feed>
"""


def test_parse_youtube_feed():
    items = parse_youtube_feed(YT_FEED, "https://www.youtube.com/feeds/videos.xml?channel_id=UCtest12345")
    assert len(items) == 1
    video = items[0]
    assert video.external_id == "abc123XYZ00"
    assert video.url == "https://www.youtube.com/watch?v=abc123XYZ00"
    assert video.title == "How London dresses now"
    assert video.author == "Test Creator"
    assert video.description == "A look at London menswear this season."
    assert video.published_at == datetime(2026, 8, 20, 10, 0, tzinfo=UTC)
    assert video.content_type == ContentType.VIDEO
    assert video.metadata["channel_id"] == "UCtest12345"
    assert video.metadata["views_at_ingest"] == "12345"


def make_video_raw(video_id="abc123XYZ00", **overrides):
    defaults = dict(
        external_id=video_id,
        url=f"https://www.youtube.com/watch?v={video_id}",
        title="How London dresses now",
        author="Test Creator",
        description="A look at London menswear.",
        published_at=datetime(2026, 8, 20, 10, 0, tzinfo=UTC),
        content_type=ContentType.VIDEO,
        metadata={"channel_id": "UCtest12345"},
    )
    defaults.update(overrides)
    return RawContentItem(**defaults)


class FakeVideoCollector:
    def __init__(self, items):
        self.items = items

    def fetch(self, source):
        return list(self.items)


def make_yt_source(session, name="Test Channel"):
    source = Source(
        name=name,
        platform="youtube",
        feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCtest12345",
        active=True,
        tier="core",
    )
    session.add(source)
    session.commit()
    return source


GOOD_ENRICHMENT = VideoEnrichment(
    metadata=VideoMetadata(
        ok=True,
        duration_seconds=754,
        chapters=[{"title": "Intro", "start_time": 0.0}],
        description="Full yt-dlp description.",
        view_count=99000,
    ),
    transcript=TranscriptResult(
        status=TranscriptStatus.AVAILABLE,
        text="Today we look at how London creatives actually dress.",
        language="en",
        is_generated=True,
    ),
)


def make_service(session, items, enrichment=GOOD_ENRICHMENT, transcript_fetcher=None):
    def enricher(url, video_id):
        if isinstance(enrichment, Exception):
            raise enrichment
        return enrichment

    return IngestionService(
        session,
        collectors={"youtube": FakeVideoCollector(items)},
        page_fetcher=lambda url: "",
        video_enricher=enricher,
        transcript_fetcher=transcript_fetcher
        or (lambda video_id: TranscriptResult(status=TranscriptStatus.FAILED, error="no fake")),
    )


def test_video_ingestion_with_transcript(session):
    make_yt_source(session)
    stats = make_service(session, [make_video_raw()]).ingest()

    assert stats.total_new_videos == 1
    assert stats.total_transcripts_available == 1
    item = session.query(ContentItem).one()
    assert item.content_type == "video"
    assert item.external_id == "abc123XYZ00"
    assert item.transcript_status == "available"
    assert "London creatives" in item.raw_text
    assert item.language == "en"
    assert item.extraction_status == "success"
    assert item.metadata_json["duration_seconds"] == 754
    assert item.metadata_json["chapters"][0]["title"] == "Intro"
    assert item.metadata_json["view_count"] == 99000
    assert item.description == "A look at London menswear."  # feed wins over yt-dlp
    assert item.content_hash is not None
    assert item.processing_status == "ready"


def test_transcript_unavailable_preserves_video(session):
    make_yt_source(session)
    enrichment = VideoEnrichment(
        metadata=VideoMetadata(ok=True, duration_seconds=100),
        transcript=TranscriptResult(status=TranscriptStatus.UNAVAILABLE, error="TranscriptsDisabled"),
    )
    stats = make_service(session, [make_video_raw()], enrichment).ingest()

    assert stats.total_new_videos == 1
    assert stats.total_transcripts_unavailable == 1
    item = session.query(ContentItem).one()
    assert item.transcript_status == "unavailable"
    assert item.raw_text is None
    assert item.title == "How London dresses now"
    assert item.metadata_json["transcript_error"] == "TranscriptsDisabled"


def test_transcript_and_metadata_failure_still_stores_video(session):
    make_yt_source(session)
    enrichment = VideoEnrichment(
        metadata=VideoMetadata(ok=False, error="429 too many requests"),
        transcript=TranscriptResult(status=TranscriptStatus.FAILED, error="IpBlocked"),
    )
    stats = make_service(session, [make_video_raw()], enrichment).ingest()

    assert stats.total_new_videos == 1
    assert stats.total_transcripts_failed == 1
    item = session.query(ContentItem).one()
    assert item.transcript_status == "failed"
    assert item.extraction_status == "failed"
    assert item.published_at is not None  # feed metadata preserved
    assert item.metadata_json["metadata_error"] == "429 too many requests"


def test_second_video_ingestion_creates_zero_duplicates(session):
    make_yt_source(session)
    service = make_service(session, [make_video_raw()])
    first = service.ingest()
    second = service.ingest()

    assert first.total_new_videos == 1
    assert second.total_new_videos == 0
    assert second.total_duplicates == 1
    assert session.query(ContentItem).count() == 1


FAILED_ENRICHMENT = VideoEnrichment(
    metadata=VideoMetadata(ok=True, duration_seconds=100),
    transcript=TranscriptResult(status=TranscriptStatus.FAILED, error="IpBlocked"),
)


def test_retry_pass_recovers_failed_transcripts(session):
    make_yt_source(session)
    make_service(session, [make_video_raw()], FAILED_ENRICHMENT).ingest()
    item = session.query(ContentItem).one()
    assert item.transcript_status == "failed"
    item.processing_status = "analyzed"  # pretend Phase 6 analyzed the bare metadata
    session.commit()

    recovery = TranscriptResult(
        status=TranscriptStatus.AVAILABLE, text="Recovered spoken words.", language="en", is_generated=True
    )
    stats = make_service(
        session, [make_video_raw()], FAILED_ENRICHMENT, transcript_fetcher=lambda vid: recovery
    ).ingest()

    assert stats.total_transcripts_recovered == 1
    item = session.query(ContentItem).one()
    assert item.transcript_status == "available"
    assert item.raw_text == "Recovered spoken words."
    assert item.content_hash is not None
    assert "transcript_error" not in item.metadata_json
    assert item.processing_status == "ready"  # re-queued for analysis with real content


def test_retry_circuit_breaker_stops_hammering_while_blocked(session):
    make_yt_source(session)
    videos = [make_video_raw(video_id=f"vid{i:07d}") for i in range(6)]
    make_service(session, videos, FAILED_ENRICHMENT).ingest()

    calls = []

    def still_blocked(video_id):
        calls.append(video_id)
        return TranscriptResult(status=TranscriptStatus.FAILED, error="IpBlocked")

    make_service(session, videos, FAILED_ENRICHMENT, transcript_fetcher=still_blocked).ingest()

    assert len(calls) == 3  # breaker tripped, remaining 3 left for next run
    assert (
        session.query(ContentItem).filter(ContentItem.transcript_status == "failed").count() == 6
    )


def test_retry_marks_confirmed_unavailable(session):
    make_yt_source(session)
    make_service(session, [make_video_raw()], FAILED_ENRICHMENT).ingest()

    unavailable = TranscriptResult(status=TranscriptStatus.UNAVAILABLE, error="TranscriptsDisabled")
    stats = make_service(
        session, [make_video_raw()], FAILED_ENRICHMENT, transcript_fetcher=lambda vid: unavailable
    ).ingest()

    assert stats.total_transcripts_recovered == 0
    assert session.query(ContentItem).one().transcript_status == "unavailable"


def test_same_video_different_url_form_is_duplicate(session):
    """A video arriving as /shorts/<id> must dedup against watch?v=<id>."""
    make_yt_source(session)
    make_service(session, [make_video_raw()]).ingest()

    short_form = make_video_raw(url="https://www.youtube.com/shorts/abc123XYZ00")
    stats = make_service(session, [short_form]).ingest()

    assert stats.total_duplicates == 1
    assert session.query(ContentItem).count() == 1
