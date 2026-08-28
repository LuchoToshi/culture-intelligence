"""Thin httpx wrapper over Supabase Auth (GoTrue)'s REST API.

No supabase-js, no client SDK. Every call site here mirrors
`culture.web.auth.send_login_email`'s style on purpose: a small function
taking `Settings` explicitly, one local import, one HTTP call, so tests can
`patch("culture.web.supabase.<name>")` exactly the way `send_login_email` is
patched today — no separate fake/stub module needed anywhere in this codebase.

The anon key authenticates end-user calls (signup, sign-in, refresh,
recovery, OTP verification). The service-role key is for admin-only
operations (creating/finding a user directly, revoking sessions) and is
never sent to a template, logged, or reachable from a non-owner code path.

Endpoint paths follow GoTrue's documented REST surface as of this writing.
GoTrue admin endpoints in particular have shifted across versions —
`admin_sign_out_user`'s path should be confirmed against the actual deployed
project during Phase 7 setup, before any production cutover.
"""

from __future__ import annotations

import contextlib
from typing import Any

import httpx

from culture.config import Settings


class SupabaseAuthError(Exception):
    """A GoTrue call returned a non-2xx response."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Supabase auth error ({status_code}): {detail}")


def _base_url(settings: Settings) -> str:
    return settings.supabase_url.rstrip("/") + "/auth/v1"


def _anon_headers(settings: Settings) -> dict[str, str]:
    return {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {settings.supabase_anon_key}",
        "Content-Type": "application/json",
    }


def _service_headers(settings: Settings) -> dict[str, str]:
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
        "Content-Type": "application/json",
    }


def _request(method: str, url: str, headers: dict[str, str], json: dict[str, Any]) -> dict:
    response = httpx.request(method, url, headers=headers, json=json, timeout=10.0)
    if response.status_code >= 400:
        detail = response.text
        with contextlib.suppress(ValueError):
            detail = response.json().get("msg") or response.json().get("error_description")
        raise SupabaseAuthError(response.status_code, detail or response.text)
    return response.json() if response.content else {}


def sign_up(email: str, password: str, redirect_to: str, settings: Settings) -> dict:
    """Create the Supabase Auth identity. A DB trigger creates the matching
    `profiles` row (status='pending') — see the profiles table migration."""
    return _request(
        "POST",
        f"{_base_url(settings)}/signup",
        _anon_headers(settings),
        {"email": email, "password": password, "options": {"email_redirect_to": redirect_to}},
    )


def sign_in_with_password(email: str, password: str, settings: Settings) -> dict:
    """Returns {access_token, refresh_token, expires_in, user, ...} on
    success. Raises SupabaseAuthError(400, ...) on bad credentials — callers
    must turn that into the generic "Email or password is incorrect."
    message (§11.3), never surface GoTrue's own wording."""
    return _request(
        "POST",
        f"{_base_url(settings)}/token?grant_type=password",
        _anon_headers(settings),
        {"email": email, "password": password},
    )


def refresh_session(refresh_token: str, settings: Settings) -> dict:
    return _request(
        "POST",
        f"{_base_url(settings)}/token?grant_type=refresh_token",
        _anon_headers(settings),
        {"refresh_token": refresh_token},
    )


def request_password_recovery(email: str, redirect_to: str, settings: Settings) -> None:
    """Always returns None on success; callers must show the same generic
    response whether or not the email exists (§11.3 enumeration resistance)."""
    _request(
        "POST",
        f"{_base_url(settings)}/recover",
        _anon_headers(settings),
        {"email": email, "options": {"redirect_to": redirect_to}},
    )


def verify_otp(token_hash: str, otp_type: str, settings: Settings) -> dict:
    """Exchanges a signup-confirmation or recovery token_hash (from the
    email link) for a session. `otp_type` is "signup" for /auth/confirm or
    "recovery" for /reset/confirm."""
    return _request(
        "POST",
        f"{_base_url(settings)}/verify",
        _anon_headers(settings),
        {"type": otp_type, "token_hash": token_hash},
    )


def update_user_password(access_token: str, new_password: str, settings: Settings) -> dict:
    """Sets a new password on the session established by verify_otp's
    recovery flow. Uses the recovery session's own access token, not the
    service-role key — this is the user acting on their own account, not an
    admin action."""
    headers = {
        "apikey": settings.supabase_anon_key,
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }
    return _request("PUT", f"{_base_url(settings)}/user", headers, {"password": new_password})


def admin_set_user_password(user_id: str, new_password: str, settings: Settings) -> dict:
    """Sets a user's password directly via the admin API — the documented
    owner-recovery path (§5 of the auth spec) for when the normal
    email-based reset isn't reachable at all (e.g. Supabase's default
    mailer locks email-template customization behind custom SMTP being
    configured, so no reset link can be sent yet). Server-only: uses the
    service-role key, never the anon key. Never called from a web route —
    only from `culture auth reset-owner-password`, which prompts for the
    new password locally rather than accepting it as an argument."""
    return _request(
        "PUT",
        f"{_base_url(settings)}/admin/users/{user_id}",
        _service_headers(settings),
        {"password": new_password},
    )


def admin_sign_out_user(user_id: str, settings: Settings) -> None:
    """Revokes every refresh token for the user (suspend/disable/demote).
    Server-only: uses the service-role key, never reachable from a member
    session. Confirm this path against the deployed GoTrue version before
    the production cutover — admin session-revocation endpoints have moved
    across GoTrue releases."""
    _request(
        "POST",
        f"{_base_url(settings)}/admin/users/{user_id}/logout",
        _service_headers(settings),
        {},
    )


def admin_get_or_create_user(email: str, settings: Settings, password: str | None = None) -> dict:
    """Used by `culture auth provision-owner` and `migrate-allowlist`: finds
    the Supabase user by email, creating one (unconfirmed if no password is
    given, matching the legacy-migration path in §9) if none exists.
    Idempotent — safe to call on every provision-owner run."""
    import secrets

    search = httpx.get(
        f"{_base_url(settings)}/admin/users",
        headers=_service_headers(settings),
        params={"email": email},
        timeout=10.0,
    )
    if search.status_code < 400:
        users = search.json().get("users", [])
        matches = [u for u in users if u.get("email", "").lower() == email.lower()]
        if matches:
            return matches[0]
    return _request(
        "POST",
        f"{_base_url(settings)}/admin/users",
        _service_headers(settings),
        {
            "email": email,
            "password": password or secrets.token_urlsafe(24),
            "email_confirm": password is not None,
        },
    )
