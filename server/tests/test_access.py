"""统一身份制：X-Identity 守卫、能力位矩阵、设备作用域与名单维护。

守卫唯一实现是 access.dependencies：未带标识 401（unauthenticated）、标识不在
名单 401（unknown-identity）、缺能力位或设备不在作用域 403（capability-denied）。
"""

import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core.problems import CAPABILITY_DENIED, ApiProblemError
from app.modules.access import service
from app.modules.access.capabilities import ALL_CAPABILITIES, DEVICE_SCOPED, Capability, user_actions
from app.modules.access.dependencies import require_capability
from app.modules.access.identity import IdentityKind
from app.modules.access.schemas import IdentityRecord


def _identity(capabilities: list[str], device_scope: list[str] | None = None) -> IdentityRecord:
    """一份身份记录，只为单测守卫；字段值与判定无关。"""
    return {
        "identity": "unit-test-identity",
        "kind": IdentityKind.WEB,
        "capabilities": capabilities,
        "device_scope": ["*"] if device_scope is None else device_scope,
        "label": "",
        "created_at": "2026-01-01T00:00:00.000Z",
    }


def test_require_capability_rejects_device_scoped_capabilities() -> None:
    """拿它去判有设备维度的能力必须当场报错。

    它没有作用域这一维，拿它判 `session` 等于绕开作用域——而作用域正是这套判定存在的
    理由之一。报错让误用者第一个请求就炸，而不是静默放开一个洞。
    """
    caller = _identity([cap.value for cap in Capability])

    for capability in DEVICE_SCOPED:
        with pytest.raises(ValueError, match="有设备维度"):
            require_capability(caller, capability)


def test_require_capability_denies_without_the_capability() -> None:
    """没有这个位就是 403 capability-denied，与 enforce 同一形状。"""
    with pytest.raises(ApiProblemError) as excinfo:
        require_capability(_identity(["session"]), Capability.TASKS)

    assert excinfo.value.problem is CAPABILITY_DENIED


def test_require_capability_passes_with_the_capability() -> None:
    """有这个位就放行，**且完全不看设备作用域**——作用域为空照样通过。

    这就是"没有设备维度"与"作用域为 `*`"的分野：名单是 SERVER scope、不分设备，
    "对哪些设备做"这一问对它不成立，所以作用域填什么都不该影响判定。
    """
    require_capability(_identity(["tasks"], device_scope=[]), Capability.TASKS)


def test_admin_surface_without_identity_is_unauthenticated(client: TestClient) -> None:
    r = client.get("/api/v1/identities")

    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/unauthenticated")


def test_unknown_identity_is_unauthorized(client: TestClient) -> None:
    r = client.get("/api/v1/identities", headers={"X-Identity": "not-in-any-list"})

    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/unknown-identity")


def test_admin_surface_requires_admin_capability(client: TestClient, ops_headers: dict[str, str]) -> None:
    """管理面要求 admin：有身份但缺该能力位 403（capability-denied）。"""
    r = client.get("/api/v1/identities", headers=ops_headers)

    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/capability-denied")


def test_identities_returns_domain_read_model(client: TestClient, admin_headers: dict[str, str]) -> None:
    """GET /identities 返回域读模型：能力位枚举与名单，一次装载。"""
    r = client.get("/api/v1/identities", headers=admin_headers)

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"capabilities", "identities"}
    caps = {item["key"]: item["label"] for item in body["capabilities"]}
    assert set(caps) == set(ALL_CAPABILITIES) == {"session", "photo", "admin", "tasks", "stats"}
    assert all(caps.values())
    admin = next(item for item in body["identities"] if item["identity"] == "admin-identity-0001")
    assert admin["kind"] == "web"
    assert "admin" in admin["capabilities"]
    assert admin["device_scope"] == ["*"]


def test_removed_endpoints_are_gone(client: TestClient, admin_headers: dict[str, str]) -> None:
    """GET /me、GET /capabilities 与旧前缀已随统一资源树删除：一律 404。"""
    for path in ("/me", "/capabilities", "/api/v1/identity", "/ops/identities"):
        r = client.get(path, headers=admin_headers)
        assert r.status_code == 404, path
        assert r.json()["type"].endswith("/problems/http-404"), path


def test_user_actions_follow_capability_matrix() -> None:
    """actions 字段的唯一来源：session→assign/settle，photo→photo，admin 不映射用户动作。"""
    assert user_actions(["session"]) == ["assign", "settle"]
    assert user_actions(["photo"]) == ["photo"]
    assert user_actions(["admin"]) == []
    assert user_actions(["session", "photo"]) == ["assign", "settle", "photo"]
    assert user_actions(["admin", "session", "photo"]) == ["assign", "settle", "photo"]


