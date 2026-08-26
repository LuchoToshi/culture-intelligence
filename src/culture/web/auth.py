"""Magic-link, invite-only auth for the web viewer.

No user table: identity is just "an email on the allow-list". A login token
proves the holder received the email; a session cookie proves they clicked a
valid, unexpired link. Both are signed, stateless tokens — nothing to store,
nothing to clean up, nothing that can leak from a database.
"""

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from culture.config import Settings, get_settings
from culture.logging import get_logger

log = get_logger("culture.web.auth")

SESSION_COOKIE = "ci_session"
LOGIN_TOKEN_MAX_AGE = 15 * 60  # 15 minutes to click the link
SESSION_MAX_AGE = 30 * 24 * 60 * 60  # 30 days

# Paths reachable without a session. /logout must stay public — it needs to
# clear a stale or already-invalid cookie, not require a valid one first.
# "/" and "/intelligence" are the public marketing homepage and its public
# signal preview — "/" checks its own session cookie to redirect logged-in
# visitors straight to /dashboard rather than showing them the homepage.
# "/brief" is the latest report marked is_public=True (see WeeklyReport) —
# the full private archive stays at /reports, behind login.
PUBLIC_PATHS = {
    "/login",
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
}


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


def create_session_value(email: str, settings: Settings) -> str:
    return _serializer(settings, "session").dumps(email.strip().lower())


def verify_session_value(value: str, settings: Settings) -> str | None:
    try:
        return _serializer(settings, "session").loads(value, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


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
                f'<p>Click to sign in — this link works for 15 minutes:</p>'
                f'<p><a href="{link}">{link}</a></p>'
                f"<p>Didn't request this? Ignore this email.</p>"
            ),
        }
    )
    log.info("login email sent to %s", email)


class AuthMiddleware(BaseHTTPMiddleware):
    """Gates every route except the login flow behind a valid session cookie."""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS or request.url.path.startswith("/static"):
            return await call_next(request)

        settings = get_settings()
        cookie = request.cookies.get(SESSION_COOKIE)
        email = verify_session_value(cookie, settings) if cookie else None
        if email is None:
            next_param = request.url.path
            if request.url.query:
                next_param += f"?{request.url.query}"
            return RedirectResponse(f"/login?next={next_param}", status_code=303)

        request.state.user_email = email
        return await call_next(request)


def set_session_cookie(response: Response, email: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_value(email, settings),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)
