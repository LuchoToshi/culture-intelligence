"""Magic-link, invite-only auth for the web viewer, with roles.

No user table: identity is "an email on the allow-list", and the role is
derived from configuration at login time and carried inside the signed
session payload. A login token proves the holder received the email; a
session cookie proves they clicked a valid, unexpired link.

Roles:
- admin  — everything, including the source registry and operational views.
- member — product views; the source registry and admin surfaces are denied
           at the route, not just hidden in the nav.
- demo   — fixed-credential account for demos: member surfaces minus the
           operational/evidence views, with source identities hidden in
           templates. The whole app is read-only over HTTP (writes happen in
           the pipeline CLI), so demo cannot modify production data by
           construction.

Auth events are audited to the auth_events table best-effort; login attempts
are rate-limited per IP per warm instance (a determined attacker can spread
across instances — the limit exists to stop email-bombing the Resend quota
and casual probing, not to be a WAF).
"""

import hashlib
import hmac
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import HTTPException
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from culture.config import Settings, get_settings
from culture.logging import get_logger

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from culture.models.profile import Profile

log = get_logger("culture.web.auth")

SESSION_COOKIE = "ci_session"
LOGIN_TOKEN_MAX_AGE = 15 * 60  # 15 minutes to click the link
SESSION_MAX_AGE = 30 * 24 * 60 * 60  # 30 days (magic-link mode)
# Supabase-mode sessions carry their own access/refresh token pair, signed
# under a separate itsdangerous salt ("sb-session") so the two payload
# shapes never collide when decoding — see verify_supabase_session_value.
# Shorter absolute lifetime than magic-link's 30 days (spec D6).
SUPABASE_SESSION_MAX_AGE = 14 * 24 * 60 * 60  # 14 days
# Refresh this many seconds before the access token's real expiry, so a
# request never races a token that's about to die mid-flight.
SUPABASE_REFRESH_SKEW = 60

# Paths reachable without a session. /logout must stay public — it needs to
# clear a stale or already-invalid cookie, not require a valid one first.
# "/" and "/intelligence" are the public marketing homepage and its public
# signal preview — "/" checks its own session cookie to redirect logged-in
# visitors straight to /dashboard rather than showing them the homepage.
# "/brief" is the latest report marked is_public=True (see WeeklyReport) —
# the full private archive stays at /reports, behind login.
PUBLIC_PATHS = {
    "/login",
    "/login/demo",
    "/auth/verify",
    "/logout",
    "/",
    "/about",
    "/the-brief",
    "/intelligence",
    "/brief",
    "/methodology",
    "/robots.txt",
    "/sitemap.xml",
    # Supabase-mode routes reachable with no session yet. Harmless to list
    # unconditionally: in magic-link mode these paths simply aren't mounted,
    # so a request to them 404s exactly as before. /pending and /denied are
    # deliberately NOT here — they're reached through the per-request status
    # gate below, which is what makes "only the matching status screen is
    # allowed" enforceable rather than a plain bypass.
    "/register",
    "/auth/confirm",
    "/reset",
    "/reset/confirm",
}

# Where a non-approved profile is sent, keyed by status. /denied carries a
# ?state= so the template can show the right copy without a second lookup.
STATUS_DESTINATION = {
    "pending": "/pending",
    "rejected": "/denied",
    "suspended": "/denied?state=suspended",
    "disabled": "/denied?state=disabled",
}


ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLE_DEMO = "demo"
# The Supabase-backed auth model's top role (culture.models.profile.Profile).
# Treated as synonymous with ROLE_ADMIN everywhere access is checked, so the
# admin workspace (Phase 4+) works identically under either auth mode.
ROLE_OWNER = "owner"
OWNER_ROLES = (ROLE_ADMIN, ROLE_OWNER)

# Route-level enforcement. Hiding a nav link is not access control.
ADMIN_ONLY_PREFIXES = ("/sources", "/admin")
DEMO_BLOCKED_PREFIXES = ("/sources", "/admin", "/stream", "/search", "/items", "/reports")


@dataclass(frozen=True)
class SessionInfo:
    email: str
    role: str


def role_for(email: str, settings: Settings) -> str:
    return ROLE_ADMIN if settings.is_admin(email) else ROLE_MEMBER


def demo_credentials_valid(email: str, password: str, settings: Settings) -> bool:
    if not settings.demo_email or not settings.demo_password_sha256:
        return False
    digest = hashlib.sha256(password.encode()).hexdigest()
    return hmac.compare_digest(
        email.strip().lower(), settings.demo_email.strip().lower()
    ) and hmac.compare_digest(digest, settings.demo_password_sha256.lower())


