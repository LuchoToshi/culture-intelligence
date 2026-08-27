from datetime import UTC, datetime, timedelta

from culture.analysis.synthesis_prompts import build_weekly_prompt
from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem
from culture.models.source import Source
from culture.services.reporting import build_digest, generate_report, previous_synthesis

NOW = datetime.now(UTC)


class FakeProvider:
    name = "fake"
    model = "fake-synth-1"
    spent_usd = 0.0
    max_spend_usd = None

    def __init__(self):
        self.prompts = []

    def generate_structured(self, system, user, output_format):
        raise AssertionError("reporting must not call generate_structured")

    def generate_text(self, system, user, max_tokens=16000):
        self.prompts.append(user)
        return "## Executive Brief\n1. Canned synthesis for testing.\n"


def seed_world(session):
    active = Source(
        name="Active Pub",
        platform="web",
        feed_url="https://a.example/feed",
        active=True,
        tier="core",
    )
    silent = Source(
        name="Silent Pub",
        platform="web",
        feed_url="https://s.example/feed",
        active=True,
        tier="core",
    )
    feedless = Source(
        name="Feedless Pub",
        platform="web",
        active=True,
        tier="core",
        collection_notes="No RSS feed found; needs custom collector.",
    )
    channel = Source(
        name="A Channel",
        platform="youtube",
        feed_url="https://yt.example/feed",
        active=True,
        tier="core",
    )
    inactive = Source(name="Inactive IG", platform="instagram", active=False, tier="watch")
    session.add_all([active, silent, feedless, channel, inactive])
    session.commit()

    fresh = ContentItem(
        source_id=active.id,
        url="https://a.example/fresh",
        title="Fresh Story",
        content_type="article",
        published_at=NOW - timedelta(days=2),
        raw_text="body",
    )
    stale = ContentItem(
        source_id=active.id,
        url="https://a.example/stale",
        title="Stale Story",
        content_type="article",
        published_at=NOW - timedelta(days=40),
        raw_text="body",
    )
    video = ContentItem(
        source_id=channel.id,
        url="https://yt.example/v1",
        external_id="v1",
        title="A Video",
        content_type="video",
        published_at=NOW - timedelta(days=1),
        transcript_status="failed",
    )
    session.add_all([fresh, stale, video])
    session.commit()

    session.add(
        ContentAnalysis(
            content_item_id=fresh.id,
            summary="Workwear keeps spreading.",
            brands=["Kapital"],
            cities=["London"],
            possible_signals=["Workwear entering mainstream London menswear"],
            why_it_matters="Signals niche-to-mainstream migration.",
            lifecycle_stage="strengthening",
            cultural_origin_score=4,
            analysis_model="m1",
            analysis_provider="fake",
            analysis_version="1",
        )
    )
    session.commit()
    return active, silent, feedless, channel, inactive, fresh, stale, video


def test_report_covers_every_active_source(session, tmp_path):
    seed_world(session)
    provider = FakeProvider()
    result = generate_report(session, provider, days=7, reports_dir=tmp_path)
    text = result.path.read_text()

    assert result.sources_covered == 4
    assert "## Active Pub" in text
    assert "## Silent Pub" in text
    assert "## Feedless Pub" in text
    assert "## A Channel" in text
    assert "Inactive IG" not in text


def test_silent_sources_state_no_content(session, tmp_path):
    seed_world(session)
    text = generate_report(session, FakeProvider(), days=7, reports_dir=tmp_path).path.read_text()
    assert text.count("No new content found during this period.") >= 2
    assert "No RSS feed found; needs custom collector." in text


def test_window_filters_old_items(session, tmp_path):
    seed_world(session)
    result = generate_report(session, FakeProvider(), days=7, reports_dir=tmp_path)
    text = result.path.read_text()
    assert "Fresh Story" in text
    assert "Stale Story" not in text
    assert result.items_covered == 2  # fresh article + video


def test_item_rendering_includes_analysis_fields(session, tmp_path):
    seed_world(session)
    text = generate_report(session, FakeProvider(), days=7, reports_dir=tmp_path).path.read_text()
    assert "Workwear keeps spreading." in text
    assert "Kapital" in text
    assert "Workwear entering mainstream London menswear" in text
    assert "**Why it matters**" in text
    assert "Lifecycle: strengthening" in text


def test_video_without_transcript_flagged(session, tmp_path):
    seed_world(session)
    text = generate_report(session, FakeProvider(), days=7, reports_dir=tmp_path).path.read_text()
    assert "metadata-only" in text
    assert "_Not yet analyzed._" in text  # the video has no analysis row


