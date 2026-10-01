"""接口调用审计：写入、关联、查询与保留期清理。"""

import json

from fastapi.testclient import TestClient

from app.core import audit

OPS_HEADERS = {"X-Ops-Token": "test-token"}
GHOST_ID = "dev-audit-ghost"


def _calls(client: TestClient, **params: str) -> list[dict]:
    r = client.get("/ops/audit", headers=OPS_HEADERS, params=params or None)
    assert r.status_code == 200, r.text
    return r.json()["calls"]


def test_ops_call_is_audited_with_masked_actor(client: TestClient) -> None:
    r = client.get("/ops/devices", headers=OPS_HEADERS)
    assert r.status_code == 200

    calls = _calls(client, actor_kind="ops")
    assert len(calls) == 1
    row = calls[0]
    assert row["method"] == "GET"
    assert row["path"] == "/ops/devices"
    assert row["status_code"] == 200
    assert row["actor_kind"] == "ops"
    # 不脱敏：调用方令牌与来源如实落库（2026-10-01 用户决定）
    assert row["actor"] == "test-token"
    assert row["duration_ms"] >= 0


def test_unauthorized_call_is_audited(client: TestClient) -> None:
    """认证失败必须留痕（OWASP：认证事件是必记项）。无令牌是匿名、错令牌是 ops。"""
    assert client.get("/ops/devices").status_code == 401
    assert client.get("/ops/devices", headers={"X-Ops-Token": "wrong"}).status_code == 401

    rows = _calls(client, limit=500)
    failed = [row for row in rows if row["status_code"] == 401]
    assert len(failed) == 2
    kinds = {row["actor_kind"] for row in failed}
    assert kinds == {"anonymous", "ops"}
    # 错的令牌也原文落库：审计的价值就在于如实（2026-10-01 不脱敏决定）
    ops_row = next(row for row in failed if row["actor_kind"] == "ops")
    assert ops_row["actor"] == "wrong"


def test_device_call_records_device_problem_and_body(client: TestClient) -> None:
    r = client.post(f"/ops/devices/{GHOST_ID}/exec", headers=OPS_HEADERS, json={"command": "id"})
    assert r.status_code == 404

    row = _calls(client)[0]
    assert row["device_id"] == GHOST_ID
    assert row["status_code"] == 404
    assert row["problem_type"]
    # 请求体原文落库不截断：发了什么命令审计里就是什么（语义比较，不依赖序列化空格）
    assert json.loads(row["request_body"]) == {"command": "id"}


def test_validation_failure_records_problem_type_and_detail(client: TestClient) -> None:
    """失败行的 detail 是给审计人看的中文答案——只有 type 代号等于没记。"""
    r = client.post("/ops/devices/dev-audit-1/input", headers=OPS_HEADERS, json={"text": "hi"})
    assert r.status_code == 422

    row = _calls(client)[0]
    assert row["status_code"] == 422
    assert row["problem_type"] == "validation-failed"
    assert row["detail"]


def test_query_string_is_recorded(client: TestClient) -> None:
    r = client.get("/ops/devices", headers=OPS_HEADERS, params={"limit": 7})
    assert r.status_code == 200

    row = _calls(client)[0]
    assert "limit=7" in row["query"]


def test_traceparent_is_honored_and_request_id_returned(client: TestClient) -> None:
    """W3C Trace Context：调用方带 traceparent 就沿用其 trace-id；响应回 X-Request-ID。"""
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    headers = {**OPS_HEADERS, "traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"}
    r = client.get("/ops/devices", headers=headers)
    assert r.status_code == 200
    request_id = r.headers["X-Request-ID"]
    assert request_id

    row = _calls(client)[0]
    assert row["trace_id"] == trace_id
    assert row["request_id"] == request_id


def test_invalid_traceparent_falls_back_to_generated(client: TestClient) -> None:
    r = client.get("/ops/devices", headers={**OPS_HEADERS, "traceparent": "garbage"})
    assert r.status_code == 200

    row = _calls(client)[0]
    assert len(row["trace_id"]) == 32


def test_static_and_health_are_not_audited(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.get("/ops/devices", headers=OPS_HEADERS).status_code == 200

    # 只有 API 前缀进审计表：healthz 与静态资源是无安全语义的噪音
    calls = _calls(client, limit=500)
    assert [row["path"] for row in calls if row["path"] == "/healthz"] == []


def test_gateway_lifecycle_rows_use_gateway_kind(client: TestClient) -> None:
    """网关行 kind=gateway、path 干净（事件语义在 kind/status，不塞进 path）。"""
    from app.core.audit import record_gateway

    record_gateway("dev-x", "connect")
    record_gateway("dev-x", "disconnect")

    rows = _calls(client, kind="gateway")
    # 倒序：最新在前——断开行(1)先于连接行(0)
    assert [row["status_code"] for row in rows] == [1, 0]
    assert all(row["path"] == "/gateway/device" for row in rows)
    assert all(row["actor_kind"] == "device" for row in rows)


def test_audit_query_filters(client: TestClient) -> None:
    assert client.get("/ops/devices", headers=OPS_HEADERS).status_code == 200
    assert client.get("/ops/audit", headers=OPS_HEADERS).status_code == 200

    by_kind = _calls(client, actor_kind="ops")
    assert all(row["actor_kind"] == "ops" for row in by_kind)

    by_device = _calls(client, device_id="dev-audit-1")
    assert all(row["device_id"] == "dev-audit-1" for row in by_device)


def test_prune_removes_only_expired_rows(client: TestClient) -> None:
    """NIST SP 800-92：保留期之外的可删，之内的必须留。"""
    assert client.get("/ops/devices", headers=OPS_HEADERS).status_code == 200
    assert audit.prune(retention_days=90) == 0
    assert len(_calls(client, limit=500)) >= 1

    # requested_at 直接改老：模拟 90 天前的一行
    from app.core.db import get_conn

    get_conn().execute(
        "UPDATE api_call SET requested_at = datetime('now', '-91 days') WHERE id = (SELECT MIN(id) FROM api_call)"
    )
    get_conn().commit()
    assert audit.prune(retention_days=90) == 1
    assert audit.prune(retention_days=90) == 0
