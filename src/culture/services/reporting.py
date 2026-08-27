import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from culture.analysis.provider import AIProvider
from culture.analysis.synthesis_prompts import WEEKLY_SYSTEM_PROMPT, build_weekly_prompt
from culture.logging import get_logger
from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem, ExtractionStatus, TranscriptStatus
from culture.models.report import WeeklyReport
from culture.models.source import Source
from culture.utils.dates import ensure_utc, now_utc

log = get_logger("culture.reporting")

# Cap the previous synthesis passed as change-tracking context.
PREVIOUS_SYNTHESIS_MAX_CHARS = 30_000


@dataclass
class ReportResult:
    path: Path
    sources_covered: int
    items_covered: int
    items_unanalyzed: int
    is_public: bool = False


def _item_time(item: ContentItem):
    return ensure_utc(item.published_at or item.discovered_at)


def latest_analyses(session: Session, item_ids: list[int]) -> dict[int, ContentAnalysis]:
    if not item_ids:
        return {}
    rows = session.scalars(
        select(ContentAnalysis)
        .where(ContentAnalysis.content_item_id.in_(item_ids))
        .order_by(ContentAnalysis.content_item_id, ContentAnalysis.id)
    )
    return {row.content_item_id: row for row in rows}  # later rows overwrite: latest wins


def _entity_line(label: str, *lists: list) -> str | None:
    values = [v for lst in lists for v in (lst or [])]
    return f"- {label}: {', '.join(values)}" if values else None


def _limitations(item: ContentItem) -> list[str]:
    notes = []
    if item.content_type == "podcast":
        notes.append("Podcast episode: analysis based on show notes only, audio not transcribed.")
    elif item.content_type == "video":
        if item.transcript_status == TranscriptStatus.AVAILABLE.value:
            notes.append("Analysis used the video transcript.")
        else:
            notes.append(
                f"Transcript {item.transcript_status}: analysis is metadata-only; "
                "the video's spoken content is unknown."
            )
    elif item.extraction_status == ExtractionStatus.PARTIAL.value:
        notes.append("Extraction was partial (likely a teaser or paywall stub).")
    elif item.extraction_status == ExtractionStatus.FAILED.value:
        notes.append("Article text could not be extracted; analysis is metadata-only.")
    return notes


def render_item(item: ContentItem, analysis: ContentAnalysis | None) -> str:
    when = _item_time(item)
    lines = [
        f"### {item.title or '(untitled)'}",
        "",
        f"- Published: {when.date().isoformat() if when else 'unknown'} · Type: {item.content_type}"
        + (f" · Author: {item.author}" if item.author else ""),
        f"- URL: {item.url}",
    ]
    for note in _limitations(item):
        lines.append(f"- Limitation: {note}")

    if analysis is None:
        lines += ["", "_Not yet analyzed._"]
        return "\n".join(lines)

    lines += ["", analysis.summary or "", ""]
    if analysis.major_points:
        lines.append("**Major points**")
        lines += [f"- {p}" for p in analysis.major_points]
        lines.append("")

    entity_lines = [
        _entity_line("People", analysis.people),
        _entity_line("Brands", analysis.brands),
        _entity_line("Products", analysis.products),
        _entity_line("Designers & creators", analysis.designers, analysis.creators),
        _entity_line("Artists & musicians", analysis.artists, analysis.musicians),
        _entity_line("Cities & neighborhoods", analysis.cities, analysis.neighborhoods),
        _entity_line("Scenes & subcultures", analysis.scenes, analysis.subcultures),
        _entity_line("Sports", analysis.sports),
        _entity_line("Garments & footwear", analysis.garments, analysis.footwear),
        _entity_line("Places", analysis.restaurants, analysis.cafes, analysis.clubs),
        _entity_line(
            "Cultural references", analysis.media_references, analysis.historical_references
        ),
        _entity_line("Consumer archetypes", analysis.consumer_archetypes),
    ]
    present_lines = [line for line in entity_lines if line is not None]
    if present_lines:
        lines += present_lines + [""]

    if analysis.possible_signals:
        lines.append("**Possible signals**")
        lines += [f"- {s}" for s in analysis.possible_signals]
        lines.append("")
    if analysis.why_it_matters:
        lines.append(f"**Why it matters**: {analysis.why_it_matters}")
        lines.append("")
    meta = []
    if analysis.lifecycle_stage:
        meta.append(f"Lifecycle: {analysis.lifecycle_stage}")
    if analysis.tags:
        meta.append(f"Tags: {', '.join(analysis.tags)}")
    if meta:
        lines.append("_" + " · ".join(meta) + "_")
    return "\n".join(lines)


def render_source_section(
    source: Source, items: list[ContentItem], analyses: dict[int, ContentAnalysis]
) -> str:
    lines = [f"## {source.name}", ""]
    detail = f"{source.platform}"
    place = ", ".join(p for p in (source.city, source.country) if p)
    if place:
        detail += f" · {place}"
    lines.append(f"_{detail} · tier: {source.tier}_")
    lines.append("")
    if not items:
        lines.append("No new content found during this period.")
        if source.platform in ("web", "youtube") and not source.feed_url:
            lines.append(f"_Note: {source.collection_notes or 'no collection method configured.'}_")
        return "\n".join(lines)
    for item in sorted(items, key=lambda i: _item_time(i) or now_utc(), reverse=True):
        lines.append(render_item(item, analyses.get(item.id)))
        lines.append("")
    return "\n".join(lines)


