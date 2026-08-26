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

# Major, widely recognized cities approved for public display. Grow this
# deliberately — every addition is a public-vocabulary decision, not a data
# import. Keyed by exact display form.
APPROVED_CITIES: set[str] = {
    # Policy's own examples
    "Los Angeles", "New York", "London", "Seoul", "Tokyo", "Amsterdam", "Stockholm",
    # Majors present in production evidence
    "Paris", "Berlin", "Copenhagen", "Madrid", "Milan", "Marseille", "Manchester",
    "San Francisco", "Chicago", "Miami", "Boston", "Philadelphia", "Dallas",
    "Austin", "Las Vegas", "Osaka", "Kyoto", "Hong Kong", "Dubai",
    "Toronto", "Vancouver", "Montreal",
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
                "Chicago metro area locale", "Chicago", "Retain original in admin only", "2026-08-26"),
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
_APPROVED_LOWER: dict[str, str] = {c.lower(): c for c in APPROVED_CITIES}


def public_city(raw: str) -> str | None:
    """The approved public label for a detected location, or None to omit."""
    name = raw.strip()
    if not name:
        return None
    if (approved := _APPROVED_LOWER.get(name.lower())) is not None:
        return approved
    if (row := MAPPINGS.get(name.lower())) is not None:
        return row.public_output or None
    return None  # fail closed: unreviewed locales never reach public output


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
