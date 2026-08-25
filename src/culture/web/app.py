"""Web interface over the intelligence database.

A viewing and exploration layer: the pipeline writes, this only reads.
Auth is opt-in via require_auth= (see culture.web.auth) — off for local/test
use, on for the hosted deployment.
"""

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import markdown as md
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from culture.database import get_engine
from culture.logging import get_logger
from culture.models.content import ContentItem, ExtractionStatus, TranscriptStatus
from culture.models.report import WeeklyReport
from culture.models.signal import Signal, SignalEvidence
from culture.models.source import Source
from culture.utils.dates import ensure_utc, now_utc
from culture.web import queries

# Presentation-layer merge only (not a data fix) — the same city shows up
# under a few spellings in free-text signal.cities because entity
# extraction isn't normalized. Source.city (the canonical list sources are
# tagged with) doesn't have this problem, so the public homepage counts
# signals against that canonical list via these aliases.
CITY_ALIASES = {
    "nyc": "New York",
    "new york city": "New York",
    "la": "Los Angeles",
    "los angeles": "Los Angeles",
}

log = get_logger("culture.web.app")

TEMPLATES_DIR = Path(__file__).parent / "templates"

SCORE_LABELS = [
    ("Cultural origin", "cultural_origin_score"),
    ("Editorial momentum", "editorial_momentum_score"),
    ("Urban adoption", "urban_adoption_score"),
    ("Meme recognition", "meme_recognition_score"),
    ("Creator adoption", "creator_adoption_score"),
    ("Commercial evidence", "commercial_evidence_score"),
    ("Saturation risk", "saturation_risk_score"),
    ("Longevity", "longevity_score"),
]

ENTITY_LABELS = [
    ("People", "people"),
    ("Brands", "brands"),
    ("Products", "products"),
    ("Designers", "designers"),
    ("Creators", "creators"),
    ("Artists & musicians", "artists"),
    ("Cities", "cities"),
    ("Neighborhoods", "neighborhoods"),
    ("Scenes & subcultures", "scenes"),
    ("Garments & footwear", "garments"),
    ("Places", "restaurants"),
    ("Cultural references", "media_references"),
]


@dataclass
class EvidenceView:
    item: ContentItem
    source: Source
    note: str | None
    summary: str | None
    when: datetime | None
    limitations: list[str]


@dataclass
class DiffusionStep:
    item: ContentItem
    source_name: str
    when: datetime
    cities: list[str]


def item_limitations(item: ContentItem) -> list[str]:
    notes = []
    if item.content_type == "post":
        if item.metadata_json.get("images"):
            notes.append("Manually submitted social post — analysis includes the attached image.")
        else:
            notes.append("Manually submitted social post — caption only, visual not captured.")
    elif item.content_type == "podcast":
        notes.append("Podcast episode — analysis is based on show notes only.")
    elif item.content_type == "video":
        if item.transcript_status != TranscriptStatus.AVAILABLE.value:
            notes.append("No transcript — analysis is metadata-only; spoken content unknown.")
    elif item.extraction_status == ExtractionStatus.PARTIAL.value:
        notes.append("Partial extraction — likely a teaser or paywall stub.")
    elif item.extraction_status == ExtractionStatus.FAILED.value:
        notes.append("Text could not be extracted — analysis is metadata-only.")
    return notes


def stage_reason(signal: Signal) -> str:
    span = ""
    if signal.first_detected_at and signal.last_evidence_at:
        days = (signal.last_evidence_at - signal.first_detected_at).days
        span = f" spanning {days} day{'s' if days != 1 else ''}"
    base = (
        f"Staged ‘{queries.STAGE_LABELS[signal.lifecycle_stage]}’ from "
        f"{signal.evidence_count} evidence item{'s' if signal.evidence_count != 1 else ''} "
        f"across {signal.source_count} source{'s' if signal.source_count != 1 else ''}{span}."
    )
    if signal.evidence_count <= 2:
        base += " Early classification — treat as provisional until more sources corroborate."
    return base