# --- login rate limiting: per-IP sliding window, per warm instance ---------
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def rate_limited(key: str, limit: int, window_seconds: int) -> bool:
    now = time.monotonic()
    bucket = _RATE_BUCKETS[key]
    while bucket and now - bucket[0] > window_seconds:
        bucket.popleft()
    if len(bucket) >= limit:
        return True
    bucket.append(now)
    return False


# --- audit -----------------------------------------------------------------
def record_event(
    event: str,
    email: str | None = None,
    ip: str | None = None,
    path: str | None = None,
    detail: str | None = None,
    engine=None,
) -> None:
    """Best-effort durable audit write. Never raises: an audit failure must
    not turn into an authentication failure.

    The engine is passed explicitly so the write always lands in the app's
    own database — with settings loading .env, a get_engine() default here
    would make the unit-test suite write audit rows into production."""
    try:
        from culture.database import get_engine, session_scope
        from culture.models.auth_event import AuthEvent

        with session_scope(engine or get_engine()) as session:
            session.add(AuthEvent(event=event, email=email, ip=ip, path=path, detail=detail))
    except Exception:  # noqa: BLE001
        log.error("audit write failed for event=%s", event, exc_info=True)


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


def _serializer(settings: Settings, salt: str) -> URLSafeTimedSerializer:
    if not settings.session_secret:
        raise RuntimeError(
            "SESSION_SECRET is not set. Generate one with: python -c "
            "'import secrets; print(secrets.token_urlsafe(32))' and set it as an env var."
        )
    return URLSafeTimedSerializer(settings.session_secret, salt=salt)


def create_login_token(email: str, settings: Settings) -> str:
    return _serializer(settings, "login").dumps(email.strip().lower())


