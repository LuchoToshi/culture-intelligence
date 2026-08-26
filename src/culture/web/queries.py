"""Read-only aggregation queries for the web interface.

Everything here derives from stored data — no interpretation is invented at
the view layer. Where a view presents inference (e.g. observation order), the
template labels it as such.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem
from culture.models.signal import Signal, SignalEvidence, SignalState
from culture.models.source import Source
from culture.utils.dates import ensure_utc, now_utc

# Display vocabulary: internal stage values -> product language. Labels
# deliberately match the internal values users see in filter URLs
# (?stage=strengthening) — external review flagged the earlier
# "Accelerating" label as a confusing mismatch against that URL vocabulary.
STAGE_LABELS = {
    "emerging": "Emerging",
    "strengthening": "Strengthening",
    "mainstream": "Mainstream",
    "saturated": "Saturated",
    "declining": "Declining",
    "unknown": "Insufficient evidence",
}
STAGE_GLYPHS = {
    "emerging": "▲",
    "strengthening": "⌃",
    "mainstream": "●",
    "saturated": "◼",
    "declining": "▽",
    "unknown": "◌",
}
STAGE_ORDER = ["emerging", "strengthening", "mainstream", "saturated", "declining", "unknown"]

# City alias canonicalization. The analyzer stores place names exactly as
# evidence gives them, so the same city arrives under several names and the
# city index double-counts ("New York" / "NYC" / "New York City" as three
# rows — confirmed in production data). Canonicalized at the display/
# aggregation layer only; stored evidence keeps its original wording. Keys
# are lowercase aliases; grow this map as new aliases show up in data.
CITY_ALIASES = {
    "nyc": "New York",
    "new york city": "New York",
    "la": "Los Angeles",
    "philly": "Philadelphia",
    "sf": "San Francisco",
    "amsterdam-oost": "Amsterdam",
    "bk": "Brooklyn",
}


def canonical_city(name: str) -> str:
    name = name.strip()
    return CITY_ALIASES.get(name.lower(), name)


def _city_matches(name: str, cities: list[str] | None) -> bool:
    target = canonical_city(name).lower()
    return any(canonical_city(c).lower() == target for c in (cities or []))


def item_time(item: ContentItem) -> datetime | None:
    return ensure_utc(item.published_at or item.discovered_at)


def latest_analyses(session: Session, item_ids: list[int]) -> dict[int, ContentAnalysis]:
    if not item_ids:
        return {}
    rows = session.scalars(
        select(ContentAnalysis)
        .where(ContentAnalysis.content_item_id.in_(item_ids))
        .order_by(ContentAnalysis.content_item_id, ContentAnalysis.id)
    )
    return {row.content_item_id: row for row in rows}


def active_signals(session: Session) -> list[Signal]:
    return list(session.scalars(select(Signal).where(Signal.state == SignalState.ACTIVE.value)))


def evidence_items_for_signal(
    session: Session, signal_id: int
) -> list[tuple[ContentItem, str | None]]:
    rows = session.execute(
        select(ContentItem, SignalEvidence.note)
        .join(SignalEvidence, SignalEvidence.content_item_id == ContentItem.id)
        .where(SignalEvidence.signal_id == signal_id)
    )
    return [(item, note) for item, note in rows]


def evidence_deltas(session: Session, signal_ids: list[int], days: int = 7) -> dict[int, int]:
    if not signal_ids:
        return {}
    since = now_utc() - timedelta(days=days)
    rows = session.execute(
        select(SignalEvidence.signal_id, func.count(SignalEvidence.id))
        .where(SignalEvidence.signal_id.in_(signal_ids), SignalEvidence.created_at >= since)
        .group_by(SignalEvidence.signal_id)
    )
    deltas = dict.fromkeys(signal_ids, 0)
    deltas.update({sid: count for sid, count in rows})
    return deltas


def monthly_series(session: Session, signal_id: int, months: int = 12) -> list[int]:
    """Evidence per calendar month for the trailing window, oldest first."""
    items = [item for item, _ in evidence_items_for_signal(session, signal_id)]
    now = now_utc()
    buckets = [0] * months
    for item in items:
        when = item_time(item)
        if when is None:
            continue
        age_months = (now.year - when.year) * 12 + (now.month - when.month)
        if 0 <= age_months < months:
            buckets[months - 1 - age_months] += 1
    return buckets


def sparkline_svg(series: list[int], width: int = 96, height: int = 20) -> str:
    """Tiny bar ledger as inline SVG. Empty months render as faint baseline ticks."""
    if not series:
        return ""
    n = len(series)
    peak = max(max(series), 1)
    bar_w = width / n
    bars = []
    for i, value in enumerate(series):
        x = round(i * bar_w + 1, 1)
        w = round(bar_w - 2, 1)
        if value == 0:
            bars.append(
                f'<rect x="{x}" y="{height - 2}" width="{w}" height="2" class="spark-empty"/>'
            )
        else:
            h = max(round(height * value / peak), 3)
            bars.append(
                f'<rect x="{x}" y="{height - h}" width="{w}" height="{h}" class="spark-bar"/>'
            )
    return (
        f'<svg viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-label="Evidence over the last {n} months: {", ".join(map(str, series))}">'
        + "".join(bars)
        + "</svg>"
    )


@dataclass
class SignalRow:
    signal: Signal
    delta_7d: int
    spark: str
    related_count: int = 0


def signal_rows(
    session: Session,
    stage: str | None = None,
    query: str | None = None,
    sort: str = "moving",
) -> list[SignalRow]:
    signals = active_signals(session)
    if stage:
        signals = [s for s in signals if s.lifecycle_stage == stage]
    if query:
        q = query.lower()
        signals = [
            s
            for s in signals
            if q in s.name.lower()
            or q in (s.description or "").lower()
            or any(q in c.lower() for c in s.cities)
        ]
    deltas = evidence_deltas(session, [s.id for s in signals])
    related = co_occurrence_counts(session)
    rows = [
        SignalRow(
            signal=s,
            delta_7d=deltas.get(s.id, 0),
            spark=sparkline_svg(monthly_series(session, s.id)),
            related_count=related.get(s.id, 0),
        )
        for s in signals
    ]
    sorters = {
        "moving": lambda r: (r.delta_7d, r.signal.evidence_count),
        "evidence": lambda r: (r.signal.evidence_count, r.delta_7d),
        "sources": lambda r: (r.signal.source_count, r.signal.evidence_count),
        "newest": lambda r: (
            r.signal.first_detected_at or datetime.min.replace(tzinfo=None).astimezone(),
        ),
    }
    rows.sort(key=sorters.get(sort, sorters["moving"]), reverse=True)
    return rows


def co_occurrence_counts(session: Session) -> dict[int, int]:
    """How many other signals each signal shares evidence items with."""
    pairs = co_occurring_pairs(session)
    counts: Counter[int] = Counter()
    for a, b, _ in pairs:
        counts[a] += 1
        counts[b] += 1
    return dict(counts)


def co_occurring_pairs(session: Session) -> list[tuple[int, int, int]]:
    """(signal_a, signal_b, shared_item_count) for signals sharing evidence."""
    a = SignalEvidence.__table__.alias("a")
    b = SignalEvidence.__table__.alias("b")
    rows = session.execute(
        select(a.c.signal_id, b.c.signal_id, func.count())
        .select_from(a.join(b, a.c.content_item_id == b.c.content_item_id))
        .where(a.c.signal_id < b.c.signal_id)
        .group_by(a.c.signal_id, b.c.signal_id)
    )
    return [(int(x), int(y), int(n)) for x, y, n in rows]


def related_signals(session: Session, signal_id: int) -> list[tuple[Signal, int]]:
    out = []
    for a, b, n in co_occurring_pairs(session):
        other = b if a == signal_id else a if b == signal_id else None
        if other is not None:
            signal = session.get(Signal, other)
            if signal is not None:
                out.append((signal, n))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


@dataclass
class ArchetypeObservation:
    text: str
    item: ContentItem
    source: Source
    cities: list[str]
    when: datetime | None
    signals: list[Signal] = field(default_factory=list)


def archetype_observations(
    session: Session, query: str | None = None, city: str | None = None, limit: int = 120
) -> list[ArchetypeObservation]:
    # Portable path: filter in Python — hundreds of rows, trivial at this scale.
    analyses = list(session.scalars(select(ContentAnalysis).order_by(ContentAnalysis.id.desc())))
    items = {
        i.id: i
        for i in session.scalars(
            select(ContentItem).where(ContentItem.id.in_([a.content_item_id for a in analyses]))
        )
    }
    sources = {s.id: s for s in session.scalars(select(Source))}
    signal_map = signals_by_item(session)
    seen_analyses: set[int] = set()
    observations: list[ArchetypeObservation] = []
    for analysis in analyses:
        if analysis.content_item_id in seen_analyses:
            continue
        seen_analyses.add(analysis.content_item_id)
        for text in analysis.consumer_archetypes or []:
            if query and query.lower() not in text.lower():
                continue
            if city and city.lower() not in [c.lower() for c in (analysis.cities or [])]:
                continue
            item = items.get(analysis.content_item_id)
            if item is None:
                continue
            observations.append(
                ArchetypeObservation(
                    text=text,
                    item=item,
                    source=sources[item.source_id],
                    cities=(analysis.cities or [])[:4],
                    when=item_time(item),
                    signals=signal_map.get(item.id, []),
                )
            )
    observations.sort(key=lambda o: o.when or now_utc(), reverse=True)
    return observations[:limit]


def signals_by_item(session: Session) -> dict[int, list[Signal]]:
    signals = {s.id: s for s in active_signals(session)}
    mapping: dict[int, list[Signal]] = defaultdict(list)
    for ev in session.scalars(select(SignalEvidence)):
        if ev.signal_id in signals:
            mapping[ev.content_item_id].append(signals[ev.signal_id])
    return mapping


@dataclass
class CityRow:
    name: str
    item_count: int
    signal_count: int
    source_count: int
    archetype_count: int
    recent_count: int


def city_rows(session: Session, min_items: int = 2) -> list[CityRow]:
    analyses = list(session.scalars(select(ContentAnalysis)))
    items = {i.id: i for i in session.scalars(select(ContentItem))}
    week_ago = now_utc() - timedelta(days=7)
    per_city_items: dict[str, set[int]] = defaultdict(set)
    per_city_arch: Counter[str] = Counter()
    per_city_recent: Counter[str] = Counter()
    per_city_sources: dict[str, set[int]] = defaultdict(set)
    for analysis in analyses:
        item = items.get(analysis.content_item_id)
        if item is None:
            continue
        for city in analysis.cities or []:
            city = canonical_city(city)
            if not city:
                continue
            per_city_items[city].add(item.id)
            per_city_sources[city].add(item.source_id)
            per_city_arch[city] += len(analysis.consumer_archetypes or [])
            when = item_time(item)
            if when and when >= week_ago:
                per_city_recent[city] += 1
    signal_cities: Counter[str] = Counter()
    for signal in active_signals(session):
        for city in {canonical_city(c) for c in signal.cities}:
            signal_cities[city] += 1
    rows = [
        CityRow(
            name=city,
            item_count=len(ids),
            signal_count=signal_cities.get(city, 0),
            source_count=len(per_city_sources[city]),
            archetype_count=per_city_arch.get(city, 0),
            recent_count=per_city_recent.get(city, 0),
        )
        for city, ids in per_city_items.items()
        if len(ids) >= min_items
    ]
    rows.sort(key=lambda r: (r.item_count, r.signal_count), reverse=True)
    return rows


def city_detail(session: Session, name: str) -> dict:
    analyses = list(session.scalars(select(ContentAnalysis)))
    items = {i.id: i for i in session.scalars(select(ContentItem))}
    sources = {s.id: s for s in session.scalars(select(Source))}
    matching_items: list[tuple[ContentItem, ContentAnalysis]] = []
    archetypes: list[tuple[str, int]] = []
    neighborhoods: Counter[str] = Counter()
    seen_items: set[int] = set()
    for analysis in analyses:
        if not _city_matches(name, analysis.cities):
            continue
        item = items.get(analysis.content_item_id)
        if item is None or item.id in seen_items:
            continue
        seen_items.add(item.id)
        matching_items.append((item, analysis))
        for archetype in analysis.consumer_archetypes or []:
            archetypes.append((archetype, item.id))
        neighborhoods.update(analysis.neighborhoods or [])
    matching_items.sort(key=lambda pair: item_time(pair[0]) or now_utc(), reverse=True)
    signals = [s for s in active_signals(session) if _city_matches(name, s.cities)]
    signals.sort(key=lambda s: s.evidence_count, reverse=True)
    target = canonical_city(name).lower()
    local_sources = [
        s for s in sources.values() if canonical_city(s.city or "").lower() == target
    ]
    return {
        "items": matching_items[:40],
        "sources_map": sources,
        "signals": signals,
        "archetypes": archetypes[:30],
        "neighborhoods": neighborhoods.most_common(12),
        "local_sources": local_sources,
        "item_count": len(seen_items),
    }


def search_everything(session: Session, query: str) -> dict:
    q = query.lower().strip()
    if not q:
        return {"signals": [], "cities": [], "archetypes": [], "items": [], "sources": []}
    signals = [
        s
        for s in active_signals(session)
        if q in s.name.lower() or q in (s.description or "").lower()
    ]
    cities = [r for r in city_rows(session, min_items=1) if q in r.name.lower()][:8]
    archetypes = archetype_observations(session, query=q, limit=12)
    items = []
    for item in session.scalars(select(ContentItem).order_by(ContentItem.id.desc()).limit(2000)):
        if q in (item.title or "").lower():
            items.append(item)
        if len(items) >= 15:
            break
    sources = [s for s in session.scalars(select(Source)) if q in s.name.lower()][:8]
    return {
        "signals": signals[:15],
        "cities": cities,
        "archetypes": archetypes,
        "items": items,
        "sources": sources,
    }


def pipeline_status(session: Session) -> dict:
    pending = session.scalar(
        select(func.count(ContentItem.id)).where(
            ContentItem.processing_status.in_(["new", "ready", "analyzing", "failed"])
        )
    )
    last_check = session.scalar(select(func.max(Source.last_successful_check_at)))
    latest_item = session.scalar(select(func.max(ContentItem.discovered_at)))
    return {
        "pending": pending or 0,
        "last_check": ensure_utc(last_check),
        "latest_item": ensure_utc(latest_item),
    }


# ── source collection health (admin view) ────────────────────────────────

SOURCE_HEALTH_LABELS = {
    "collecting": "Collecting",
    "stale": "Stale",
    "failing": "Failing",
    "never_collected": "Never collected",
    "needs_method": "Needs collection method",
    "paused": "Paused / inactive",
}
SOURCE_HEALTH_ORDER = [
    "failing", "stale", "never_collected", "needs_method", "collecting", "paused",
]

STALE_AFTER_DAYS = 7


def source_health_state(source: Source) -> str:
    """Deterministic collection state from fields the pipeline already
    maintains — no new bookkeeping. 'Failing' means the collector has been
    trying (recent check) without a recent success; 'stale' means nothing
    has been attempted or landed lately; social platforms collect via Apify
    and legitimately have no feed_url."""
    if not source.active:
        return "paused"
    needs_feed = source.platform not in ("instagram", "tiktok")
    if needs_feed and not source.feed_url:
        return "needs_method"
    if source.last_successful_check_at is None:
        return "never_collected"
    now = now_utc()
    last_success = ensure_utc(source.last_successful_check_at)
    last_check = ensure_utc(source.last_checked_at) if source.last_checked_at else None
    if last_success and (now - last_success).days >= STALE_AFTER_DAYS:
        if last_check and (now - last_check).days < STALE_AFTER_DAYS:
            return "failing"  # recently attempted, no recent success
        return "stale"
    return "collecting"


def source_health_rows(session: Session) -> tuple[list[dict], Counter]:
    item_counts = {
        source_id: count
        for source_id, count in session.execute(
            select(ContentItem.source_id, func.count(ContentItem.id)).group_by(
                ContentItem.source_id
            )
        )
    }
    rows = []
    state_counts: Counter[str] = Counter()
    for source in session.scalars(select(Source).order_by(Source.tier, Source.name)):
        state = source_health_state(source)
        state_counts[state] += 1
        rows.append({"source": source, "state": state, "items": item_counts.get(source.id, 0)})
    rows.sort(key=lambda r: (SOURCE_HEALTH_ORDER.index(r["state"]), str(r["source"].name)))
    return rows, state_counts


@dataclass
class ArchetypeGroup:
    """Identical archetype descriptions grouped into one row, so a repeated
    observation reads as recurrence instead of as N unrelated sightings —
    and, critically, so one sighting can never masquerade as an established
    consumer group (reviewer §23)."""

    text: str
    observations: list[ArchetypeObservation]

    @property
    def sightings(self) -> int:
        return len(self.observations)

    @property
    def source_count(self) -> int:
        return len({o.source.id for o in self.observations})

    @property
    def cities(self) -> list[str]:
        seen: dict[str, None] = {}
        for o in self.observations:
            for c in o.cities:
                seen.setdefault(canonical_city(c), None)
        return list(seen)[:4]

    @property
    def latest(self) -> datetime | None:
        stamps = [o.when for o in self.observations if o.when]
        return max(stamps) if stamps else None

    @property
    def state(self) -> str:
        # Recurrence requires independent sources, not just repetition —
        # the same account describing the same person twice is one sighting
        # of one observer's view, not corroboration.
        if self.sightings >= 2 and self.source_count >= 2:
            return "recurring"
        if self.sightings >= 2:
            return "repeated (single source)"
        return "single sighting"

    @property
    def state_key(self) -> str:
        """Stable slug for filtering and section grouping."""
        if self.sightings >= 2 and self.source_count >= 2:
            return "recurring"
        if self.sightings >= 2:
            return "repeated"
        return "single"


def archetype_groups(
    session: Session,
    query: str | None = None,
    city: str | None = None,
    standing: str | None = None,
    sort: str = "recurrence",
) -> list[ArchetypeGroup]:
    grouped: dict[str, list[ArchetypeObservation]] = defaultdict(list)
    for obs in archetype_observations(session, query=query, city=city, limit=500):
        grouped[obs.text.strip().lower()].append(obs)
    groups = [ArchetypeGroup(text=v[0].text, observations=v) for v in grouped.values()]
    if standing in {"recurring", "repeated", "single"}:
        groups = [g for g in groups if g.state_key == standing]
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    if sort == "recency":
        groups.sort(key=lambda g: g.latest or epoch, reverse=True)
    elif sort == "corroboration":
        groups.sort(key=lambda g: (g.source_count, g.latest or epoch), reverse=True)
    else:  # recurrence — the default and the page's argument
        groups.sort(key=lambda g: (g.source_count, g.sightings), reverse=True)
    return groups
