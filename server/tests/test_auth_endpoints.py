"""认证端点与设备接入令牌。

前者验证签名 Cookie 的完整生命周期（签发、校验、篡改拒绝、过期拒绝、注销），
后者验证「开启校验后，不带令牌或令牌绑定设备与 hello 自报不一致时，连接在握手
阶段就被拒」。
"""

from __future__ import annotations

import base64
import json
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tests import frames

from app.auth import crud
from app.core.config import settings
from app.db.session import Database
from app.main import SESSION_COOKIE_NAME
from app.modules.devices import crud as devices

USER = "admin-web"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def account(fresh_db: str) -> None:
    database = Database(settings.effective_database_url)
    try:
        with database.new_session() as session:
            crud.create_user(session, USER, password=PASSWORD)
    finally:
        database.dispose()


def test_login_rejects_wrong_password(client: TestClient, account: None) -> None:
    resp = client.post("/api/v1/auth/login", json={"username": USER, "password": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["type"].endswith("/problems/unauthenticated")
    assert PASSWORD not in resp.text


def test_login_sets_cookie_and_me_returns_account(client: TestClient, account: None) -> None:
    resp = client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    assert resp.status_code == 204
    assert SESSION_COOKIE_NAME in resp.cookies

    me = client.get("/api/v1/users/me")
    assert me.status_code == 200
    assert me.json()["username"] == USER


def test_logout_drops_the_session(client: TestClient, account: None) -> None:
    client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/users/me").status_code == 401


def test_tampered_cookie_is_rejected(client: TestClient, account: None) -> None:
    resp = client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    token = resp.cookies[SESSION_COOKIE_NAME] + "tampered"
    # httpx2 2.16 起 set() 不再替换 jar 里的同名条目：两条同名 cookie 并存发出，
    # 服务端按首个匹配取到未篡改的有效会话。先清空，保证请求只携带篡改值。
    client.cookies.clear()
    client.cookies.set(SESSION_COOKIE_NAME, token)
    assert client.get("/api/v1/users/me").status_code == 401


def test_expired_token_is_rejected(client: TestClient, account: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """签出一枚真实签名但时间戳早于 max_age 的 Cookie。

    用库里同一把密钥（itsdangerous TimestampSigner，与 SessionMiddleware 同源）签，
    只把取当前时刻的钟拨回去——签发时刻早于有效期，unsign 时按 max_age 拒收。
    """
    from itsdangerous.timed import TimestampSigner

    # context() 只回滚这一段补丁：直接 undo() 会连 autouse 夹具（数据库隔离）的
    # 补丁一起退掉，CI 上审计中间件就会写进未隔离的默认库。
    payload = base64.b64encode(json.dumps({"u": USER}).encode("utf-8"))
    with monkeypatch.context() as m:
        m.setattr(
            "itsdangerous.timed.TimestampSigner.get_timestamp",
            lambda _self: int(time.time()) - settings.session_idle_seconds - 60,
        )
        # sign(bytes) 返回 bytes，与 SessionMiddleware 落 Cookie 前的 decode 一致
        stale = TimestampSigner(settings.session_secret).sign(payload).decode("ascii")

    client.cookies.set(SESSION_COOKIE_NAME, stale)
    assert client.get("/api/v1/users/me").status_code == 401


def test_session_cookie_grants_operator_access(client: TestClient, account: None) -> None:
    """会话通道是操作台：能进用户域端点（scopes 恒 *），不需要另带 Bearer 密钥。"""
    assert client.get("/api/v1/users").status_code == 401
    client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    resp = client.get("/api/v1/users")
    assert resp.status_code == 200
    assert "users" in resp.json()

    me = client.get("/api/v1/users/me")
    assert me.status_code == 200
    assert me.json()["scopes"] == ["*"]
    assert me.json()["key_name"] is None


def test_absolute_timeout_is_enforced_server_side(
    client: TestClient, account: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OWASP absolute timeout：距 first_iat 超过 absolute 秒即失效，即使 Cookie 未过期。"""
    client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    client.get("/api/v1/users/me")  # 补齐 first_iat（登录时已写入）

    # 把会话字典里的 first_iat 拨回绝对上限之外：直接构造签名 Cookie 太绕，
    # 用真实中间件语义在服务端验证——把 settings 的 absolute 收紧到已超过。
    monkeypatch.setattr(settings, "session_absolute_seconds", 0)
    assert client.get("/api/v1/users/me").status_code == 401


def test_disabled_account_cannot_keep_a_session(client: TestClient, account: None) -> None:
    client.post("/api/v1/auth/login", json={"username": USER, "password": PASSWORD})
    database = Database(settings.effective_database_url)
    try:
        with database.new_session() as session:
            assert crud.update_user(session, USER, disabled=True) is not None
    finally:
        database.dispose()
    assert client.get("/api/v1/users/me").status_code == 401


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
            token = devices.issue(session, device_id)
    finally:
        database.dispose()
    assert token is not None

    monkeypatch.setattr(settings, "require_device_token", True)

    # 无凭据：握手先 accept，再以 1008（RFC 6455 Policy Violation）关闭。关闭码必须
    # 随 WebSocket 关闭帧送达客户端——设备端据此清凭据并走 claim 自注册；若退回
    # accept 前的 HTTP 403，客户端只能看到 onFailure，无法与网络断连区分。
    with client.websocket_connect("/gateway/device") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_text()
        assert int(closed.value.code) == 1008

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