def build_digest(
    sources: list[Source],
    items_by_source: dict[int, list[ContentItem]],
    analyses: dict[int, ContentAnalysis],
) -> str:
    """Compact per-item evidence lines for the synthesis prompt."""
    entries = []
    for source in sources:
        for item in items_by_source.get(source.id, []):
            analysis = analyses.get(item.id)
            if analysis is None:
                continue
            when = _item_time(item)
            parts = [
                f"[{source.name} · {item.content_type} · "
                f"{when.date().isoformat() if when else '?'}] {item.title or '(untitled)'}",
                f"  Summary: {analysis.summary}",
            ]
            limitations = _limitations(item)
            if limitations:
                parts.append(f"  Evidence limitation: {' '.join(limitations)}")
            entity_bits = []
            for label, values in (
                ("brands", analysis.brands),
                ("people", analysis.people + analysis.designers + analysis.creators),
                ("cities", analysis.cities),
                ("scenes", analysis.scenes + analysis.subcultures),
                ("objects", analysis.garments + analysis.footwear + analysis.lifestyle_objects),
                ("topics", analysis.topics),
            ):
                if values:
                    entity_bits.append(f"{label}: {', '.join(values[:8])}")
            if entity_bits:
                parts.append("  Entities: " + " | ".join(entity_bits))
            if analysis.possible_signals:
                parts.append("  Signals: " + " / ".join(analysis.possible_signals))
            if analysis.consumer_archetypes:
                parts.append("  Archetypes: " + " / ".join(analysis.consumer_archetypes))
            scores = {
                "origin": analysis.cultural_origin_score,
                "editorial": analysis.editorial_momentum_score,
                "adoption": analysis.urban_adoption_score,
                "meme": analysis.meme_recognition_score,
                "creator": analysis.creator_adoption_score,
                "commercial": analysis.commercial_evidence_score,
                "saturation": analysis.saturation_risk_score,
                "longevity": analysis.longevity_score,
            }
            score_bits = [f"{k}={v}" for k, v in scores.items() if v is not None]
            if score_bits or analysis.lifecycle_stage:
                stage = f" stage={analysis.lifecycle_stage}" if analysis.lifecycle_stage else ""
                parts.append(f"  Scores: {' '.join(score_bits)}{stage}")
            entries.append("\n".join(parts))
    return "\n\n".join(entries)


def build_signal_registry_digest(session: Session, since) -> tuple[str, list]:
    """Registry lines for the synthesis prompt + rows for the report appendix."""
    from culture.models.signal import Signal, SignalEvidence, SignalState

    signals = list(session.scalars(select(Signal).where(Signal.state == SignalState.ACTIVE.value)))
    if not signals:
        return "", []
    new_evidence: dict[int, int] = {}
    for signal in signals:
        new_evidence[signal.id] = (
            session.query(SignalEvidence)
            .filter(SignalEvidence.signal_id == signal.id, SignalEvidence.created_at >= since)
            .count()
        )
    rows = sorted(
        signals, key=lambda s: (new_evidence.get(s.id, 0), s.evidence_count), reverse=True
    )
    lines = []
    for s in rows:
        delta = new_evidence.get(s.id, 0)
        first = s.first_detected_at.date().isoformat() if s.first_detected_at else "?"
        lines.append(
            f"- {s.name} · stage: {s.lifecycle_stage} · evidence: {s.evidence_count} items "
            f"from {s.source_count} sources (+{delta} this window) · first seen {first}"
            + (f" · cities: {', '.join(s.cities[:5])}" if s.cities else "")
            + (f"\n  {s.description}" if s.description else "")
        )
    return "\n".join(lines), [(s, new_evidence.get(s.id, 0)) for s in rows]


def build_registry_changes(session: Session, since) -> list[str]:
    """Deterministic source-registry changes for the window: tier transitions
    (from tier_history), newly discovered candidates, and quiet-core flags."""
    from culture.services.source_lifecycle import quiet_core_sources

    lines: list[str] = []
    transitions = []
    new_candidates = []
    for source in session.scalars(select(Source)):
        for entry in source.discovery_json.get("tier_history", []):
            at = ensure_utc(datetime.fromisoformat(entry["at"]))
            if at >= since:
                transitions.append((source.name, entry))
        if (
            source.source_type == "discovered"
            and ensure_utc(source.created_at) >= since
        ):
            d = source.discovery_json
            new_candidates.append(
                f"- **{source.name}** ({source.platform}): cited by "
                f"{len(d.get('citing_sources', []))} sources, {d.get('mentions', '?')} mentions"
            )

    if transitions:
        lines.append("**Tier changes this window:**")
        for name, entry in transitions:
            lines.append(f"- **{name}**: {entry['from']} → {entry['to']} ({entry['reason']})")
        lines.append("")
    if new_candidates:
        lines.append("**Newly discovered candidates (unverified, not yet collected):**")
        lines.extend(new_candidates)
        lines.append("")
    flagged = quiet_core_sources(session)
    if flagged:
        lines.append(
            "**Core sources gone quiet (recommendation only, core is never auto-retired):**"
        )
        for source, quiet in flagged:
            lines.append(f"- **{source.name}**: no new content in {quiet} days")
        lines.append("")
    if not lines:
        lines = ["No registry changes this window.", ""]
    return ["## Source registry changes", ""] + lines


