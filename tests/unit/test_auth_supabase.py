"""Supabase-mode auth flows: register, verify, login, reset, status gating.

GoTrue is mocked exactly the way Resend is mocked for the magic-link flow —
`patch("culture.web.supabase.<fn>")` on the thin wrapper functions, never a
real network call. AUTH_MODE=supabase switches which route set create_app
mounts (see app.py's _mount_supabase_auth_routes); the existing
test_auth.py's 21 magic-link tests are untouched and still exercise the
default AUTH_MODE=magiclink path.

Since the unit-test suite runs on in-memory SQLite (Base.metadata.create_all,
no Alembic), the Postgres trigger that normally creates a profiles row on
Supabase signup doesn't exist here — _insert_profile stands in for it.
"""

import time
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from culture.config import get_settings
from culture.database import Base
from culture.models.auth_event import AuthEvent
from culture.models.profile import ROLE_OWNER, Profile
from culture.web.app import create_app
from culture.web.auth import SupabaseSessionInfo, create_supabase_session_value
from culture.web.supabase import SupabaseAuthError

OWNER_ID = "00000000-0000-0000-0000-000000000001"
MEMBER_ID = "00000000-0000-0000-0000-000000000002"


@pytest.fixture(autouse=True)
def supabase_auth_env(monkeypatch):
    from culture.web.auth import _RATE_BUCKETS

    _RATE_BUCKETS.clear()
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("AUTH_MODE", "supabase")
    monkeypatch.setenv("SUPABASE_URL", "https://fake.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "fake-anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "fake-service")
    monkeypatch.setenv("SITE_URL", "https://app.example.com")
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.com")
    monkeypatch.setenv("RESEND_API_KEY", "fake")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    app = create_app(engine=engine, require_auth=True)
    test_client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    test_client.app_engine = engine
    return test_client


def _insert_profile(engine, user_id: str, email: str, status: str, role: str | None = None):
    with Session(engine) as session:
        session.add(
            Profile(
                id=UUID(user_id), email=email, status=status, role=role,
                email_verified_at=datetime.now(UTC),
            )
        )
        session.commit()


def _login_as(client, user_id: str, email: str, expires_in: float = 3600) -> None:
    info = SupabaseSessionInfo(
        user_id=user_id, email=email, access_token="at", refresh_token="rt",
        expires_at=time.time() + expires_in,
    )
    client.cookies.set("ci_session", create_supabase_session_value(info, get_settings()))


def _audit_events(engine) -> list[str]:
    with Session(engine) as session:
        return [e.event for e in session.scalars(select(AuthEvent))]


def _fake_signup_response(user_id: str) -> dict:
    return {"user": {"id": user_id, "email": "new@example.com"}}


# --- registration -----------------------------------------------------------


def test_registration_creates_pending_profile_with_no_access(client):
    with patch(
        "culture.web.supabase.sign_up", return_value=_fake_signup_response(MEMBER_ID)
    ):
        response = client.post(
            "/register",
            data={
                "full_name": "New Person", "email": "new@example.com",
                "organization": "", "password": "longenough1", "password_confirm": "longenough1",
            },
        )
    assert response.status_code == 200
    assert "check your email" in response.text.lower()
    assert "access_requested" in _audit_events(client.app_engine)


def test_registration_backfills_name_when_profile_already_exists(client):
    # Simulates the Postgres trigger having already created the row.
    _insert_profile(client.app_engine, MEMBER_ID, "new@example.com", status="pending")
    with patch(
        "culture.web.supabase.sign_up", return_value=_fake_signup_response(MEMBER_ID)
    ):
        client.post(
            "/register",
            data={
                "full_name": "New Person", "email": "new@example.com",
                "organization": "Acme", "password": "longenough1",
                "password_confirm": "longenough1",
            },
        )
    with Session(client.app_engine) as session:
        profile = session.get(Profile, UUID(MEMBER_ID))
        assert profile.full_name == "New Person"
        assert profile.organization == "Acme"
        assert profile.status == "pending"
        assert profile.role is None


def test_duplicate_registration_gets_generic_response(client):
    with patch(
        "culture.web.supabase.sign_up",
        side_effect=SupabaseAuthError(422, "User already registered"),
    ):
        response = client.post(
            "/register",
            data={
                "full_name": "New Person", "email": "existing@example.com",
                "organization": "", "password": "longenough1", "password_confirm": "longenough1",
            },
        )
    assert response.status_code == 200
    assert "check your email" in response.text.lower()
    assert "signup_duplicate" in _audit_events(client.app_engine)


