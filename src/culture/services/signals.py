from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from culture.analysis.provider import AIProvider, BudgetExceededError
from culture.analysis.signal_prompts import SIGNAL_SYSTEM_PROMPT, build_signal_prompt
from culture.logging import get_logger
from culture.models.analysis import ContentAnalysis, LifecycleStage
from culture.models.content import ContentItem, ProcessingStatus
from culture.models.signal import Signal, SignalEvidence, SignalState
from culture.models.source import Source
from culture.schemas.signal import SignalLinkDecision, SignalMatchResponse
from culture.utils.dates import ensure_utc, now_utc

log = get_logger("culture.signals")

BATCH_SIZE = 20
# The full active registry is sent as context on every matching batch. Left
# uncapped, this cost grows forever as more signals accumulate, independent
# of how much new content is actually being processed. Capping to the most
# recently active signals bounds that growth: cold signals stop being offered
# as match targets (they still exist, still appear in reports — they just
# age out of the LLM's candidate list), which is the same "dormant" logic
# source lifecycle already applies, just not yet automated for signals.
MAX_REGISTRY_SIGNALS = 60

_SCORE_FIELDS = (
    "cultural_origin_score",
    "editorial_momentum_score",
    "urban_adoption_score",
    "meme_recognition_score",
    "creator_adoption_score",
    "commercial_evidence_score",
    "saturation_risk_score",
    "longevity_score",
)


@dataclass
class SignalUpdateStats:
    items_processed: int = 0
    links_created: int = 0
    signals_created: int = 0
    invalid_links: int = 0
    new_signal_names: list[str] = field(default_factory=list)
    budget_stopped: bool = False
    spent_usd: float = 0.0


def compute_lifecycle(
    evidence_count: int,
    source_count: int,
    span_days: int,
    avg_saturation: float | None,
    avg_editorial: float | None,
    avg_adoption: float | None,
) -> str:
    """Deterministic v1 heuristic — transparent and tunable, never vibes.

    Declining requires trend-over-time data we don't have yet; it is not
    produced by this version.
    """
    if avg_saturation is not None and avg_saturation >= 4:
        return LifecycleStage.SATURATED.value
    if (
        source_count >= 4
        and avg_editorial is not None
        and avg_editorial >= 4
        and avg_adoption is not None
        and avg_adoption >= 3
    ):
        return LifecycleStage.MAINSTREAM.value
    if source_count >= 3 and span_days >= 14:
        return LifecycleStage.STRENGTHENING.value
    if evidence_count >= 2:
        return LifecycleStage.EMERGING.value
    return LifecycleStage.UNKNOWN.value


def _latest_analysis_map(session: Session, item_ids: list[int]) -> dict[int, ContentAnalysis]:
    if not item_ids:
        return {}
    rows = session.scalars(
        select(ContentAnalysis)
        .where(ContentAnalysis.content_item_id.in_(item_ids))
        .order_by(ContentAnalysis.content_item_id, ContentAnalysis.id)
    )
    return {row.content_item_id: row for row in rows}


def _registry_for_prompt(registry: list[Signal]) -> list[Signal]:
    """Cap the registry sent to the matcher, most-recently-active first.

    Keeps matching cost bounded as the registry grows; cold signals just stop
    being offered as match targets (they remain in the DB, reports, and API).
    """
    if len(registry) <= MAX_REGISTRY_SIGNALS:
        return registry

    def sort_key(signal: Signal):
        last = ensure_utc(signal.last_evidence_at)
        return (last is not None, last, signal.evidence_count)

    return sorted(registry, key=sort_key, reverse=True)[:MAX_REGISTRY_SIGNALS]


