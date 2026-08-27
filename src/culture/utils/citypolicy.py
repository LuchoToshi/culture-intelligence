"""Public city naming policy: controlled vocabulary + normalization table.

Public-facing content shows major, widely recognized cities only. Anything
else — small towns, resorts, regions, niche locales — is either mapped to an
appropriate major metro (when that is accurate) or omitted from public views
entirely. Internal admin and operational views keep original locations; this
module is applied at the public display layer only, never to stored evidence.

The default is deny: a location with no approved match and no mapping row is
omitted publicly. That is the policy's own rule ("if a location cannot be
confidently mapped to an approved city, leave it unlisted"), and it means an
unreviewed new locale fails closed instead of leaking onto a public page.

The mapping rows follow the operator-specified record format (original,
normalized, mapping_type, confidence, rationale, public_output, notes,
last_updated) so the table doubles as its own decision log. public_output is
authoritative: blank means omit, whatever the other fields say. Rows were
seeded from a survey of the 171 distinct city strings in production on
2026-08-26, not invented.

This vocabulary is internal. Do not render mapping rows, rationales, or the
existence of omitted locations in public output.
"""

from __future__ import annotations

from dataclasses import dataclass

# The candidate vocabulary: major cities we recognize and may monitor,
# organized by the 26 Aug tier policy. Being in this set makes a city
# *nameable*; it does not make it publicly displayable. Display approval is
# governed by CITY_REGISTRY below (27 Aug evidence policy): a city renders
# publicly only when its registry entry is Approved, which requires both a
# documented evidence entry and a dedicated source.
CANDIDATE_CITIES: set[str] = {
    # Core global cities (deepest continuous monitoring)
    "London", "Paris", "Milan", "New York", "Tokyo", "Seoul", "Los Angeles",
    # European core
    "Copenhagen", "Stockholm", "Amsterdam", "Berlin", "Antwerp", "Brussels",
    # European watch network
    "Tbilisi", "Lisbon", "Madrid", "Barcelona", "Manchester", "Helsinki",
    "Florence", "Istanbul", "Warsaw", "Vienna",
    # Early-signal watch cities
    "Osaka", "Hong Kong", "Bangkok", "Taipei", "São Paulo", "Lagos",
    "Johannesburg", "Cape Town", "Buenos Aires", "Montreal",
    # Core urban taste cities
    "Shanghai", "Mexico City", "Melbourne", "Sydney",
    # Majors present in production evidence, retained from the initial policy
    "Marseille", "San Francisco", "Chicago", "Miami", "Boston", "Philadelphia",
    "Dallas", "Austin", "Las Vegas", "Kyoto", "Dubai", "Toronto", "Vancouver",
}


@dataclass(frozen=True)
class CityMapping:
    original_location: str
    normalized_city: str
    country_or_region: str
    mapping_type: str  # direct | metro | omit
    confidence: str  # high | medium | low
    rationale: str
    public_output: str  # normalized_city, or "" to omit
    internal_notes: str
    last_updated: str


