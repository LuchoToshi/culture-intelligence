from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from culture.database import Base
from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem
from culture.models.signal import Signal, SignalEvidence
from culture.models.source import Source
from culture.web.app import create_app

NOW = datetime.now(UTC)


@pytest.fixture
def client(tmp_path):
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
        )
        session.add(item)
        session.flush()
        session.add(
            ContentAnalysis(
                content_item_id=item.id,
                summary="Workwear keeps spreading through London menswear.",
                analysis_model="m",
                analysis_provider="p",
                analysis_version="1",
            )
        )
        signal = Signal(
            name="Japanese workwear in London menswear",
            description="Workwear crossover.",
            lifecycle_stage="emerging",
            evidence_count=1,
            source_count=1,
            cities=["London", "Tokyo"],
            first_detected_at=NOW - timedelta(days=5),
            cultural_origin_score=4,
        )
        session.add(signal)
        session.flush()
        session.add(SignalEvidence(signal_id=signal.id, content_item_id=item.id, note="direct"))
        session.commit()
        signal_id = signal.id

    (tmp_path / "2026-W35.md").write_text(
        "# Part 1\nhello\n\n# Part 2\n## Executive Brief\nBig week."
    )
    app = create_app(engine=engine, reports_dir=tmp_path)
    test_client = TestClient(app)
    test_client.signal_id = signal_id  # type: ignore[attr-defined]
    return test_client


def test_index_shows_counts_and_movers(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Japanese workwear in London menswear" in response.text
    assert "Active signals" in response.text
    assert "2026-W35" in response.text


def test_signals_list_and_stage_filter(client):
    assert "Japanese workwear" in client.get("/signals").text
    assert "Japanese workwear" in client.get("/signals?stage=emerging").text
    assert "Japanese workwear" not in client.get("/signals?stage=saturated").text


def test_signal_detail_shows_evidence(client):
    response = client.get(f"/signals/{client.signal_id}")
    assert response.status_code == 200
    assert "Tokyo workwear moves west" in response.text
    assert "direct" in response.text
    assert "Workwear keeps spreading" in response.text
    assert client.get("/signals/99999").status_code == 404


def test_stream_and_sources(client):
    assert "Tokyo workwear moves west" in client.get("/stream").text
    sources_page = client.get("/sources").text
    assert "Sabukaru" in sources_page
    assert "Tokyo" in sources_page


def test_reports_render_markdown(client):
    assert "2026-W35" in client.get("/reports").text
    view = client.get("/reports/2026-W35")
    assert view.status_code == 200
    assert "<h2>Executive Brief</h2>" in view.text
    assert client.get("/reports/nope").status_code == 404
    assert client.get("/reports/..%2Fsecret").status_code == 404
