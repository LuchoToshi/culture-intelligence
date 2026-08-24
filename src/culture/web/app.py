"""Read-only local web interface over the intelligence database.

Deliberately a viewing layer, not a product dashboard: the pipeline writes,
this only reads. No auth — bind to localhost only.
"""

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import markdown as md
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from culture.database import get_engine
from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem
from culture.models.signal import Signal, SignalEvidence, SignalState
from culture.models.source import Source
from culture.utils.dates import ensure_utc, now_utc

PROJECT_ROOT = Path(__file__).resolve().parents[3]
TEMPLATES_DIR = Path(__file__).parent / "templates"

STAGE_ORDER = ["strengthening", "mainstream", "saturated", "emerging", "declining", "unknown"]


def create_app(engine: Engine | None = None, reports_dir: Path | None = None) -> FastAPI:
    app = FastAPI(title="Culture Intelligence", docs_url=None, redoc_url=None)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    engine = engine or get_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    reports_path = reports_dir or PROJECT_ROOT / "reports"

    def db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def latest_analysis(session: Session, item_id: int) -> ContentAnalysis | None:
        return session.scalar(
            select(ContentAnalysis)
            .where(ContentAnalysis.content_item_id == item_id)
            .order_by(ContentAnalysis.id.desc())
            .limit(1)
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, session: Session = Depends(db)):
        week_ago = now_utc() - timedelta(days=7)
        signals = list(
            session.scalars(select(Signal).where(Signal.state == SignalState.ACTIVE.value))
        )
        deltas = {
            s.id: session.scalar(
                select(func.count(SignalEvidence.id)).where(
                    SignalEvidence.signal_id == s.id, SignalEvidence.created_at >= week_ago
                )
            )
            or 0
            for s in signals
        }
        movers = sorted(signals, key=lambda s: (deltas[s.id], s.evidence_count), reverse=True)[:8]
        counts = {
            "sources": session.scalar(select(func.count(Source.id)).where(Source.active.is_(True))),
            "content_items": session.scalar(select(func.count(ContentItem.id))),
            "analyzed": session.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.processing_status == "analyzed"
                )
            ),
            "pending": session.scalar(
                select(func.count(ContentItem.id)).where(
                    ContentItem.processing_status.in_(["new", "ready", "failed"])
                )
            ),
            "signals": len(signals),
        }
        reports = sorted(reports_path.glob("*-W*.md"), reverse=True)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "counts": counts,
                "movers": [(s, deltas[s.id]) for s in movers],
                "latest_report": reports[0].stem if reports else None,
                "active_nav": "home",
            },
        )

    @app.get("/signals", response_class=HTMLResponse)
    def signals(request: Request, session: Session = Depends(db), stage: str | None = None):
        week_ago = now_utc() - timedelta(days=7)
        rows = list(session.scalars(select(Signal).where(Signal.state == SignalState.ACTIVE.value)))
        if stage:
            rows = [s for s in rows if s.lifecycle_stage == stage]
        deltas = {
            s.id: session.scalar(
                select(func.count(SignalEvidence.id)).where(
                    SignalEvidence.signal_id == s.id, SignalEvidence.created_at >= week_ago
                )
            )
            or 0
            for s in rows
        }
        rows.sort(key=lambda s: (deltas[s.id], s.evidence_count), reverse=True)
        return templates.TemplateResponse(
            request,
            "signals.html",
            {
                "signals": [(s, deltas[s.id]) for s in rows],
                "stage": stage,
                "stage_order": STAGE_ORDER,
                "active_nav": "signals",
            },
        )

    @app.get("/signals/{signal_id}", response_class=HTMLResponse)
    def signal_detail(signal_id: int, request: Request, session: Session = Depends(db)):
        signal = session.get(Signal, signal_id)
        if signal is None:
            raise HTTPException(404, "No such signal")
        evidence_rows = list(
            session.execute(
                select(SignalEvidence, ContentItem, Source)
                .join(ContentItem, SignalEvidence.content_item_id == ContentItem.id)
                .join(Source, ContentItem.source_id == Source.id)
                .where(SignalEvidence.signal_id == signal.id)
            )
        )
        evidence = []
        for ev, item, source in evidence_rows:
            analysis = latest_analysis(session, item.id)
            evidence.append(
                {
                    "item": item,
                    "source": source,
                    "note": ev.note,
                    "summary": analysis.summary if analysis else None,
                    "when": ensure_utc(item.published_at or item.discovered_at),
                }
            )
        evidence.sort(key=lambda e: e["when"] or now_utc(), reverse=True)
        scores = [
            ("Cultural origin", signal.cultural_origin_score),
            ("Editorial momentum", signal.editorial_momentum_score),
            ("Urban adoption", signal.urban_adoption_score),
            ("Meme recognition", signal.meme_recognition_score),
            ("Creator adoption", signal.creator_adoption_score),
            ("Commercial evidence", signal.commercial_evidence_score),
            ("Saturation risk", signal.saturation_risk_score),
            ("Longevity", signal.longevity_score),
        ]
        return templates.TemplateResponse(
            request,
            "signal_detail.html",
            {"signal": signal, "evidence": evidence, "scores": scores, "active_nav": "signals"},
        )

    @app.get("/stream", response_class=HTMLResponse)
    def stream(request: Request, session: Session = Depends(db)):
        items = list(
            session.scalars(
                select(ContentItem)
                .order_by(func.coalesce(ContentItem.published_at, ContentItem.discovered_at).desc())
                .limit(60)
            )
        )
        sources = {s.id: s for s in session.scalars(select(Source))}
        rows = []
        for item in items:
            analysis = latest_analysis(session, item.id)
            rows.append(
                {
                    "item": item,
                    "source": sources.get(item.source_id),
                    "summary": analysis.summary if analysis else None,
                    "when": ensure_utc(item.published_at or item.discovered_at),
                }
            )
        return templates.TemplateResponse(
            request, "stream.html", {"rows": rows, "active_nav": "stream"}
        )

    @app.get("/sources", response_class=HTMLResponse)
    def sources(request: Request, session: Session = Depends(db)):
        rows = list(session.scalars(select(Source).order_by(Source.tier, Source.name)))
        item_counts: dict[int, int] = {
            source_id: count
            for source_id, count in session.execute(
                select(ContentItem.source_id, func.count(ContentItem.id)).group_by(
                    ContentItem.source_id
                )
            )
        }
        return templates.TemplateResponse(
            request,
            "sources.html",
            {"sources": rows, "item_counts": item_counts, "active_nav": "sources"},
        )

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request):
        files = sorted(reports_path.glob("*-W*.md"), reverse=True)
        return templates.TemplateResponse(
            request, "reports.html", {"reports": [f.stem for f in files], "active_nav": "reports"}
        )

    @app.get("/reports/{name}", response_class=HTMLResponse)
    def report_view(name: str, request: Request):
        if "/" in name or ".." in name:
            raise HTTPException(404)
        path = reports_path / f"{name}.md"
        if not path.exists():
            raise HTTPException(404, "No such report")
        html = md.markdown(path.read_text(encoding="utf-8"), extensions=["tables"])
        return templates.TemplateResponse(
            request, "report_view.html", {"name": name, "content": html, "active_nav": "reports"}
        )

    return app