_ROWS: list[CityMapping] = [
    # Aliases: same city, different spelling. Always safe.
    CityMapping("NYC", "New York", "US", "direct", "high",
                "Alias of an approved city", "New York", "", "2026-08-26"),
    CityMapping("New York City", "New York", "US", "direct", "high",
                "Alias of an approved city", "New York", "", "2026-08-26"),
    CityMapping("LA", "Los Angeles", "US", "direct", "high",
                "Alias of an approved city", "Los Angeles", "", "2026-08-26"),
    CityMapping("Philly", "Philadelphia", "US", "direct", "high",
                "Alias of an approved city", "Philadelphia", "", "2026-08-26"),
    CityMapping("SF", "San Francisco", "US", "direct", "high",
                "Alias of an approved city", "San Francisco", "", "2026-08-26"),
    CityMapping("Amsterdam-Oost", "Amsterdam", "NL", "direct", "high",
                "Neighborhood of an approved city", "Amsterdam", "", "2026-08-26"),
    CityMapping("Sao Paulo", "São Paulo", "BR", "direct", "high",
                "ASCII spelling of an approved city", "São Paulo", "", "2026-08-26"),
    CityMapping("İstanbul", "Istanbul", "TR", "direct", "high",
                "Local spelling of an approved city", "Istanbul", "", "2026-08-26"),
    CityMapping("CDMX", "Mexico City", "MX", "direct", "high",
                "Alias of an approved city", "Mexico City", "", "2026-08-26"),
    CityMapping("Ciudad de México", "Mexico City", "MX", "direct", "high",
                "Local name of an approved city", "Mexico City", "", "2026-08-26"),
    # Metro normalization: locale genuinely inside a major metro.
    CityMapping("Brooklyn", "New York", "US", "metro", "high",
                "Borough of an approved city", "New York", "", "2026-08-26"),
    CityMapping("BK", "New York", "US", "metro", "high",
                "Borough alias of an approved city", "New York", "", "2026-08-26"),
    CityMapping("Santa Monica", "Los Angeles", "US", "metro", "high",
                "LA metro locale", "Los Angeles", "", "2026-08-26"),
    CityMapping("Laguna Beach", "Los Angeles", "US", "metro", "medium",
                "Map small locale to nearest major metro when appropriate",
                "Los Angeles", "Retain original in admin only", "2026-08-26"),
    CityMapping("Bedford, New York", "New York", "US", "metro", "medium",
                "NY metro area locale", "New York", "Retain original in admin only", "2026-08-26"),
    CityMapping("Gary, Indiana", "Chicago", "US", "metro", "medium",
                "Chicago metro area locale", "Chicago", "Retain original in admin only",
                "2026-08-26"),
    CityMapping("Yokohama", "Tokyo", "JP", "metro", "medium",
                "Greater Tokyo area", "Tokyo", "Retain original in admin only", "2026-08-26"),
    CityMapping("Napa Valley", "San Francisco", "US", "metro", "low",
                "Map region to major metro only when justified; otherwise omit",
                "San Francisco", "Prefer omit if mapping is disputed", "2026-08-26"),
    # Omissions: resorts, regions, and locales with no accurate major-metro home.
    CityMapping("Aspen", "", "US", "omit", "low",
                "Cannot confidently map to an approved city", "",
                "Keep original location internal only", "2026-08-26"),
    CityMapping("Myrtle Beach", "", "US", "omit", "low",
                "Resort town, no approved metro", "", "Internal only", "2026-08-26"),
    CityMapping("Cannes", "", "FR", "omit", "low",
                "Resort town, no approved metro", "", "Internal only", "2026-08-26"),
    CityMapping("Ibiza", "", "ES", "omit", "low",
                "Resort island, no approved metro", "", "Internal only", "2026-08-26"),
    CityMapping("Central Coast", "", "US", "omit", "low",
                "Region, not a city", "", "Internal only", "2026-08-26"),
    CityMapping("Lancaster, Ohio", "", "US", "omit", "low",
                "Small town, mapping to Columbus not approved", "", "Internal only", "2026-08-26"),
    CityMapping("Richmond, Virginia", "", "US", "omit", "low",
                "Own small metro, not in approved vocabulary", "", "Internal only", "2026-08-26"),
    CityMapping("Winchester", "", "UK", "omit", "low",
                "Small city, not in approved vocabulary", "", "Internal only", "2026-08-26"),
    CityMapping("Leeds", "", "UK", "omit", "low",
                "Regional city, not in approved vocabulary", "", "Internal only", "2026-08-26"),
    CityMapping("Bristol", "", "UK", "omit", "low",
                "Regional city, not in approved vocabulary", "", "Internal only", "2026-08-26"),
    CityMapping("Nara", "", "JP", "omit", "low",
                "Kansai locale, single-metro mapping disputed", "", "Internal only", "2026-08-26"),
    CityMapping("Sendai", "", "JP", "omit", "low",
                "Not widely recognized internationally; no accurate metro map", "",
                "Internal only", "2026-08-26"),
    CityMapping("Lausanne", "", "CH", "omit", "low",
                "Not in approved vocabulary; Geneva mapping inaccurate", "",
                "Internal only", "2026-08-26"),
]

MAPPINGS: dict[str, CityMapping] = {row.original_location.lower(): row for row in _ROWS}
_CANDIDATE_LOWER: dict[str, str] = {c.lower(): c for c in CANDIDATE_CITIES}


