import pytest

from culture.analysis.item_analyzer import ItemAnalyzer
from culture.analysis.prompts import MAX_ANALYSIS_CHARS, build_item_prompt
from culture.analysis.provider import ProviderError, get_provider
from culture.config import Settings
from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem
from culture.models.source import Source
from culture.schemas.analysis import ItemAnalysisResponse, ItemScores

GOOD_RESPONSE = ItemAnalysisResponse(
    summary="Japanese workwear labels are opening London stockists.",
    major_points=["Workwear references are moving into mainstream menswear."],
    brands=["Kapital", "orSlow"],
    cities=["London", "Tokyo"],
    garments=["chore jacket"],
    topics=["workwear", "japanese fashion"],
    possible_signals=["Japanese workwear entering mainstream London menswear"],
    why_it_matters="Signals migration from niche to mainstream.",
    facts=["Kapital opened a London stockist."],
    interpretations=["Workwear is crossing from enthusiast circles to mainstream."],
    scores=ItemScores(cultural_origin=4, editorial_momentum=3),
    lifecycle_stage="strengthening",
)


class FakeProvider:
    name = "fake"
    model = "fake-model-1"

    def __init__(self, response=GOOD_RESPONSE, fail_titles=()):
        self.response = response
        self.fail_titles = fail_titles
        self.prompts = []

    def generate_structured(self, system, user, output_format):
        self.prompts.append(user)
        for title in self.fail_titles:
            if title in user:
                raise ProviderError("simulated provider failure")
        return self.response

    def generate_text(self, system, user, max_tokens=16000):
        return "text"


def make_item(session, title="Tokyo workwear", text="Long article body here.", **overrides):
    source = session.query(Source).first()
    if source is None:
        source = Source(name="Test Pub", platform="web", tier="core", categories=["fashion"])
        session.add(source)
        session.commit()
    defaults = dict(
        source_id=source.id,
        url=f"https://example.com/{title.replace(' ', '-').lower()}",
        title=title,
        content_type="article",
        raw_text=text,
        processing_status="ready",
    )
    defaults.update(overrides)
    item = ContentItem(**defaults)
    session.add(item)
    session.commit()
    return item


def test_analysis_stores_full_row(session):
    item = make_item(session)
    provider = FakeProvider()
    stats = ItemAnalyzer(session, provider).analyze_pending()

    assert stats.analyzed == 1
    assert stats.failed == 0
    row = session.query(ContentAnalysis).one()
    assert row.content_item_id == item.id
    assert row.summary.startswith("Japanese workwear")
    assert row.brands == ["Kapital", "orSlow"]
    assert row.cities == ["London", "Tokyo"]
    assert row.cultural_origin_score == 4
    assert row.urban_adoption_score is None
    assert row.lifecycle_stage == "strengthening"
    assert row.facts_json == {"claims": ["Kapital opened a London stockist."]}
    assert row.interpretations_json == {
        "inferences": ["Workwear is crossing from enthusiast circles to mainstream."]
    }
    assert row.analysis_provider == "fake"
    assert row.analysis_model == "fake-model-1"
    assert row.analysis_version == "1"
    assert row.analyzed_at is not None
    assert item.processing_status == "analyzed"


def test_failure_marks_item_failed_and_continues(session):
    make_item(session, title="Will Fail")
    make_item(session, title="Will Succeed")
    provider = FakeProvider(fail_titles=("Will Fail",))
    stats = ItemAnalyzer(session, provider).analyze_pending()

    assert stats.analyzed == 1
    assert stats.failed == 1
    failed = session.query(ContentItem).filter_by(title="Will Fail").one()
    ok = session.query(ContentItem).filter_by(title="Will Succeed").one()
    assert failed.processing_status == "failed"
    assert ok.processing_status == "analyzed"
    assert session.query(ContentAnalysis).count() == 1


def test_failed_items_are_retried_next_run(session):
    make_item(session, title="Flaky Item")
    failing = FakeProvider(fail_titles=("Flaky Item",))
    ItemAnalyzer(session, failing).analyze_pending()
    assert session.query(ContentItem).one().processing_status == "failed"

    stats = ItemAnalyzer(session, FakeProvider()).analyze_pending()
    assert stats.analyzed == 1
    assert session.query(ContentItem).one().processing_status == "analyzed"


def test_analyzed_items_are_not_reprocessed(session):
    make_item(session)
    ItemAnalyzer(session, FakeProvider()).analyze_pending()
    stats = ItemAnalyzer(session, FakeProvider()).analyze_pending()
    assert stats.analyzed == 0
    assert session.query(ContentAnalysis).count() == 1


def test_limit_respected(session):
    for i in range(5):
        make_item(session, title=f"Item {i}")
    stats = ItemAnalyzer(session, FakeProvider()).analyze_pending(limit=2)
    assert stats.analyzed == 2


def test_boilerplate_stripped_from_prompt(session):
    boiler = "Subscribe to our excellent newsletter for daily fashion intelligence."
    for i in range(5):
        make_item(session, title=f"Item {i}", text=f"{boiler}\nUnique analysis number {i}.")
    provider = FakeProvider()
    ItemAnalyzer(session, provider).analyze_pending()
    assert all(boiler not in prompt for prompt in provider.prompts)
    assert any("Unique analysis number" in prompt for prompt in provider.prompts)


def test_video_without_transcript_prompt(session):
    source = Source(name="Channel", platform="youtube", tier="core")
    session.add(source)
    session.commit()
    item = ContentItem(
        source_id=source.id,
        url="https://www.youtube.com/watch?v=abc",
        title="A video",
        content_type="video",
        transcript_status="unavailable",
        metadata_json={"duration_seconds": 300},
    )
    prompt = build_item_prompt(source, item, None)
    assert "NO TRANSCRIPT" in prompt
    assert "TEXT: none available" in prompt
    assert "Duration: 300 seconds" in prompt


def test_long_text_truncated_in_prompt(session):
    source = Source(name="Pub2", platform="web")
    session.add(source)
    session.commit()
    item = ContentItem(
        source_id=source.id, url="https://x.example/long", content_type="article"
    )
    prompt = build_item_prompt(source, item, "x" * (MAX_ANALYSIS_CHARS + 5000))
    assert "truncated" in prompt
    assert len(prompt) < MAX_ANALYSIS_CHARS + 2000


def test_response_validation_rejects_bad_scores():
    with pytest.raises(ValueError):
        ItemScores(cultural_origin=7)
    with pytest.raises(ValueError):
        ItemAnalysisResponse(summary="ok", lifecycle_stage="exploding")


def test_provider_factory_requires_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    settings = Settings(_env_file=None, ai_provider="anthropic", anthropic_api_key="")
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        get_provider(settings)


def test_provider_factory_unknown_provider():
    settings = Settings(_env_file=None, ai_provider="tarot")
    with pytest.raises(ProviderError, match="Unknown AI_PROVIDER"):
        get_provider(settings)
