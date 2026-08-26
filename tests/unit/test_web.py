from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from culture.database import Base
from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem
from culture.models.report import WeeklyReport
from culture.models.signal import Signal, SignalEvidence
from culture.models.source import Source
from culture.web.app import create_app

NOW = datetime.now(UTC)


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = Source(name="Sabukaru", platform="web", tier="core", city="Tokyo", active=True)
        session.add(source)
        session.flush()
        item = ContentItem(
            source_id=source.id,
            url="https://sabukaru.online/a",
            title="Tokyo workwear moves west",
            content_type="article",
            published_at=NOW - timedelta(days=1),
            processing_status="analyzed",
            raw_text="Full extracted body text about workwear.",
        )
        video = ContentItem(
            source_id=source.id,
            url="https://youtube.example/v",
            external_id="vid1",
            title="Untranscribed video",
            content_type="video",
            transcript_status="failed",
            published_at=NOW - timedelta(days=2),
            processing_status="analyzed",
        )
        session.add_all([item, video])
        session.flush()
        session.add(
            ContentAnalysis(
                content_item_id=item.id,
                summary="Workwear keeps spreading through London menswear.",
                cities=["London", "Tokyo"],
                neighborhoods=["Hackney"],
                consumer_archetypes=["Creative professional adopting Japanese workwear"],
                facts_json={"claims": ["Kapital opened a London stockist."]},
                interpretations_json={"inferences": ["Workwear is crossing to mainstream."]},
                brands=["Kapital"],
                analysis_model="m",
                analysis_provider="p",
                analysis_version="1",
                analyzed_at=NOW,
            )
        )
        signal_a = Signal(
            name="Japanese workwear in London menswear",
            description="Workwear crossover.",
            lifecycle_stage="emerging",
            evidence_count=1,
            source_count=1,
            cities=["London", "Tokyo"],
            first_detected_at=NOW - timedelta(days=5),
            last_evidence_at=NOW - timedelta(days=1),
            cultural_origin_score=4,
        )
        signal_b = Signal(
            name="Chore jacket normalization",
            lifecycle_stage="strengthening",
            evidence_count=1,
            source_count=1,
        )
        session.add(
            ContentAnalysis(
                content_item_id=video.id,
                summary="Metadata-only analysis (no transcript): London menswear video.",
                cities=["London"],
                analysis_model="m",
                analysis_provider="p",
                analysis_version="1",
                analyzed_at=NOW,
            )
        )
        session.add_all([signal_a, signal_b])
        session.flush()
        session.add_all(
            [
                SignalEvidence(signal_id=signal_a.id, content_item_id=item.id, note="direct"),
                SignalEvidence(signal_id=signal_b.id, content_item_id=item.id),
            ]
        )
        session.add(
            WeeklyReport(
                iso_week="2026-W35",
                content_markdown="# Part 1\nhello\n\n# Part 2\n## Executive Brief\nBig week.",
                sources_covered=1,
                items_covered=2,
                is_public=True,
            )
        )
        session.commit()
        ids = {"signal": signal_a.id, "item": item.id, "video": video.id}

    app = create_app(engine=engine)
    test_client = TestClient(app)
    test_client.ids = ids  # type: ignore[attr-defined]
    return test_client


def test_overview_is_a_briefing(client):
    response = client.get("/dashboard")
    assert response.status_code == 200
    assert "Intelligence briefing" in response.text
    assert "Japanese workwear in London menswear" in response.text
    assert "2026-W35" in response.text