@dataclass(frozen=True)
class CityRecord:
    """One internal evidence-and-sourcing entry per candidate city.

    The 27 Aug policy's acceptance checks, enforced by tests: an Approved
    entry must document what was observed, when, the signals used, and a
    dedicated source — a specific, attributable origin reviewable
    internally, not secondary mentions. Anything less stays Pending review,
    Draft, or Blocked, and is excluded from public display. This module is
    internal; none of these fields may ever reach a rendered page."""

    name: str
    tier: str
    status: str  # Draft | Pending review | Approved | Blocked
    evidence_summary: str
    evidence_date: str
    signals_used: tuple[str, ...]
    source_identifier: str  # internal only — dedicated source(s) of record
    reviewer: str
    last_updated: str


_REV = "Claude, from production coverage query"
_DATE = "2026-08-27"
_PERIOD = "2026-04 through 2026-08"


def _approved(name, tier, summary, sigs, src):
    return CityRecord(name, tier, "Approved", summary, _PERIOD, tuple(sigs), src, _REV, _DATE)


def _pending(name, tier, why):
    return CityRecord(name, tier, "Pending review", why, _PERIOD, (), "", _REV, _DATE)


def _draft(name, tier):
    return CityRecord(
        name, tier, "Draft", "No observations yet; no dedicated source.", "", (), "", _REV, _DATE
    )


CITY_REGISTRY: tuple[CityRecord, ...] = (
    _approved("London", "core-global",
              "58 evidence mentions across 11 dedicated sources; "
              "strongest signal density on the platform.",
              ("Western Internet Rap Producers Crossing Into K-pop Mainstream",
               "Producers Stepping Out as Front-Facing Solo Artists"),
              "11 active London-based sources incl. Dazed, i-D, CULTED"),
    _approved("New York", "core-global",
              "86 evidence mentions across 14 dedicated sources; "
              "deepest coverage of any city.",
              ("Digital Detox as Premium Brand Positioning",
               "Japanese Spatial Design Vocabulary Entering Retail and Brand Environments"),
              "14 active NY-based sources incl. Office Magazine, Drew Joiner"),
    _approved("Tokyo", "core-global",
              "25 evidence mentions across 3 dedicated sources; consistently signal-productive.",
              ("Japan as Validation Circuit for Western Internet-Native Artists",
               "Anime Exhibitions as Club Music Venues"),
              "Sabukaru, FASHIONSNAP, HOUYHNHNM"),
    _approved("Seoul", "core-global",
              "6 evidence mentions across 2 dedicated sources; high signal yield per mention.",
              ("Seoul/Tokyo Underground Collectives Fusing Fashion Subcultures with Hardcore/Noise",
               "Cross-Border Diaspora Networks Linking Seoul Underground to Global Scenes"),
              "EYESMAG, MUSINSA"),
    _approved("Paris", "core-global",
              "32 evidence mentions; one dedicated source plus heavy coverage from global sources.",
              ("Japan as Validation Circuit for Western Internet-Native Artists",
               "Japanese Heritage Sport Brands Using Rap Ambassadors"),
              "Yugnat999"),
    _approved("Amsterdam", "eu-core",
              "5 evidence mentions across 2 dedicated sources.",
              ("Avant-Garde 'Freaky Footwear' Micro-Trend",),
              "Saints & Stars Locker Room, Oat Milk Élite"),
    _approved("Berlin", "eu-core",
              "11 evidence mentions across 3 dedicated sources.",
              ("Kawaii Robotics as Cute-Tech Accessories",),
              "032c, Highsnobiety, Berlin Club Memes"),
    _approved("Madrid", "eu-watch",
              "3 evidence mentions; one dedicated source.",
              ("Actors With Archive-Fashion Backgrounds Platformed as Style "
               "Voices on Menswear Podcasts",),
              "Fucking Young!"),
    _approved("Montreal", "early-signal",
              "1 evidence mention; one dedicated source.",
              ("Japan as Validation Circuit for Western Internet-Native Artists",),
              "SSENSE Editorial"),
    # Dedicated source active but nothing observed yet.
    _pending("Brussels", "eu-core", "Dedicated source active; zero observations so far."),
    # Observed only through secondary mentions — no dedicated source, so not
    # approvable under the 27 Aug policy until one is activated.
    _pending("Milan", "core-global", "2 secondary mentions; no dedicated source."),
    _pending("Los Angeles", "core-global",
             "28 secondary mentions and 5 signals via global sources; no dedicated source."),
    _pending("Copenhagen", "eu-core", "7 secondary mentions, 1 signal; no dedicated source."),
    _pending("Stockholm", "eu-core", "3 secondary mentions; no dedicated source."),
    _pending("Antwerp", "eu-core", "2 secondary mentions, 1 signal; no dedicated source."),
    _pending("Lisbon", "eu-watch", "1 secondary mention; no dedicated source."),
    _pending("Barcelona", "eu-watch", "2 secondary mentions; no dedicated source."),
    _pending("Manchester", "eu-watch", "6 secondary mentions; no dedicated source."),
    _pending("Helsinki", "eu-watch", "1 secondary mention; no dedicated source."),
    _pending("Osaka", "early-signal", "5 secondary mentions, 3 signals; no dedicated source."),
    _pending("Hong Kong", "early-signal", "6 secondary mentions, 3 signals; no dedicated source."),
    _pending("Bangkok", "early-signal", "1 secondary mention; no dedicated source."),
    _pending("Shanghai", "urban-taste", "2 secondary mentions, 1 signal; no dedicated source."),
    _pending("Melbourne", "urban-taste", "1 secondary mention; no dedicated source."),
    _pending("Sydney", "urban-taste", "1 secondary mention; no dedicated source."),
    # Retained majors from the initial policy: displayable history but no
    # dedicated source — same rule applies.
    _pending("Marseille", "retained", "2 secondary mentions; no dedicated source."),
    _pending("San Francisco", "retained", "3 secondary mentions; no dedicated source."),
    _pending("Chicago", "retained", "6 secondary mentions; no dedicated source."),
    _pending("Miami", "retained", "6 secondary mentions; no dedicated source."),
    _pending("Boston", "retained", "2 secondary mentions; no dedicated source."),
    _pending("Philadelphia", "retained", "4 secondary mentions; no dedicated source."),
    _pending("Dallas", "retained", "3 secondary mentions; no dedicated source."),
    _pending("Austin", "retained", "3 secondary mentions; no dedicated source."),
    _pending("Las Vegas", "retained", "3 secondary mentions; no dedicated source."),
    _pending("Kyoto", "retained", "2 secondary mentions; no dedicated source."),
    _pending("Dubai", "retained", "4 secondary mentions; no dedicated source."),
    _pending("Toronto", "retained", "4 secondary mentions; no dedicated source."),
    _pending("Vancouver", "retained", "2 secondary mentions; no dedicated source."),
    _draft("Tbilisi", "eu-watch"),
    _draft("Florence", "eu-watch"),
    _draft("Istanbul", "eu-watch"),
    _draft("Warsaw", "eu-watch"),
    _draft("Vienna", "eu-watch"),
    _draft("Taipei", "early-signal"),
    _draft("São Paulo", "early-signal"),
    _draft("Lagos", "early-signal"),
    _draft("Johannesburg", "early-signal"),
    _draft("Cape Town", "early-signal"),
    _draft("Buenos Aires", "early-signal"),
    _draft("Mexico City", "urban-taste"),
)

