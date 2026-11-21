"""用户域：名单维护、账号生命周期与守卫的基本拒绝路径。

守卫唯一实现是 auth.dependencies：未带凭据 401（unauthenticated）、凭据无效
401（invalid-token，RFC 6750）、缺权限 403（insufficient-scope）。权限矩阵与
密钥的细粒度判定在 test_api_keys.py / test_permissions.py。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests.conftest import ADMIN_USER

from app.auth.permissions import ALL_SCOPES


@contextmanager
def _store() -> Iterator[Session]:
    """指向当前用例库的会话；名单写入与断言都走数据层。"""
    from app.core.config import settings
    from app.db.session import Database

    database = Database(f"sqlite:///{Path(settings.db_path).as_posix()}")
    try:
        with database.new_session() as session:
            yield session
    finally:
        database.dispose()


def test_admin_surface_without_credentials_is_unauthenticated(client: TestClient) -> None:
    r = client.get("/api/v1/users")

    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/unauthenticated")


def test_unknown_token_is_invalid(client: TestClient) -> None:
    """凭据无效是 RFC 6750 的 invalid-token，不再是 unknown-identity。"""
    r = client.get("/api/v1/users", headers={"Authorization": "Bearer not-a-valid-token"})

    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/invalid-token")


def test_users_surface_requires_users_write(client: TestClient, ops_headers: dict[str, str]) -> None:
    """有身份但缺 users:write → 403（insufficient-scope，RFC 6750）。"""
    r = client.get("/api/v1/users", headers=ops_headers)

    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/insufficient-scope")


def test_users_returns_domain_read_model(client: TestClient, admin_headers: dict[str, str]) -> None:
    """GET /users 返回域读模型：权限字典与名单，用户页一次装载。"""
    r = client.get("/api/v1/users", headers=admin_headers)

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"scopes", "users"}
    scopes = {item["scope"]: item["label"] for item in body["scopes"]}
    assert set(scopes) == ALL_SCOPES
    assert all(scopes.values())
    admin = next(item for item in body["users"] if item["username"] == ADMIN_USER)
    assert admin["has_password"] is False
    assert admin["disabled"] is False


def test_legacy_identities_prefix_is_gone(client: TestClient, admin_headers: dict[str, str]) -> None:
    """API 面的 /identities 旧前缀已随术语统一删除：一律 404（identities → users）。
    非 API 的 /identities/me 现在是前端路由（SPA 入口），不再断言 404。"""
    for path in ("/api/v1/identities", "/api/v1/identity"):
        r = client.get(path, headers=admin_headers)
        assert r.status_code == 404, path


def test_user_lifecycle_over_http(client: TestClient, admin_headers: dict[str, str]) -> None:
    """登记 → 编辑 → 名单 → 删除： username 唯一、编辑只改传了的字段。"""
    created = client.post(
        "/api/v1/users",
        headers=admin_headers,
        json={"username": "dorm-system-01", "name": "宿舍系统", "password": "s3cret-passphrase"},
    )
    assert created.status_code == 200
    assert created.json()["has_password"] is True

    edited = client.put("/api/v1/users/dorm-system-01", headers=admin_headers, json={"name": "宿舍自助系统"})
    assert edited.status_code == 200
    assert edited.json()["name"] == "宿舍自助系统"
    assert edited.json()["has_password"] is True  # 未传口令时不动

    duplicate = client.post("/api/v1/users", headers=admin_headers, json={"username": "dorm-system-01"})
    assert duplicate.status_code == 422

    deleted = client.delete("/api/v1/users/dorm-system-01", headers=admin_headers)
    assert deleted.status_code == 204
    # 幂等：再删一次仍 204（DELETE 的语义，与旧名单域一致）
    assert client.delete("/api/v1/users/dorm-system-01", headers=admin_headers).status_code == 204


def test_cannot_delete_self(client: TestClient, admin_headers: dict[str, str]) -> None:
    """管理台最后一名操作者删掉自己就把自己锁在门外：拒绝。"""
    r = client.delete(f"/api/v1/users/{ADMIN_USER}", headers=admin_headers)

    assert r.status_code == 422


def test_users_me_reports_key_identity(client: TestClient, ops_headers: dict[str, str]) -> None:
    """密钥通道查自己：外部调用方据此验证身份与权限。"""
    r = client.get("/api/v1/users/me", headers=ops_headers)

    assert r.status_code == 200
    body = r.json()
    assert body["scopes"] == ["sessions:write", "devices:photo"]
    assert body["device_scope"] == ["*"]
    assert body["key_name"] == "测试密钥"


def test_disabled_user_keys_fail_immediately(client: TestClient, admin_headers: dict[str, str]) -> None:
    """禁用用户：其密钥下一次请求即 401（守卫每请求查库，无缓存可绕）。"""
    created = client.post("/api/v1/users", headers=admin_headers, json={"username": "suspend-me-001"})
    username = created.json()["username"]
    token = client.post(
        f"/api/v1/users/{username}/keys",
        headers=admin_headers,
        json={"name": "k", "scopes": ["sessions:write"]},
    ).json()["token"]
    assert client.get("/api/v1/devices", headers={"Authorization": f"Bearer {token}"}).status_code == 200

    assert client.put(f"/api/v1/users/{username}", headers=admin_headers, json={"disabled": True}).status_code == 200
    assert client.get("/api/v1/devices", headers={"Authorization": f"Bearer {token}"}).status_code == 401
