"""The /admin workspace: approvals, users, audit, settings.

Reuses the supabase-mode fixtures from test_auth_supabase.py (that's where
the owner/member/pending profile machinery lives) plus one magic-link-mode
check confirming the admin workspace also works under the *current*,
unconverted deployment — ROLE_ADMIN and ROLE_OWNER are treated as the same
top permission tier everywhere access is checked (see auth.OWNER_ROLES), so
the admin workspace doesn't have to wait for the Supabase cutover to be
usable.
"""

from unittest.mock import patch
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session
from test_auth_supabase import (  # noqa: F401  (fixtures)
    MEMBER_ID,
    OWNER_ID,
    _insert_profile,
    _login_as,
    client,
    supabase_auth_env,
)

from culture.config import get_settings
from culture.models.app_settings import AppSettings
from culture.models.auth_event import AuthEvent
from culture.models.email_log import EmailLog
from culture.models.profile import ROLE_MEMBER, ROLE_OWNER, Profile
from culture.web.auth import create_csrf_token


def _login_owner(client_):
    _insert_profile(client_.app_engine, OWNER_ID, "owner@example.com", "approved", ROLE_OWNER)
    _login_as(client_, OWNER_ID, "owner@example.com")
    return create_csrf_token_for(client_)


def create_csrf_token_for(client_) -> str:
    # A CSRF token is bound to the caller's current session cookie value, so
    # it must be minted after _login_as has set that cookie.
    from starlette.requests import Request

    scope = {
        "type": "http",
        "headers": [(b"cookie", f"ci_session={client_.cookies.get('ci_session')}".encode())],
    }
    return create_csrf_token(Request(scope), get_settings())


def _events(engine) -> list[str]:
    with Session(engine) as session:
        return [e.event for e in session.scalars(select(AuthEvent))]


# --- access control -----------------------------------------------------


def test_anonymous_is_redirected_from_admin(client):  # noqa: F811
    response = client.get("/admin/approvals")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_pending_profile_is_redirected_to_pending_not_admin(client):  # noqa: F811
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", "pending")
    _login_as(client, MEMBER_ID, "member@example.com")
    response = client.get("/admin/approvals")
    assert response.status_code == 303
    assert response.headers["location"] == "/pending"


def test_approved_member_is_denied_admin(client):  # noqa: F811
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", "approved")
    _login_as(client, MEMBER_ID, "member@example.com")
    for path in ("/admin", "/admin/approvals", "/admin/users", "/admin/audit", "/admin/settings"):
        response = client.get(path)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/dashboard", path
    assert "access_denied" in _events(client.app_engine)


def test_owner_reaches_every_admin_tab(client):  # noqa: F811
    _login_owner(client)
    for path in (
        "/admin/approvals", "/admin/users", "/admin/audit", "/admin/settings",
    ):
        assert client.get(path).status_code == 200, path


def test_member_rendered_dashboard_has_no_admin_link(client):  # noqa: F811
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", "approved")
    _login_as(client, MEMBER_ID, "member@example.com")
    page = client.get("/dashboard").text
    assert '/admin' not in page


def test_owner_rendered_dashboard_has_admin_link(client):  # noqa: F811
    _login_owner(client)
    page = client.get("/dashboard").text
    assert 'href="/admin"' in page


# --- approvals ------------------------------------------------------------


def test_approve_grants_member_access(client):  # noqa: F811
    csrf = _login_owner(client)
    _insert_profile(client.app_engine, MEMBER_ID, "new@example.com", "pending")

    with patch("culture.web.supabase.admin_sign_out_user"):
        response = client.post(
            f"/admin/approvals/{MEMBER_ID}/approve", data={"csrf_token": csrf}
        )
    assert response.status_code == 303

    with Session(client.app_engine) as session:
        profile = session.get(Profile, UUID(MEMBER_ID))
        assert profile.status == "approved"
        assert profile.role == ROLE_MEMBER
        assert profile.approved_by == "owner@example.com"
    assert "account_approved" in _events(client.app_engine)
    with Session(client.app_engine) as session:
        logs = list(session.scalars(select(EmailLog)))
        assert any(log.template == "account_approved" for log in logs)


def test_approve_refuses_unverified_email(client):  # noqa: F811
    csrf = _login_owner(client)
    with Session(client.app_engine) as session:
        session.add(
            Profile(id=UUID(MEMBER_ID), email="new@example.com", status="pending")
        )
        session.commit()
    response = client.post(f"/admin/approvals/{MEMBER_ID}/approve", data={"csrf_token": csrf})
    assert response.status_code == 409
    with Session(client.app_engine) as session:
        assert session.get(Profile, UUID(MEMBER_ID)).status == "pending"