def refresh_signal(session: Session, signal: Signal) -> None:
    """Recompute a signal's aggregates from its evidence. Deterministic."""
    items = list(
        session.scalars(
            select(ContentItem)
            .join(SignalEvidence, SignalEvidence.content_item_id == ContentItem.id)
            .where(SignalEvidence.signal_id == signal.id)
        )
    )
    analyses = _latest_analysis_map(session, [i.id for i in items])

    signal.evidence_count = len(items)
    signal.source_count = len({i.source_id for i in items})

    times = sorted(t for t in (ensure_utc(i.published_at or i.discovered_at) for i in items) if t)
    if times:
        signal.first_detected_at = times[0]
        signal.last_evidence_at = times[-1]
    span_days = (times[-1] - times[0]).days if len(times) > 1 else 0

    city_counts: Counter[str] = Counter()
    country_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()
    score_values: dict[str, list[int]] = {f: [] for f in _SCORE_FIELDS}
    for item in items:
        analysis = analyses.get(item.id)
        if analysis is None:
            continue
        city_counts.update(analysis.cities or [])
        country_counts.update(analysis.countries or [])
        topic_counts.update(analysis.topics or [])
        for score_field in _SCORE_FIELDS:
            value = getattr(analysis, score_field)
            if value is not None:
                score_values[score_field].append(value)

    signal.cities = [c for c, _ in city_counts.most_common(10)]
    signal.countries = [c for c, _ in country_counts.most_common(10)]
    signal.categories = [t for t, _ in topic_counts.most_common(8)]

    averages: dict[str, float | None] = {}
    for score_field, values in score_values.items():
        avg = sum(values) / len(values) if values else None
        averages[score_field] = avg
        setattr(signal, score_field, round(avg) if avg is not None else None)

    signal.lifecycle_stage = compute_lifecycle(
        evidence_count=signal.evidence_count,
        source_count=signal.source_count,
        span_days=span_days,
        avg_saturation=averages["saturation_risk_score"],
        avg_editorial=averages["editorial_momentum_score"],
        avg_adoption=averages["urban_adoption_score"],
    )


