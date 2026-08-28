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
    monkeypatch.delenv("AUTH_MODE", raising=False)
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
