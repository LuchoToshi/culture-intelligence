from datetime import UTC, datetime, timedelta

from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem
from culture.models.source import Source
from culture.services.discovery import _classify_link, run_discovery

NOW = datetime.now(UTC)


def make_source(session, name, url=None, platform="web"):
    source = Source(name=name, platform=platform, url=url, tier="core", active=True)
    session.add(source)
    session.commit()
    return source


def make_item(session, source, title, raw_text=None, description=None, creators=None, refs=None):
    item = ContentItem(
        source_id=source.id,
        url=f"https://{source.name.lower().replace(' ', '')}.example/{title.replace(' ', '-')}",
        title=title,
        content_type="article",
        published_at=NOW - timedelta(days=2),
        raw_text=raw_text,
        description=description,
        processing_status="analyzed",
    )
    session.add(item)
    session.flush()
    if creators is not None or refs is not None:
        session.add(
            ContentAnalysis(
                content_item_id=item.id,
                summary="s",
                creators=creators or [],
                media_references=refs or [],
                analysis_model="m",
                analysis_provider="p",
                analysis_version="1",
            )
        )
    session.commit()
    return item


def test_entity_mentioned_by_two_sources_becomes_candidate(session):
    a = make_source(session, "Pub A")
    b = make_source(session, "Pub B")
    make_item(session, a, "one", creators=["Throwing Darts Pod"])
    make_item(session, b, "two", creators=["Throwing Darts Pod"])

    stats = run_discovery(session)

    assert stats.created == ["Throwing Darts Pod"]
    candidate = session.query(Source).filter_by(name="Throwing Darts Pod").one()
    assert candidate.tier == "candidate"
    assert candidate.active is False
    assert candidate.source_type == "discovered"
    assert candidate.discovery_json["via"] == "entity"
    assert candidate.discovery_json["items"] == 2
    assert sorted(candidate.discovery_json["citing_sources"]) == ["Pub A", "Pub B"]
    assert "Verify before activating" in candidate.collection_notes


def test_single_source_mention_stays_below_threshold(session):
    a = make_source(session, "Pub A")
    make_item(session, a, "one", creators=["Lonely Mention"])
    make_item(session, a, "two", creators=["Lonely Mention"])  # same source twice

    stats = run_discovery(session)

    assert stats.created == []
    assert stats.below_threshold >= 1
    assert session.query(Source).filter_by(name="Lonely Mention").count() == 0


def test_existing_sources_are_never_recreated_or_modified(session):
    a = make_source(session, "Pub A")
    b = make_source(session, "Pub B")
    make_source(session, "Sabukaru", url="https://sabukaru.online")
    make_item(session, a, "one", refs=["Sabukaru"], raw_text="see https://sabukaru.online/articles")
    make_item(session, b, "two", refs=["Sabukaru"], raw_text="see https://sabukaru.online/other")

    stats = run_discovery(session)

    assert stats.created == []
    assert stats.skipped_known >= 1
    existing = session.query(Source).filter_by(name="Sabukaru").one()
    assert existing.tier == "core"
    assert existing.discovery_json == {}


def test_platform_profile_links_create_platform_candidates(session):
    a = make_source(session, "Pub A")
    b = make_source(session, "Pub B")
    text = "follow https://www.instagram.com/dalstonsuperstoned and https://superstack.substack.com/p/post"
    make_item(session, a, "one", description=text)
    make_item(session, b, "two", raw_text=text)

    stats = run_discovery(session)

    names = set(stats.created)
    assert "dalstonsuperstoned" in names
    assert "superstack" in names
    ig = session.query(Source).filter_by(name="dalstonsuperstoned").one()
    assert ig.platform == "instagram"
    assert ig.url == "https://www.instagram.com/dalstonsuperstoned"
    sub = session.query(Source).filter_by(name="superstack").one()
    assert sub.platform == "substack"
    assert sub.url == "https://superstack.substack.com"


def test_blocklisted_and_self_domains_are_ignored(session):
    a = make_source(session, "Pub A")
    b = make_source(session, "Pub B")
    text = "https://open.spotify.com/show/x https://en.wikipedia.org/wiki/Y"
    make_item(session, a, "one", raw_text=text)
    make_item(session, b, "two", raw_text=text)

    stats = run_discovery(session)
    assert stats.created == []


def test_rerun_updates_stats_without_duplicating(session):
    a = make_source(session, "Pub A")
    b = make_source(session, "Pub B")
    make_item(session, a, "one", creators=["Repeat Creator"])
    make_item(session, b, "two", creators=["Repeat Creator"])
    first = run_discovery(session)
    assert first.created == ["Repeat Creator"]

    c = make_source(session, "Pub C")
    make_item(session, c, "three", creators=["Repeat Creator"])
    second = run_discovery(session)

    assert second.created == []
    assert second.updated == 1
    assert session.query(Source).filter_by(name="Repeat Creator").count() == 1
    refreshed = session.query(Source).filter_by(name="Repeat Creator").one()
    assert refreshed.discovery_json["items"] == 3
    assert len(refreshed.discovery_json["citing_sources"]) == 3


def test_classify_link_shapes():
    assert _classify_link("https://www.tiktok.com/@some.creator") == (
        "some.creator",
        "tiktok",
        "https://www.tiktok.com/@some.creator",
    )
    assert _classify_link("https://x.com/somehandle")[1] == "x"
    assert _classify_link("https://youtube.com/@channelname")[1] == "youtube"
    assert _classify_link("https://newmag.example/story")[0] == "newmag.example"
    assert _classify_link("https://bit.ly/abc") is None
    # deep platform links (a single post, not a profile) are not profiles
    assert _classify_link("https://www.instagram.com/someone/p/Cxyz/") is None
