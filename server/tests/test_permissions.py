"""权限字典与端点映射的唯一对应：漂移在这里当场暴露。

判据来自 app.openapi()（外部调用方拿到的契约就是它）：每个操作的 security
scopes 必须出自字典；字典里每个 scope 必须至少有一个端点在用——加了权限忘了
接端点、端点换了权限没改字典，都算漂移。公开面不出现 security 要求。
"""

from __future__ import annotations

from app.auth.permissions import ALL_SCOPES, SCOPE_ACTIONS, SCOPE_LABELS, Scope, allowed_actions, scopes_satisfied

#: 已知公开面（无 security 要求）：设备硬编码契约与 security.md 既定的查看类。
PUBLIC_OPERATIONS: tuple[tuple[str, str], ...] = (
    ("/api/v1/devices", "get"),
    ("/api/v1/doors", "get"),
    ("/api/v1/ota/releases", "get"),
    ("/ota/manifest", "get"),
    ("/api/v1/auth/login", "post"),
    ("/api/v1/auth/logout", "post"),
    ("/healthz", "get"),
)


def _operation_security(spec: dict, path: str, method: str) -> list[str]:
    """一个操作的 security scopes（跨方案合并）；无要求即空表。"""
    op = spec["paths"].get(path, {}).get(method)
    assert op is not None, f"{method} {path} 不在契约里"
    return [scope for scheme in (op.get("security") or []) for scopes in scheme.values() for scope in scopes]


def test_every_required_scope_comes_from_the_dictionary(openapi_spec: dict) -> None:
    for path, operations in openapi_spec["paths"].items():
        for method in operations:
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            unknown = set(_operation_security(openapi_spec, path, method)) - ALL_SCOPES
            assert not unknown, f"{method} {path} 要求了字典里没有的权限：{unknown}"


def test_every_scope_is_used_by_at_least_one_endpoint(openapi_spec: dict) -> None:
    used: set[str] = set()
    for path, operations in openapi_spec["paths"].items():
        for method in operations:
            if method in {"get", "post", "put", "patch", "delete"}:
                used.update(_operation_security(openapi_spec, path, method))
    unused = ALL_SCOPES - used - {Scope.WILDCARD}
    assert not unused, f"字典里这些权限没有任何端点在用（死权限=漂移）：{sorted(unused)}"


def test_public_surface_declares_no_security(openapi_spec: dict) -> None:
    for path, method in PUBLIC_OPERATIONS:
        assert _operation_security(openapi_spec, path, method) == [], f"{method} {path} 应为公开面"


def test_spot_mappings_are_pinned(openapi_spec: dict) -> None:
    """关键映射抽查：外部业务入口、设备管理、配置、审计、用户域。"""
    assert _operation_security(openapi_spec, "/api/v1/devices/{device_id}/assign", "post") == ["sessions:write"]
    assert _operation_security(openapi_spec, "/api/v1/devices/{device_id}/exec", "post") == ["devices:write"]
    assert _operation_security(openapi_spec, "/api/v1/config", "put") == ["config:write"]
    assert _operation_security(openapi_spec, "/api/v1/audit/calls", "get") == ["audit:read"]
    assert _operation_security(openapi_spec, "/api/v1/users", "post") == ["users:write"]
    assert _operation_security(openapi_spec, "/api/v1/ota/releases", "post") == ["ota:write"]
    assert _operation_security(openapi_spec, "/api/v1/jobs/status", "get") == ["jobs:read"]


def test_openapi_scheme_carries_the_dictionary(openapi_spec: dict) -> None:
    """OpenAPI 的 oauth2 scheme 带全部 scope 与中文说明——外部对接文档的唯一来源。"""
    schemes = openapi_spec["components"]["securitySchemes"]
    assert len(schemes) == 1
    scheme = next(iter(schemes.values()))
    assert set(scheme["flows"]["password"]["scopes"]) == ALL_SCOPES


def test_user_actions_follow_scope_matrix() -> None:
    """actions 字段的唯一来源：sessions:write→assign/settle，devices:photo→photo。"""
    assert allowed_actions(["sessions:write"]) == ["assign", "settle"]
    assert allowed_actions(["devices:photo"]) == ["photo"]
    assert allowed_actions(["devices:write"]) == []
    assert allowed_actions(["sessions:write", "devices:photo"]) == ["assign", "settle", "photo"]
    assert allowed_actions(["*"]) == ["assign", "settle", "photo"]


def test_wildcard_grants_everything() -> None:
    assert scopes_satisfied(["*"], ["sessions:write", "audit:read"]) is True
    assert scopes_satisfied(["sessions:write"], ["sessions:write"]) is True
    assert scopes_satisfied(["sessions:write"], ["sessions:write", "audit:read"]) is False
    assert scopes_satisfied([], []) is True


def test_labels_are_complete() -> None:
    """每个 scope 都有中文说明：密钥管理页表单没有空格子。"""
    assert set(SCOPE_LABELS) == ALL_SCOPES
    assert all(SCOPE_LABELS.values())
    assert set(SCOPE_ACTIONS) <= ALL_SCOPES
