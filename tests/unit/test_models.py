from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from culture.models import (
    ContentAnalysis,
    ContentItem,
    ContentType,
    ExtractionStatus,
    ProcessingStatus,
    Source,
    SourceTier,
    TranscriptStatus,
)


def make_source(**overrides) -> Source:
    defaults = dict(
        name="Sabukaru",
        platform="web",
        source_type="publication",
        url="https://sabukaru.online",
        country="Japan",
        city="Tokyo",
        categories=["fashion", "youth culture"],
        tier=SourceTier.CORE.value,
    )
    defaults.update(overrides)
    return Source(**defaults)


def test_source_creation_with_defaults(session):
    source = make_source()
    session.add(source)
    session.commit()

    assert source.id is not None
    assert source.active is True
    assert source.categories == ["fashion", "youth culture"]
    assert source.intelligence_layers == []
    assert source.created_at.year == datetime.now(UTC).year
    assert source.last_checked_at is None


def test_source_tier_defaults_to_candidate(session):
    source = make_source(name="New Candidate", tier=None)
    source.tier = SourceTier.CANDIDATE.value
    session.add(source)
    session.commit()
    assert source.tier == "candidate"


def test_source_name_must_be_unique(session):
    session.add(make_source())
    session.commit()
    session.add(make_source())
    with pytest.raises(IntegrityError):
        session.commit()


def test_content_item_creation_with_defaults(session):
    source = make_source()
    session.add(source)
    session.commit()

    item = ContentItem(
        source_id=source.id,
        url="https://sabukaru.online/articles/tokyo-workwear",
        title="Tokyo workwear",
        content_type=ContentType.ARTICLE.value,
    )
    session.add(item)
    session.commit()

    assert item.id is not None
    assert item.extraction_status == ExtractionStatus.NOT_ATTEMPTED.value
    assert item.transcript_status == TranscriptStatus.NOT_APPLICABLE.value
    assert item.processing_status == ProcessingStatus.NEW.value
    assert item.metadata_json == {}
    assert item.discovered_at is not None
    assert item.source.name == "Sabukaru"


def test_duplicate_url_per_source_rejected(session):
    source = make_source()
    session.add(source)
    session.commit()

    url = "https://sabukaru.online/articles/tokyo-workwear"
    session.add(ContentItem(source_id=source.id, url=url, content_type="article"))
    session.commit()
    session.add(ContentItem(source_id=source.id, url=url, content_type="article"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_same_url_allowed_across_different_sources(session):
    a = make_source(name="Source A")
    b = make_source(name="Source B")
    session.add_all([a, b])
    session.commit()

    url = "https://example.com/shared-story"
    session.add(ContentItem(source_id=a.id, url=url, content_type="article"))
    session.add(ContentItem(source_id=b.id, url=url, content_type="article"))
    session.commit()


def test_duplicate_external_id_per_source_rejected(session):
    source = make_source(name="YouTube Channel", platform="youtube")
    session.add(source)
    session.commit()

    session.add(
        ContentItem(
            source_id=source.id,
            external_id="video-abc123",
            url="https://www.youtube.com/watch?v=abc123",
            content_type=ContentType.VIDEO.value,
        )
    )
    session.commit()
    session.add(
        ContentItem(
            source_id=source.id,
            external_id="video-abc123",
            url="https://www.youtube.com/watch?v=abc123&x=1",
            content_type=ContentType.VIDEO.value,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_analysis_creation_and_null_scores(session):
    source = make_source()
    session.add(source)
    session.commit()
    item = ContentItem(
        source_id=source.id,
        url="https://sabukaru.online/articles/tokyo-workwear",
        content_type="article",
    )
    session.add(item)
    session.commit()

    analysis = ContentAnalysis(
        content_item_id=item.id,
        summary="Japanese workwear references entering mainstream menswear.",
        brands=["Kapital", "orSlow"],
        cities=["Tokyo"],
        possible_signals=["Japanese workwear moving into mainstream menswear"],
        cultural_origin_score=4,
        analysis_provider="anthropic",
        analysis_model="claude-fable-5",
        analysis_version="v1",
        analyzed_at=datetime.now(UTC),
    )
    session.add(analysis)
    session.commit()

    assert analysis.id is not None
    # Scores the item could not support stay null instead of being forced.
    assert analysis.commercial_evidence_score is None
    assert analysis.lifecycle_stage is None
    assert analysis.facts_json == {}
    assert analysis.brands == ["Kapital", "orSlow"]
    assert analysis.content_item.id == item.id


def test_multiple_analyses_per_item_allowed(session):
    source = make_source()
    session.add(source)
    session.commit()
    item = ContentItem(source_id=source.id, url="https://x.example/a", content_type="article")
    session.add(item)
    session.commit()

    session.add(ContentAnalysis(content_item_id=item.id, analysis_version="v1"))
    session.add(ContentAnalysis(content_item_id=item.id, analysis_version="v2"))
    session.commit()