# Public display derives from the registry — never edit this set directly.
APPROVED_CITIES: frozenset[str] = frozenset(
    r.name for r in CITY_REGISTRY if r.status == "Approved"
)


def public_city(raw: str) -> str | None:
    """The approved public label for a detected location, or None to omit.

    Two gates: the location must resolve to a known candidate city (vocab
    match or mapping row), and that city's registry entry must be Approved.
    A candidate without evidence and a dedicated source resolves internally
    but never renders publicly."""
    name = raw.strip()
    if not name:
        return None
    label = _CANDIDATE_LOWER.get(name.lower())
    if label is None and (row := MAPPINGS.get(name.lower())) is not None:
        label = row.public_output or None
    if label is None:
        return None  # fail closed: unreviewed locales never reach public output
    return label if label in APPROVED_CITIES else None


def public_cities(raw_list: list[str] | None) -> list[str]:
    """Approved labels for a list, deduplicated, original order preserved."""
    seen: dict[str, None] = {}
    for raw in raw_list or []:
        if (city := public_city(raw)) is not None:
            seen.setdefault(city, None)
    return list(seen)


def first_public_city(raw_list: list[str] | None) -> str | None:
    """First approved label in a list — for single-city chips on public cards."""
    for raw in raw_list or []:
        if (city := public_city(raw)) is not None:
            return city
    return None