def _mount_auth_routes(app: FastAPI) -> None:
    from fastapi import Form
    from fastapi.responses import RedirectResponse

    from culture.config import get_settings
    from culture.web import auth

    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    @app.get("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_form(request: Request, next: str = "/dashboard", sent: str | None = None):
        return templates.TemplateResponse(
            request, "login.html", {"next": next, "sent": sent, "error": None}
        )

    @app.post("/login", response_class=HTMLResponse, include_in_schema=False)
    def login_submit(request: Request, email: str = Form(...), next: str = Form("/dashboard")):
        settings = get_settings()
        normalized = email.strip().lower()
        if normalized in settings.allowed_email_set:
            token = auth.create_login_token(normalized, settings)
            base_url = str(request.base_url)
            try:
                auth.send_login_email(normalized, token, base_url, settings)
            except Exception:
                log.error("failed to send login email to %s", normalized, exc_info=True)
                return templates.TemplateResponse(
                    request,
                    "login.html",
                    {
                        "next": next,
                        "sent": None,
                        "error": "Could not send the sign-in email. Try again shortly.",
                    },
                )
        # Same response whether or not the email is allow-listed — don't leak the list.
        return templates.TemplateResponse(
            request, "login.html", {"next": next, "sent": email, "error": None}
        )

    @app.get("/auth/verify", include_in_schema=False)
    def verify(token: str, next: str = "/dashboard"):
        settings = get_settings()
        email = auth.verify_login_token(token, settings)
        if email is None or email not in settings.allowed_email_set:
            return RedirectResponse("/login?error=expired", status_code=303)
        response = RedirectResponse(next or "/dashboard", status_code=303)
        auth.set_session_cookie(response, email, settings)
        return response

    @app.get("/logout", include_in_schema=False)
    def logout():
        response = RedirectResponse("/login", status_code=303)
        auth.clear_session_cookie(response)
        return response


def create_app(
    engine: Engine | None = None,
    require_auth: bool = False,
) -> FastAPI:
    app = FastAPI(title="Culture Intelligence", docs_url=None, redoc_url=None)
    if require_auth:
        from culture.web.auth import AuthMiddleware

        app.add_middleware(AuthMiddleware)
        _mount_auth_routes(app)
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    templates.env.globals["stage_labels"] = queries.STAGE_LABELS
    templates.env.globals["stage_glyphs"] = queries.STAGE_GLYPHS
    engine = engine or get_engine()
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def db() -> Iterator[Session]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    def render(request: Request, session: Session | None, template: str, context: dict):
        context.setdefault("q", None)
        context.setdefault(
            "freshness", queries.pipeline_status(session) if session is not None else None
        )
        return templates.TemplateResponse(request, template, context)

    def monitored_city_names(session: Session) -> list[str]:
        active_sources = session.scalars(select(Source).where(Source.active.is_(True)))
        return sorted({s.city for s in active_sources if s.city})

    def public_footer_stats(session: Session, active_signals: list[Signal]) -> dict:
        return {
            "sources": sum(1 for s in session.scalars(select(Source)) if s.active),
            "signals": len(active_signals),
            "cities": len(monitored_city_names(session)),
        }

    def hero_stats(top: Signal | None) -> dict:
        if top is None:
            return {"observations": 0, "sources": 0, "first_detected": "—", "confidence": "—"}
        detected = top.first_detected_at.strftime("%-d %b") if top.first_detected_at else "—"
        return {
            "observations": top.evidence_count,
            "sources": top.source_count,
            "first_detected": detected,
            "confidence": "medium" if top.source_count >= 3 else "early",
        }

    def latest_public_report(session: Session) -> WeeklyReport | None:
        return session.scalar(
            select(WeeklyReport)
            .where(WeeklyReport.is_public.is_(True))
            .order_by(WeeklyReport.iso_week.desc())
        )

    def report_excerpt(content_markdown: str, max_words: int = 130) -> str:
        """Plain-text teaser from Part 2 (the synthesis), not the raw source
        roundup in Part 1 — trims markdown emphasis/heading marks lightly
        rather than rendering full HTML, since this is a one-paragraph
        homepage teaser, not the full report view."""
        import re as _re

        match = _re.search(r"^# Part 2.*?\n+", content_markdown, _re.MULTILINE)
        text = content_markdown[match.end() :] if match else content_markdown
        text = _re.sub(r"^#{1,6}\s*.*$", "", text, flags=_re.MULTILINE)  # drop headings
        text = _re.sub(r"[*_`]", "", text)  # light emphasis stripping
        words = text.split()
        excerpt = " ".join(words[:max_words])
        return excerpt + ("…" if len(words) > max_words else "")

    def weekly_movement_stats(session: Session, active_signals: list[Signal]) -> dict:
        """Real counts, computed independently of report text — not parsed
        from the narrative, so they can't drift from what the DB shows."""
        week_ago = now_utc() - timedelta(days=7)
        moved = [
            s for s in active_signals if (ev := ensure_utc(s.last_evidence_at)) and ev >= week_ago
        ]
        new_count = sum(
            1
            for s in active_signals
            if (fd := ensure_utc(s.first_detected_at)) and fd >= week_ago
        )
        strengthening_count = sum(1 for s in moved if s.lifecycle_stage == "strengthening")
        return {
            "strengthening": strengthening_count,
            "new_this_week": new_count,
            "updated": len(moved),
        }

    @app.get("/", response_class=HTMLResponse)
    def public_home(request: Request, session: Session = Depends(db)):
        from culture.config import get_settings
        from culture.web import auth as auth_module

        settings = get_settings()
        cookie = request.cookies.get(auth_module.SESSION_COOKIE)
        if (
            cookie
            and settings.session_secret
            and auth_module.verify_session_value(cookie, settings) is not None
        ):
            return RedirectResponse("/dashboard", status_code=303)

        rows = queries.signal_rows(session, sort="evidence")
        active = queries.active_signals(session)
        monitored_cities = monitored_city_names(session)

        def canonical_city(raw: str) -> str | None:
            name = CITY_ALIASES.get(raw.strip().lower(), raw.strip())
            return name if name in monitored_cities else None

        signal_count_by_city: Counter[str] = Counter()
        strengthening_by_city: Counter[str] = Counter()
        for s in active:
            seen = {c for raw in s.cities if (c := canonical_city(raw))}
            for c in seen:
                signal_count_by_city[c] += 1
                if s.lifecycle_stage == "strengthening":
                    strengthening_by_city[c] += 1
        city_rows = [
            {
                "name": c,
                "signal_count": signal_count_by_city[c],
                "strengthening": strengthening_by_city[c],
            }
            for c in sorted(monitored_cities, key=lambda c: -signal_count_by_city[c])
            if signal_count_by_city[c] > 0
        ][:6]

        featured = rows[:4]
        pulse = rows[:2]
        top = rows[0].signal if rows else None

        multi_city = next((r.signal for r in rows if len(r.signal.cities) >= 2), None)

        noise_candidates = sorted(
            (r for r in rows if r.signal.source_count == 1), key=lambda r: -r.signal.evidence_count
        )
        noise_example = noise_candidates[0].signal if noise_candidates else (top or Signal())
        strong_example = max(rows, key=lambda r: r.signal.source_count).signal if rows else Signal()

        entity_web_nodes = (
            [top.name.split(" ")[0]] + top.categories[:5] if top and top.categories else []
        )

        report_row = latest_public_report(session)

        return render(
            request,
            session,
            "homepage.html",
            {
                "monitored_cities": monitored_cities,
                "pulse_signals": pulse,
                "featured_signals": featured,
                "city_rows": city_rows,
                "city_link_a": multi_city.cities[0] if multi_city else None,
                "city_link_b": multi_city.cities[1] if multi_city else None,
                "hero_stats": hero_stats(top),
                "entity_web_nodes": entity_web_nodes,
                "noise_example": {
                    "posts": noise_example.evidence_count,
                    "source_count": noise_example.source_count,
                },
                "strong_example": {
                    "evidence_count": strong_example.evidence_count,
                    "source_count": strong_example.source_count,
                },
                "stage_order": queries.STAGE_ORDER,
                "lifecycle_example_stage": top.lifecycle_stage if top else "unknown",
                "report": report_row,
                "report_excerpt": (
                    report_excerpt(report_row.content_markdown) if report_row else None
                ),
                "week_stats": weekly_movement_stats(session, active),
                "footer_stats": public_footer_stats(session, active),
            },
        )

    @app.get("/intelligence", response_class=HTMLResponse)
    def intelligence(request: Request, session: Session = Depends(db)):
        rows = queries.signal_rows(session, sort="evidence")[:15]
        active = queries.active_signals(session)
        return render(
            request,
            session,
            "intelligence.html",
            {"rows": rows, "footer_stats": public_footer_stats(session, active)},
        )

    @app.get("/dashboard", response_class=HTMLResponse)
    def index(request: Request, session: Session = Depends(db)):
        rows = queries.signal_rows(session, sort="moving")
        movers = [r for r in rows if r.delta_7d > 0][:8] or rows[:6]
        fortnight = now_utc() - timedelta(days=14)
        newly = [
            r.signal
            for r in rows
            if (fd := ensure_utc(r.signal.first_detected_at)) and fd >= fortnight
        ][:5]
        weak = [r.signal for r in rows if r.signal.evidence_count == 1][:5]
        approaching = [r for r in rows if r.signal.lifecycle_stage in ("mainstream", "saturated")][
            :6
        ]
        sources = {s.id: s for s in session.scalars(select(Source))}
        latest_items = []
        for item in session.scalars(
            select(ContentItem)
            .order_by(func.coalesce(ContentItem.published_at, ContentItem.discovered_at).desc())
            .limit(5)
        ):
            latest_items.append(
                {
                    "item": item,
                    "source_name": sources[item.source_id].name,
                    "when": queries.item_time(item),
                }
            )
        return render(
            request,
            session,
            "index.html",
            {
                "counts": {
                    "signals": len(rows),
                    "sources": sum(1 for s in sources.values() if s.active),
                },
                "movers": movers,
                "newly_detected": newly,
                "weak_signals": weak,
                "approaching": approaching,
                "top_cities": queries.city_rows(session)[:5],
                "latest_items": latest_items,
                "latest_report": session.scalar(
                    select(WeeklyReport.iso_week).order_by(WeeklyReport.generated_at.desc())
                ),
                "today": now_utc().strftime("%A %d %B %Y"),
                "active_nav": "home",
            },
        )

    @app.get("/signals", response_class=HTMLResponse)
    def signals(
        request: Request,
        session: Session = Depends(db),
        stage: str | None = None,
        sort: str = "moving",
    ):
        rows = queries.signal_rows(session, stage=stage, sort=sort)
        return render(
            request,
            session,
            "signals.html",
            {
                "rows": rows,
                "stage": stage,
                "sort": sort,
                "stage_order": queries.STAGE_ORDER,
                "active_nav": "signals",
            },
        )

    @app.get("/signals/{signal_id}", response_class=HTMLResponse)
    def signal_detail(signal_id: int, request: Request, session: Session = Depends(db)):
        signal = session.get(Signal, signal_id)
        if signal is None:
            raise HTTPException(404, "No such signal")
        pairs = queries.evidence_items_for_signal(session, signal.id)
        analyses = queries.latest_analyses(session, [i.id for i, _ in pairs])
        sources = {s.id: s for s in session.scalars(select(Source))}
        evidence: list[EvidenceView] = []
        for item, note in pairs:
            analysis = analyses.get(item.id)
            evidence.append(
                EvidenceView(
                    item=item,
                    source=sources[item.source_id],
                    note=note,
                    summary=analysis.summary if analysis else None,
                    when=queries.item_time(item),
                    limitations=item_limitations(item),
                )
            )
        evidence.sort(key=lambda e: e.when or now_utc(), reverse=True)
        diffusion_steps = [
            DiffusionStep(
                item=e.item,
                source_name=e.source.name,
                when=e.when,
                cities=(analyses[e.item.id].cities[:3] if e.item.id in analyses else []),
            )
            for e in sorted(evidence, key=lambda e: e.when or now_utc())
            if e.when
        ]
        archetype_obs = []
        for item, _ in pairs:
            analysis = analyses.get(item.id)
            if analysis:
                for text in analysis.consumer_archetypes or []:
                    archetype_obs.append(
                        queries.ArchetypeObservation(
                            text=text,
                            item=item,
                            source=sources[item.source_id],
                            cities=(analysis.cities or [])[:3],
                            when=queries.item_time(item),
                        )
                    )
        series = queries.monthly_series(session, signal.id)
        month_names = _trailing_month_labels(len(series))
        return render(
            request,
            session,
            "signal_detail.html",
            {
                "signal": signal,
                "evidence": evidence,
                "delta": queries.evidence_deltas(session, [signal.id]).get(signal.id, 0),
                "related": queries.related_signals(session, signal.id),
                "archetype_observations": archetype_obs[:6],
                "scores": [(label, getattr(signal, attr)) for label, attr in SCORE_LABELS],
                "big_spark": queries.sparkline_svg(series, width=1000, height=46),
                "ledger_start": month_names[0],
                "ledger_end": month_names[-1],
                "diffusion_steps": diffusion_steps,
                "stage_reason": stage_reason(signal),
                "active_nav": "signals",
            },
        )

    @app.get("/taste-systems", response_class=HTMLResponse)
    def taste_systems(request: Request, session: Session = Depends(db)):
        raw_pairs = queries.co_occurring_pairs(session)
        raw_pairs.sort(key=lambda t: t[2], reverse=True)
        pairs = []
        for a_id, b_id, shared in raw_pairs[:20]:
            a, b = session.get(Signal, a_id), session.get(Signal, b_id)
            if a and b:
                pairs.append((a, b, shared))
        return render(
            request, session, "taste_systems.html", {"pairs": pairs, "active_nav": "taste-systems"}
        )

    @app.get("/archetypes", response_class=HTMLResponse)
    def archetypes(
        request: Request,
        session: Session = Depends(db),
        q: str | None = None,
        city: str | None = None,
    ):
        observations = queries.archetype_observations(session, query=q, city=city)
        return render(
            request,
            session,
            "archetypes.html",
            {"observations": observations, "q": q, "city": city, "active_nav": "archetypes"},
        )

    @app.get("/cities", response_class=HTMLResponse)
    def cities(request: Request, session: Session = Depends(db)):
        return render(
            request,
            session,
            "cities.html",
            {"cities": queries.city_rows(session), "active_nav": "cities"},
        )

    @app.get("/cities/{name}", response_class=HTMLResponse)
    def city(name: str, request: Request, session: Session = Depends(db)):
        detail = queries.city_detail(session, name)
        if detail["item_count"] == 0:
            raise HTTPException(404, "No evidence references this city")
        return render(
            request,
            session,
            "city_detail.html",
            {"name": name, "detail": detail, "active_nav": "cities"},
        )

    @app.get("/items/{item_id}", response_class=HTMLResponse)
    def item_detail(item_id: int, request: Request, session: Session = Depends(db)):
        item = session.get(ContentItem, item_id)
        if item is None:
            raise HTTPException(404, "No such evidence record")
        source = session.get(Source, item.source_id)
        analysis = queries.latest_analyses(session, [item.id]).get(item.id)
        signal_ids = [
            ev.signal_id
            for ev in session.scalars(
                select(SignalEvidence).where(SignalEvidence.content_item_id == item.id)
            )
        ]
        signals = [s for sid in signal_ids if (s := session.get(Signal, sid))]
        entity_rows = []
        if analysis:
            for label, attr in ENTITY_LABELS:
                values = list(getattr(analysis, attr) or [])
                if attr == "artists":
                    values += analysis.musicians or []
                if attr == "scenes":
                    values += analysis.subcultures or []
                if attr == "garments":
                    values += analysis.footwear or []
                if attr == "restaurants":
                    values += (analysis.cafes or []) + (analysis.clubs or [])
                if values:
                    entity_rows.append((label, values[:12]))
        return render(
            request,
            session,
            "item_detail.html",
            {
                "item": item,
                "source": source,
                "analysis": analysis,
                "signals": signals,
                "entity_rows": entity_rows,
                "when": queries.item_time(item),
                "limitations": item_limitations(item),
                "active_nav": None,
            },
        )

    @app.get("/search", response_class=HTMLResponse)
    def search(request: Request, session: Session = Depends(db), q: str | None = None):
        results = queries.search_everything(session, q or "")
        return render(
            request,
            session,
            "search.html",
            {"results": results, "q": q, "active_nav": None},
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
        analyses = queries.latest_analyses(session, [i.id for i in items])
        rows = [
            {
                "item": item,
                "source": sources.get(item.source_id),
                "summary": analyses[item.id].summary if item.id in analyses else None,
                "when": queries.item_time(item),
                "limitations": item_limitations(item),
            }
            for item in items
        ]
        return render(request, session, "stream.html", {"rows": rows, "active_nav": None})

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
        return render(
            request,
            session,
            "sources.html",
            {"sources": rows, "item_counts": item_counts, "active_nav": "sources"},
        )

    @app.get("/reports", response_class=HTMLResponse)
    def reports(request: Request, session: Session = Depends(db)):
        weeks = list(
            session.scalars(select(WeeklyReport.iso_week).order_by(WeeklyReport.iso_week.desc()))
        )
        return render(
            request, session, "reports.html", {"reports": weeks, "active_nav": "reports"}
        )

    @app.get("/reports/{name}", response_class=HTMLResponse)
    def report_view(name: str, request: Request, session: Session = Depends(db)):
        report_row = session.scalar(select(WeeklyReport).where(WeeklyReport.iso_week == name))
        if report_row is None:
            raise HTTPException(404, "No such report")
        html = md.markdown(report_row.content_markdown, extensions=["tables"])
        return render(
            request,
            session,
            "report_view.html",
            {"name": name, "content": html, "active_nav": "reports"},
        )

    @app.get("/brief", response_class=HTMLResponse)
    def public_brief(request: Request, session: Session = Depends(db)):
        active = queries.active_signals(session)
        stats = public_footer_stats(session, active)
        report_row = session.scalar(
            select(WeeklyReport)
            .where(WeeklyReport.is_public.is_(True))
            .order_by(WeeklyReport.iso_week.desc())
        )
        if report_row is None:
            return render(request, session, "brief.html", {"report": None, "footer_stats": stats})
        html = md.markdown(report_row.content_markdown, extensions=["tables"])
        return render(
            request,
            session,
            "brief.html",
            {"report": report_row, "content": html, "footer_stats": stats},
        )

    return app


def _trailing_month_labels(months: int) -> list[str]:
    now = now_utc()
    labels = []
    year, month = now.year, now.month
    for _ in range(months):
        labels.append(f"{year}-{month:02d}")
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return list(reversed(labels))