class SignalService:
    def __init__(self, session: Session, provider: AIProvider | None = None) -> None:
        self.session = session
        self.provider = provider  # required for update_signals, not for reads

    def unprocessed_items(self, limit: int | None = None) -> list[ContentItem]:
        query = (
            select(ContentItem)
            .where(
                ContentItem.processing_status == ProcessingStatus.ANALYZED.value,
                ContentItem.signals_processed_at.is_(None),
            )
            .order_by(ContentItem.id)
        )
        if limit:
            query = query.limit(limit)
        return list(self.session.scalars(query))

    def active_signals(self) -> list[Signal]:
        return list(
            self.session.scalars(
                select(Signal).where(Signal.state == SignalState.ACTIVE.value).order_by(Signal.id)
            )
        )

    def update_signals(self, limit: int | None = None) -> SignalUpdateStats:
        stats = SignalUpdateStats()
        items = self.unprocessed_items(limit)
        if not items:
            return stats
        log.info("signal matching started: %d items", len(items))
        for start in range(0, len(items), BATCH_SIZE):
            batch = items[start : start + BATCH_SIZE]
            try:
                self._process_batch(batch, stats)
            except BudgetExceededError as exc:
                # Not a per-batch failure — the call was never attempted, and
                # every remaining batch would raise the same way. Stop the
                # whole loop instead of iterating through each one for nothing.
                self.session.rollback()
                stats.budget_stopped = True
                log.warning(
                    "%s (%d item(s) left unprocessed this run)",
                    exc,
                    len(items) - stats.items_processed,
                )
                break
            except Exception as exc:
                # Leave the batch unprocessed for the next run; keep going.
                self.session.rollback()
                log.error("signal batch failed (items %d-%d): %s", batch[0].id, batch[-1].id, exc)
                continue
            self.session.commit()
        stats.spent_usd = getattr(self.provider, "spent_usd", 0.0)
        log.info(
            "signal matching completed: %d items, %d links, %d new signals",
            stats.items_processed,
            stats.links_created,
            stats.signals_created,
        )
        return stats

    def _process_batch(self, batch: list[ContentItem], stats: SignalUpdateStats) -> None:
        assert self.provider is not None, "update_signals requires a provider"
        analyses = _latest_analysis_map(self.session, [i.id for i in batch])
        registry = self.active_signals()
        prompt = build_signal_prompt(
            [self._registry_line(s) for s in _registry_for_prompt(registry)],
            [self._item_line(i, analyses.get(i.id)) for i in batch],
        )
        response = self.provider.generate_structured(
            SIGNAL_SYSTEM_PROMPT, prompt, SignalMatchResponse
        )
        batch_ids = {i.id for i in batch}
        touched: dict[int, Signal] = {}
        links_per_item: Counter[int] = Counter()
        for link in response.links:
            signal = self._apply_link(link, batch_ids, registry, links_per_item, stats)
            if signal is not None:
                touched[signal.id or id(signal)] = signal
        for item in batch:
            item.signals_processed_at = now_utc()
            stats.items_processed += 1
        self.session.flush()
        for signal in touched.values():
            refresh_signal(self.session, signal)

    def _apply_link(
        self,
        link: SignalLinkDecision,
        batch_ids: set[int],
        registry: list[Signal],
        links_per_item: Counter,
        stats: SignalUpdateStats,
    ) -> Signal | None:
        if link.content_item_id not in batch_ids:
            stats.invalid_links += 1
            log.warning("link for unknown item %s skipped", link.content_item_id)
            return None
        if bool(link.existing_signal_id) == bool(link.new_signal_name):
            stats.invalid_links += 1
            log.warning("link must set exactly one of existing/new (item %s)", link.content_item_id)
            return None
        if links_per_item[link.content_item_id] >= 3:
            stats.invalid_links += 1
            return None

        if link.existing_signal_id:
            signal = self.session.get(Signal, link.existing_signal_id)
            if signal is None:
                stats.invalid_links += 1
                log.warning("link to unknown signal %s skipped", link.existing_signal_id)
                return None
        else:
            name = (link.new_signal_name or "").strip()
            # Defensive dedupe: an LLM-proposed "new" signal matching an existing
            # name (case-insensitive) links instead of duplicating.
            signal = next(
                (s for s in self.active_signals() if s.name.lower() == name.lower()), None
            )
            if signal is None:
                signal = Signal(name=name, description=link.new_signal_description)
                self.session.add(signal)
                self.session.flush()
                registry.append(signal)
                stats.signals_created += 1
                stats.new_signal_names.append(name)
                log.info("new signal: %s", name)

        exists = self.session.scalar(
            select(SignalEvidence).where(
                SignalEvidence.signal_id == signal.id,
                SignalEvidence.content_item_id == link.content_item_id,
            )
        )
        if exists:
            return signal
        self.session.add(
            SignalEvidence(
                signal_id=signal.id, content_item_id=link.content_item_id, note=link.note
            )
        )
        links_per_item[link.content_item_id] += 1
        stats.links_created += 1
        return signal

    @staticmethod
    def _registry_line(signal: Signal) -> str:
        description = (signal.description or "").replace("\n", " ")[:200]
        return (
            f"{signal.id} · {signal.name} · {signal.lifecycle_stage} · "
            f"{signal.evidence_count} items · {description}"
        )

    def _item_line(self, item: ContentItem, analysis: ContentAnalysis | None) -> str:
        source = self.session.get(Source, item.source_id)
        assert source is not None
        when = ensure_utc(item.published_at or item.discovered_at)
        lines = [
            f"ITEM {item.id} [{source.name} · {item.content_type} · "
            f"{when.date().isoformat() if when else '?'}] {item.title or '(untitled)'}"
        ]
        if analysis:
            lines.append(f"  Summary: {(analysis.summary or '')[:600]}")
            if analysis.possible_signals:
                lines.append("  Candidate signals: " + " / ".join(analysis.possible_signals))
            bits = []
            for label, values in (
                ("brands", analysis.brands),
                ("cities", analysis.cities),
                ("scenes", (analysis.scenes or []) + (analysis.subcultures or [])),
            ):
                if values:
                    bits.append(f"{label}: {', '.join(values[:6])}")
            if bits:
                lines.append("  " + " | ".join(bits))
        return "\n".join(lines)