def test_registration_rejects_mismatched_passwords(client):
    response = client.post(
        "/register",
        data={
            "full_name": "New Person", "email": "new@example.com",
            "organization": "", "password": "longenough1", "password_confirm": "different1",
        },
    )
    assert "match" in response.text.lower() and "new@example.com" in response.text


# --- email verification ------------------------------------------------------


def test_auth_confirm_marks_verified_and_redirects_to_pending(client):
    _insert_profile(client.app_engine, MEMBER_ID, "new@example.com", status="pending")
    with Session(client.app_engine) as session:
        session.get(Profile, UUID(MEMBER_ID)).email_verified_at = None
        session.commit()

    with patch(
        "culture.web.supabase.verify_otp",
        return_value={
            "user": {"id": MEMBER_ID, "email": "new@example.com"},
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
        },
    ):
        response = client.get("/auth/confirm?token_hash=abc&type=signup")

    assert response.status_code == 303
    assert response.headers["location"] == "/pending"
    with Session(client.app_engine) as session:
        assert session.get(Profile, UUID(MEMBER_ID)).email_verified_at is not None
    assert "email_verified" in _audit_events(client.app_engine)


def test_auth_confirm_with_bad_token_is_a_branded_error(client):
    with patch(
        "culture.web.supabase.verify_otp", side_effect=SupabaseAuthError(403, "invalid token")
    ):
        response = client.get("/auth/confirm?token_hash=bad&type=signup")
    assert response.status_code == 400


# --- status gate: pending / rejected / suspended / disabled / approved ------


@pytest.mark.parametrize(
    ("status", "expected_location"),
    [
        ("pending", "/pending"),
        ("rejected", "/denied"),
        ("suspended", "/denied?state=suspended"),
        ("disabled", "/denied?state=disabled"),
    ],
)
def test_non_approved_status_redirects_from_every_protected_route(
    client, status, expected_location
):
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", status=status)
    _login_as(client, MEMBER_ID, "member@example.com")

    for path in ("/dashboard", "/signals", "/signals/1/peek"):
        response = client.get(path)
        assert response.status_code == 303, path
        assert response.headers["location"] == expected_location, path


def test_pending_user_can_still_reach_pending_page(client):
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", status="pending")
    _login_as(client, MEMBER_ID, "member@example.com")
    response = client.get("/pending")
    assert response.status_code == 200


def test_approved_user_visiting_pending_is_sent_to_dashboard(client):
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", status="approved")
    _login_as(client, MEMBER_ID, "member@example.com")
    response = client.get("/pending")
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_approved_member_reaches_dashboard(client):
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", status="approved")
    _login_as(client, MEMBER_ID, "member@example.com")
    response = client.get("/dashboard")
    assert response.status_code == 200


def test_stale_session_dies_immediately_after_suspension(client):
    """The spec's own requirement: status is re-read from the DB on every
    request, so a still-valid cookie loses access the instant the DB row
    changes — no waiting for the access token to expire."""
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", status="approved")
    _login_as(client, MEMBER_ID, "member@example.com")
    assert client.get("/dashboard").status_code == 200

    with Session(client.app_engine) as session:
        session.get(Profile, UUID(MEMBER_ID)).status = "suspended"
        session.commit()

    response = client.get("/dashboard")
    assert response.status_code == 303
    assert response.headers["location"] == "/denied?state=suspended"


def test_member_is_denied_admin_routes_owner_is_not(client):
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", status="approved")
    _login_as(client, MEMBER_ID, "member@example.com")
    assert client.get("/admin").status_code == 303
    assert client.get("/admin").headers["location"] == "/dashboard"

    _insert_profile(
        client.app_engine, OWNER_ID, "owner@example.com", status="approved", role=ROLE_OWNER
    )
    _login_as(client, OWNER_ID, "owner@example.com")
    # No /admin route is mounted yet (Phase 4) — 404, not the member's 303.
    assert client.get("/admin").status_code == 404


# --- sign-in -----------------------------------------------------------------


def test_login_with_valid_credentials_sets_a_session(client):
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", status="approved")
    with patch(
        "culture.web.supabase.sign_in_with_password",
        return_value={
            "user": {"id": MEMBER_ID, "email": "member@example.com"},
            "access_token": "at", "refresh_token": "rt", "expires_in": 3600,
        },
    ):
        response = client.post(
            "/login", data={"email": "member@example.com", "password": "correct-horse"}
        )
    assert response.status_code == 303
    assert "ci_session" in response.cookies
    assert "login_success" in _audit_events(client.app_engine)


