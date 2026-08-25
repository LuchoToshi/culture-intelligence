from datetime import UTC, datetime, timedelta

import pytest

from culture.models.content import ContentItem
from culture.models.signal import Signal, SignalEvidence
from culture.models.source import Source
from culture.services.source_lifecycle import (
    apply_lifecycle,
    mark_reviewed,
    quiet_core_sources,
    review_queue,
)

NOW = datetime.now(UTC)


def make_source(session, name, tier="candidate", platform="web", active=True, **kw):
    source = Source(
        name=name, platform=platform, tier=tier, active=active,
        feed_url=kw.pop("feed_url", f"https://{name.lower().replace(' ', '')}.example/feed"),
        last_successful_check_at=kw.pop("last_successful_check_at", NOW - timedelta(hours=3)),
        **kw,
    )
    session.add(source)
    session.commit()
    return source


def add_items(session, source, n, first_days_ago=20, last_days_ago=1, evidence=0):
    items = []
    for i in range(n):
        spread = first_days_ago - (first_days_ago - last_days_ago) * (i / max(n - 1, 1))
        item = ContentItem(
            source_id=source.id,
            url=f"https://x.example/{source.id}-{i}",
            title=f"item {i}",
            content_type="article",
            discovered_at=NOW - timedelta(days=first_days_ago),
            published_at=NOW - timedelta(days=spread),
            processing_status="analyzed",
        )
        session.add(item)
        items.append(item)
    session.flush()
    if evidence:
        signal = Signal(name=f"sig for {source.name}")
        session.add(signal)
        session.flush()
        for item in items[:evidence]:
            session.add(SignalEvidence(signal_id=signal.id, content_item_id=item.id))
    session.commit()
    return items


def test_candidate_with_evidence_promotes_to_watch(session):
    source = make_source(session, "Trial Pub", tier="candidate")
    add_items(session, source, 4, first_days_ago=20, evidence=1)

    stats = apply_lifecycle(session)

    assert [t.to_tier for t in stats.promoted] == ["watch"]
    assert source.tier == "watch"
    history = source.discovery_json["tier_history"]
    assert history[-1]["from"] == "candidate"
    assert "trial passed" in history[-1]["reason"]


def test_candidate_without_evidence_stays(session):
    source = make_source(session, "No Evidence Pub", tier="candidate")
    add_items(session, source, 5, first_days_ago=30, evidence=0)
    stats = apply_lifecycle(session)
    assert stats.promoted == []
    assert source.tier == "candidate"


def test_watch_with_sustained_evidence_promotes_to_core(session):
    source = make_source(session, "Strong Pub", tier="watch")
    add_items(session, source, 8, first_days_ago=40, evidence=6)
    stats = apply_lifecycle(session)
    assert [t.to_tier for t in stats.promoted] == ["core"]
    assert source.tier == "core"


def test_quiet_watch_source_retires_to_dormant(session):
    source = make_source(session, "Quiet Pub", tier="watch")
    add_items(session, source, 3, first_days_ago=120, last_days_ago=90)
    stats = apply_lifecycle(session)
    assert [t.to_tier for t in stats.retired] == ["dormant"]
    assert source.tier == "dormant"
    assert source.active is False
    assert "no new content" in source.discovery_json["tier_history"][-1]["reason"]


def test_broken_collection_retires_watch_source(session):
    source = make_source(
        session, "Broken Pub", tier="watch",
        last_successful_check_at=NOW - timedelta(days=30),
    )
    add_items(session, source, 3, first_days_ago=40, last_days_ago=25)
    stats = apply_lifecycle(session)
    assert [t.to_tier for t in stats.retired] == ["dormant"]


def test_core_is_never_auto_retired_only_flagged(session):
    source = make_source(session, "Quiet Core", tier="core")
    add_items(session, source, 3, first_days_ago=200, last_days_ago=100)

    stats = apply_lifecycle(session)
    assert stats.retired == []
    assert source.tier == "core"
    assert source.active is True

    flagged = quiet_core_sources(session)
    assert [(s.name, q >= 100) for s, q in flagged] == [("Quiet Core", True)]


def test_manual_platforms_are_untouched(session):
    ig = make_source(session, "Meme Page", tier="watch", platform="instagram", feed_url=None)
    stats = apply_lifecycle(session)
    assert stats.promoted == [] and stats.retired == []
    assert ig.tier == "watch"


def test_lifecycle_is_idempotent(session):
    source = make_source(session, "Trial Pub", tier="candidate")
    add_items(session, source, 4, first_days_ago=20, evidence=1)
    apply_lifecycle(session)
    second = apply_lifecycle(session)
    assert second.promoted == [] and second.retired == []
    assert len(source.discovery_json["tier_history"]) == 1


def test_review_queue_lists_manual_accounts_and_candidates(session):
    make_source(session, "IG Never Reviewed", platform="instagram", tier="watch", feed_url=None)
    reviewed = make_source(
        session, "IG Recently Reviewed", platform="instagram", tier="core", feed_url=None
    )
    reviewed.last_reviewed_at = NOW - timedelta(days=2)
    session.add(
        Source(
            name="Discovered Thing", platform="other", tier="candidate", active=False,
            source_type="discovered",
            discovery_json={"via": "entity", "mentions": 4, "citing_sources": ["A", "B"]},
        )
    )
    session.commit()

    queue = review_queue(session)
    due_names = [s.name for s, _ in queue["due"]]
    assert "IG Never Reviewed" in due_names
    assert "IG Recently Reviewed" not in due_names
    assert [c.name for c in queue["candidates"]] == ["Discovered Thing"]


def test_mark_reviewed_updates_timestamp(session):
    source = make_source(session, "IG Page", platform="instagram", tier="core", feed_url=None)
    assert source.last_reviewed_at is None
    mark_reviewed(session, "IG Page")
    assert source.last_reviewed_at is not None
    with pytest.raises(ValueError):
        mark_reviewed(session, "Nope")
