"""认证端点与设备接入令牌。

前者验证签名 Cookie 的完整生命周期，后者验证「开启校验后，不带令牌或令牌绑定设备
与 hello 自报不一致时，连接在握手阶段就被拒」。
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tests import frames

from app.auth import sessions
from app.core.config import settings
from app.db.session import Database
from app.persistence import device_credentials, devices, users

USER = "admin-web"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def account(fresh_db: str) -> None:
    database = Database(settings.effective_database_url)
    try:
        with database.new_session() as session:
            users.create_user(session, USER, PASSWORD, ["admin", "session"], ["*"])
    finally:
        database.dispose()


def test_login_rejects_wrong_password(client: TestClient, account: None) -> None:
    resp = client.post("/api/v1/auth/login", json={"username": USER, "password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["type"].endswith("/problems/unauthenticated")
    assert PASSWORD not in resp.text


def test_login_sets_cookie_and_me_returns_account(client: TestClient, account: None) -> None:
    resp = client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    assert resp.status_code == 200
    assert resp.json()["capabilities"] == ["admin", "session"]
    assert sessions.COOKIE_NAME in resp.cookies

    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["username"] == USER
    assert "password_hash" not in me.json()


def test_logout_drops_the_session(client: TestClient, account: None) -> None:
    client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_tampered_cookie_is_rejected(client: TestClient, account: None) -> None:
    token = sessions.issue(USER) + "tampered"
    client.cookies.set(sessions.COOKIE_NAME, token)
    assert client.get("/api/v1/auth/me").status_code == 401


def test_expired_token_is_rejected(client: TestClient, account: None) -> None:
    client.cookies.set(sessions.COOKIE_NAME, sessions.issue(USER, ttl_seconds=-1))
    assert client.get("/api/v1/auth/me").status_code == 401


def test_session_cookie_grants_admin_capability(client: TestClient, account: None) -> None:
    """会话身份走的是同一条守卫：能进管理端点，不需要再带 X-Identity。"""
    assert client.get("/api/v1/identities").status_code == 401
    client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    resp = client.get("/api/v1/identities")
    assert resp.status_code == 200
    assert "capabilities" in resp.json()


def test_disabled_account_cannot_keep_a_session(client: TestClient, account: None) -> None:
    client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    database = Database(settings.effective_database_url)
    try:
        with database.new_session() as session:
            assert users.set_disabled(session, USER, True) is True
    finally:
        database.dispose()
    assert client.get("/api/v1/auth/me").status_code == 401


def test_device_token_required(client: TestClient, fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """开启校验后：无令牌拒绝；有效令牌放行；换一台设备自报则拒绝。"""
    from datetime import UTC, datetime

    device_id = "dev-token-0001"
    database = Database(settings.effective_database_url)
    try:
        with database.new_session() as session:
            devices.register(
                session,
                device_id=device_id,
                app_version="0.6.5",
                version_code=605,
                os_version="4.4.4",
                capabilities=[],
                ip="127.0.0.1",
                booted_at=datetime.now(UTC),
                seen_at=datetime.now(UTC),
            )
            token = device_credentials.issue(session, device_id)
    finally:
        database.dispose()
    assert token is not None

    monkeypatch.setattr(settings, "require_device_token", True)

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.frame("link.hello", {"device_id": device_id}))

    with client.websocket_connect("/gateway/device", headers={"Authorization": f"Bearer {token}"}) as ws:
        ws.send_text(frames.frame("link.hello", {"device_id": device_id}))
        ack = frames.recv_until(ws, "link.hello_ack")
        assert ack["type"] == "link.hello_ack"

    with client.websocket_connect("/gateway/device", headers={"Authorization": f"Bearer {token}"}) as ws:
        ws.send_text(frames.frame("link.hello", {"device_id": "another-device"}))
        error_frame = json.loads(ws.receive_text())
        assert error_frame["type"] == "link.error"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_text()
