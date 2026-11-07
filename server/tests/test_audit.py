"""审计域：/audit/calls 与 /audit/commands 只读出口、actor 原文、体入库规则与 device_id 来源。

api_call 的写入面由 core/audit 的路径前缀决定，前缀必须覆盖统一资源树全部域
（跨域钉子测试保证新增域漏登时立刻失败）；device_id 的取值规则（路径段或
query）与数据重型端点的响应排除用纯函数直接钉住。
"""

import io
import json

from fastapi.testclient import TestClient
from tests import apk, frames

from app.core import audit
from app.modules.audit import repository as command_audit


def _calls(client: TestClient, admin_headers: dict[str, str], **params: object) -> list[dict[str, object]]:
    """取 /audit/calls 裸数组（admin 专属）。

    审计行在响应生成之后写入，因此结果里不含本次查询自身，rows[0] 即上一个请求。
    """
    r = client.get("/api/v1/audit/calls", headers=admin_headers, params=params or None)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    return body


def test_every_domain_is_audited(client: TestClient, admin_headers: dict[str, str]) -> None:
    """资源树每个域的调用都必须留痕（含 401/403）：漏登一个前缀即在此失败。"""
    probes = [
        ("/api/v1/devices", None),
        ("/api/v1/doors", None),
        ("/api/v1/config", None),
        ("/api/v1/identities", None),
        ("/api/v1/ota/releases", None),
        ("/api/v1/audit/commands", None),
    ]
    for path, _headers in probes:
        # 每个探测都不带身份：401 也必须留痕（OWASP 要求认证失败必记）
        client.get(path)
    audited = _calls(client, admin_headers, limit=500)
    recorded_paths = {str(row["path"]) for row in audited}
    for path, _headers in probes:
        assert path in recorded_paths, f"域 {path} 的调用没有审计行"


def test_audit_surface_requires_admin(client: TestClient, ops_headers: dict[str, str]) -> None:
    """审计域 admin 专属：无身份 401，非 admin 403。"""
    assert client.get("/api/v1/audit/calls").status_code == 401
    assert client.get("/api/v1/audit/commands").status_code == 401
    assert client.get("/api/v1/audit/calls", headers=ops_headers).status_code == 403


def test_identity_call_records_raw_actor(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.get("/api/v1/ota/releases", headers=admin_headers)
    assert r.status_code == 200

    row = _calls(client, admin_headers)[0]
    assert row["method"] == "GET"
    assert row["path"] == "/api/v1/ota/releases"
    assert row["status_code"] == 200
    assert row["actor_kind"] == "token"
    # 只记凭据摘要前 12 位：足够对账，不足以冒用；明文身份与凭据都不进审计
    assert len(row["actor"]) == 12
    assert row["actor"] != "admin-identity-0001"
    assert row["duration_ms"] >= 0


def test_anonymous_call_recorded_without_identity(client: TestClient, admin_headers: dict[str, str]) -> None:
    """未带标识记 anonymous：actor 为空串（OWASP：匿名访问同样留痕）。"""
    assert client.get("/api/v1/ota/releases").status_code == 200

    row = _calls(client, admin_headers)[0]
    assert row["actor_kind"] == "anonymous"
    assert row["actor"] == ""


def test_successful_response_body_is_stored(client: TestClient, admin_headers: dict[str, str]) -> None:
    """JSON 响应体原文入库。"""
    assert client.get("/api/v1/ota/releases", headers=admin_headers).status_code == 200

    row = _calls(client, admin_headers)[0]
    assert isinstance(json.loads(str(row["response_body"])), list)


def test_multipart_request_body_is_not_stored(client: TestClient, admin_headers: dict[str, str]) -> None:
    """请求体只对 JSON 形态落库：multipart 本体不进审计行。"""
    r = client.post(
        "/api/v1/ota/releases",
        headers=admin_headers,
        data={"version": "0.1.0", "version_code": 1},
        files={"file": ("x.apk", io.BytesIO(apk.build_apk("0.1.0", 1)), "application/octet-stream")},
    )
    assert r.status_code == 200

    row = _calls(client, admin_headers)[0]
    assert row["request_body"] is None


def test_problem_response_records_type_detail_and_body(client: TestClient, admin_headers: dict[str, str]) -> None:
    """problem 响应带 problem_type 与 detail，响应体同样入库。"""
    r = client.get("/api/v1/ota/releases/9.9.9/download", headers=admin_headers)
    assert r.status_code == 404

    row = _calls(client, admin_headers)[0]
    assert row["problem_type"] == "release-not-found"
    assert row["detail"]
    assert json.loads(str(row["response_body"]))["type"].endswith("/problems/release-not-found")


def test_query_string_is_recorded(client: TestClient, admin_headers: dict[str, str]) -> None:
    client.get("/ota/manifest", headers=admin_headers, params={"device_id": "dev-q", "version_code": 6})

    row = _calls(client, admin_headers)[0]
    assert "device_id=dev-q" in str(row["query"])


def test_traceparent_is_honored_and_request_id_returned(client: TestClient, admin_headers: dict[str, str]) -> None:
    """W3C Trace Context：调用方带合法 traceparent 就沿用其 trace-id；响应回 X-Request-ID。"""
    trace_id = "4bf92f3577b34da6a3ce929d0e0e4736"
    headers = {**admin_headers, "traceparent": f"00-{trace_id}-00f067aa0ba902b7-01"}
    r = client.get("/api/v1/ota/releases", headers=headers)
    assert r.status_code == 200
    request_id = r.headers["X-Request-ID"]
    assert request_id

    row = _calls(client, admin_headers)[0]
    assert row["trace_id"] == trace_id
    assert row["request_id"] == request_id


def test_invalid_traceparent_falls_back_to_generated(client: TestClient, admin_headers: dict[str, str]) -> None:
    client.get("/api/v1/ota/releases", headers={**admin_headers, "traceparent": "garbage"})

    row = _calls(client, admin_headers)[0]
    assert len(str(row["trace_id"])) == 32


def test_gateway_lifecycle_is_recorded(client: TestClient, admin_headers: dict[str, str]) -> None:
    """设备网关接入记一行 connect（kind=gateway，status 0=连接 1=断开），可按类别过滤。"""
    device_id = "dev-gw-audit"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame(device_id))
        frames.recv_until(ws, "link.hello_ack")

    rows = _calls(client, admin_headers, kind="gateway")
    connect_rows = [row for row in rows if row["device_id"] == device_id and row["status_code"] == 0]
    assert connect_rows
    assert all(row["actor_kind"] == "device" for row in rows)

    # 断开行由网关在连接收尾时写入；测试客户端的断开会取消网关协程，直接验证写入路径
    audit.record_gateway(device_id, "disconnect")
    rows = _calls(client, admin_headers, kind="gateway")
    disconnect_rows = [row for row in rows if row["device_id"] == device_id and row["status_code"] == 1]
    assert disconnect_rows