def verify_login_token(token: str, settings: Settings) -> str | None:
    try:
        return _serializer(settings, "login").loads(token, max_age=LOGIN_TOKEN_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def create_session_value(email: str, settings: Settings, role: str | None = None) -> str:
    payload = {"e": email.strip().lower(), "r": role or role_for(email, settings)}
    return _serializer(settings, "session").dumps(payload)


def verify_session_value(value: str, settings: Settings) -> SessionInfo | None:
    try:
        payload = _serializer(settings, "session").loads(value, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None
    if isinstance(payload, str):  # pre-role session cookies stay valid
        return SessionInfo(email=payload, role=role_for(payload, settings))
    if isinstance(payload, dict) and payload.get("e"):
        return SessionInfo(email=payload["e"], role=payload.get("r") or ROLE_MEMBER)
    return None


@dataclass(frozen=True)
class SupabaseSessionInfo:
    """A Supabase-mode session. Deliberately carries no role/status: the
    spec requires re-reading the profiles row fresh on every request (§4) —
    trusting a cached role/status here would let a suspended account keep
    working until the access token happened to expire."""

    user_id: str
    email: str
    access_token: str
    refresh_token: str
    expires_at: float

    def needs_refresh(self) -> bool:
        return time.time() >= self.expires_at - SUPABASE_REFRESH_SKEW


def create_supabase_session_value(info: SupabaseSessionInfo, settings: Settings) -> str:
    payload = {
        "uid": info.user_id,
        "e": info.email.strip().lower(),
        "at": info.access_token,
        "rt": info.refresh_token,
        "exp": info.expires_at,
    }
    return _serializer(settings, "sb-session").dumps(payload)


def verify_supabase_session_value(value: str, settings: Settings) -> SupabaseSessionInfo | None:
    try:
        payload = _serializer(settings, "sb-session").loads(
            value, max_age=SUPABASE_SESSION_MAX_AGE
        )
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict) or not payload.get("uid"):
        return None
    return SupabaseSessionInfo(
        user_id=payload["uid"],
        email=payload["e"],
        access_token=payload["at"],
        refresh_token=payload["rt"],
        expires_at=payload["exp"],
    )


def set_supabase_session_cookie(
    response: Response, info: SupabaseSessionInfo, settings: Settings
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_supabase_session_value(info, settings),
        max_age=SUPABASE_SESSION_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
    )


def refresh_supabase_session(
    info: SupabaseSessionInfo, settings: Settings
) -> SupabaseSessionInfo | None:
    """Best-effort silent refresh. Returns None on any failure (expired or
    revoked refresh token, network error) — the caller sends the user back
    to /login rather than propagating a raw Supabase error mid-request."""
    try:
        from culture.web import supabase

        data = supabase.refresh_session(info.refresh_token, settings)
        return SupabaseSessionInfo(
            user_id=info.user_id,
            email=info.email,
            access_token=data["access_token"],
            refresh_token=data["refresh_token"],
            expires_at=time.time() + float(data.get("expires_in", 3600)),
        )
    except Exception:  # noqa: BLE001
        log.info("supabase session refresh failed for uid=%s", info.user_id, exc_info=True)
        return None


_STATUS_PAGES = {home.split("?", 1)[0] for home in STATUS_DESTINATION.values()}


def status_destination(status: str, path: str) -> str | None:
    """Where a profile must be redirected from `path`, or None if `path` is
    already correct. Approved profiles are sent to /dashboard if they land
    on a status page meant for someone else (e.g. a stale /pending
    bookmark after approval) and otherwise pass through untouched — the
    normal ADMIN_ONLY_PREFIXES/role check runs separately, after this."""
    if path == "/logout":
        return None
    if status == "approved":
        return "/dashboard" if path in _STATUS_PAGES else None
    home = STATUS_DESTINATION.get(status, "/denied")
    if path == home.split("?", 1)[0]:
        return None
    return home


def resolve_profile(session: "Session", user_id: str) -> "Profile | None":
    """The current profiles row for a Supabase user id, or None. A thin
    wrapper (not a bare `session.get(Profile, user_id)` at call sites) so the
    lazy import matches this module's own convention and callers never need
    to import the model themselves. `user_id` arrives as a plain string
    (from the session cookie payload, JSON-serialized) but `profiles.id` is
    a Uuid column — SQLAlchemy's Uuid bind processor requires an actual
    uuid.UUID instance, not its string form."""
    from uuid import UUID

    from culture.models.profile import Profile

    return session.get(Profile, UUID(user_id))


def require_authenticated(request: Request) -> None:
    """Raise 401 unless the request carries a resolved session. Route
    handlers call this in addition to AuthMiddleware (defense-in-depth,
    matching how /sources already double-checks admin at the route)."""
    if getattr(request.state, "user_email", None) is None:
        raise HTTPException(401, "Sign in required.")


def require_approved(request: Request) -> None:
    """Raise 403 if the session belongs to a profile that isn't approved.
    Magic-link sessions carry no `user_status` (that concept doesn't exist
    in that mode) and are treated as approved."""
    require_authenticated(request)
    status = getattr(request.state, "user_status", None)
    if status is not None and status != "approved":
        raise HTTPException(403, "Your account is not active.")


def require_owner(request: Request) -> None:
    """Raise 403 unless the session's role is the top permission tier
    (ROLE_ADMIN in magic-link mode, ROLE_OWNER in Supabase mode — see
    OWNER_ROLES). Every /admin route calls this explicitly, in addition to
    the middleware's ADMIN_ONLY_PREFIXES check."""
    require_authenticated(request)
    role = getattr(request.state, "user_role", None)
    if role not in OWNER_ROLES:
        record_event(
            "access_denied",
            email=getattr(request.state, "user_email", None),
            ip=client_ip(request),
            path=request.url.path,
            detail=f"role={role}",
            engine=getattr(request.app.state, "engine", None),
        )
        raise HTTPException(403, "You don't have access to this page.")


def create_csrf_token(request: Request, settings: Settings) -> str:
    """A token bound to the caller's own session cookie value, so it can't
    be replayed from a different session. Every state-changing admin route
    is POST-only and checks this in addition to SameSite=Lax cookies."""
    session_value = request.cookies.get(SESSION_COOKIE, "") or "anonymous"
    return _serializer(settings, "csrf").dumps(session_value)


def verify_csrf_token(token: str, request: Request, settings: Settings) -> bool:
    session_value = request.cookies.get(SESSION_COOKIE, "") or "anonymous"
    try:
        bound_value = _serializer(settings, "csrf").loads(token, max_age=SUPABASE_SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return False
    return bound_value == session_value


def require_csrf(token: str, request: Request, settings: Settings) -> None:
    if not verify_csrf_token(token, request, settings):
        raise HTTPException(403, "This form has expired. Reload the page and try again.")


def send_login_email(email: str, token: str, base_url: str, settings: Settings) -> None:
    import resend

    resend.api_key = settings.resend_api_key
    link = f"{base_url.rstrip('/')}/auth/verify?token={token}"
    resend.Emails.send(
        {
            "from": settings.email_from,
            "to": [email],
            "subject": "Sign in to Culture Intelligence",
            "html": (
                f'<p>Click to sign in: this link works for 15 minutes.</p>'
                f'<p><a href="{link}">{link}</a></p>'
                f"<p>Didn't request this? Ignore this email.</p>"
            ),
        }
    )
    log.info("login email sent to %s", email)


def _login_redirect(request: Request) -> RedirectResponse:
    next_param = request.url.path
    if request.url.query:
        next_param += f"?{request.url.query}"
    return RedirectResponse(f"/login?next={next_param}", status_code=303)


class AuthMiddleware(BaseHTTPMiddleware):
    """Gates every route except the login flow behind a valid session cookie.

    Branches on Settings.auth_mode: "magiclink" is today's behavior,
    unchanged (see _dispatch_magiclink); "supabase" additionally re-reads
    the profiles row on every request (§4 of the auth spec) rather than
    trusting anything cached in the session cookie as truth.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/static"):
            return await call_next(request)
        settings = get_settings()
        if settings.auth_mode == "supabase":
            return await self._dispatch_supabase(request, call_next, settings)
        return await self._dispatch_magiclink(request, call_next, settings)

    async def _dispatch_magiclink(self, request: Request, call_next, settings: Settings):
        cookie = request.cookies.get(SESSION_COOKIE)
        info = verify_session_value(cookie, settings) if cookie else None
        if info is None:
            return _login_redirect(request)

        path = request.url.path
        blocked = (
            info.role != ROLE_ADMIN and path.startswith(ADMIN_ONLY_PREFIXES)
        ) or (info.role == ROLE_DEMO and path.startswith(DEMO_BLOCKED_PREFIXES))
        if blocked:
            record_event(
                "access_denied", email=info.email, ip=client_ip(request), path=path,
                detail=f"role={info.role}",
                engine=getattr(request.app.state, "engine", None),
            )
            return RedirectResponse("/dashboard", status_code=303)

        request.state.user_email = info.email
        request.state.user_role = info.role
        return await call_next(request)

    async def _dispatch_supabase(self, request: Request, call_next, settings: Settings):
        cookie = request.cookies.get(SESSION_COOKIE)

        # The demo account never has a Supabase identity or a profiles row —
        # it keeps using the plain magic-link-shaped, differently-salted
        # session payload regardless of auth_mode (D3: kept unchanged).
        demo = verify_session_value(cookie, settings) if cookie else None
        if demo is not None and demo.role == ROLE_DEMO:
            path = request.url.path
            if path.startswith(DEMO_BLOCKED_PREFIXES):
                record_event(
                    "access_denied", email=demo.email, ip=client_ip(request), path=path,
                    detail="role=demo", engine=getattr(request.app.state, "engine", None),
                )
                return RedirectResponse("/dashboard", status_code=303)
            request.state.user_email = demo.email
            request.state.user_role = ROLE_DEMO
            request.state.user_status = "approved"
            return await call_next(request)

        info = verify_supabase_session_value(cookie, settings) if cookie else None
        if info is None:
            return _login_redirect(request)

        refreshed = False
        if info.needs_refresh():
            new_info = refresh_supabase_session(info, settings)
            if new_info is None:
                response = _login_redirect(request)
                response.delete_cookie(SESSION_COOKIE)
                return response
            info = new_info
            refreshed = True

        from culture.database import session_scope

        with session_scope(getattr(request.app.state, "engine", None)) as session:
            profile = resolve_profile(session, info.user_id)
            status = profile.status if profile is not None else "pending"
            is_owner = profile is not None and profile.role == ROLE_OWNER

        path = request.url.path
        destination = status_destination(status, path)
        if destination is not None:
            response = RedirectResponse(destination, status_code=303)
        elif not is_owner and path.startswith(ADMIN_ONLY_PREFIXES):
            record_event(
                "access_denied", email=info.email, ip=client_ip(request), path=path,
                detail="role=member", engine=getattr(request.app.state, "engine", None),
            )
            response = RedirectResponse("/dashboard", status_code=303)
        else:
            request.state.user_email = info.email
            request.state.user_role = ROLE_OWNER if is_owner else ROLE_MEMBER
            request.state.user_status = status
            response = await call_next(request)

        if refreshed:
            set_supabase_session_cookie(response, info, settings)
        return response


def set_session_cookie(
    response: Response, email: str, settings: Settings, role: str | None = None
) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_value(email, settings, role=role),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)
