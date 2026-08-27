from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool

from culture.config import get_settings
from culture.database import Base
from culture.web.app import create_app
from culture.web.auth import create_login_token, verify_login_token, verify_session_value


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    # The login rate limiter is module-global state; isolate every test.
    from culture.web.auth import _RATE_BUCKETS

    _RATE_BUCKETS.clear()
    monkeypatch.setenv("SESSION_SECRET", "test-secret")
    monkeypatch.setenv("ALLOWED_EMAILS", "allowed@example.com, Also@Example.com")
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
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def test_unauthenticated_request_redirects_to_login(client):
    response = client.get("/signals")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/signals"


def test_login_and_verify_flow_grants_session(client):
    with patch("culture.web.auth.send_login_email") as mock_send:
        submit = client.post("/login", data={"email": "allowed@example.com", "next": "/signals"})
    assert submit.status_code == 200
    assert "allowed@example.com" in submit.text
    token = mock_send.call_args.args[1]

    verify = client.get(f"/auth/verify?token={token}&next=/signals")
    assert verify.status_code == 303
    assert verify.headers["location"] == "/signals"
    assert "ci_session" in verify.cookies

    now_authenticated = client.get("/signals")
    assert now_authenticated.status_code == 200


# The original anti-enumeration contract (identical response for unknown
# emails) was deliberately reversed by the 26 Aug access-control ruling:
# unknown addresses now get an explicit rejection. That behavior is covered
# by test_unknown_email_is_rejected_explicitly_and_no_link_is_sent below.


def test_email_allow_list_is_case_insensitive(client):
    with patch("culture.web.auth.send_login_email") as mock_send:
        client.post("/login", data={"email": "ALSO@EXAMPLE.COM", "next": "/"})
    mock_send.assert_called_once()


def test_expired_or_forged_token_rejected(client):
    response = client.get("/auth/verify?token=not-a-real-token")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=expired"


def test_logout_clears_session(client):
    with patch("culture.web.auth.send_login_email") as mock_send:
        client.post("/login", data={"email": "allowed@example.com", "next": "/"})
    token = mock_send.call_args.args[1]
    client.get(f"/auth/verify?token={token}")

    logout = client.get("/logout")
    assert logout.status_code == 303
    assert logout.headers["location"] == "/login"

    after_logout = client.get("/signals")
    assert after_logout.status_code == 303


def test_token_and_session_are_independently_scoped(auth_env):
    login_token = create_login_token("allowed@example.com", auth_env)
    # A login token must never validate as a session cookie value, or vice versa.
    assert verify_session_value(login_token, auth_env) is None
    assert verify_login_token(login_token, auth_env) == "allowed@example.com"


