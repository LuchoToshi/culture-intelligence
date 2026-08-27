from culture.utils.citypolicy import (
    APPROVED_CITIES,
    CANDIDATE_CITIES,
    CITY_REGISTRY,
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


def test_metro_locales_map_to_major_city_when_target_is_approved():
    assert public_city("Brooklyn") == "New York"
    assert public_city("Yokohama") == "Tokyo"
    # Chicago's registry entry is Pending (no dedicated source), so its
    # metro locales are omitted publicly rather than displayed.
    assert public_city("Gary, Indiana") is None


def test_policy_example_rows_resolve_but_display_is_evidence_gated():
    # The mapping table still records the decisions verbatim, but the 27 Aug
    # evidence policy gates display: LA and SF lack a dedicated source, so
    # both resolve internally and render as nothing publicly.
    assert MAPPINGS["laguna beach"].public_output == "Los Angeles"
    assert MAPPINGS["napa valley"].public_output == "San Francisco"
    assert public_city("Laguna Beach") is None
    assert public_city("Napa Valley") is None
    assert public_city("Aspen") is None


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
            assert row.public_output in CANDIDATE_CITIES


def test_public_cities_dedupes_and_preserves_order():
    raw = ["Brooklyn", "NYC", "Aspen", "Tokyo", "New York"]
    assert public_cities(raw) == ["New York", "Tokyo"]


def test_first_public_city_skips_omitted_and_unapproved_entries():
    assert first_public_city(["Aspen", "Napa Valley", "Berlin"]) == "Berlin"
    assert first_public_city(["Aspen", "Nowhere"]) is None
    assert first_public_city([]) is None


def test_every_mapping_row_is_schema_complete():
    for row in MAPPINGS.values():
        assert row.mapping_type in {"direct", "metro", "omit"}
        assert row.confidence in {"high", "medium", "low"}
        assert row.rationale and row.last_updated


def test_watch_cities_are_candidates_but_not_displayed_until_evidenced():
    # 26 Aug expansion made these nameable; 27 Aug policy keeps them off
    # public output until each has evidence and a dedicated source.
    for city in ("Antwerp", "Tbilisi", "Taipei", "Lagos", "Mexico City", "Sydney"):
        assert city in CANDIDATE_CITIES, city
        assert public_city(city) is None, city


# --- 27 Aug evidence policy: the acceptance checks, mechanically enforced ---


def test_every_candidate_city_has_a_registry_entry():
    assert {r.name for r in CITY_REGISTRY} == CANDIDATE_CITIES


def test_approved_entries_have_evidence_and_a_dedicated_source():
    for r in CITY_REGISTRY:
        if r.status == "Approved":
            assert r.evidence_summary and r.evidence_date, r.name
            assert r.signals_used, r.name
            assert r.source_identifier, r.name
            assert r.reviewer and r.last_updated, r.name


def test_entries_missing_evidence_or_source_are_excluded_from_display():
    for r in CITY_REGISTRY:
        if r.status != "Approved":
            assert r.name not in APPROVED_CITIES, r.name
    assert {r.name for r in CITY_REGISTRY if r.status == "Approved"} == APPROVED_CITIES


def test_registry_statuses_are_from_the_policy_vocabulary():
    for r in CITY_REGISTRY:
        assert r.status in {"Draft", "Pending review", "Approved", "Blocked"}, r.name
