import pytest

from culture.collectors.apify_social import ApifySocialCollector
from culture.collectors.base import CollectorError
from culture.models.content import ContentItem, ContentType
from culture.models.source import Source
from culture.services import intake
from culture.services.ingestion import IngestionService

# Real field shapes from a live apify/instagram-post-scraper run.
IG_ROWS = [
    {
        "caption": "She was escorted off the premises shortly after",
        "url": "https://www.instagram.com/p/DcR2kSOxk1T/",
        "shortCode": "DcR2kSOxk1T",
        "timestamp": "2026-08-20T22:37:59.000Z",
        "type": "Image",
        "displayUrl": "https://cdn.example/img1.jpg",
        "ownerUsername": "nolitadirtbag",
        "likesCount": 6554,
    },
    {
        "caption": "",
        "url": "https://www.instagram.com/p/DcRgitbyRLg/",
        "shortCode": "DcRgitbyRLg",
        "timestamp": "2026-08-20T19:26:52.000Z",
        "type": "Image",
        "displayUrl": "https://cdn.example/img2.jpg",
        "ownerUsername": "nolitadirtbag",
        "likesCount": 4457,
    },
]


@pytest.fixture(autouse=True)
def media_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(intake, "MEDIA_DIR", tmp_path / "media")


def ig_source(session, handle="nolitadirtbag"):
    source = Source(
        name="Nolita Dirtbag",
        platform="instagram",
        url=f"https://www.instagram.com/{handle}",
        tier="watch",
        active=True,
    )
    session.add(source)
    session.commit()
    return source


def test_instagram_rows_normalize_to_posts(session):
    source = ig_source(session)
    collector = ApifySocialCollector(lambda actor, inp: IG_ROWS)
    items = collector.fetch(source)

    assert len(items) == 2
    first = items[0]
    assert first.content_type == ContentType.POST
    assert first.external_id == "DcR2kSOxk1T"
    assert first.title == "She was escorted off the premises shortly after"
    assert first.metadata["image_url"] == "https://cdn.example/img1.jpg"
    assert first.metadata["platform"] == "instagram"
    assert first.published_at is not None
    # empty caption falls back to a generated title
    assert items[1].title.startswith("Instagram post by")


def test_handle_resolved_from_external_identifier(session):
    source = Source(
        name="Handle Source", platform="tiktok", external_identifier="@some.creator",
        url=None, tier="watch", active=True,
    )
    session.add(source)
    session.commit()
    captured = {}

    def runner(actor, inp):
        captured["actor"] = actor
        captured["input"] = inp
        return []

    ApifySocialCollector(runner).fetch(source)
    assert captured["actor"] == "clockworks/tiktok-scraper"
    assert captured["input"]["profiles"] == ["some.creator"]


def test_missing_handle_raises(session):
    source = Source(name="No Handle", platform="instagram", url=None, tier="watch", active=True)
    session.add(source)
    session.commit()
    with pytest.raises(CollectorError, match="handle"):
        ApifySocialCollector(lambda a, i: []).fetch(source)


def test_ingestion_stores_posts_and_downloads_images(session):
    ig_source(session)
    collector = ApifySocialCollector(lambda a, i: IG_ROWS)
    fetched_urls = []

    def fake_image_fetcher(url):
        fetched_urls.append(url)
        return (b"\x89PNG_fake_bytes", "image/png")

    service = IngestionService(
        session,
        collectors={"instagram": collector},
        page_fetcher=lambda url: "",
        image_fetcher=fake_image_fetcher,
    )
    stats = service.ingest()

    assert stats.total_new_posts == 2
    items = session.query(ContentItem).all()
    assert len(items) == 2
    assert all(i.content_type == "post" for i in items)
    stored = items[0]
    assert stored.metadata_json["images"][0]["media_type"] == "image/png"
    assert stored.processing_status == "ready"
    assert len(fetched_urls) == 2  # one image per post


def test_second_ingestion_creates_no_duplicate_posts(session):
    ig_source(session)
    collector = ApifySocialCollector(lambda a, i: IG_ROWS)
    kw = dict(
        collectors={"instagram": collector},
        page_fetcher=lambda url: "",
        image_fetcher=lambda url: (b"x", "image/png"),
    )
    IngestionService(session, **kw).ingest()
    second = IngestionService(session, **kw).ingest()

    assert second.total_new_posts == 0
    assert second.total_duplicates == 2
    assert session.query(ContentItem).count() == 2


def test_image_download_failure_still_stores_post(session):
    ig_source(session)
    collector = ApifySocialCollector(lambda a, i: IG_ROWS[:1])

    def failing_fetcher(url):
        raise ValueError("cdn 403")

    service = IngestionService(
        session,
        collectors={"instagram": collector},
        page_fetcher=lambda url: "",
        image_fetcher=failing_fetcher,
    )
    stats = service.ingest()

    assert stats.total_new_posts == 1
    item = session.query(ContentItem).one()
    assert "images" not in item.metadata_json
    assert item.metadata_json["image_error"] == "cdn 403"
    assert item.processing_status == "ready"  # analyzable from caption alone


def test_collector_failure_does_not_kill_run(session):
    ig_source(session)

    def exploding(actor, inp):
        raise CollectorError("apify down")

    service = IngestionService(
        session,
        collectors={"instagram": ApifySocialCollector(exploding)},
        page_fetcher=lambda url: "",
        image_fetcher=lambda url: (b"x", "image/png"),
    )
    stats = service.ingest()
    assert len(stats.failed) == 1
    assert "apify down" in stats.failed[0].error