def test_double_approve_is_rejected(client):  # noqa: F811
    csrf = _login_owner(client)
    _insert_profile(client.app_engine, MEMBER_ID, "new@example.com", "pending")
    with patch("culture.web.supabase.admin_sign_out_user"):
        client.post(f"/admin/approvals/{MEMBER_ID}/approve", data={"csrf_token": csrf})
        second = client.post(f"/admin/approvals/{MEMBER_ID}/approve", data={"csrf_token": csrf})
    assert second.status_code == 409


def test_reject_records_actor_and_audit(client):  # noqa: F811
    csrf = _login_owner(client)
    _insert_profile(client.app_engine, MEMBER_ID, "new@example.com", "pending")
    client.post(f"/admin/approvals/{MEMBER_ID}/reject", data={"csrf_token": csrf})
    with Session(client.app_engine) as session:
        profile = session.get(Profile, UUID(MEMBER_ID))
        assert profile.status == "rejected"
        assert profile.rejected_by == "owner@example.com"


def test_approval_action_without_valid_csrf_is_rejected(client):  # noqa: F811
    _login_owner(client)
    _insert_profile(client.app_engine, MEMBER_ID, "new@example.com", "pending")
    response = client.post(
        f"/admin/approvals/{MEMBER_ID}/approve", data={"csrf_token": "forged"}
    )
    assert response.status_code == 403
    with Session(client.app_engine) as session:
        assert session.get(Profile, UUID(MEMBER_ID)).status == "pending"


# --- users ------------------------------------------------------------


def test_suspend_revokes_the_supabase_session(client):  # noqa: F811
    csrf = _login_owner(client)
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", "approved")
    with patch("culture.web.supabase.admin_sign_out_user") as mock_signout:
        response = client.post(
            f"/admin/users/{MEMBER_ID}/suspend", data={"csrf_token": csrf}
        )
    assert response.status_code == 303
    mock_signout.assert_called_once_with(MEMBER_ID, get_settings())
    with Session(client.app_engine) as session:
        assert session.get(Profile, UUID(MEMBER_ID)).status == "suspended"


def test_reactivate_restores_approved_status(client):  # noqa: F811
    csrf = _login_owner(client)
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", "suspended")
    client.post(f"/admin/users/{MEMBER_ID}/reactivate", data={"csrf_token": csrf})
    with Session(client.app_engine) as session:
        assert session.get(Profile, UUID(MEMBER_ID)).status == "approved"


def test_owner_row_cannot_be_modified_from_users_screen(client):  # noqa: F811
    csrf = _login_owner(client)
    response = client.post(f"/admin/users/{OWNER_ID}/suspend", data={"csrf_token": csrf})
    assert response.status_code == 403
    with Session(client.app_engine) as session:
        assert session.get(Profile, UUID(OWNER_ID)).status == "approved"


# --- settings ------------------------------------------------------------


def test_settings_update_persists_and_audits(client):  # noqa: F811
    csrf = _login_owner(client)
    response = client.post(
        "/admin/settings",
        data={
            "csrf_token": csrf, "invite_only": "on",
            "allowed_domains": "example.com", "allowlist_emails": "vip@example.com",
        },
    )
    assert response.status_code == 303
    with Session(client.app_engine) as session:
        row = session.get(AppSettings, 1)
        assert row.invite_only is True
        assert row.registration_open is False  # checkbox omitted == unchecked
        assert row.allowed_domains == "example.com"
        assert row.updated_by == "owner@example.com"
    assert "settings_changed" in _events(client.app_engine)


# --- magic-link mode: the admin workspace works before any cutover -------


def test_admin_reaches_admin_workspace_under_magiclink_mode(monkeypatch):
    # Explicit, not delenv: pydantic-settings reads .env directly regardless
    # of os.environ, so deleting the OS var doesn't stop a real value in a
    # developer's local .env from winning. Setting it wins over .env.
    monkeypatch.setenv("AUTH_MODE", "magiclink")
    get_settings.cache_clear()
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from culture.database import Base
    from culture.web.app import create_app
    from culture.web.auth import create_session_value

    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ALLOWED_EMAILS", "boss@example.com")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "fake")
    get_settings.cache_clear()

    from fastapi.testclient import TestClient

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    app = create_app(engine=engine, require_auth=True)
    magic_client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    magic_client.cookies.set("ci_session", create_session_value("boss@example.com", get_settings()))

    response = magic_client.get("/admin")
    assert response.status_code == 303
    assert response.headers["location"] == "/admin/approvals"
    get_settings.cache_clear()