def test_assign_requires_session_capability(client: TestClient, fresh_db: str) -> None:
    """只有 photo 能力位的身份对分配被拒（capability-denied，先于设备存在性检查）。"""
    from app.modules.access import repository
    from app.modules.access.identity import IdentityKind

    repository.put_identity("photo-only-0001", IdentityKind.WEB, ["photo"], "只拍照", device_scope=["*"])

    r = client.post(
        "/api/v1/devices/dev-any-0001/assign",
        json={"door_no": 1, "cert_no": "C", "password": "P"},
        headers={"X-Identity": "photo-only-0001"},
    )

    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/capability-denied")


def test_photo_requires_photo_capability(client: TestClient, fresh_db: str) -> None:
    """只有 session 能力位的身份对拍照被拒。"""
    from app.modules.access import repository
    from app.modules.access.identity import IdentityKind

    repository.put_identity("session-only-001", IdentityKind.WEB, ["session"], "只分配", device_scope=["*"])

    r = client.post("/api/v1/devices/dev-any-0001/photo", headers={"X-Identity": "session-only-001"})

    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/capability-denied")


def test_wildcard_scope_passes_guard_for_any_device(
    client: TestClient,
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """作用域 ["*"] 的身份对任何设备放行：请求穿过全部守卫，落到喷头预检。"""
    from app.core.connections import manager

    helpers.register_trusted_device("dev-any-0001")
    monkeypatch.setattr(manager, "_connections", {"dev-any-0001": object()})
    from app.modules.access import repository
    from app.modules.access.identity import IdentityKind

    repository.put_identity("wildcard-000001", IdentityKind.WEB, ["session"], "通配", device_scope=["*"])

    r = client.post(
        "/api/v1/devices/dev-any-0001/assign",
        json={"door_no": 1, "cert_no": "C", "password": "P"},
        headers={"X-Identity": "wildcard-000001"},
    )

    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/door-not-found")


def test_list_scope_blocks_device_outside_list(client: TestClient, fresh_db: str) -> None:
    """作用域限定具体设备后，对列表外设备的操作被拒。"""
    from app.modules.access import repository
    from app.modules.access.identity import IdentityKind

    repository.put_identity(
        "scoped-0000001", IdentityKind.WEB, ["session"], "限定设备", device_scope=["dev-allow-0001"]
    )

    r = client.post(
        "/api/v1/devices/dev-other-002/assign",
        json={"door_no": 1, "cert_no": "C", "password": "P"},
        headers={"X-Identity": "scoped-0000001"},
    )

    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/capability-denied")


def test_unknown_device_in_scope_is_rejected_on_create_and_edit(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """作用域里的 device_id 必须真实存在：写错身份永远执行不了。"""
    created = client.post(
        "/api/v1/identities",
        headers=admin_headers,
        json={"kind": "web", "capabilities": ["session"], "device_scope": ["no-such-device-01"], "label": "x"},
    )
    assert created.status_code == 422

    seeded = client.post(
        "/api/v1/identities",
        headers=admin_headers,
        json={"kind": "web", "capabilities": ["session"], "device_scope": ["*"], "label": "占位"},
    )
    identity = seeded.json()["identity"]
    edited = client.put(
        f"/api/v1/identities/{identity}",
        headers=admin_headers,
        json={"kind": "web", "capabilities": ["session"], "device_scope": ["no-such-device-01"]},
    )
    assert edited.status_code == 422


def test_put_identity_edit_takes_effect_immediately(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUT 编辑热生效：守卫每个请求实时查库，无缓存；编辑不改变标识本身。"""
    from app.core.connections import manager

    helpers.register_trusted_device("dev-edit-0001")
    monkeypatch.setattr(manager, "_connections", {"dev-edit-0001": object()})

    created = client.post(
        "/api/v1/identities",
        headers=admin_headers,
        json={
            "kind": "web",
            "capabilities": ["session"],
            "device_scope": ["dev-edit-0001"],
            "label": "初始",
        },
    )
    identity = created.json()["identity"]
    payload = {"door_no": 1, "cert_no": "C", "password": "P"}
    before = client.post("/api/v1/devices/dev-edit-0001/assign", json=payload, headers={"X-Identity": identity})
    assert before.status_code == 404  # 穿过身份与作用域守卫，到达喷头预检

    updated = client.put(
        f"/api/v1/identities/{identity}",
        headers=admin_headers,
        json={"kind": "web", "capabilities": ["photo"], "device_scope": ["*"], "label": "改后"},
    )
    assert updated.status_code == 200
    # 编辑沿用原标识：不得生成新标识另起一行
    assert updated.json()["identity"] == identity
    assert len(service.list_identities()) == 2  # 管理员身份 + 该用户

    after = client.post("/api/v1/devices/dev-edit-0001/assign", json=payload, headers={"X-Identity": identity})
    assert after.status_code == 403
    assert after.json()["type"].endswith("/problems/capability-denied")


def test_delete_identity_revokes_immediately(client: TestClient, admin_headers: dict[str, str]) -> None:
    """DELETE 即失权：被撤销的标识下一次请求立即 401；删除幂等。"""
    created = client.post(
        "/api/v1/identities", headers=admin_headers, json={"kind": "web", "capabilities": ["session"], "label": "临时"}
    )
    identity = created.json()["identity"]
    assert client.get("/api/v1/devices", headers={"X-Identity": identity}).status_code == 200

    assert client.delete(f"/api/v1/identities/{identity}", headers=admin_headers).status_code == 204
    assert client.delete(f"/api/v1/identities/{identity}", headers=admin_headers).status_code == 204  # 幂等
    assert client.get("/api/v1/devices", headers={"X-Identity": identity}).status_code == 401


def test_unknown_capability_name_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.post(
        "/api/v1/identities",
        headers=admin_headers,
        json={"kind": "openid", "openid": "openid-abcdefgh", "capabilities": ["ops", "delete-everything"]},
    )

    assert r.status_code == 422
    assert r.json()["type"].endswith("/problems/unknown-capability")


def test_empty_capability_list_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    """一个能力都不给与"未授权"没有区别，登记时即拒绝。"""
    r = client.post(
        "/api/v1/identities",
        headers=admin_headers,
        json={"kind": "openid", "openid": "openid-abcdefgh", "capabilities": []},
    )

    assert r.status_code == 422


def test_openid_kind_requires_openid(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.post(
        "/api/v1/identities",
        headers=admin_headers,
        json={"kind": "openid", "capabilities": ["session"], "openid": ""},
    )

    assert r.status_code == 422
    assert r.json()["type"].endswith("/problems/validation-failed")


def test_unknown_kind_is_rejected(client: TestClient, admin_headers: dict[str, str]) -> None:
    """kind 是闭集：来源只有 openid 与 web 两种。"""
    r = client.post("/api/v1/identities", headers=admin_headers, json={"kind": "bogus", "capabilities": ["session"]})

    assert r.status_code == 422


def test_web_identity_is_issued_once_and_usable(client: TestClient, admin_headers: dict[str, str]) -> None:
    """kind=web 由服务端签发不可枚举的标识，签发后随即可用。"""
    r = client.post(
        "/api/v1/identities",
        headers=admin_headers,
        json={"kind": "web", "label": "给张三的网页入口", "capabilities": ["session"]},
    )

    assert r.status_code == 200
    identity = r.json()["identity"]
    assert identity
    found = service.resolve_identity(identity)
    assert found is not None
    assert found["kind"] == "web"
    assert found["label"] == "给张三的网页入口"
    assert client.get("/api/v1/devices", headers={"X-Identity": identity}).status_code == 200


def test_list_shows_both_kinds(client: TestClient, admin_headers: dict[str, str]) -> None:
    """名单同时呈现小程序用户与网页标识。"""
    client.post(
        "/api/v1/identities",
        headers=admin_headers,
        json={"kind": "openid", "openid": "openid-list000001", "capabilities": ["session"]},
    )
    client.post(
        "/api/v1/identities",
        headers=admin_headers,
        json={"kind": "web", "label": "网页入口", "capabilities": ["photo"]},
    )

    kinds = {item["kind"] for item in client.get("/api/v1/identities", headers=admin_headers).json()["identities"]}

    assert kinds == {"openid", "web"}


def test_repeated_registration_overwrites_single_row(client: TestClient, admin_headers: dict[str, str]) -> None:
    """重复登记同一标识等于整体更新，不产生第二行。"""
    openid = "openid-update001"
    client.post(
        "/api/v1/identities",
        headers=admin_headers,
        json={"kind": "openid", "openid": openid, "capabilities": ["session"], "device_scope": ["*"]},
    )
    client.post(
        "/api/v1/identities",
        headers=admin_headers,
        json={"kind": "openid", "openid": openid, "capabilities": ["photo"], "device_scope": ["*"]},
    )

    found = service.resolve_identity(openid)
    assert found is not None
    assert found["capabilities"] == ["photo"]
    assert len(service.list_identities()) == 2  # 管理员身份 + 该用户


def test_resolve_unknown_identity_is_none(fresh_db: str) -> None:
    assert service.resolve_identity("nobody") is None
