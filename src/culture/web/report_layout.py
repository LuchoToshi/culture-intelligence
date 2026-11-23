"""Turn a weekly report into a visual, structured page model.

Reports are generated as Markdown (``culture report``): Part 1 is the per-source
roundup, Part 2 the cross-source synthesis with fixed ``##`` sections, Part 3 the
signal registry table. This module

1. parses Part 2 into those sections, executive-brief points, named entries
   ("**Kallmeyer.** text") and numbered hypotheses;
2. reads the reporting window's own evidence from the database: items per
   platform and per day, sources, cities, signals with their weekly evidence,
   and item images;
3. picks visualizations from what the data supports. A chart, map or image only
   appears when there is evidence behind it; every image links to its item.

Templates render whatever this returns, so every future report gets the same
design without per-report work.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import markdown as md
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor
from sqlalchemy import select
from sqlalchemy.orm import Session

from culture.models.analysis import ContentAnalysis
from culture.models.content import ContentItem
from culture.models.report import WeeklyReport
from culture.models.signal import Signal, SignalEvidence, SignalState
from culture.models.source import Source
from culture.services.images import image_url
from culture.utils.dates import ensure_utc

# Report sections, grouped into the page's chapters. Order inside a chapter is reading order.
TREND_SECTIONS = ["Strengthening Signals", "Emerging Signals"]
UNDERLYING_SECTIONS = [
    "Product & Object Radar",
    "Brands to Watch",
    "People to Watch",
    "City Intelligence",
    "Scene & Subculture Movement",
    "Consumer Archetypes",
    "Psychology & Desire",
]
STRATEGIC_SECTIONS = [
    "Cross-Source Patterns",
    "Commercial Reality Check",
    "Saturation Radar",
    "Contradictions",
    "What Changed Since Last Week",
]
WATCH_SECTION = "What to Watch Next"

PLATFORM_LABELS = {
    "web": "Publications",
    "instagram": "Instagram",
    "substack": "Substack",
    "newsletter": "Newsletters",
    "youtube": "YouTube",
    "podcast": "Podcasts",
    "tiktok": "TikTok",
    "x": "X",
    "reddit": "Reddit",
    "other": "Other",
}

# Map positions for cities that appear in evidence (WGS84, rounded). Facts, not data:
# a city without coordinates is simply not drawn on the map; it still shows in the list.
CITY_COORDS: dict[str, tuple[float, float]] = {
    "London": (51.51, -0.13),
    "Paris": (48.86, 2.35),
    "Milan": (45.46, 9.19),
    "New York": (40.71, -74.01),
    "Tokyo": (35.68, 139.69),
    "Seoul": (37.57, 126.98),
    "Los Angeles": (34.05, -118.24),
    "Copenhagen": (55.68, 12.57),
    "Stockholm": (59.33, 18.07),
    "Amsterdam": (52.37, 4.90),
    "Berlin": (52.52, 13.40),
    "Antwerp": (51.22, 4.40),
    "Brussels": (50.85, 4.35),
    "Tbilisi": (41.72, 44.79),
    "Lisbon": (38.72, -9.14),
    "Madrid": (40.42, -3.70),
    "Barcelona": (41.39, 2.17),
    "Manchester": (53.48, -2.24),
    "Helsinki": (60.17, 24.94),
    "Florence": (43.77, 11.26),
    "Istanbul": (41.01, 28.98),
    "Warsaw": (52.23, 21.01),
    "Vienna": (48.21, 16.37),
    "Osaka": (34.69, 135.50),
    "Hong Kong": (22.32, 114.17),
    "Bangkok": (13.76, 100.50),
    "Taipei": (25.03, 121.57),
    "São Paulo": (-23.55, -46.63),
    "Lagos": (6.52, 3.38),
    "Johannesburg": (-26.20, 28.05),
    "Cape Town": (-33.92, 18.42),
    "Buenos Aires": (-34.60, -58.38),
    "Montreal": (45.50, -73.57),
    "Shanghai": (31.23, 121.47),
    "Mexico City": (19.43, -99.13),
    "Melbourne": (-37.81, 144.96),
    "Sydney": (-33.87, 151.21),
    "Marseille": (43.30, 5.37),
    "San Francisco": (37.77, -122.42),
    "Chicago": (41.88, -87.63),
    "Miami": (25.76, -80.19),
    "Boston": (42.36, -71.06),
    "Philadelphia": (39.95, -75.17),
    "Dallas": (32.78, -96.80),
    "Austin": (30.27, -97.74),
    "Las Vegas": (36.17, -115.14),
    "Kyoto": (35.01, 135.77),
    "Dubai": (25.20, 55.27),
    "Toronto": (43.65, -79.38),
    "Vancouver": (49.28, -123.12),
    "Kobe": (34.69, 135.20),
    "Beijing": (39.90, 116.41),
    "Singapore": (1.35, 103.82),
    "Hiroshima": (34.39, 132.46),
    "Atlanta": (33.75, -84.39),
    "Santa Fe": (35.69, -105.94),
    "Venice": (45.44, 12.32),
    "Rome": (41.90, 12.50),
    "Zurich": (47.38, 8.54),
    "Oslo": (59.91, 10.75),
    "Vilnius": (54.69, 25.28),
    "Rouen": (49.44, 1.10),
    "Fukuoka": (33.59, 130.40),
    "Nagoya": (35.18, 136.91),
    "Mumbai": (19.08, 72.88),
    "Delhi": (28.61, 77.21),
    "Washington": (38.91, -77.04),
    "Portland": (45.52, -122.68),
    "Seattle": (47.61, -122.33),
}
_CITY_ALIASES = {
    "nyc": "New York",
    "new york city": "New York",
    "la": "Los Angeles",
    "tokyo, japan": "Tokyo",
}

_WINDOW = re.compile(r"Window:\s*(\d{4}-\d{2}-\d{2})\s*to\s*(\d{4}-\d{2}-\d{2})")
_ENTRY = re.compile(r"^\*\*(.+?)\*\*\s*(.*)$", re.DOTALL)
_NUMBERED = re.compile(r"^\s*(\d+)\.\s+(.*)$")
_WORD = re.compile(r"[A-Za-zÀ-ÿ0-9&'’.-]{3,}")
_STOP = {
    "the",
    "and",
    "for",
    "with",
    "this",
    "that",
    "from",
    "week",
    "into",
    "are",
    "was",
    "its",
    "has",
    "but",
    "not",
    "one",
    "two",
    "new",
    "more",
    "most",
    "than",
    "their",
    "they",
    "have",
    "been",
    "about",
    "over",
    "also",
    "only",
    "same",
    "fashion",
    "brand",
    "brands",
    "report",
    "reported",
    "items",
    "item",
    "sources",
    "source",
    "digest",
    "signal",
    "signals",
    "luxury",
}


# ---------- parsing ----------
@dataclass
class Entry:
    name: str
    html: str
    text: str


@dataclass
class Section:
    title: str
    slug: str
    html: str
    entries: list[Entry] = field(default_factory=list)
    lead: str = ""  # first sentence: what the section says, for the collapsed view
    word_count: int = 0


@dataclass
class BriefPoint:
    number: int
    headline: str
    body_html: str
    body_text: str
    image: dict | None = None


@dataclass
class ParsedReport:
    week_title: str
    window: tuple[date, date] | None
    meta_line: str
    intro_html: str
    brief: list[BriefPoint]
    sections: dict[str, Section]
    hypotheses: list[Entry]
    appendix_md: str | None
    has_parts: bool


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _md(text: str) -> str:
    # Report text quotes scraped titles; raw HTML in it must not render.
    return render_markdown(text.strip())


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


_SAFE_URL = re.compile(r"^(https?://|mailto:|/(?!/)|#)", re.IGNORECASE)


class _SafeLinks(Treeprocessor):
    """Drop href/src values whose scheme is not http(s), mailto or a local path,
    checked on the parsed element tree, not on serialized HTML."""

    def run(self, root):
        for el in root.iter():
            for attr in ("href", "src"):
                value = el.get(attr)
                if value is not None and not _SAFE_URL.match(value.strip()):
                    del el.attrib[attr]


class _SafeLinksExtension(Extension):
    def extendMarkdown(self, md_instance):  # noqa: N802 (Markdown API name)
        md_instance.treeprocessors.register(_SafeLinks(md_instance), "safe_links", 0)


def render_markdown(text: str) -> str:
    """Markdown to HTML for text built from scraped pages: raw HTML is escaped and
    unsafe link schemes are removed (Markdown would emit javascript: links)."""
    return md.markdown(_escape_html(text), extensions=["tables", _SafeLinksExtension()])


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`#>\[\]]", "", text)).strip()


def _first_sentence(text: str, limit: int = 220) -> str:
    plain = _plain(text)
    match = re.match(r"(.{40,}?[.!?])\s", plain + " ")
    sentence = match.group(1) if match else plain
    return sentence if len(sentence) <= limit else sentence[: limit - 1].rsplit(" ", 1)[0] + "…"


def _split_blocks(text: str) -> list[str]:
    return [
        b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip() and b.strip() != "---"
    ]


def _entries(body: str) -> list[Entry]:
    out = []
    for block in _split_blocks(body):
        match = _ENTRY.match(block)
        if match:
            name = match.group(1).strip().rstrip(".:")
            rest = match.group(2).strip()
            if 1 <= len(name) <= 120:
                out.append(Entry(name=name, html=_md(rest), text=_plain(rest)))
    return out


def _numbered(body: str) -> list[tuple[int, str]]:
    items: list[tuple[int, str]] = []
    for line in body.splitlines():
        match = _NUMBERED.match(line)
        if match:
            items.append((int(match.group(1)), match.group(2).strip()))
        elif items and line.strip() and not line.startswith("#"):
            n, prev = items[-1]
            items[-1] = (n, prev + " " + line.strip())
    return items


def parse_report(markdown_text: str) -> ParsedReport:
    header_window = _WINDOW.search(markdown_text[:2000])
    window = (
        (date.fromisoformat(header_window.group(1)), date.fromisoformat(header_window.group(2)))
        if header_window
        else None
    )
    meta_match = re.search(r"^_(Window:.*?)_\s*$", markdown_text[:2000], re.MULTILINE)
    meta_line = meta_match.group(1) if meta_match else ""

    part1 = re.search(r"^# Part 1.*?$", markdown_text, re.MULTILINE)
    part2 = re.search(r"^# Part 2.*?$", markdown_text, re.MULTILINE)
    part3 = re.search(r"^# Part 3.*?$", markdown_text, re.MULTILINE)
    has_parts = bool(part2)
    if part2:
        synthesis = markdown_text[part2.end() : part3.start() if part3 else len(markdown_text)]
    else:
        synthesis = markdown_text
    appendix = (
        markdown_text[part1.start() : part2.start()]
        if part1 and part2 and part1.start() < part2.start()
        else None
    )

    title_match = re.search(r"^# (?!Part )(.+)$", synthesis, re.MULTILINE)
    week_title = title_match.group(1).strip() if title_match else ""
    body = synthesis[title_match.end() :] if title_match else synthesis
    chunks = re.split(r"^## (.+)$", body, flags=re.MULTILINE)
    intro = chunks[0]
    sections: dict[str, Section] = {}
    for title, content in zip(chunks[1::2], chunks[2::2], strict=False):
        title = title.strip()
        content = content.strip()
        sections[title] = Section(
            title=title,
            slug=_slug(title),
            html=_md(content),
            entries=_entries(content),
            lead=_first_sentence(content),
            word_count=len(content.split()),
        )

    brief: list[BriefPoint] = []
    if "Executive Brief" in sections:
        raw = chunks[1::2].index(next(t for t in chunks[1::2] if t.strip() == "Executive Brief"))
        for number, text in _numbered(chunks[2::2][raw]):
            match = _ENTRY.match(text)
            headline, rest = (
                (match.group(1).strip(), match.group(2).strip())
                if match
                else (_first_sentence(text), text)
            )
            brief.append(
                BriefPoint(
                    number=number,
                    headline=headline.rstrip(),
                    body_html=_md(rest),
                    body_text=_plain(rest),
                )
            )

    hypotheses = []
    if WATCH_SECTION in sections:
        raw = next(
            c
            for t, c in zip(chunks[1::2], chunks[2::2], strict=False)
            if t.strip() == WATCH_SECTION
        )
        for number, text in _numbered(raw):
            match = _ENTRY.match(text)
            name = match.group(1).strip().rstrip(".:") if match else f"Hypothesis {number}"
            rest = match.group(2).strip() if match else text
            hypotheses.append(Entry(name=name, html=_md(rest), text=_plain(rest)))

    intro_html = _md(intro) if intro.strip() else ""
    return ParsedReport(
        week_title=week_title,
        window=window,
        meta_line=meta_line,
        intro_html=intro_html,
        brief=brief,
        sections=sections,
        hypotheses=hypotheses,
        appendix_md=appendix,
        has_parts=has_parts,
    )


# ---------- evidence from the database ----------
def _canonical_city(name: str) -> str:
    key = name.strip().lower()
    if key in _CITY_ALIASES:
        return _CITY_ALIASES[key]
    for city in CITY_COORDS:
        if city.lower() == key:
            return city
    return name.strip()


def _item_time(item: ContentItem) -> datetime | None:
    value = item.published_at or item.discovered_at
    return ensure_utc(value) if value else None


def _window_bounds(report: WeeklyReport, parsed: ParsedReport) -> tuple[datetime, datetime]:
    from datetime import UTC

    if parsed.window:
        start = datetime.combine(parsed.window[0], datetime.min.time(), tzinfo=UTC)
        end = datetime.combine(parsed.window[1], datetime.min.time(), tzinfo=UTC) + timedelta(
            days=1
        )
        return start, end
    end = ensure_utc(report.generated_at)
    return end - timedelta(days=7), end


def _item_card(item: ContentItem, source: Source | None, analysis: ContentAnalysis | None) -> dict:
    when = _item_time(item)
    return {
        "id": item.id,
        "title": (item.title or "").strip() or "Untitled",
        "url": item.url,
        "source": source.name if source else "",
        "platform": source.platform if source else "",
        "date": when.date().isoformat() if when else "",
        "image": image_url(item),
        "summary": (analysis.summary or "")[:240] if analysis else "",
    }


def _bars(counter: Counter, limit: int, labels: dict | None = None) -> list[dict]:
    top = counter.most_common(limit)
    peak = top[0][1] if top else 1
    return [
        {
            "key": key,
            "label": (labels or {}).get(key, key),
            "value": value,
            "pct": round(100 * value / peak, 1),
        }
        for key, value in top
    ]


def _entity_names(analysis: ContentAnalysis) -> set[str]:
    names: set[str] = set()
    for attr in ("brands", "designers", "people", "creators", "products", "footwear", "garments"):
        for value in getattr(analysis, attr, None) or []:
            if isinstance(value, str) and len(value.strip()) >= 3:
                names.add(value.strip())
    return names


def _match_image(text: str, candidates: list[tuple[dict, set[str]]], used: set[int]) -> dict | None:
    """The item this text is demonstrably about, if it has an image.

    Counts named entities of the item (brands, designers, products) that appear
    in the text as whole words. Needs two different names; the
    item's source named in the text only breaks ties. Otherwise no image: a wrong
    picture is worse than none."""
    lowered = f" {text.lower()} "
    best, best_score = None, 0
    for card, names in candidates:
        if card["id"] in used or not card["image"]:
            continue
        hits = {
            n
            for n in names
            if len(n) >= 4
            and n[0].isupper()
            and n.lower() not in _STOP
            and re.search(rf"(?<![\w]){re.escape(n.lower())}(?![\w])", lowered)
        }
        source_named = bool(card["source"]) and _source_short(card["source"]) in lowered
        if len(hits) >= 2:
            score = sum(len(h) for h in hits) + (10 if source_named else 0)
            if score > best_score:
                best, best_score = card, score
    if best:
        used.add(best["id"])
    return best


def _source_short(name: str) -> str:
    """'Business of Fashion (YouTube)' -> 'business of fashion'; reports also write 'BoF'."""
    base = re.sub(r"\s*\(.*\)\s*$", "", name).strip().lower()
    return {"business of fashion": "bof"}.get(base, base)


@dataclass
class ReportPage:
    report: WeeklyReport
    parsed: ParsedReport
    since: date
    until: date
    figures: list[dict]
    platform_bars: list[dict]
    source_bars: list[dict]
    daily: list[dict]
    signals: list[dict]
    new_signals: list[dict]
    cities: list[dict]
    map_points: list[dict]
    gallery: list[dict]
    cover: dict | None
    chapters: list[dict]
    total_items: int


def build_report_page(session: Session, report: WeeklyReport) -> ReportPage:
    parsed = parse_report(report.content_markdown)
    start, end = _window_bounds(report, parsed)

    items = [
        i
        for i in session.scalars(
            select(ContentItem).where(
                ((ContentItem.published_at >= start) & (ContentItem.published_at < end))
                | (
                    (ContentItem.published_at.is_(None))
                    & (ContentItem.discovered_at >= start)
                    & (ContentItem.discovered_at < end)
                )
            )
        )
    ]
    ids = [i.id for i in items]
    sources = {s.id: s for s in session.scalars(select(Source))}
    analyses: dict[int, ContentAnalysis] = {}
    if ids:
        for a in session.scalars(
            select(ContentAnalysis)
            .where(ContentAnalysis.content_item_id.in_(ids))
            .order_by(ContentAnalysis.id)
        ):
            analyses[a.content_item_id] = a

    # Collection: what the report is built on.
    platform_counts = Counter(
        sources[i.source_id].platform for i in items if i.source_id in sources
    )
    source_counts = Counter(sources[i.source_id].name for i in items if i.source_id in sources)
    days = Counter(_item_time(i).date() for i in items if _item_time(i))
    span = [start.date() + timedelta(days=n) for n in range((end.date() - start.date()).days)]
    day_peak = max([days.get(d, 0) for d in span] or [1]) or 1
    daily = [
        {
            "date": d.isoformat(),
            "label": d.strftime("%a %d"),
            "value": days.get(d, 0),
            "pct": round(100 * days.get(d, 0) / day_peak, 1),
        }
        for d in span
    ]

    # Signals moving in this window, with twelve weeks of weekly evidence each.
    # "This week" for a signal means evidence linked during the window, the same
    # definition the report's own registry table uses ("+This window").
    evidence_rows = session.execute(
        select(SignalEvidence.signal_id, SignalEvidence.content_item_id, SignalEvidence.created_at)
    ).all()
    weeks_back = 12
    first_week = start - timedelta(weeks=weeks_back - 1)
    in_window: dict[int, list[int]] = defaultdict(list)
    weekly: dict[int, list[int]] = defaultdict(lambda: [0] * weeks_back)
    for signal_id, item_id, created in evidence_rows:
        when = ensure_utc(created) if created else None
        if not when:
            continue
        if start <= when < end:
            in_window[signal_id].append(item_id)
        if first_week <= when < end:
            idx = min(weeks_back - 1, (when - first_week).days // 7)
            weekly[signal_id][idx] += 1
    active = {
        s.id: s
        for s in session.scalars(select(Signal).where(Signal.state == SignalState.ACTIVE.value))
    }
    cards = {i.id: _item_card(i, sources.get(i.source_id), analyses.get(i.id)) for i in items}
    # Evidence linked this week can point at items published a little earlier.
    extra = {iid for ids_ in in_window.values() for iid in ids_} - set(cards)
    if extra:
        for item in session.scalars(select(ContentItem).where(ContentItem.id.in_(extra))):
            cards[item.id] = _item_card(item, sources.get(item.source_id), None)
    moving = sorted(
        (sid for sid in in_window if sid in active),
        key=lambda sid: (-len(in_window[sid]), -active[sid].evidence_count),
    )
    peak_window = max((len(in_window[s]) for s in moving), default=1) or 1
    signals = []
    for sid in moving[:16]:
        sig = active[sid]
        series = weekly[sid]
        series_peak = max(series) or 1
        evidence = [cards[i] for i in in_window[sid] if i in cards]
        signals.append(
            {
                "id": sid,
                "name": sig.name,
                "stage": sig.lifecycle_stage or "unknown",
                "window": len(in_window[sid]),
                "window_pct": round(100 * len(in_window[sid]) / peak_window, 1),
                "total": sig.evidence_count,
                "sources": sig.source_count,
                "cities": [_canonical_city(c) for c in (sig.cities or [])[:3]],
                "first_seen": ensure_utc(sig.first_detected_at).date().isoformat()
                if sig.first_detected_at
                else "",
                "new": bool(sig.first_detected_at and ensure_utc(sig.first_detected_at) >= start),
                "series": [{"v": v, "pct": round(100 * v / series_peak, 1)} for v in series],
                "image": next((e for e in evidence if e["image"]), None),
                "evidence": evidence[:3],
            }
        )
    new_signals = [s for s in signals if s["new"]]

    # Cities named in this window's analyses.
    city_counts: Counter = Counter()
    for a in analyses.values():
        for city in {
            (_canonical_city(c)) for c in (a.cities or []) if isinstance(c, str) and c.strip()
        }:
            city_counts[city] += 1
    cities = _bars(city_counts, 14)
    peak_city = cities[0]["value"] if cities else 1
    map_points = [
        {
            "city": c["key"],
            "value": c["value"],
            "x": round((CITY_COORDS[c["key"]][1] + 180) / 360 * 1000, 1),
            "y": round((90 - CITY_COORDS[c["key"]][0]) / 180 * 500, 1),
            "r": round(4 + 18 * (c["value"] / peak_city) ** 0.5, 1),
        }
        for c in _bars(city_counts, 30)
        if c["key"] in CITY_COORDS
    ]

    # Images: brief points first (matched on named entities), then a gallery of the window.
    candidates = [(cards[i.id], _entity_names(analyses[i.id])) for i in items if i.id in analyses]
    used: set[int] = set()
    for point in parsed.brief:
        point.image = _match_image(point.headline + " " + point.body_text, candidates, used)
    # Cover: a picture that belongs to one of the brief's points, labelled with that
    # point. No fallback to another story's image: a wrong cover misleads.
    cover = next(({**p.image, "point": p.number} for p in parsed.brief if p.image), None)
    gallery = [
        c for c in cards.values() if c["image"] and c["id"] not in used and c["id"] in analyses
    ]
    # Publication photography first; Instagram (often memes) after.
    gallery.sort(key=lambda c: (c["platform"] == "instagram", c["date"]), reverse=False)
    gallery = gallery[:12]

    figures = [
        {"value": len(items), "label": "items read", "note": f"from {len(source_counts)} sources"},
        {"value": len(moving), "label": "signals moved", "note": "with new evidence this week"},
        {"value": len(new_signals), "label": "new signals", "note": "first seen this week"},
    ]
    if cities:
        figures.append(
            {
                "value": cities[0]["key"],
                "label": "most cited city",
                "note": f"{cities[0]['value']} items",
            }
        )

    chapters = [
        {"slug": "summary", "title": "Executive summary"},
        {"slug": "trends", "title": "Main trends"},
        {"slug": "signals", "title": "Underlying signals"},
        {"slug": "insights", "title": "Strategic insights"},
        {"slug": "sources", "title": "Sources"},
    ]

    return ReportPage(
        report=report,
        parsed=parsed,
        since=start.date(),
        until=(end - timedelta(days=1)).date(),
        figures=figures,
        platform_bars=_bars(platform_counts, 8, PLATFORM_LABELS),
        source_bars=_bars(source_counts, 12),
        daily=daily,
        signals=signals,
        new_signals=new_signals,
        cities=cities,
        map_points=map_points,
        gallery=gallery,
        cover=cover,
        chapters=chapters,
        total_items=len(items),
    )


def report_summary(report: WeeklyReport) -> dict:
    """Cheap card data for the reports index: no database work beyond the row."""
    parsed = parse_report(report.content_markdown)
    lead = parsed.brief[0].headline if parsed.brief else parsed.week_title
    return {
        "name": report.iso_week,
        "week_title": parsed.week_title,
        "headline": lead,
        "points": [p.headline for p in parsed.brief[1:4]],
        "window": parsed.window,
        "items": report.items_covered,
        "sources": report.sources_covered,
        "brief_count": len(parsed.brief),
        "hypotheses": len(parsed.hypotheses),
        "generated": ensure_utc(report.generated_at).date().isoformat()
        if report.generated_at
        else "",
        "public": report.is_public,
        # Everything a reader might look for: every headline and every named thing.
        "search_text": " ".join(
            [p.headline for p in parsed.brief]
            + [e.name for sec in parsed.sections.values() for e in sec.entries]
            + [h.name for h in parsed.hypotheses]
        ),
    }


def signals_csv(page: ReportPage) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "signal",
            "stage",
            "evidence_this_week",
            "evidence_total",
            "sources",
            "cities",
            "first_seen",
        ]
    )
    for s in page.signals:
        writer.writerow(
            [
                s["name"],
                s["stage"],
                s["window"],
                s["total"],
                s["sources"],
                "; ".join(s["cities"]),
                s["first_seen"],
            ]
        )
    return buf.getvalue()


def items_csv(session: Session, page: ReportPage) -> str:
    import csv
    import io
    from datetime import UTC

    lo = datetime.combine(page.since, datetime.min.time(), tzinfo=UTC)
    hi = datetime.combine(page.until, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
    sources = {s.id: s for s in session.scalars(select(Source))}
    rows = []
    for item in session.scalars(
        select(ContentItem).where(
            ((ContentItem.published_at >= lo) & (ContentItem.published_at < hi))
            | (
                (ContentItem.published_at.is_(None))
                & (ContentItem.discovered_at >= lo)
                & (ContentItem.discovered_at < hi)
            )
        )
    ):
        when = _item_time(item)
        src = sources.get(item.source_id)
        rows.append(
            [
                when.date().isoformat() if when else "",
                src.name if src else "",
                src.platform if src else "",
                item.title or "",
                item.url,
            ]
        )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["date", "source", "platform", "title", "url"])
    writer.writerows(sorted(rows))
    return buf.getvalue()