# --- sources (Phase 6) ----------------------------------------------------


def _sources(engine):
    from culture.models.source import Source

    with Session(engine) as session:
        return list(session.scalars(select(Source)))


def test_owner_creates_an_instagram_source(client):  # noqa: F811
    csrf = _login_owner(client)
    response = client.post(
        "/admin/sources/new",
        data={
            "csrf_token": csrf, "name": "Desfile Diario", "platform": "instagram",
            "external_identifier": "@DesfileDiario", "cadence": "daily",
        },
    )
    assert response.status_code == 303
    sources = _sources(client.app_engine)
    assert len(sources) == 1
    assert sources[0].normalized_identifier == "instagram:desfilediario"
    assert "source_created" in _events(client.app_engine)


def test_instagram_source_requires_a_handle_or_url(client):  # noqa: F811
    csrf = _login_owner(client)
    response = client.post(
        "/admin/sources/new",
        data={"csrf_token": csrf, "name": "No Handle", "platform": "instagram"},
    )
    assert response.status_code == 200
    assert "required" in response.text.lower()
    assert _sources(client.app_engine) == []


def test_web_source_requires_a_feed_url(client):  # noqa: F811
    csrf = _login_owner(client)
    response = client.post(
        "/admin/sources/new",
        data={"csrf_token": csrf, "name": "No Feed", "platform": "web"},
    )
    assert response.status_code == 200
    assert "feed url is required" in response.text.lower()


def test_duplicate_name_is_rejected(client):  # noqa: F811
    csrf = _login_owner(client)
    payload = {
        "csrf_token": csrf, "name": "Dup Source", "platform": "web",
        "feed_url": "https://a.example.com/feed",
    }
    client.post("/admin/sources/new", data=payload)
    second = client.post("/admin/sources/new", data={**payload, "feed_url": "https://b.example.com/feed"})
    assert second.status_code == 200
    assert "already exists" in second.text.lower()
    assert len(_sources(client.app_engine)) == 1


def test_normalized_identifier_collision_is_rejected_even_with_different_names(client):  # noqa: F811
    csrf = _login_owner(client)
    client.post(
        "/admin/sources/new",
        data={
            "csrf_token": csrf, "name": "First Name", "platform": "instagram",
            "external_identifier": "@samehandle",
        },
    )
    second = client.post(
        "/admin/sources/new",
        data={
            "csrf_token": csrf, "name": "Second Name", "platform": "instagram",
            "url": "https://instagram.com/SameHandle/",
        },
    )
    assert second.status_code == 200
    assert "already exists" in second.text.lower()
    assert len(_sources(client.app_engine)) == 1


def test_member_gets_403_on_every_source_mutation(client):  # noqa: F811
    csrf_owner = _login_owner(client)
    client.post(
        "/admin/sources/new",
        data={
            "csrf_token": csrf_owner, "name": "Target", "platform": "web",
            "feed_url": "https://a.example.com/feed",
        },
    )
    source_id = _sources(client.app_engine)[0].id
    client.cookies.clear()

    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", "approved")
    _login_as(client, MEMBER_ID, "member@example.com")
    for path, data in [
        ("/admin/sources/new", {"name": "x", "platform": "web", "feed_url": "y"}),
        (f"/admin/sources/{source_id}/edit", {"name": "x", "platform": "web"}),
        (f"/admin/sources/{source_id}/enable", {}),
        (f"/admin/sources/{source_id}/disable", {}),
        (f"/admin/sources/{source_id}/archive", {}),
        (f"/admin/sources/{source_id}/collect", {}),
    ]:
        response = client.post(path, data={**data, "csrf_token": "irrelevant"})
        assert response.status_code == 303, path
        assert response.headers["location"] == "/dashboard", path
    assert client.get("/admin/sources").status_code == 303


def test_edit_updates_fields_and_audits(client):  # noqa: F811
    csrf = _login_owner(client)
    client.post(
        "/admin/sources/new",
        data={
            "csrf_token": csrf, "name": "Original", "platform": "web",
            "feed_url": "https://a.example.com/feed",
        },
    )
    source_id = _sources(client.app_engine)[0].id
    response = client.post(
        f"/admin/sources/{source_id}/edit",
        data={
            "csrf_token": csrf, "name": "Renamed", "platform": "web",
            "feed_url": "https://a.example.com/feed", "cadence": "hourly",
        },
    )
    assert response.status_code == 303
    sources = _sources(client.app_engine)
    assert sources[0].name == "Renamed"
    assert sources[0].cadence == "hourly"
    assert "source_updated" in _events(client.app_engine)


