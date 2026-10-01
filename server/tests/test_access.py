"""访问授权：登录、名单、能力判定。

不触网：微信换取用打桩替代（真实调用需要有效 code，且受频率限制）。
"""

import urllib.error

import pytest
from fastapi.testclient import TestClient

from app.core import config as config_module
from app.core import wechat
from app.modules.access import repository, service
from app.modules.access.identity import IdentityKind

OPS_HEADERS = {"X-Ops-Token": "test-token"}


def _stub_openid(monkeypatch: pytest.MonkeyPatch, openid: str | None) -> None:
    """把 code 换 openid 打桩成固定结果。

    打桩目标是 service 模块里的名字而非 wechat 模块的属性：service 用的是
    `from app.core.wechat import code_to_openid`，那是直接绑定，改源模块无效。
    """
    monkeypatch.setattr(service, "code_to_openid", lambda _code: openid)


def _grant(client: TestClient, openid: str, capabilities: list[str], label: str = "某人") -> None:
    r = client.post(
        "/ops/identities",
        headers=OPS_HEADERS,
        json={"kind": "openid", "openid": openid, "capabilities": capabilities, "label": label},
    )
    assert r.status_code == 200


# ── 登录 ──


def test_login_returns_identity_and_capabilities(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """登录成功返回 openid 本身作为标识——不再包一层令牌。"""
    _stub_openid(monkeypatch, "openid-abcdefgh")
    _grant(client, "openid-abcdefgh", ["ops"])

    r = client.post("/api/v1/identity", json={"code": "wx-code"})

    assert r.status_code == 200
    body = r.json()
    # 登录返回与名单完全一致的记录形状（一个资源一个模型），
    # 而不是只含 identity+capabilities 的子集
    assert body["identity"] == "openid-abcdefgh"
    assert body["kind"] == "openid"
    assert body["capabilities"] == ["ops"]


def test_login_rejected_when_not_granted(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """不在名单里的用户登录失败——"不在名单"与"换不到 openid"对外不区分。"""
    _stub_openid(monkeypatch, "openid-outsider")

    r = client.post("/api/v1/identity", json={"code": "wx-code"})

    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/login-rejected")


def test_login_rejected_when_wechat_unavailable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_openid(monkeypatch, None)

    r = client.post("/api/v1/identity", json={"code": "wx-code"})

    assert r.status_code == 401


def test_login_rejected_when_wechat_not_configured(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """未配置小程序凭证时登录不可用，但不把配置异常抛给调用方——那是运维问题。"""

    def not_configured(_code: str) -> str | None:
        raise wechat.WeChatNotConfiguredError

    monkeypatch.setattr(service, "code_to_openid", not_configured)

    r = client.post("/api/v1/identity", json={"code": "wx-code"})

    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/login-rejected")


def test_login_requires_code(client: TestClient) -> None:
    # 空 code 是参数错误，不是认证失败
    assert client.post("/api/v1/identity", json={"code": ""}).status_code == 422


# ── 标识解析 ──


def test_resolve_unknown_identity_is_none(client: TestClient) -> None:
    assert service.resolve_identity("nobody") is None


def test_resolve_known_identity_returns_capabilities(client: TestClient) -> None:
    _grant(client, "openid-known0001", ["ops", "photo"])

    found = service.resolve_identity("openid-known0001")

    assert found is not None
    assert sorted(found["capabilities"]) == ["ops", "photo"]


def test_grant_is_idempotent_and_updates_capabilities(client: TestClient) -> None:
    """重复登记同一身份等于更新其能力集，而不是留两行。"""
    _grant(client, "openid-update001", ["ops"])
    _grant(client, "openid-update001", ["photo"])

    assert service.resolve_identity("openid-update001")["capabilities"] == ["photo"]
    assert len(service.list_identities()) == 1


def test_revoke_removes_identity(client: TestClient) -> None:
    _grant(client, "openid-revoke0001", ["ops"])

    assert service.revoke_identity("openid-revoke0001") is True
    assert service.resolve_identity("openid-revoke0001") is None
    assert service.revoke_identity("openid-revoke0001") is False


# ── 守卫 ──


def test_missing_identity_header_is_unauthenticated(client: TestClient) -> None:
    r = client.get("/api/v1/identity")

    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/unauthenticated")


def test_me_returns_all_capabilities(client: TestClient, ops_headers: dict[str, str]) -> None:
    """一次返回全部能力，前端据此决定按钮显隐，不必按能力逐个试探。"""
    r = client.get("/api/v1/identity", headers=ops_headers)

    assert r.status_code == 200
    assert sorted(r.json()["capabilities"]) == ["ops", "photo"]


def test_capability_denied_when_missing(client: TestClient) -> None:
    """有身份但缺该能力：403 且 type 可区分于"没身份"。"""
    repository.put_identity("photo-only-user", IdentityKind.WEB, ["photo"], "只拍照")

    r = client.post(
        "/api/v1/sessions/assign",
        json={"device_id": "dev-x", "door_no": 1, "cert_no": "C", "password": "P"},
        headers={"X-Identity": "photo-only-user"},
    )

    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/capability-denied")


# ── 管理面 ──


def test_ops_endpoints_require_ops_token(client: TestClient) -> None:
    """授权名单只能由运维通道改动，普通身份不得触碰。"""
    assert client.get("/ops/identities").status_code == 401
    assert client.get("/ops/identities", headers={"X-Ops-Token": "wrong"}).status_code == 401


def test_unknown_capability_name_is_rejected(client: TestClient) -> None:
    r = client.post(
        "/ops/identities",
        headers=OPS_HEADERS,
        json={"kind": "openid", "openid": "openid-abcdefgh", "capabilities": ["ops", "delete-everything"]},
    )

    assert r.status_code == 422
    assert r.json()["type"].endswith("/problems/unknown-capability")


def test_empty_capability_list_is_rejected(client: TestClient) -> None:
    # 一个能力都不给，与"未授权"没有区别
    r = client.post("/ops/identities", headers=OPS_HEADERS, json={"openid": "openid-abcdefgh", "capabilities": []})

    assert r.status_code == 422


def test_web_identity_can_be_issued_and_used(client: TestClient) -> None:
    """网页拿不到 openid：我方签发标识，该标识随即可用。"""
    r = client.post(
        "/ops/identities",
        headers=OPS_HEADERS,
        json={"kind": "web", "label": "给张三的网页入口", "capabilities": ["ops"]},
    )

    assert r.status_code == 200
    identity = r.json()["identity"]
    assert service.resolve_identity(identity)["label"] == "给张三的网页入口"
    assert client.get("/api/v1/identity", headers={"X-Identity": identity}).status_code == 200


def test_grants_list_shows_both_kinds(client: TestClient) -> None:
    """名单里同时能看到小程序用户与网页标识，才分得清谁是谁。"""
    _grant(client, "openid-list000001", ["ops"])
    client.post(
        "/ops/identities",
        headers=OPS_HEADERS,
        json={"kind": "web", "label": "网页入口", "capabilities": ["photo"]},
    )

    kinds = {item["kind"] for item in client.get("/ops/identities", headers=OPS_HEADERS).json()}

    assert kinds == {"openid", "web"}


def test_delete_grant_idempotent_from_api(client: TestClient) -> None:
    _grant(client, "openid-del0000001", ["ops"])

    assert client.delete("/ops/identities/openid-del0000001", headers=OPS_HEADERS).status_code == 204
    assert client.delete("/ops/identities/openid-del0000001", headers=OPS_HEADERS).status_code == 204


# ── 微信客户端 ──


def test_code_to_openid_raises_when_not_configured(client: TestClient) -> None:
    """未配置 appid/secret 时明确报错，而不是发一个注定失败的请求。"""
    assert wechat.is_configured() is False
    with pytest.raises(wechat.WeChatNotConfiguredError):
        wechat.code_to_openid("code")


def test_is_configured_true_with_credentials(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "wechat_appid", "appid")
    monkeypatch.setattr(config_module.settings, "wechat_secret", "secret")

    assert wechat.is_configured() is True


class _FakeResponse:
    """urlopen 的替身：只需要 read() 与上下文协议。"""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def test_code_to_openid_parses_openid(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "wechat_appid", "appid")
    monkeypatch.setattr(config_module.settings, "wechat_secret", "secret")
    payload = b'{"openid": "openid-from-wechat", "session_key": "k"}'
    monkeypatch.setattr(wechat.urllib.request, "urlopen", lambda *_a, **_kw: _FakeResponse(payload))

    assert wechat.code_to_openid("code") == "openid-from-wechat"


def test_code_to_openid_returns_none_on_wechat_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """微信返回错误码（code 已用过/过期）时返回 None，由调用方按"登录失败"处理。"""
    monkeypatch.setattr(config_module.settings, "wechat_appid", "appid")
    monkeypatch.setattr(config_module.settings, "wechat_secret", "secret")
    monkeypatch.setattr(
        wechat.urllib.request,
        "urlopen",
        lambda *_a, **_kw: _FakeResponse(b'{"errcode": 40029, "errmsg": "invalid code"}'),
    )

    assert wechat.code_to_openid("used-code") is None


def test_code_to_openid_returns_none_on_network_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """网络故障不抛异常：调用方只关心"换不到"，具体原因记日志。"""
    monkeypatch.setattr(config_module.settings, "wechat_appid", "appid")
    monkeypatch.setattr(config_module.settings, "wechat_secret", "secret")

    def fail(*_a: object, **_kw: object) -> None:
        message = "network down"
        raise urllib.error.URLError(message)

    monkeypatch.setattr(wechat.urllib.request, "urlopen", fail)

    assert wechat.code_to_openid("code") is None
