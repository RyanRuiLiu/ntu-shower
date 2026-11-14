"""API 密钥：签发、列表、吊销与权限×作用域的细粒度判定。

密钥是权限的唯一载体（GitHub fine-grained PAT 同型）：默认永不过期、多枚共存、
单枚吊销即 401 且不影响其他密钥与面板登录。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from tests import device_link, identity

KEY_PATH = "/api/v1/users/dorm-system-01/keys"


@pytest.fixture
def dorm_user(client: TestClient, admin_headers: dict[str, str]) -> str:
    """一枚被管理的目标用户：密钥用例都挂它名下。"""
    created = client.post("/api/v1/users", headers=admin_headers, json={"username": "dorm-system-01"})
    assert created.status_code == 200
    return "dorm-system-01"


def test_issued_key_is_prefixed_and_usable(client: TestClient, admin_headers: dict[str, str], dorm_user: str) -> None:
    """密钥带 ntushower_ 前缀（Stripe/GitHub/GitLab 官方前缀惯例），签发即可用。"""
    r = client.post(KEY_PATH, headers=admin_headers, json={"name": "对接钥", "scopes": ["sessions:write"]})

    assert r.status_code == 200
    body = r.json()
    assert body["token"].startswith("ntushower_")
    assert body["token_hint"]
    assert client.get("/api/v1/users/me", headers=identity.bearer(body["token"])).status_code == 200


def test_issued_key_plaintext_never_returns_again(
    client: TestClient, admin_headers: dict[str, str], dorm_user: str
) -> None:
    """明文只在签发响应出现一次；列表只有摘要前 12 位（Stripe/GitHub 官方做法）。"""
    token = client.post(KEY_PATH, headers=admin_headers, json={"name": "k1", "scopes": ["jobs:read"]}).json()["token"]

    listed = client.get(KEY_PATH, headers=admin_headers).json()["keys"]

    assert len(listed) == 1
    assert "token" not in listed[0]
    assert listed[0]["token_hint"]
    assert token not in str(listed)


def test_revoke_is_key_scoped(client: TestClient, admin_headers: dict[str, str], dorm_user: str) -> None:
    """吊销一枚：该枚 401，同用户其他密钥与面板用户档案不受影响。"""
    first = client.post(KEY_PATH, headers=admin_headers, json={"name": "a", "scopes": ["jobs:read"]}).json()
    second = client.post(KEY_PATH, headers=admin_headers, json={"name": "b", "scopes": ["jobs:read"]}).json()

    assert client.delete(f"{KEY_PATH}/{first['id']}", headers=admin_headers).status_code == 204
    # 幂等：再吊一次仍 204
    assert client.delete(f"{KEY_PATH}/{first['id']}", headers=admin_headers).status_code == 204

    assert client.get("/api/v1/jobs/status", headers=identity.bearer(first["token"])).status_code == 401
    assert client.get("/api/v1/jobs/status", headers=identity.bearer(second["token"])).status_code == 200
    assert client.get("/api/v1/users/me", headers=admin_headers).status_code == 200
    revoked = client.get(KEY_PATH, headers=admin_headers).json()["keys"]
    assert next(k for k in revoked if k["id"] == first["id"])["revoked_at"]


def test_unknown_scope_name_is_rejected(client: TestClient, admin_headers: dict[str, str], dorm_user: str) -> None:
    r = client.post(KEY_PATH, headers=admin_headers, json={"name": "x", "scopes": ["ops", "delete-everything"]})

    assert r.status_code == 422
    assert r.json()["type"].endswith("/problems/unknown-scope")


def test_empty_scope_list_is_rejected(client: TestClient, admin_headers: dict[str, str], dorm_user: str) -> None:
    """一个权限都不给与"未授权"没有区别，签发时即拒绝。"""
    r = client.post(KEY_PATH, headers=admin_headers, json={"name": "x", "scopes": []})

    assert r.status_code == 422


def test_unknown_device_in_scope_is_rejected(client: TestClient, admin_headers: dict[str, str], dorm_user: str) -> None:
    """作用域里的 device_id 必须真实存在：写错的授权永远执行不了。"""
    r = client.post(
        KEY_PATH,
        headers=admin_headers,
        json={"name": "x", "scopes": ["jobs:read"], "device_scope": ["no-such-device-01"]},
    )

    assert r.status_code == 422


def test_key_for_missing_user_is_not_found(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.post(
        "/api/v1/users/nobody-here/keys", headers=admin_headers, json={"name": "x", "scopes": ["jobs:read"]}
    )

    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/user-not-found")


def test_scopes_gate_endpoints(client: TestClient, fresh_db: str) -> None:
    """scope 矩阵：photo 钥对分配 403（insufficient-scope），sessions 钥对拍照 403。"""
    photo_token = identity.register_key("photo-only-0001", ["devices:photo"], ["*"])
    session_token = identity.register_key("session-only-001", ["sessions:write"], ["*"])

    r = client.post(
        "/api/v1/devices/dev-any-0001/assign",
        json={"door_no": 1, "cert_no": "C", "password": "P"},
        headers=identity.bearer(photo_token),
    )
    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/insufficient-scope")

    r = client.post("/api/v1/devices/dev-any-0001/photo", headers=identity.bearer(session_token))
    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/insufficient-scope")


def test_wildcard_scope_passes_guard_for_any_device(
    client: TestClient,
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作用域 ["*"] 的密钥对任何设备放行：请求穿过全部守卫，落到喷头预检。"""
    from app.core.connections import manager

    device_link.register_trusted_device("dev-any-0001")
    monkeypatch.setattr(manager, "_connections", {"dev-any-0001": object()})
    token = identity.register_key("wildcard-000001", ["sessions:write"], ["*"])

    r = client.post(
        "/api/v1/devices/dev-any-0001/assign",
        json={"door_no": 1, "cert_no": "C", "password": "P"},
        headers=identity.bearer(token),
    )

    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/door-not-found")