def test_enable_disable_toggle_active_flag(client):  # noqa: F811
    csrf = _login_owner(client)
    client.post(
        "/admin/sources/new",
        data={
            "csrf_token": csrf, "name": "Togglable", "platform": "web",
            "feed_url": "https://a.example.com/feed",
        },
    )
    source_id = _sources(client.app_engine)[0].id
    client.post(f"/admin/sources/{source_id}/disable", data={"csrf_token": csrf})
    assert _sources(client.app_engine)[0].active is False
    client.post(f"/admin/sources/{source_id}/enable", data={"csrf_token": csrf})
    assert _sources(client.app_engine)[0].active is True
    events = _events(client.app_engine)
    assert "source_disabled" in events and "source_enabled" in events


def test_archive_disables_and_hides_from_the_active_list_but_never_deletes(client):  # noqa: F811
    csrf = _login_owner(client)
    client.post(
        "/admin/sources/new",
        data={
            "csrf_token": csrf, "name": "Archivable", "platform": "web",
            "feed_url": "https://a.example.com/feed",
        },
    )
    source_id = _sources(client.app_engine)[0].id
    response = client.post(f"/admin/sources/{source_id}/archive", data={"csrf_token": csrf})
    assert response.status_code == 303

    sources = _sources(client.app_engine)
    assert len(sources) == 1  # archive, not delete
    assert sources[0].archived_at is not None
    assert sources[0].active is False
    assert "source_archived" in _events(client.app_engine)

    active_list = client.get("/admin/sources").text
    assert "Archivable" not in active_list
    archived_list = client.get("/admin/sources?archived=1").text
    assert "Archivable" in archived_list


def test_collect_now_queues_a_request(client):  # noqa: F811
    csrf = _login_owner(client)
    client.post(
        "/admin/sources/new",
        data={
            "csrf_token": csrf, "name": "Collectable", "platform": "web",
            "feed_url": "https://a.example.com/feed",
        },
    )
    source_id = _sources(client.app_engine)[0].id
    response = client.post(f"/admin/sources/{source_id}/collect", data={"csrf_token": csrf})
    assert response.status_code == 303
    assert "collection_triggered" in _events(client.app_engine)

    from culture.models.collection_request import CollectionRequest

    with Session(client.app_engine) as session:
        requests_ = list(session.scalars(select(CollectionRequest)))
        assert len(requests_) == 1
        assert requests_[0].fulfilled_at is None


def test_cli_ingest_queued_drains_and_stamps_fulfilled(monkeypatch, tmp_path):
    from unittest.mock import MagicMock

    from sqlalchemy import create_engine

    from culture import cli
    from culture.database import Base as _Base
    from culture.models.collection_request import CollectionRequest
    from culture.models.source import Source

    db_path = tmp_path / "cli_drain.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    _Base.metadata.create_all(engine)
    with Session(engine) as session:
        source = Source(name="Collectable", platform="web")
        session.add(source)
        session.flush()
        session.add(CollectionRequest(source_id=source.id))
        session.commit()

    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    get_settings.cache_clear()

    fake_service = MagicMock()
    with patch("culture.services.ingestion.IngestionService", return_value=fake_service):
        cli._ingest_queued()

    with Session(engine) as session:
        drained = list(session.scalars(select(CollectionRequest)))
        assert drained[0].fulfilled_at is not None
    fake_service.ingest.assert_called_once_with(source_name="Collectable")
    get_settings.cache_clear()


def test_cli_ingest_queued_is_a_noop_with_nothing_queued(monkeypatch, tmp_path, capsys):
    from culture import cli
    from culture.database import Base as _Base

    db_path = tmp_path / "cli_drain_empty.db"
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    _Base.metadata.create_all(engine)
    monkeypatch.setenv("DATABASE_URL", f"sqlite+pysqlite:///{db_path}")
    get_settings.cache_clear()

    cli._ingest_queued()
    assert "No queued collection requests" in capsys.readouterr().out
    get_settings.cache_clear()


def test_admin_source_templates_never_reference_env_secrets():
    """Static guard: the admin source form/list templates must never grow a
    field bound to an API key or other env secret (config.py's own
    Settings), even indirectly — Source rows never carry credentials."""
    from pathlib import Path

    templates_dir = Path(__file__).resolve().parents[2] / "src" / "culture" / "web" / "templates"
    forbidden = ["apify_token", "anthropic_api_key", "openai_api_key", "supabase_service_role_key"]
    for name in ("admin_sources.html", "admin_source_form.html"):
        text = (templates_dir / name).read_text(encoding="utf-8").lower()
        for term in forbidden:
            assert term not in text, f"{name} references {term}"
