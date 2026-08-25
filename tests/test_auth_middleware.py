"""Auth middleware behavior for remote clients."""

import pytest
from fastapi.testclient import TestClient

from iris.app.core.auth import ensure_token
from iris.app.core.config import settings
from iris.app.main import app


@pytest.fixture()
def remote_client(monkeypatch):
    """A client that looks like a phone on the LAN with auth enforced."""
    monkeypatch.setattr(settings, "REQUIRE_AUTH", True)
    with TestClient(app, client=("192.168.1.55", 50000)) as client:
        yield client


def test_ui_shell_is_public(remote_client):
    assert remote_client.get("/").status_code == 200
    assert remote_client.get("/static/style.css").status_code == 200
    assert remote_client.get("/health").status_code == 200


def test_api_requires_token_remotely(remote_client):
    assert remote_client.post("/api/v1/chat", json={"message": "hi"}).status_code == 401


def test_api_accepts_valid_token(remote_client):
    token = ensure_token()
    res = remote_client.post(
        "/api/v1/chat", json={"message": "hello"}, headers={"X-Iris-Token": token}
    )
    assert res.status_code == 200
    res = remote_client.get(f"/api/v1/llm/status?token={token}")
    assert res.status_code == 200


def test_loopback_needs_no_token(monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_AUTH", True)
    with TestClient(app) as client:  # testclient host counts as loopback
        assert client.post("/api/v1/chat", json={"message": "hi"}).status_code == 200