def test_list_scope_blocks_device_outside_list(client: TestClient, fresh_db: str) -> None:
    """作用域限定具体设备后，对列表外设备的操作被拒（对象级检查）。"""
    token = identity.register_key("scoped-0000001", ["sessions:write"], ["dev-allow-0001"])

    r = client.post(
        "/api/v1/devices/dev-other-002/assign",
        json={"door_no": 1, "cert_no": "C", "password": "P"},
        headers=identity.bearer(token),
    )

    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/insufficient-scope")


def test_editing_user_does_not_rotate_keys(client: TestClient, admin_headers: dict[str, str], dorm_user: str) -> None:
    """编辑用户不动密钥：轮换是显式的（发新→切换→吊旧），不再是"编辑即轮换"。"""
    key = client.post(KEY_PATH, headers=admin_headers, json={"name": "k", "scopes": ["jobs:read"]}).json()
    token = key["token"]

    assert client.put(f"/api/v1/users/{dorm_user}", headers=admin_headers, json={"name": "改名"}).status_code == 200
    assert client.get("/api/v1/jobs/status", headers=identity.bearer(token)).status_code == 200


def test_update_key_takes_effect_immediately(client: TestClient, admin_headers: dict[str, str], dorm_user: str) -> None:
    """编辑密钥：令牌不变，权限改动下一次请求即生效（Stripe RAK 形态）。"""
    key = client.post(KEY_PATH, headers=admin_headers, json={"name": "k", "scopes": ["jobs:read"]}).json()
    headers = identity.bearer(key["token"])
    assert client.get("/api/v1/jobs/status", headers=headers).status_code == 200

    edited = client.put(f"{KEY_PATH}/{key['id']}", headers=admin_headers, json={"scopes": ["audit:read"]})
    assert edited.status_code == 200
    assert edited.json()["scopes"] == ["audit:read"]
    assert edited.json()["token_hint"] == key["token_hint"]  # 令牌不变：摘要前缀相同

    assert client.get("/api/v1/jobs/status", headers=headers).status_code == 403
    assert client.get("/api/v1/audit/calls", headers=headers).status_code == 200


def test_revoked_key_cannot_be_edited(client: TestClient, admin_headers: dict[str, str], dorm_user: str) -> None:
    """吊销即终态：已吊销的密钥拒绝编辑，要改就发新枚。"""
    key = client.post(KEY_PATH, headers=admin_headers, json={"name": "k", "scopes": ["jobs:read"]}).json()
    assert client.delete(f"{KEY_PATH}/{key['id']}", headers=admin_headers).status_code == 204

    edited = client.put(f"{KEY_PATH}/{key['id']}", headers=admin_headers, json={"name": "x"})
    assert edited.status_code == 422


def test_update_key_unknown_scope_is_rejected(
    client: TestClient, admin_headers: dict[str, str], dorm_user: str
) -> None:
    key = client.post(KEY_PATH, headers=admin_headers, json={"name": "k", "scopes": ["jobs:read"]}).json()
    r = client.put(f"{KEY_PATH}/{key['id']}", headers=admin_headers, json={"scopes": ["nope"]})

    assert r.status_code == 422
    assert r.json()["type"].endswith("/problems/unknown-scope")


def test_update_missing_key_is_not_found(client: TestClient, admin_headers: dict[str, str], dorm_user: str) -> None:
    r = client.put(f"{KEY_PATH}/999", headers=admin_headers, json={"name": "x"})

    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/key-not-found")


def test_multiple_keys_coexist(client: TestClient, admin_headers: dict[str, str], dorm_user: str) -> None:
    """多枚共存：Stripe 官方 "Create as many RAKs as you want"，轮换的重叠窗口由此而来。"""
    client.post(KEY_PATH, headers=admin_headers, json={"name": "a", "scopes": ["jobs:read"]})
    client.post(KEY_PATH, headers=admin_headers, json={"name": "b", "scopes": ["jobs:read"]})
    client.post(KEY_PATH, headers=admin_headers, json={"name": "c", "scopes": ["jobs:read"]})

    keys = client.get(KEY_PATH, headers=admin_headers).json()["keys"]

    assert len(keys) == 3
