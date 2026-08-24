from pathlib import Path

import pytest
import yaml

from culture.models import Source, SourceTier
from culture.repositories.sources import SourceRepository
from culture.schemas.source import SeedSource
from culture.services.seeding import import_seeds, load_seed_records

VALID_RECORDS = [
    {
        "name": "Sabukaru",
        "platform": "web",
        "source_type": "publication",
        "url": "https://sabukaru.online",
        "feed_url": "https://sabukaru.online/articles?format=rss",
        "country": "Japan",
        "city": "Tokyo",
        "categories": ["fashion", "youth culture"],
        "intelligence_layers": ["scene_subculture"],
        "tier": "core",
        "active": True,
        "collection_method": "rss",
    },
    {
        "name": "Drew Joiner",
        "platform": "youtube",
        "source_type": "creator",
        "url": "https://www.youtube.com/@DrewJoiner",
        "external_identifier": "UCc0-cA0mThWPk7_PGjT8oFA",
        "feed_url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCc0-cA0mThWPk7_PGjT8oFA",
        "tier": "core",
        "collection_method": "rss",
    },
    {
        "name": "Nolita Dirtbag",
        "platform": "instagram",
        "url": "https://www.instagram.com/nolitadirtbag",
        "tier": "watch",
        "active": False,
        "notes": "No Instagram collector in V0.",
    },
]


def test_seed_import_creates_sources(session):
    result = import_seeds(session, VALID_RECORDS)
    session.commit()

    assert result.ok
    assert sorted(result.created) == ["Drew Joiner", "Nolita Dirtbag", "Sabukaru"]

    repo = SourceRepository(session)
    sabukaru = repo.get_by_name("Sabukaru")
    assert sabukaru.tier == "core"
    assert sabukaru.categories == ["fashion", "youth culture"]
    instagram = repo.get_by_name("Nolita Dirtbag")
    assert instagram.active is False
    assert instagram.collection_notes == "No Instagram collector in V0."


def test_second_import_is_idempotent(session):
    import_seeds(session, VALID_RECORDS)
    session.commit()
    result = import_seeds(session, VALID_RECORDS)
    session.commit()

    assert result.created == []
    assert result.updated == []
    assert len(result.unchanged) == 3
    assert session.query(Source).count() == 3


def test_reimport_updates_changed_seed_fields(session):
    import_seeds(session, VALID_RECORDS)
    session.commit()

    changed = [dict(VALID_RECORDS[0], tier="watch")]
    result = import_seeds(session, changed)
    session.commit()

    assert result.updated == ["Sabukaru"]
    assert SourceRepository(session).get_by_name("Sabukaru").tier == "watch"


def test_reimport_preserves_operational_fields(session):
    from datetime import UTC, datetime

    import_seeds(session, VALID_RECORDS)
    session.commit()
    source = SourceRepository(session).get_by_name("Sabukaru")
    checked = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
    source.last_checked_at = checked
    session.commit()

    import_seeds(session, VALID_RECORDS)
    session.commit()
    assert SourceRepository(session).get_by_name("Sabukaru").last_checked_at is not None


def test_invalid_record_reported_but_others_imported(session):
    records = [
        {"name": "Good Source", "platform": "web", "url": "https://example.com"},
        {"name": "Bad Platform", "platform": "carrier_pigeon"},
        {"name": "Bad URL", "platform": "web", "feed_url": "not-a-url"},
    ]
    result = import_seeds(session, records)
    session.commit()

    assert result.created == ["Good Source"]
    assert len(result.errors) == 2
    assert not result.ok
    assert session.query(Source).count() == 1


def test_seed_source_defaults():
    seed = SeedSource(name="Minimal", platform="web")
    assert seed.tier == SourceTier.CANDIDATE
    assert seed.active is True
    assert seed.categories == []


def test_load_seed_records_rejects_bad_shape(tmp_path: Path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(["just", "a", "list"]))
    with pytest.raises(ValueError):
        load_seed_records(bad)
    with pytest.raises(FileNotFoundError):
        load_seed_records(tmp_path / "missing.yaml")


def test_real_seed_file_is_fully_valid(session):
    seed_file = Path(__file__).parents[2] / "seeds" / "sources.yaml"
    records = load_seed_records(seed_file)
    result = import_seeds(session, records)
    session.commit()

    assert result.ok, result.errors
    assert len(result.created) == len(records)
    # Every active RSS-collectable source must have a feed URL; sources
    # without one must carry an explanatory note instead.
    for source, _ in SourceRepository(session).list_with_latest_item():
        if source.platform in ("web", "youtube") and source.collection_method == "rss":
            assert source.feed_url, f"{source.name} claims rss but has no feed_url"
        if source.platform in ("web", "youtube") and not source.feed_url:
            assert source.collection_notes, f"{source.name} has no feed and no note"


def test_list_with_latest_item_handles_sources_without_content(session):
    import_seeds(session, VALID_RECORDS)
    session.commit()
    rows = SourceRepository(session).list_with_latest_item()
    assert len(rows) == 3
    assert all(latest is None for _, latest in rows)
