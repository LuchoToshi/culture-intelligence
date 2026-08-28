"""`culture auth provision-owner` / `migrate-allowlist`.

CLI commands always resolve their own engine via `get_engine()` (no
injection point like the web app's `create_app(engine=...)`), so these
tests point DATABASE_URL at a file-based SQLite database (not `:memory:`,
which would give each internal `get_engine()` call its own empty database)
and call the Typer command functions directly rather than through
`typer.testing.CliRunner` — simpler, and it exercises the same business
logic. Typer's `typer.Option(...)` default objects are only resolved by the
CLI parsing layer, so every Option-typed parameter must be passed explicitly
when calling a command function directly (`dry_run=True`, not omitted).
"""

from unittest.mock import patch
from uuid import UUID

import pytest
import typer
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from culture import cli
from culture.config import get_settings
from culture.database import Base
from culture.models.auth_event import AuthEvent
from culture.models.email_log import EmailLog
from culture.models.profile import ROLE_OWNER, STATUS_APPROVED, STATUS_PENDING, Profile


@pytest.fixture(autouse=True)
def cli_auth_env(tmp_path, monkeypatch):
    db_path = tmp_path / "cli_auth.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(engine)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("ALLOWED_EMAILS", "owner@example.com, Legacy@Example.com")
    monkeypatch.setenv("ADMIN_EMAILS", "")
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "fake-anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-service")
    get_settings.cache_clear()
    yield engine
    get_settings.cache_clear()


def _fake_user(email: str, user_id: str) -> dict:
    return {"id": user_id, "email": email}


def test_provision_owner_creates_approved_owner_profile(cli_auth_env):
    engine = cli_auth_env
    owner_id = "11111111-1111-1111-1111-111111111111"
    with patch(
        "culture.web.supabase.admin_get_or_create_user",
        return_value=_fake_user("owner@example.com", owner_id),
    ):
        cli.auth_provision_owner()

    with engine.connect() as conn:
        from sqlalchemy.orm import Session

        with Session(conn) as session:
            profile = session.get(Profile, UUID(owner_id))
            assert profile is not None
            assert profile.role == ROLE_OWNER
            assert profile.status == STATUS_APPROVED
            events = [e.event for e in session.scalars(select(AuthEvent))]
            assert "owner_provisioned" in events


def test_provision_owner_is_idempotent(cli_auth_env):
    engine = cli_auth_env
    owner_id = "11111111-1111-1111-1111-111111111111"
    with patch(
        "culture.web.supabase.admin_get_or_create_user",
        return_value=_fake_user("owner@example.com", owner_id),
    ):
        cli.auth_provision_owner()
        cli.auth_provision_owner()

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        owners = list(session.scalars(select(Profile).where(Profile.role == ROLE_OWNER)))
        assert len(owners) == 1


def test_provision_owner_refuses_a_second_distinct_owner(cli_auth_env):
    engine = cli_auth_env
    with patch(
        "culture.web.supabase.admin_get_or_create_user",
        return_value=_fake_user(
            "owner@example.com", "11111111-1111-1111-1111-111111111111"
        ),
    ):
        cli.auth_provision_owner()

    with (
        patch(
            "culture.web.supabase.admin_get_or_create_user",
            return_value=_fake_user(
                "someone-else@example.com", "22222222-2222-2222-2222-222222222222"
            ),
        ),
        pytest.raises(typer.Exit),
    ):
        cli.auth_provision_owner()

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        owners = list(session.scalars(select(Profile).where(Profile.role == ROLE_OWNER)))
        assert len(owners) == 1
        assert owners[0].email == "owner@example.com"


def test_migrate_allowlist_dry_run_writes_nothing(cli_auth_env):
    engine = cli_auth_env
    with patch("culture.web.supabase.admin_get_or_create_user") as mock_create:
        cli.auth_migrate_allowlist(dry_run=True)
    mock_create.assert_not_called()

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        assert list(session.scalars(select(Profile))) == []


def test_migrate_allowlist_creates_pending_legacy_profiles(cli_auth_env):
    engine = cli_auth_env
    legacy_id = "33333333-3333-3333-3333-333333333333"
    with patch(
        "culture.web.supabase.admin_get_or_create_user",
        return_value=_fake_user("legacy@example.com", legacy_id),
    ):
        cli.auth_migrate_allowlist(dry_run=False)

    from sqlalchemy.orm import Session

    with Session(engine) as session:
        profile = session.get(Profile, UUID(legacy_id))
        assert profile is not None
        assert profile.status == STATUS_PENDING
        assert profile.legacy_magic_link is True
        # OWNER_EMAIL is excluded — provision-owner handles it, not this command.
        assert session.scalar(select(Profile).where(Profile.email == "owner@example.com")) is None
        logs = list(session.scalars(select(EmailLog)))
        assert len(logs) == 1
        assert logs[0].template == "activation_sent"


def test_migrate_allowlist_is_idempotent(cli_auth_env):
    with patch(
        "culture.web.supabase.admin_get_or_create_user",
        return_value=_fake_user(
            "legacy@example.com", "33333333-3333-3333-3333-333333333333"
        ),
    ) as mock_create:
        cli.auth_migrate_allowlist(dry_run=False)
        cli.auth_migrate_allowlist(dry_run=False)

    assert mock_create.call_count == 1


def test_reset_owner_password_sets_it_via_admin_api(cli_auth_env):
    owner_id = "11111111-1111-1111-1111-111111111111"
    with patch(
        "culture.web.supabase.admin_get_or_create_user",
        return_value=_fake_user("owner@example.com", owner_id),
    ):
        cli.auth_provision_owner()

    with (
        patch("getpass.getpass", side_effect=["correct-horse-1", "correct-horse-1"]),
        patch("culture.web.supabase.admin_set_user_password") as mock_set,
    ):
        cli.auth_reset_owner_password()

    mock_set.assert_called_once_with(owner_id, "correct-horse-1", get_settings())
    with Session(cli_auth_env) as session:
        events = [e.event for e in session.scalars(select(AuthEvent))]
        assert "owner_recovery" in events


def test_reset_owner_password_rejects_mismatched_confirmation(cli_auth_env):
    owner_id = "11111111-1111-1111-1111-111111111111"
    with patch(
        "culture.web.supabase.admin_get_or_create_user",
        return_value=_fake_user("owner@example.com", owner_id),
    ):
        cli.auth_provision_owner()

    with (
        patch("getpass.getpass", side_effect=["correct-horse-1", "different-1"]),
        patch("culture.web.supabase.admin_set_user_password") as mock_set,
        pytest.raises(typer.Exit),
    ):
        cli.auth_reset_owner_password()
    mock_set.assert_not_called()


def test_reset_owner_password_without_a_provisioned_owner_fails_cleanly(cli_auth_env):
    with pytest.raises(typer.Exit):
        cli.auth_reset_owner_password()