def test_calls_filters_by_actor_kind_and_device(client: TestClient, admin_headers: dict[str, str]) -> None:
    """查询参数按列过滤：device_id 与 actor_kind。"""
    client.get("/ota/manifest", headers=admin_headers, params={"device_id": "dev-filter-1", "version_code": 6})

    by_device = _calls(client, admin_headers, device_id="dev-filter-1")
    assert by_device
    assert all(row["device_id"] == "dev-filter-1" for row in by_device)

    by_actor = _calls(client, admin_headers, actor_kind="token", limit=1)
    assert by_actor
    assert all(row["actor_kind"] == "token" for row in by_actor)


def test_commands_lists_command_audit_rows(client: TestClient, admin_headers: dict[str, str]) -> None:
    """GET /audit/commands 裸数组：命令流水，可按设备过滤。"""
    command_audit.insert_command("cmd-audit-1", "dev-audit-cmd", "session.assign")

    rows = client.get("/api/v1/audit/commands", headers=admin_headers, params={"device_id": "dev-audit-cmd"}).json()
    assert isinstance(rows, list)
    assert [row["command_id"] for row in rows] == ["cmd-audit-1"]
    assert rows[0]["device_id"] == "dev-audit-cmd"


def test_device_of_resolves_path_segment_then_query() -> None:
    """审计行的 device_id 只来自路径段或 query：路径优先，都没有为空。"""
    assert audit._device_of("/api/v1/devices/dev-1/exec", "") == "dev-1"
    assert audit._device_of("/api/v1/devices/dev-1/exec", "x=1") == "dev-1"
    assert audit._device_of("/api/v1/devices/dev-1/exec", "device_id=dev-from-query") == "dev-1"  # 路径优先
    assert audit._device_of("/ota/manifest", "device_id=dev-from-query") == "dev-from-query"
    assert audit._device_of("/api/v1/config", "") == ""
    assert audit._device_of("/api/v1/devices", "") == ""


def test_data_heavy_paths_are_excluded_from_response_storage() -> None:
    """截图、拍照、UI 节点树端点的响应不落库（数据重型且可重取）。"""
    assert audit._DATA_HEAVY_PATH.search("/api/v1/devices/dev-1/capture")
    assert audit._DATA_HEAVY_PATH.search("/api/v1/devices/dev-1/dump")
    assert audit._DATA_HEAVY_PATH.search("/api/v1/devices/dev-1/photo")
    assert audit._DATA_HEAVY_PATH.search("/api/v1/devices/dev-1/exec") is None
    assert audit._DATA_HEAVY_PATH.search("/ota/manifest") is None


def test_prune_removes_only_expired_rows(client: TestClient, admin_headers: dict[str, str]) -> None:
    """保留期之内必须留，之外的可删（NIST SP 800-92）；清理幂等。"""
    assert client.get("/api/v1/ota/releases", headers=admin_headers).status_code == 200
    assert audit.prune(retention_days=90) == 0
    assert len(_calls(client, admin_headers, limit=500)) == 1

    from app.core.db import get_conn

    conn = get_conn()
    conn.execute(
        "UPDATE api_call SET requested_at = datetime('now', '-91 days') WHERE id = (SELECT MIN(id) FROM api_call)"
    )
    conn.commit()

    assert audit.prune(retention_days=90) == 1
    assert audit.prune(retention_days=90) == 0


def test_store_body_replaces_oversize_payload() -> None:
    """超限的体以占位说明入库，绝不截断；限内原文返回。"""
    body = "x" * (audit.BODY_MAX_BYTES + 1)

    assert audit.store_body("compact") == "compact"
    assert audit.store_body(body).startswith("[JSON 体")
