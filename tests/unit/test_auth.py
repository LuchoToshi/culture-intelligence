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


def test_login_response_identical_for_disallowed_email(client):
    with patch("culture.web.auth.send_login_email") as mock_send:
        response = client.post("/login", data={"email": "stranger@example.com", "next": "/"})
    assert response.status_code == 200
    assert "stranger@example.com" in response.text  # same "check your inbox" copy
    mock_send.assert_not_called()  # but no email actually sent


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