def test_public_homepage_reachable_without_a_session(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Understand the scene before it becomes a trend" in response.text


def test_public_intelligence_page_reachable_without_a_session(client):
    response = client.get("/intelligence")
    assert response.status_code == 200


def test_authenticated_visitor_is_redirected_off_the_public_homepage(client):
    with patch("culture.web.auth.send_login_email") as mock_send:
        client.post("/login", data={"email": "allowed@example.com", "next": "/dashboard"})
    token = mock_send.call_args.args[1]
    client.get(f"/auth/verify?token={token}")

    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_missing_session_secret_raises_clear_error(monkeypatch):
    monkeypatch.setenv("SESSION_SECRET", "")
    get_settings.cache_clear()
    settings = get_settings()
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        create_login_token("a@b.com", settings)


# --- roles, demo account, rate limiting, audit (26 Aug 2026) ---------------


@pytest.fixture
def role_env(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setenv("DEMO_EMAIL", "demo@example.com")
    # sha256("demo-pass")
    import hashlib

    monkeypatch.setenv("DEMO_PASSWORD_SHA256", hashlib.sha256(b"demo-pass").hexdigest())
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


@pytest.fixture
def role_client(role_env):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    app = create_app(engine=engine, require_auth=True)
    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    client.app_engine = engine
    return client


def _login_as(client, email, role=None):
    from culture.web.auth import create_session_value

    client.cookies.set("ci_session", create_session_value(email, get_settings(), role=role))


def _audit_events(engine):
    from sqlalchemy.orm import Session

    from culture.models.auth_event import AuthEvent

    with Session(engine) as s:
        return [(e.event, e.email) for e in s.query(AuthEvent).all()]


def test_session_carries_role_and_legacy_payload_still_verifies(role_env):
    from culture.web.auth import _serializer, create_session_value, verify_session_value

    settings = get_settings()
    info = verify_session_value(create_session_value("boss@example.com", settings), settings)
    assert (info.email, info.role) == ("boss@example.com", "admin")
    info = verify_session_value(create_session_value("allowed@example.com", settings), settings)
    assert info.role == "member"
    # Pre-role cookie: bare email string payload.
    legacy = _serializer(settings, "session").dumps("allowed@example.com")
    info = verify_session_value(legacy, settings)
    assert (info.email, info.role) == ("allowed@example.com", "member")


def test_member_is_denied_admin_routes_at_the_route(role_client):
    _login_as(role_client, "allowed@example.com")
    response = role_client.get("/sources")
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    assert ("access_denied", "allowed@example.com") in _audit_events(role_client.app_engine)


def test_admin_reaches_admin_routes(role_client):
    _login_as(role_client, "boss@example.com")
    assert role_client.get("/sources").status_code == 200


def test_demo_login_and_blocked_surfaces(role_client):
    response = role_client.post(
        "/login/demo", data={"email": "demo@example.com", "password": "demo-pass"}
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"
    # Demo may browse member surfaces but not evidence/operational ones.
    assert role_client.get("/signals").status_code == 200
    for path in ("/stream", "/search", "/sources", "/reports"):
        denied = role_client.get(path)
        assert denied.status_code == 303, path
        assert denied.headers["location"] == "/dashboard"
    events = _audit_events(role_client.app_engine)
    assert ("demo_login", "demo@example.com") in events


def test_demo_login_rejects_wrong_password(role_client):
    response = role_client.post(
        "/login/demo", data={"email": "demo@example.com", "password": "wrong"}
    )
    assert response.status_code == 200
    assert "not recognized" in response.text
    assert ("demo_login_failed", "demo@example.com") in _audit_events(role_client.app_engine)


def test_demo_banner_and_source_hiding(role_client):
    response = role_client.post(
        "/login/demo", data={"email": "demo@example.com", "password": "demo-pass"}
    )
    assert response.status_code == 303
    text = role_client.get("/dashboard").text
    assert "Demo account" in text
    assert "Sabukaru" not in text  # fixture source name never shown to demo


def test_login_rate_limit_kicks_in(role_client):
    for _ in range(5):
        role_client.post("/login", data={"email": "allowed@example.com"})
    response = role_client.post("/login", data={"email": "allowed@example.com"})
    assert "Too many attempts" in response.text
    assert any(e == "rate_limited" for e, _ in _audit_events(role_client.app_engine))


def test_unknown_email_is_rejected_explicitly_and_no_link_is_sent(role_client):
    # 26 Aug ruling: explicit rejection over enumeration resistance.
    with patch("culture.web.auth.send_login_email") as send:
        response = role_client.post("/login", data={"email": "stranger@example.com"})
    assert "does not have access" in response.text
    send.assert_not_called()
    assert ("access_requested", "stranger@example.com") in _audit_events(role_client.app_engine)


def test_known_email_gets_link_and_affirmative_message(role_client):
    with patch("culture.web.auth.send_login_email") as send:
        response = role_client.post("/login", data={"email": "allowed@example.com"})
    assert "sign-in link is on its way" in response.text
    send.assert_called_once()
    assert ("link_requested", "allowed@example.com") in _audit_events(role_client.app_engine)


def test_invite_enforced_even_with_a_valid_token_for_unknown_email(role_client):
    # A signed token for a non-member must not create a session: the
    # allow-list is re-checked at verification, not just at request time.
    token = create_login_token("stranger@example.com", get_settings())
    response = role_client.get(f"/auth/verify?token={token}")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?error=expired"
    assert role_client.cookies.get("ci_session") is None


def test_demo_access_is_read_only_by_construction(role_client):
    # The demo account cannot modify anything because nothing modifiable is
    # exposed: the only non-GET routes in the whole app are the login forms.
    mutating = [
        route.path
        for route in role_client.app.routes
        if hasattr(route, "methods") and (route.methods - {"GET", "HEAD"})
    ]
    assert sorted(mutating) == ["/login", "/login/demo"]