def test_login_with_bad_credentials_is_generic(client):
    with patch(
        "culture.web.supabase.sign_in_with_password",
        side_effect=SupabaseAuthError(400, "Invalid login credentials"),
    ):
        response = client.post(
            "/login", data={"email": "member@example.com", "password": "wrong"}
        )
    assert response.status_code == 200
    assert "incorrect" in response.text.lower()
    assert "wrong" not in response.text.lower()
    assert "login_failed" in _audit_events(client.app_engine)


# --- password reset -----------------------------------------------------------


def test_reset_request_gives_identical_response_for_known_and_unknown_email(client):
    with patch("culture.web.supabase.request_password_recovery") as mock_recover:
        known = client.post("/reset", data={"email": "member@example.com"})
    with patch(
        "culture.web.supabase.request_password_recovery",
        side_effect=SupabaseAuthError(400, "not found"),
    ):
        unknown = client.post("/reset", data={"email": "nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.text == unknown.text
    mock_recover.assert_called_once()


def test_reset_confirm_with_invalid_token_is_flagged_invalid(client):
    with patch(
        "culture.web.supabase.verify_otp", side_effect=SupabaseAuthError(403, "expired")
    ):
        response = client.post(
            "/reset/confirm",
            data={
                "token_hash": "stale", "password": "newlongpass1",
                "password_confirm": "newlongpass1",
            },
        )
    assert response.status_code == 200
    assert "invalid or has expired" in response.text.lower()


def test_reset_confirm_success_revokes_other_sessions_and_clears_cookie(client):
    with (
        patch(
            "culture.web.supabase.verify_otp",
            return_value={
                "access_token": "recovery-at",
                "user": {"id": MEMBER_ID, "email": "member@example.com"},
            },
        ),
        patch("culture.web.supabase.update_user_password") as mock_update,
        patch("culture.web.supabase.admin_sign_out_user") as mock_signout,
    ):
        response = client.post(
            "/reset/confirm",
            data={
                "token_hash": "good", "password": "newlongpass1",
                "password_confirm": "newlongpass1",
            },
        )
    assert response.status_code == 200
    assert "password changed" in response.text.lower()
    mock_update.assert_called_once_with("recovery-at", "newlongpass1", get_settings())
    mock_signout.assert_called_once()
    assert "password_reset_completed" in _audit_events(client.app_engine)


# --- session refresh ----------------------------------------------------------


def test_expired_access_token_triggers_silent_refresh(client):
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", status="approved")
    _login_as(client, MEMBER_ID, "member@example.com", expires_in=-10)
    with patch(
        "culture.web.supabase.refresh_session",
        return_value={"access_token": "new-at", "refresh_token": "new-rt", "expires_in": 3600},
    ) as mock_refresh:
        response = client.get("/dashboard")
    assert response.status_code == 200
    mock_refresh.assert_called_once_with("rt", get_settings())
    assert "ci_session" in response.cookies


def test_refresh_failure_sends_user_back_to_login(client):
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", status="approved")
    _login_as(client, MEMBER_ID, "member@example.com", expires_in=-10)
    with patch(
        "culture.web.supabase.refresh_session", side_effect=SupabaseAuthError(401, "revoked")
    ):
        response = client.get("/dashboard")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


# --- logout -------------------------------------------------------------------


def test_logout_revokes_the_supabase_session(client):
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", status="approved")
    _login_as(client, MEMBER_ID, "member@example.com")
    with patch("culture.web.supabase.admin_sign_out_user") as mock_signout:
        response = client.get("/logout")
    assert response.status_code == 303
    mock_signout.assert_called_once_with(MEMBER_ID, get_settings())
    assert "logout" in _audit_events(client.app_engine)


# --- demo stays identical regardless of auth_mode ----------------------------


def test_demo_login_still_works_under_supabase_mode(client, monkeypatch):
    import hashlib

    monkeypatch.setenv("DEMO_EMAIL", "demo@example.com")
    monkeypatch.setenv("DEMO_PASSWORD_SHA256", hashlib.sha256(b"demo-pass").hexdigest())
    get_settings.cache_clear()
    response = client.post(
        "/login/demo", data={"email": "demo@example.com", "password": "demo-pass"}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    follow = client.get("/dashboard")
    assert follow.status_code == 200
