from culture.utils.citypolicy import (
    APPROVED_CITIES,
    MAPPINGS,
    first_public_city,
    public_cities,
    public_city,
)


def test_approved_city_passes_through_case_insensitively():
    assert public_city("Tokyo") == "Tokyo"
    assert public_city("tokyo") == "Tokyo"
    assert public_city(" LONDON ") == "London"


def test_aliases_normalize_to_approved_label():
    assert public_city("NYC") == "New York"
    assert public_city("new york city") == "New York"
    assert public_city("Philly") == "Philadelphia"


def test_metro_locales_map_to_major_city():
    assert public_city("Brooklyn") == "New York"
    assert public_city("Laguna Beach") == "Los Angeles"
    assert public_city("Gary, Indiana") == "Chicago"


def test_policy_example_rows_hold():
    # The three worked examples from the policy document, verbatim.
    assert public_city("Laguna Beach") == "Los Angeles"
    assert public_city("Aspen") is None
    assert public_city("Napa Valley") == "San Francisco"


def test_resorts_and_regions_are_omitted():
    for place in ("Myrtle Beach", "Cannes", "Ibiza", "Central Coast"):
        assert public_city(place) is None, place


def test_unknown_locale_fails_closed():
    # Default-deny: anything unreviewed is omitted, not displayed.
    assert public_city("Ouddorp") is None
    assert public_city("Some Future Scene Town") is None


def test_blank_public_output_means_omit_regardless_of_other_fields():
    for row in MAPPINGS.values():
        if row.mapping_type == "omit":
            assert row.public_output == ""
        if row.public_output:
            assert row.public_output in APPROVED_CITIES


def test_public_cities_dedupes_and_preserves_order():
    raw = ["Brooklyn", "NYC", "Aspen", "Tokyo", "New York"]
    assert public_cities(raw) == ["New York", "Tokyo"]


def test_first_public_city_skips_omitted_entries():
    assert first_public_city(["Aspen", "Napa Valley", "Berlin"]) == "San Francisco"
    assert first_public_city(["Aspen", "Nowhere"]) is None
    assert first_public_city([]) is None


def test_every_mapping_row_is_schema_complete():
    for row in MAPPINGS.values():
        assert row.mapping_type in {"direct", "metro", "omit"}
        assert row.confidence in {"high", "medium", "low"}
        assert row.rationale and row.last_updated


def test_expanded_vocabulary_tiers_are_approved():
    # 26 Aug expansion: watch-network and early-signal cities.
    for city in ("Antwerp", "Tbilisi", "Lisbon", "Taipei", "São Paulo",
                 "Lagos", "Cape Town", "Shanghai", "Mexico City", "Sydney"):
        assert public_city(city) == city, city
    assert public_city("Sao Paulo") == "São Paulo"
    assert public_city("CDMX") == "Mexico City"
