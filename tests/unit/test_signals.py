from datetime import UTC, datetime, timedelta

from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem
from culture.models.signal import Signal, SignalEvidence
from culture.models.source import Source
from culture.schemas.signal import SignalLinkDecision, SignalMatchResponse
from culture.services.signals import SignalService, compute_lifecycle, refresh_signal

NOW = datetime.now(UTC)


class FakeProvider:
    name = "fake"
    model = "fake-1"

    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def generate_structured(self, system, user, output_format):
        self.prompts.append(user)
        return self.responses.pop(0)

    def generate_text(self, system, user, max_tokens=16000):
        return "text"


def make_analyzed_item(session, source, title, cities=None, saturation=None, days_ago=1):
    item = ContentItem(
        source_id=source.id,
        url=f"https://x.example/{title.replace(' ', '-').lower()}",
        title=title,
        content_type="article",
        published_at=NOW - timedelta(days=days_ago),
        processing_status="analyzed",
    )
    session.add(item)
    session.flush()
    session.add(
        ContentAnalysis(
            content_item_id=item.id,
            summary=f"Summary of {title}.",
            cities=cities or [],
            possible_signals=[f"signal from {title}"],
            saturation_risk_score=saturation,
            editorial_momentum_score=3,
            analysis_model="m", analysis_provider="fake", analysis_version="1",
        )
    )
    session.commit()
    return item


def make_source(session, name="Pub A"):
    source = Source(name=name, platform="web", tier="core")
    session.add(source)
    session.commit()
    return source


def link(item_id, existing=None, new_name=None, new_desc=None):
    return SignalLinkDecision(
        content_item_id=item_id,
        existing_signal_id=existing,
        new_signal_name=new_name,
        new_signal_description=new_desc,
        note="test",
    )


def test_new_signal_created_with_evidence_and_aggregates(session):
    source = make_source(session)
    a = make_analyzed_item(session, source, "Workwear One", cities=["London"], days_ago=5)
    b = make_analyzed_item(session, source, "Workwear Two", cities=["London", "Tokyo"], days_ago=1)
    provider = FakeProvider([
        SignalMatchResponse(links=[
            link(a.id, new_name="Japanese workwear in London menswear", new_desc="desc"),
            link(b.id, existing=None, new_name="Japanese workwear in London menswear"),
        ])
    ])

    stats = SignalService(session, provider).update_signals()

    assert stats.signals_created == 1  # second "new" deduped by name onto the first
    assert stats.links_created == 2
    assert stats.items_processed == 2
    signal = session.query(Signal).one()
    assert signal.evidence_count == 2
    assert signal.source_count == 1
    assert signal.cities == ["London", "Tokyo"]
    assert signal.first_detected_at.date() == (NOW - timedelta(days=5)).date()
    assert signal.last_evidence_at.date() == (NOW - timedelta(days=1)).date()
    assert signal.lifecycle_stage == "emerging"
    assert a.signals_processed_at is not None


def test_linking_to_existing_signal_accumulates(session):
    source_a = make_source(session, "Pub A")
    source_b = make_source(session, "Pub B")
    first = make_analyzed_item(session, source_a, "First Evidence")
    provider1 = FakeProvider([
        SignalMatchResponse(links=[link(first.id, new_name="Gorpcore normalization")])
    ])
    SignalService(session, provider1).update_signals()
    signal = session.query(Signal).one()

    second = make_analyzed_item(session, source_b, "Second Evidence")
    provider2 = FakeProvider([
        SignalMatchResponse(links=[link(second.id, existing=signal.id)])
    ])
    stats = SignalService(session, provider2).update_signals()

    assert stats.signals_created == 0
    assert signal.evidence_count == 2
    assert signal.source_count == 2


def test_invalid_links_skipped_items_still_marked_processed(session):
    source = make_source(session)
    item = make_analyzed_item(session, source, "Valid Item")
    provider = FakeProvider([
        SignalMatchResponse(links=[
            link(999999, new_name="Ghost signal"),                    # unknown item
            link(item.id),                                            # neither existing nor new
            link(item.id, existing=424242),                           # unknown signal id
        ])
    ])
    stats = SignalService(session, provider).update_signals()

    assert stats.invalid_links == 3
    assert stats.links_created == 0
    assert stats.signals_created <= 1  # ghost signal may exist but has no evidence
    assert item.signals_processed_at is not None


def test_items_with_no_links_are_processed_once(session):
    source = make_source(session)
    make_analyzed_item(session, source, "Boring PR Item")
    provider = FakeProvider([SignalMatchResponse(links=[])])
    service = SignalService(session, provider)
    stats = service.update_signals()
    assert stats.items_processed == 1

    # second run: nothing pending, provider not called again
    stats2 = SignalService(session, FakeProvider([])).update_signals()
    assert stats2.items_processed == 0


def test_duplicate_evidence_is_noop(session):
    source = make_source(session)
    item = make_analyzed_item(session, source, "Dup Evidence")
    provider = FakeProvider([
        SignalMatchResponse(links=[
            link(item.id, new_name="Some signal"),
            link(item.id, new_name="Some signal"),
        ])
    ])
    SignalService(session, provider).update_signals()
    assert session.query(SignalEvidence).count() == 1


def test_failed_batch_leaves_items_for_next_run(session):
    source = make_source(session)
    item = make_analyzed_item(session, source, "Retry Me")

    class ExplodingProvider(FakeProvider):
        def generate_structured(self, system, user, output_format):
            raise RuntimeError("api down")

    stats = SignalService(session, ExplodingProvider([])).update_signals()
    assert stats.items_processed == 0
    session.expire_all()
    assert item.signals_processed_at is None  # will be retried


def test_registry_passed_to_prompt(session):
    source = make_source(session)
    first = make_analyzed_item(session, source, "Seed Item")
    SignalService(
        session, FakeProvider([SignalMatchResponse(links=[link(first.id, new_name="Named Signal")])])
    ).update_signals()

    second = make_analyzed_item(session, source, "Later Item")
    provider = FakeProvider([SignalMatchResponse(links=[])])
    SignalService(session, provider).update_signals()
    assert "Named Signal" in provider.prompts[0]
    assert "Later Item" in provider.prompts[0]


def test_compute_lifecycle_heuristic():
    assert compute_lifecycle(1, 1, 0, None, None, None) == "unknown"
    assert compute_lifecycle(2, 1, 3, None, None, None) == "emerging"
    assert compute_lifecycle(6, 3, 20, None, None, None) == "strengthening"
    assert compute_lifecycle(9, 4, 30, 2.0, 4.5, 3.5) == "mainstream"
    assert compute_lifecycle(9, 4, 30, 4.2, 4.5, 3.5) == "saturated"


def test_refresh_signal_score_averages(session):
    source = make_source(session)
    a = make_analyzed_item(session, source, "Sat A", saturation=4)
    b = make_analyzed_item(session, source, "Sat B", saturation=5)
    signal = Signal(name="Oversaturated thing")
    session.add(signal)
    session.flush()
    session.add_all([
        SignalEvidence(signal_id=signal.id, content_item_id=a.id),
        SignalEvidence(signal_id=signal.id, content_item_id=b.id),
    ])
    session.commit()

    refresh_signal(session, signal)
    assert signal.saturation_risk_score == 4  # round(4.5) banker's rounding
    assert signal.lifecycle_stage == "saturated"
    assert signal.cultural_origin_score is None