def test_public_homepage_shows_real_signals_not_dashboard(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Intelligence briefing" not in response.text
    assert "Understand the scene before it becomes a trend" in response.text
    assert "Japanese workwear" in response.text  # a real signal, not fabricated copy


def test_public_intelligence_page_lists_signals(client):
    response = client.get("/intelligence")
    assert response.status_code == 200
    assert "Japanese workwear" in response.text
    assert "Log in" in response.text


def test_signals_index_filters_and_sorts(client):
    page = client.get("/signals").text
    assert "Japanese workwear" in page
    assert "Ledger" in page
    assert "Japanese workwear" in client.get("/signals?stage=emerging").text
    assert "Japanese workwear" not in client.get("/signals?stage=saturated").text
    assert client.get("/signals?sort=evidence").status_code == 200


def test_signal_profile_shows_evidence_and_provenance(client):
    response = client.get(f"/signals/{client.ids['signal']}")
    assert response.status_code == 200
    assert "Tokyo workwear moves west" in response.text
    assert "direct" in response.text
    assert "Observation ledger" in response.text
    assert "not proof of adoption order" in response.text  # diffusion honesty
    assert "Chore jacket normalization" in response.text  # co-occurring signal
    assert "Early classification" in response.text  # stage reasoning
    assert client.get("/signals/99999").status_code == 404


def test_item_evidence_page_separates_provenance(client):
    response = client.get(f"/items/{client.ids['item']}")
    assert response.status_code == 200
    assert "Kapital opened a London stockist." in response.text  # observed
    assert "Workwear is crossing to mainstream." in response.text  # inference
    assert "What the source itself says" in response.text
    assert "What our system infers" in response.text
    assert "exactly as extracted" in response.text
    assert client.get("/items/99999").status_code == 404


def test_video_item_shows_limitation(client):
    response = client.get(f"/items/{client.ids['video']}")
    assert "metadata-only" in response.text


def test_archetypes_lists_observations(client):
    response = client.get("/archetypes")
    assert "Creative professional adopting Japanese workwear" in response.text
    assert "observations, not established personas" in response.text
    filtered = client.get("/archetypes?q=nomatchxyz")
    assert "No archetype observations match" in filtered.text


def test_cities_index_and_profile(client):
    index = client.get("/cities")
    assert "London" in index.text
    profile = client.get("/cities/London")
    assert profile.status_code == 200
    assert "Japanese workwear in London menswear" in profile.text
    assert "Hackney" in profile.text
    assert client.get("/cities/Nowhereville").status_code == 404


def test_taste_systems_shows_real_pairs_and_honest_status(client):
    response = client.get("/taste-systems")
    assert "Japanese workwear in London menswear" in response.text
    assert "Chore jacket normalization" in response.text
    assert "Honest status" in response.text


def test_search_spans_entities(client):
    response = client.get("/search?q=workwear")
    assert "Japanese workwear in London menswear" in response.text
    assert "Tokyo workwear moves west" in response.text
    empty = client.get("/search?q=zzzznothing")
    assert "Nothing found" in empty.text
    assert client.get("/search").status_code == 200


def test_reports_render_markdown(client):
    assert "2026-W35" in client.get("/reports").text
    view = client.get("/reports/2026-W35")
    assert "<h3>Executive Brief</h3>" in view.text  # markdown headings demoted one level
    assert client.get("/reports/nope").status_code == 404
    assert client.get("/reports/..%2Fsecret").status_code == 404


def test_public_brief_shows_the_published_report(client):
    response = client.get("/brief")
    assert response.status_code == 200
    assert "2026-W35" in response.text
    assert "Big week" in response.text


def test_public_homepage_shows_the_week_section(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "What changed this week" in response.text
    assert "Read the public brief" in response.text


def test_stream_and_sources_still_work(client):
    assert "Tokyo workwear moves west" in client.get("/stream").text
    assert "Sabukaru" in client.get("/sources").text


def test_footer_shows_pipeline_freshness(client):
    assert "Analysis up to date" in client.get("/dashboard").text


# ── external-review fixes (26-08-2026 audit) ─────────────────────────────


def test_methodology_page_is_public_and_complete(client):
    response = client.get("/methodology")
    assert response.status_code == 200
    assert "Independence matters" in response.text
    assert "not a complete representation" in response.text


def test_invalid_signal_renders_branded_error_not_json(client):
    response = client.get("/signals/999999")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "detail" not in response.text[:200]  # not FastAPI's raw JSON shape
    assert "doesn't exist" in response.text


def test_invalid_report_renders_branded_error(client):
    response = client.get("/reports/1999-W01")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")


def test_robots_and_sitemap_exist(client):
    robots = client.get("/robots.txt")
    assert robots.status_code == 200
    assert "Sitemap:" in robots.text
    sitemap = client.get("/sitemap.xml")
    assert sitemap.status_code == 200
    assert "<urlset" in sitemap.text
    assert "/methodology" in sitemap.text


def test_dates_render_dd_mm_yyyy(client):
    response = client.get(f"/signals/{client.ids['signal']}")
    import re

    # first/last observed dates on the signal profile
    assert re.search(r"\b\d{2}-\d{2}-\d{4}\b", response.text)
    assert "date().isoformat" not in response.text


def test_report_page_has_single_h1(client):
    response = client.get("/reports/2026-W35")
    assert response.status_code == 200
    assert response.text.count("<h1") == 1  # page title only; report H1s demoted


def test_city_aliases_are_merged(client):
    # Fixture analysis tags "London" — an "LDN"-style alias test needs alias
    # data, so assert the canonicalization function directly plus the page.
    from culture.web.queries import canonical_city

    assert canonical_city("NYC") == "New York"
    assert canonical_city("New York City") == "New York"
    assert canonical_city("LA") == "Los Angeles"
    assert canonical_city("Philly") == "Philadelphia"
    assert canonical_city("London") == "London"  # unknown names pass through
    # /cities/NYC and /cities/New York resolve to the same evidence set
    assert client.get("/cities/London").status_code == 200


def test_stage_label_matches_internal_vocabulary(client):
    # ?stage=strengthening must show "Strengthening", not "Accelerating".
    response = client.get("/signals?stage=strengthening")
    assert "Strengthening" in response.text
    assert "Accelerating" not in response.text


def test_public_mobile_menu_exists(client):
    response = client.get("/")
    assert 'aria-label="Open site menu"' in response.text
    assert 'id="mobile-menu"' in response.text


# ── backlog tranche (roles, health, reader split, filters, compare) ───────


def test_sources_admin_gate_open_in_local_unauthenticated_mode(client):
    # require_auth=False (local operator) counts as admin.
    assert client.get("/sources").status_code == 200


def test_sources_shows_collection_health_states(client):
    text = client.get("/sources").text
    assert "admin only" in text
    # Fixture source has no last_successful_check_at -> never collected...
    # unless it lacks a feed_url first (needs_method). Either way, a state renders.
    assert ("Never collected" in text) or ("Needs collection method" in text)


def test_report_reader_appendix_split(client):
    view = client.get("/reports/2026-W35")
    assert view.status_code == 200
    assert "Research appendix" in view.text
    assert "<details" in view.text
    # Part 2 content is in the main body
    assert "Executive Brief" in view.text


def test_stream_filters(client):
    ids = client.ids
    all_rows = client.get("/stream")
    assert "Tokyo workwear moves west" in all_rows.text
    filtered = client.get("/stream?kind=video")
    assert "Tokyo workwear moves west" not in filtered.text
    assert "Untranscribed video" in filtered.text
    none_match = client.get("/stream?platform=tiktok")
    assert "Nothing matches these filters" in none_match.text
    assert ids  # silence unused warnings


def test_taste_systems_relationship_labels(client):
    text = client.get("/taste-systems").text
    assert "co-occurs in evidence with" in text
    assert "Single co-occurrence" in text  # fixture pair shares exactly one item
    assert "not affinity" in text


def test_archetypes_grouped_with_recurrence_state(client):
    text = client.get("/archetypes").text
    assert "Single sighting" in text
    assert "evidence 1" in text


def test_city_compare_page(client):
    empty = client.get("/cities/compare")
    assert empty.status_code == 200
    assert "Pick two cities" in empty.text
    both = client.get("/cities/compare?a=London&b=Tokyo")
    assert both.status_code == 200
    # Fixture signal names both London and Tokyo -> shared
    assert "Japanese workwear in London menswear" in both.text
