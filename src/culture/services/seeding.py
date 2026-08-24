from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError
from sqlalchemy.orm import Session

from culture.logging import get_logger
from culture.repositories.sources import SourceRepository
from culture.schemas.source import SeedSource

log = get_logger("culture.seeding")


@dataclass
class SeedImportResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def load_seed_records(path: Path) -> list[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Seed file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("sources"), list):
        raise ValueError(f"{path} must contain a top-level 'sources' list")
    return data["sources"]


def import_seeds(session: Session, records: list[dict]) -> SeedImportResult:
    """Validate and upsert seed records. Invalid records are reported, valid ones proceed."""
    repo = SourceRepository(session)
    result = SeedImportResult()
    for i, record in enumerate(records):
        label = record.get("name") if isinstance(record, dict) else f"record #{i + 1}"
        try:
            seed = SeedSource.model_validate(record)
        except ValidationError as exc:
            errors = "; ".join(e["msg"] for e in exc.errors())
            result.errors.append(f"{label}: {errors}")
            log.error("seed record invalid: %s: %s", label, errors)
            continue
        _, status = repo.upsert_seed(seed)
        getattr(result, status).append(seed.name)
        log.debug("seed %s: %s", status, seed.name)
    return result
