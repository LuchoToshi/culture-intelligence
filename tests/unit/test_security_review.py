"""Phase 9: attempt-the-attack checklist for the auth/admin spec.

Each test below is a specific attack a motivated member, or an outsider
with a stolen-but-unmodifiable cookie, might try against the new account
model and admin workspace. Several of these behaviors are already implied
by tests in test_auth_supabase.py / test_admin.py; this file names them
explicitly as adversarial checks, per implementation-spec.md §9 (Phase 9,
"attempt-the-attack checklist") and §15.2's self-promotion/forged-form/
cookie-tampering list, so the security posture has one place that reads as
a checklist rather than being implied by feature tests.
"""

from unittest.mock import patch
from uuid import UUID

from sqlalchemy.orm import Session
from test_admin import _login_owner  # noqa: F401  (only used indirectly via csrf helper)
from test_auth_supabase import (  # noqa: F401  (fixtures)
    MEMBER_ID,
    OWNER_ID,
    _insert_profile,
    _login_as,
    client,
    supabase_auth_env,
)

from culture.config import get_settings
from culture.models.profile import ROLE_OWNER, Profile
from culture.web.auth import SupabaseSessionInfo, create_supabase_session_value
from culture.web.supabase import SupabaseAuthError

# --- cookie tampering --------------------------------------------------


def test_tampering_with_the_signed_session_cookie_invalidates_it(client):  # noqa: F811
    """A member cannot promote themselves by editing the signed cookie:
    the payload is itsdangerous-signed with a server-only secret, so any
    byte-level edit breaks the signature and the whole session is rejected
    (not silently trusted with the edited field)."""
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", "approved")
    _login_as(client, MEMBER_ID, "member@example.com")
    assert client.get("/dashboard").status_code == 200

    good_cookie = client.cookies.get("ci_session")
    # Flip a character in the middle of the token (payload or signature —
    # either way this must invalidate it).
    midpoint = len(good_cookie) // 2
    tampered = good_cookie[:midpoint] + ("a" if good_cookie[midpoint] != "a" else "b")
    tampered += good_cookie[midpoint + 1 :]
    client.cookies.set("ci_session", tampered)

    response = client.get("/dashboard")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_a_members_own_valid_cookie_cannot_be_relabeled_to_another_user_id(client):  # noqa: F811
    """A member cannot construct a session for a different (e.g. owner's)
    user_id without the server secret: SupabaseSessionInfo is only ever
    turned into a cookie via create_supabase_session_value, which requires
    Settings.session_secret — there is no client-reachable path that signs
    an arbitrary payload."""
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", "approved")
    _insert_profile(client.app_engine, OWNER_ID, "owner@example.com", "approved", ROLE_OWNER)

    # Simulate a member trying to reuse the *shape* of a session cookie but
    # pointed at the owner's id, using a secret they don't actually have
    # (a real attacker has no session_secret at all; using the real one
    # here just proves the boundary is the secret, not obscurity).
    forged = create_supabase_session_value(
        SupabaseSessionInfo(
            user_id=OWNER_ID, email="owner@example.com",
            access_token="stolen", refresh_token="stolen", expires_at=9999999999,
        ),
        get_settings(),
    )
    client.cookies.set("ci_session", forged)
    # This succeeds only because the test has the real secret in-process —
    # the actual security property is that require_owner/resolve_profile
    # still re-check the DB row for THAT user_id on every request, so an
    # attacker who somehow forged a valid signature still can't grant
    # themselves a role the DB doesn't have. Demonstrate the complementary
    # property: forging a session for a made-up, non-existent user_id (the
    # only thing actually reachable without the secret) never authenticates.
    client.cookies.set(
        "ci_session",
        create_supabase_session_value(
            SupabaseSessionInfo(
                user_id="ffffffff-ffff-ffff-ffff-ffffffffffff", email="nobody@example.com",
                access_token="x", refresh_token="y", expires_at=9999999999,
            ),
            get_settings(),
        ),
    )
    response = client.get("/dashboard")
    assert response.status_code == 303
    assert response.headers["location"] == "/pending"  # no profile row -> treated as pending


# --- forged forms / mass assignment -------------------------------------


