"""Deterministic source-tier transitions, computed from collection evidence.

Mirrors the signal-lifecycle philosophy: transparent thresholds over stored
metrics, never a per-source model judgment. Rules (v1):

- candidate -> watch: a collectable trial source proved itself — enough items,
  at least one signal-evidence contribution, over a minimum relationship age.
- watch -> core: sustained signal contribution over a longer window.
- watch/candidate -> dormant: the feed went quiet or collection has been
  failing for weeks. Dormant sources stop collecting (active=False) but keep
  their history; revival is a human decision.
- core is NEVER auto-retired. Quiet cores are surfaced as recommendations in
  the weekly report; demoting the curated core stays a human call.
- Platforms without collectors (instagram, tiktok, other) are untouched —
  their lifecycle runs through the manual review queue.

Every transition is appended to discovery_json["tier_history"] with a reason,
so the weekly report can state exactly what changed and why.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from culture.logging import get_logger
from culture.models.content import ContentItem
from culture.models.signal import SignalEvidence
from culture.models.source import Source, SourceTier
from culture.utils.dates import ensure_utc, now_utc

log = get_logger("culture.source_lifecycle")

# candidate -> watch (trial passed)
TRIAL_MIN_ITEMS = 3
TRIAL_MIN_EVIDENCE = 1
TRIAL_MIN_DAYS = 14
# watch -> core (sustained contribution)
CORE_MIN_EVIDENCE = 5
CORE_MIN_DAYS = 28
# watch/candidate -> dormant
QUIET_RETIRE_DAYS = 60
BROKEN_CHECK_DAYS = 21
# core: recommendation-only flag
CORE_QUIET_FLAG_DAYS = 45

COLLECTABLE_PLATFORMS = {"web", "youtube", "substack", "newsletter", "podcast"}


@dataclass
class SourceMetrics:
    item_count: int = 0
    evidence_count: int = 0
    first_collected: datetime | None = None
    last_content: datetime | None = None


@dataclass
class Transition:
    source_name: str
    from_tier: str
    to_tier: str
    reason: str


@dataclass
class LifecycleStats:
    promoted: list[Transition] = field(default_factory=list)
    retired: list[Transition] = field(default_factory=list)


def gather_metrics(session: Session) -> dict[int, SourceMetrics]:
    metrics: dict[int, SourceMetrics] = {}
    rows = session.execute(
        select(
            ContentItem.source_id,
            func.count(ContentItem.id),
            func.min(ContentItem.discovered_at),
            func.max(func.coalesce(ContentItem.published_at, ContentItem.discovered_at)),
        ).group_by(ContentItem.source_id)
    )
    for source_id, count, first, last in rows:
        metrics[source_id] = SourceMetrics(
            item_count=count,
            first_collected=ensure_utc(first),
            last_content=ensure_utc(last),
        )
    evidence_rows = session.execute(
        select(ContentItem.source_id, func.count(SignalEvidence.id))
        .join(SignalEvidence, SignalEvidence.content_item_id == ContentItem.id)
        .group_by(ContentItem.source_id)
    )
    for source_id, count in evidence_rows:
        metrics.setdefault(source_id, SourceMetrics()).evidence_count = count
    return metrics


def _record(source: Source, to_tier: str, reason: str, now: datetime) -> Transition:
    transition = Transition(
        source_name=source.name, from_tier=source.tier, to_tier=to_tier, reason=reason
    )
    history = list(source.discovery_json.get("tier_history", []))
    history.append(
        {"from": source.tier, "to": to_tier, "at": now.isoformat(), "reason": reason}
    )
    source.discovery_json = {**source.discovery_json, "tier_history": history}
    source.tier = to_tier
    log.info("source %s: %s -> %s (%s)", source.name, transition.from_tier, to_tier, reason)
    return transition


def apply_lifecycle(session: Session) -> LifecycleStats:
    """Apply tier transitions. Idempotent: a source only moves when a rule fires."""
    stats = LifecycleStats()
    now = now_utc()
    metrics = gather_metrics(session)

    for source in session.scalars(select(Source)):
        if source.platform not in COLLECTABLE_PLATFORMS:
            continue
        m = metrics.get(source.id, SourceMetrics())
        age_days = (now - m.first_collected).days if m.first_collected else 0
        quiet_days = (now - m.last_content).days if m.last_content else None
        check_ok = ensure_utc(source.last_successful_check_at)
        check_stale_days = (now - check_ok).days if check_ok else None

        if source.tier in (SourceTier.CANDIDATE.value, SourceTier.WATCH.value) and source.active:
            if quiet_days is not None and quiet_days >= QUIET_RETIRE_DAYS:
                stats.retired.append(
                    _record(
                        source,
                        SourceTier.DORMANT.value,
                        f"no new content in {quiet_days} days",
                        now,
                    )
                )
                source.active = False
                continue
            if (
                check_stale_days is not None
                and check_stale_days >= BROKEN_CHECK_DAYS
                and source.feed_url
            ):
                stats.retired.append(
                    _record(
                        source,
                        SourceTier.DORMANT.value,
                        f"collection failing for {check_stale_days} days",
                        now,
                    )
                )
                source.active = False
                continue

        if (
            source.tier == SourceTier.CANDIDATE.value
            and source.active
            and m.item_count >= TRIAL_MIN_ITEMS
            and m.evidence_count >= TRIAL_MIN_EVIDENCE
            and age_days >= TRIAL_MIN_DAYS
        ):
            stats.promoted.append(
                _record(
                    source,
                    SourceTier.WATCH.value,
                    f"trial passed: {m.item_count} items, "
                    f"{m.evidence_count} signal-evidence contributions over {age_days} days",
                    now,
                )
            )
            continue

        if (
            source.tier == SourceTier.WATCH.value
            and source.active
            and m.evidence_count >= CORE_MIN_EVIDENCE
            and age_days >= CORE_MIN_DAYS
        ):
            stats.promoted.append(
                _record(
                    source,
                    SourceTier.CORE.value,
                    f"sustained contribution: {m.evidence_count} signal-evidence "
                    f"items over {age_days} days",
                    now,
                )
            )

    session.commit()
    if stats.promoted or stats.retired:
        log.info(
            "lifecycle: %d promoted, %d retired", len(stats.promoted), len(stats.retired)
        )
    return stats


def quiet_core_sources(session: Session) -> list[tuple[Source, int]]:
    """Core sources gone quiet — surfaced as recommendations, never auto-retired."""
    now = now_utc()
    metrics = gather_metrics(session)
    flagged = []
    for source in session.scalars(
        select(Source).where(Source.tier == SourceTier.CORE.value, Source.active.is_(True))
    ):
        if source.platform not in COLLECTABLE_PLATFORMS:
            continue
        m = metrics.get(source.id)
        if m is None or m.last_content is None:
            continue
        quiet = (now - m.last_content).days
        if quiet >= CORE_QUIET_FLAG_DAYS:
            flagged.append((source, quiet))
    flagged.sort(key=lambda pair: pair[1], reverse=True)
    return flagged


def review_queue(session: Session, review_after_days: int = 7) -> dict:
    """What needs human eyes: manual-platform accounts due a look, and
    discovery candidates awaiting verification."""
    now = now_utc()
    due: list[tuple[Source, int | None]] = []
    candidates: list[Source] = []
    for source in session.scalars(select(Source).order_by(Source.name)):
        if source.platform in COLLECTABLE_PLATFORMS and source.source_type != "discovered":
            continue
        if source.source_type == "discovered" and source.tier == SourceTier.CANDIDATE.value:
            candidates.append(source)
            continue
        if source.platform in ("instagram", "tiktok") and source.tier != SourceTier.DORMANT.value:
            reviewed = ensure_utc(source.last_reviewed_at)
            if reviewed is None:
                due.append((source, None))
            elif (overdue_days := (now - reviewed).days) >= review_after_days:
                due.append((source, overdue_days))
    return {"due": due, "candidates": candidates}


def mark_reviewed(session: Session, name: str) -> Source:
    source = session.scalar(select(Source).where(Source.name == name))
    if source is None:
        raise ValueError(f"No source named {name!r}")
    source.last_reviewed_at = now_utc()
    session.commit()
    return source