def test_synthesis_receives_digest_not_raw_articles(session, tmp_path):
    seed_world(session)
    provider = FakeProvider()
    generate_report(session, provider, days=7, reports_dir=tmp_path)
    prompt = provider.prompts[0]
    assert "Workwear keeps spreading." in prompt
    assert "Signals: Workwear entering mainstream London menswear" in prompt
    assert "Stale Story" not in prompt
    assert "first report" in prompt  # no previous report exists
    # part 2 lands in the file
    assert "Canned synthesis" in (tmp_path / provider_filename(tmp_path)).read_text()


def provider_filename(tmp_path):
    return next(p.name for p in tmp_path.glob("*-W*.md"))


def test_previous_report_feeds_change_tracking(session, tmp_path):
    (tmp_path / "2020-W01.md").write_text(
        "# Cultural Intelligence Report\n# Part 2 — Weekly Cultural Intelligence\n"
        "Old insight about gorpcore.\n"
    )
    seed_world(session)
    provider = FakeProvider()
    generate_report(session, provider, days=7, reports_dir=tmp_path)
    assert "Old insight about gorpcore." in provider.prompts[0]
    assert "first report" not in provider.prompts[0]


def test_previous_synthesis_ignores_current_week_file(tmp_path):
    (tmp_path / "2026-W35.md").write_text("# Part 2\ncurrent week")
    assert previous_synthesis(tmp_path, "2026-W35.md") is None


def test_report_filename_is_iso_week(session, tmp_path):
    seed_world(session)
    result = generate_report(session, FakeProvider(), days=7, reports_dir=tmp_path)
    iso = NOW.isocalendar()
    assert result.path.name == f"{iso.year}-W{iso.week:02d}.md"


def test_build_digest_skips_unanalyzed(session):
    active, *_, fresh, stale, video = seed_world(session)
    from culture.services.reporting import latest_analyses

    analyses = latest_analyses(session, [fresh.id, video.id])
    digest = build_digest([active], {active.id: [fresh, video]}, analyses)
    assert "Fresh Story" in digest
    assert "A Video" not in digest


def test_report_includes_signal_registry(session, tmp_path):
    from culture.models.signal import Signal, SignalEvidence

    active, *_, fresh, stale, video = seed_world(session)
    signal = Signal(
        name="Workwear entering mainstream London menswear",
        description="Japanese workwear references crossing over.",
        lifecycle_stage="emerging",
        cities=["London"],
    )
    session.add(signal)
    session.flush()
    session.add(SignalEvidence(signal_id=signal.id, content_item_id=fresh.id))
    session.commit()
    from culture.services.signals import refresh_signal

    refresh_signal(session, signal)
    session.commit()

    provider = FakeProvider()
    result = generate_report(session, provider, days=7, reports_dir=tmp_path)
    text = result.path.read_text()

    assert "# Part 3. Signal Registry" in text
    assert "| Workwear entering mainstream London menswear |" in text
    # synthesis prompt received the registry with the weekly delta
    assert "PERSISTENT SIGNAL REGISTRY" in provider.prompts[0]
    assert "+1 this window" in provider.prompts[0]


def test_build_weekly_prompt_shapes():
    with_prior = build_weekly_prompt("digest here", "prior synthesis", 7)
    assert "prior synthesis" in with_prior
    without = build_weekly_prompt("digest here", None, 7)
    assert "first report" in without


def test_report_includes_source_registry_changes(session, tmp_path):
    from datetime import UTC as _UTC
    from datetime import datetime as _dt

    seed_world(session)
    session.add(
        Source(
            name="Freshly Discovered",
            platform="other",
            tier="candidate",
            active=False,
            source_type="discovered",
            discovery_json={"via": "entity", "mentions": 5, "citing_sources": ["A", "B", "C"]},
        )
    )
    promoted = Source(
        name="Recently Promoted",
        platform="web",
        tier="watch",
        active=True,
        discovery_json={
            "tier_history": [
                {
                    "from": "candidate",
                    "to": "watch",
                    "at": _dt.now(_UTC).isoformat(),
                    "reason": "trial passed: 4 items",
                }
            ]
        },
    )
    session.add(promoted)
    session.commit()

    text = generate_report(session, FakeProvider(), days=7, reports_dir=tmp_path).path.read_text()
    assert "## Source registry changes" in text
    assert "Recently Promoted" in text
    assert "candidate → watch" in text
    assert "Freshly Discovered" in text
    assert "unverified, not yet collected" in text