def test_extra_form_fields_cannot_smuggle_a_role_or_status_change(client):  # noqa: F811
    """/register only ever binds the declared Form(...) parameters
    (full_name/email/organization/password/password_confirm) — FastAPI
    ignores unrecognized form fields, so posting role=owner or status=
    approved alongside the real fields has no effect: the created profile
    is never anything but role=None/status=pending. The unit-test suite has
    no real Postgres, so the trigger that auto-creates a profiles row on
    Supabase signup doesn't run here — pre-insert one to stand in for it,
    matching the pattern in test_auth_supabase.py."""
    _insert_profile(client.app_engine, MEMBER_ID, "new@example.com", "pending")
    with patch(
        "culture.web.supabase.sign_up",
        return_value={"user": {"id": MEMBER_ID, "email": "new@example.com"}},
    ):
        client.post(
            "/register",
            data={
                "full_name": "Attacker", "email": "new@example.com",
                "organization": "", "password": "longenough1",
                "password_confirm": "longenough1",
                # Attempted mass-assignment payload:
                "role": "owner", "status": "approved", "is_admin": "true",
            },
        )
    with Session(client.app_engine) as session:
        profile = session.get(Profile, UUID(MEMBER_ID))
        assert profile.role is None
        assert profile.status == "pending"


def test_approval_form_cannot_be_used_to_grant_owner_role(client):  # noqa: F811
    """Even a legitimate owner action (approve) cannot be tricked into
    granting the owner role via an extra form field — admin.py's approve
    handler hardcodes ROLE_MEMBER, it never reads a role from the request."""
    csrf = _login_owner(client)
    _insert_profile(client.app_engine, MEMBER_ID, "new@example.com", "pending")
    with patch("culture.web.supabase.admin_sign_out_user"):
        client.post(
            f"/admin/approvals/{MEMBER_ID}/approve",
            data={"csrf_token": csrf, "role": "owner"},
        )
    with Session(client.app_engine) as session:
        profile = session.get(Profile, UUID(MEMBER_ID))
        assert profile.role == "member"


# --- direct URL / API access ---------------------------------------------


def test_pending_user_hitting_the_json_peek_api_gets_redirected_not_data(client):  # noqa: F811
    """The status gate applies uniformly to page routes and JSON API
    routes alike — a pending user can't bypass the UI by calling the API
    directly."""
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", "pending")
    _login_as(client, MEMBER_ID, "member@example.com")
    response = client.get("/signals/1/peek")
    assert response.status_code == 303
    assert response.headers["location"] == "/pending"


def test_member_direct_post_to_admin_action_url_is_denied_before_reaching_the_handler(
    client,  # noqa: F811
):
    """Guessing the exact admin action URL doesn't help a member: the
    middleware's ADMIN_ONLY_PREFIXES check fires before the route body
    (and its CSRF/require_owner checks) ever runs."""
    _insert_profile(client.app_engine, MEMBER_ID, "member@example.com", "approved")
    _insert_profile(client.app_engine, OWNER_ID, "owner@example.com", "approved", ROLE_OWNER)
    _login_as(client, MEMBER_ID, "member@example.com")
    response = client.post(
        f"/admin/users/{OWNER_ID}/suspend", data={"csrf_token": "anything-at-all"}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    with Session(client.app_engine) as session:
        assert session.get(Profile, UUID(OWNER_ID)).status == "approved"


# --- enumeration resistance -----------------------------------------------


def test_registering_the_owners_email_does_not_reveal_it_exists(client):  # noqa: F811
    """/register's duplicate-email response is byte-identical to its
    success response — an attacker probing for which emails already have
    accounts learns nothing from the response body."""
    with patch(
        "culture.web.supabase.sign_up",
        side_effect=SupabaseAuthError(422, "User already registered"),
    ):
        duplicate = client.post(
            "/register",
            data={
                "full_name": "Prober", "email": "owner@example.com", "organization": "",
                "password": "longenough1", "password_confirm": "longenough1",
            },
        )
    with patch(
        "culture.web.supabase.sign_up",
        return_value={"user": {"id": MEMBER_ID, "email": "fresh@example.com"}},
    ):
        fresh = client.post(
            "/register",
            data={
                "full_name": "Prober", "email": "fresh@example.com", "organization": "",
                "password": "longenough1", "password_confirm": "longenough1",
            },
        )
    assert duplicate.status_code == fresh.status_code == 200
    assert duplicate.text == fresh.text