def render_signal_appendix(rows: list) -> list[str]:
    lines = [
        "# Part 3. Signal Registry",
        "",
        "_Persistent signals with accumulated evidence. Stages are computed from"
        " evidence (sources, time span, scores), not asserted weekly._",
        "",
        "| Signal | Stage | Evidence | Sources | +This window | Cities | First seen |",
        "|---|---|---|---|---|---|---|",
    ]
    for signal, delta in rows:
        first = signal.first_detected_at.date().isoformat() if signal.first_detected_at else "-"
        lines.append(
            f"| {signal.name} | {signal.lifecycle_stage} | {signal.evidence_count} "
            f"| {signal.source_count} | +{delta} "
            f"| {', '.join(signal.cities[:3]) or '-'} | {first} |"
        )
    return lines


def previous_synthesis(reports_dir: Path, current_filename: str) -> str | None:
    candidates = sorted(p for p in reports_dir.glob("*-W*.md") if p.name != current_filename)
    if not candidates:
        return None
    text = candidates[-1].read_text(encoding="utf-8")
    match = re.search(r"^# Part 2.*?$", text, re.MULTILINE)
    section = text[match.start() :] if match else text
    return section[:PREVIOUS_SYNTHESIS_MAX_CHARS]


def generate_report(
    session: Session,
    provider: AIProvider,
    days: int = 7,
    reports_dir: Path | None = None,
    publish: bool = False,
) -> ReportResult:
    now = now_utc()
    since = now - timedelta(days=days)
    reports_dir = reports_dir or Path("reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    iso = now.isocalendar()
    iso_week = f"{iso.year}-W{iso.week:02d}"
    filename = f"{iso_week}.md"
    path = reports_dir / filename

    sources = list(
        session.scalars(select(Source).where(Source.active.is_(True)).order_by(Source.name))
    )
    items_by_source: dict[int, list[ContentItem]] = {}
    all_items: list[ContentItem] = []
    for source in sources:
        items = [
            i
            for i in session.scalars(select(ContentItem).where(ContentItem.source_id == source.id))
            if (_item_time(i)) and since <= _item_time(i) <= now
        ]
        items_by_source[source.id] = items
        all_items.extend(items)

    analyses = latest_analyses(session, [i.id for i in all_items])
    unanalyzed = sum(1 for i in all_items if i.id not in analyses)

    log.info(
        "weekly report: %d sources, %d items in window (%d unanalyzed)",
        len(sources),
        len(all_items),
        unanalyzed,
    )

    digest = build_digest(sources, items_by_source, analyses)
    registry_digest, registry_rows = build_signal_registry_digest(session, since)
    prior = previous_synthesis(reports_dir, filename)
    synthesis = provider.generate_text(
        WEEKLY_SYSTEM_PROMPT,
        build_weekly_prompt(digest, prior, days, signal_registry=registry_digest or None),
        max_tokens=32000,
    )

    header = [
        f"# Cultural Intelligence Report: {filename.removesuffix('.md')}",
        "",
        f"_Window: {since.date().isoformat()} to {now.date().isoformat()} · "
        f"{len(sources)} monitored sources · {len(all_items)} content items · "
        f"synthesis model: {provider.model}_",
        "",
        "# Part 1. Complete Source Roundup",
        "",
    ]
    part1 = [
        render_source_section(source, items_by_source[source.id], analyses) for source in sources
    ]
    part2 = ["# Part 2. Weekly Cultural Intelligence", "", synthesis, ""]
    part3 = render_signal_appendix(registry_rows) if registry_rows else []
    part3 += [""] + build_registry_changes(session, since)

    content = "\n".join(header + part1 + [""] + part2 + part3)
    path.write_text(content, encoding="utf-8")
    log.info("weekly report created: %s", path)

    # The pipeline runs locally; the deployed web app is stateless and has
    # no access to this local file, so the report is also stored in the
    # database — that's the only copy the hosted site can ever read.
    row = session.scalar(select(WeeklyReport).where(WeeklyReport.iso_week == iso_week))
    if row is None:
        row = WeeklyReport(iso_week=iso_week)
        session.add(row)
    row.content_markdown = content
    row.sources_covered = len(sources)
    row.items_covered = len(all_items)
    row.generated_at = now
    if publish:
        row.is_public = True
        row.published_at = row.published_at or now
    session.commit()

    return ReportResult(
        path=path,
        sources_covered=len(sources),
        items_covered=len(all_items),
        items_unanalyzed=unanalyzed,
        is_public=row.is_public,
    )
