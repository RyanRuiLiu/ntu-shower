"""设备域 API：精简清单与 actions、运维档案、档案 PATCH 与远程动作端点。"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests import device_link, frames

from app.core import actions, db
from app.core.clock import now_rfc3339
from app.core.connections import manager
from app.modules.sessions import service as sessions_service

DEVICE_ID = "dev-debug-1"


def _put_identity(device_id: str, identity: str, capabilities: list[str], scope: list[str]) -> dict[str, str]:
    """登记一个身份并返回其请求头。"""
    from app.modules.access import repository
    from app.modules.access.identity import IdentityKind

    repository.put_identity(identity, IdentityKind.WEB, capabilities, "测试", device_scope=scope)
    return {"X-Identity": identity}


def test_device_list_is_public_and_anonymous_actions_are_empty(client: TestClient, fresh_db: str) -> None:
    """精简清单公开；匿名调用 actions 恒为空数组，但字段仍在。"""
    device_link.register_trusted_device(DEVICE_ID)

    r = client.get("/api/v1/devices")

    assert r.status_code == 200
    listed = next(item for item in r.json() if item["device_id"] == DEVICE_ID)
    assert set(listed) == {"device_id", "name", "online", "actions"}
    assert listed["online"] is False
    assert listed["actions"] == []


def test_device_list_actions_follow_capabilities(client: TestClient, admin_headers: dict[str, str]) -> None:
    """带身份时 actions 按 USER_ACTIONS 算：session→assign/settle，photo→photo。"""
    device_link.register_trusted_device(DEVICE_ID)

    r = client.get("/api/v1/devices", headers=admin_headers)

    listed = next(item for item in r.json() if item["device_id"] == DEVICE_ID)
    assert listed["actions"] == ["assign", "settle", "photo"]


def test_device_list_actions_empty_outside_scope(client: TestClient, fresh_db: str) -> None:
    """作用域列表外的设备 actions 恒为空：作用域裁剪发生在服务端。"""
    device_link.register_trusted_device("dev-in-scope-1")
    device_link.register_trusted_device("dev-out-scope-1")
    headers = _put_identity("dev-in-scope-1", "scoped-dev-0001", ["session", "photo"], ["dev-in-scope-1"])

    items = {item["device_id"]: item for item in client.get("/api/v1/devices", headers=headers).json()}

    assert items["dev-in-scope-1"]["actions"] == ["assign", "settle", "photo"]
    assert items["dev-out-scope-1"]["actions"] == []


def test_device_list_unknown_identity_is_rejected(client: TestClient) -> None:
    """公开端点带了不在名单的标识同样 401：视图按调用方裁剪，先要认识调用方。"""
    r = client.get("/api/v1/devices", headers={"X-Identity": "no-such-identity"})

    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/unknown-identity")


def test_device_list_reports_online_flag(
    client: TestClient,
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """online 来自内存连接表而非档案表。"""
    device_link.register_trusted_device(DEVICE_ID)
    monkeypatch.setattr(manager, "_connections", {DEVICE_ID: object()})

    devices = client.get("/api/v1/devices").json()
    listed = next(item for item in devices if item["device_id"] == DEVICE_ID)
    assert listed["online"] is True


def test_runtime_requires_admin(client: TestClient, ops_headers: dict[str, str]) -> None:
    """运维档案 admin 专属：无身份 401，非 admin 403 capability-denied。"""
    assert client.get("/api/v1/devices/runtime").status_code == 401

    r = client.get("/api/v1/devices/runtime", headers=ops_headers)
    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/capability-denied")


def test_runtime_returns_full_record(client: TestClient, admin_headers: dict[str, str]) -> None:
    """GET /devices/runtime 返回完整档案：版本、信任、IP、钟偏差、在线都在。"""
    device_link.register_trusted_device(DEVICE_ID)

    r = client.get("/api/v1/devices/runtime", headers=admin_headers)

    assert r.status_code == 200
    record = next(item for item in r.json() if item["device_id"] == DEVICE_ID)
    assert set(record) == {
        "device_id",
        "name",
        "trusted",
        "app_version",
        "version_code",
        "os_version",
        "capabilities",
        "online",
        "last_seen_at",
        "last_ip",
        "clock_skew_s",
        "sort_order",
        "booted_at",
    }
    assert record["trusted"] is True
    assert record["version_code"] == 57
    assert record["last_ip"] == "127.0.0.1"
    assert record["online"] is False


def test_patch_requires_admin(client: TestClient, ops_headers: dict[str, str]) -> None:
    assert client.patch(f"/api/v1/devices/{DEVICE_ID}", json={"trusted": True}).status_code == 401
    assert client.patch(f"/api/v1/devices/{DEVICE_ID}", headers=ops_headers, json={"trusted": True}).status_code == 403


def test_patch_device_updates_only_provided_fields(
    client: TestClient,
    admin_headers: dict[str, str],
    fresh_db: str,
) -> None:
    """只改传了的字段：改名不应顺手把信任位抹掉；空体是 no-op；响应是完整记录。"""
    device_link.register_trusted_device(DEVICE_ID)

    trusted = client.patch(f"/api/v1/devices/{DEVICE_ID}", headers=admin_headers, json={"trusted": True})
    assert trusted.status_code == 200
    assert trusted.json()["trusted"] is True
    assert trusted.json()["device_id"] == DEVICE_ID

    renamed = client.patch(f"/api/v1/devices/{DEVICE_ID}", headers=admin_headers, json={"name": "二楼浴室"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "二楼浴室"
    assert renamed.json()["trusted"] is True

    noop = client.patch(f"/api/v1/devices/{DEVICE_ID}", headers=admin_headers, json={})
    assert noop.status_code == 200
    assert noop.json()["name"] == "二楼浴室"


def test_patch_unknown_device_returns_not_found(
    client: TestClient,
    admin_headers: dict[str, str],
    fresh_db: str,
) -> None:
    """改一台不存在的设备 → 404，不用成功响应体里的 found: false 掩盖错误。"""
    r = client.patch("/api/v1/devices/ghost", headers=admin_headers, json={"name": "n"})

    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/device-not-found")


def test_patch_publishes_trimmed_device_update(
    client: TestClient,
    admin_headers: dict[str, str],
    fresh_db: str,
) -> None:
    """档案修改发布 device.update 精简载荷：IP、版本、时钟偏差不进公开推送。"""
    import json

    device_link.register_trusted_device(DEVICE_ID)

    with client.websocket_connect("/api/v1/stream") as ws:
        client.patch(f"/api/v1/devices/{DEVICE_ID}", headers=admin_headers, json={"name": "改名触发推送"})
        message = json.loads(ws.receive_text())

    assert message["type"] == "device.update"
    payload = message["payload"]
    assert set(payload) == {"device_id", "name", "online", "at"}
    assert payload["device_id"] == DEVICE_ID
    assert payload["name"] == "改名触发推送"


def test_set_name_unknown_device_returns_false(fresh_db: str) -> None:
    from app.modules.devices import repository as devices_repository

    assert devices_repository.set_name("ghost", "n") is False


def test_corrupt_capabilities_are_read_as_empty(fresh_db: str) -> None:
    """capabilities 列被写入非法 JSON 或非数组时读为空列表（未声明态）。"""
    from app.core.db import get_conn
    from app.modules.devices import repository as devices_repository

    devices_repository.register(DEVICE_ID, "0.4.13", 57, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")
    devices_repository.register(
        "dev-bad-cap", "0.4.13", 57, "4.4.4", {"not": "a list"}, "x", "2026-06-01T08:00:00.000Z"
    )

    conn = get_conn()
    conn.execute("UPDATE device SET capabilities = '{broken' WHERE device_id = ?", (DEVICE_ID,))
    conn.commit()

    assert devices_repository.get(DEVICE_ID)["capabilities"] == []
    assert devices_repository.get("dev-bad-cap")["capabilities"] == []


# ── 远程动作（admin）：守卫链与动作分发 ──


@pytest.fixture
def ready(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """设备已信任在线，命令往返被替身截获并记录 action/params/timeout。"""
    device_link.register_trusted_device(DEVICE_ID)
    monkeypatch.setattr(manager, "_connections", {DEVICE_ID: object()})

    captured: dict[str, Any] = {}

    async def fake_send(device_id: str, action: str, params: dict[str, Any], timeout_s: float = 0.0) -> dict[str, Any]:
        captured.update(device_id=device_id, action=action, params=params, timeout_s=timeout_s)
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": {"image_base64": "ZGF0YQ=="}}

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    return captured


def test_device_action_endpoints_reject_unusable_devices(client: TestClient, admin_headers: dict[str, str]) -> None:
    """设备不存在 404、未信任 403、离线 409：守卫先于命令下发。"""
    from app.modules.devices import repository as devices_repository

    assert (
        client.post("/api/v1/devices/dev-ghost-1/exec", headers=admin_headers, json={"command": "id"}).status_code
        == 404
    )

    devices_repository.register(DEVICE_ID, "0.4.13", 57, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")
    assert (
        client.post(f"/api/v1/devices/{DEVICE_ID}/exec", headers=admin_headers, json={"command": "id"}).status_code
        == 403
    )

    devices_repository.set_trusted(DEVICE_ID, True)
    assert (
        client.post(f"/api/v1/devices/{DEVICE_ID}/exec", headers=admin_headers, json={"command": "id"}).status_code
        == 409
    )


def test_device_action_endpoints_require_admin(client: TestClient) -> None:
    assert client.post(f"/api/v1/devices/{DEVICE_ID}/exec", json={"command": "id"}).status_code == 401


@pytest.mark.parametrize(
    ("path", "action", "payload"),
    [
        ("exec", actions.DEVICE_EXEC, {"command": "id"}),
        ("capture", actions.SCREEN_CAPTURE, None),
        ("app/restart", actions.APP_RESTART, None),
        ("reboot", actions.DEVICE_REBOOT, None),
        ("dump", actions.UI_DUMP, None),
        ("bath/restart", actions.BATH_RESTART, None),
        ("clock/set", actions.CLOCK_SET, {"datetime": "2026-10-02 12:00:00"}),
        ("power/schedule", actions.POWER_SCHEDULE, {"off_at": "23:58", "on_at": "10:55"}),
    ],
)
def test_action_endpoints_dispatch_registered_actions(
    client: TestClient,
    admin_headers: dict[str, str],
    ready: dict[str, Any],
    path: str,
    action: str,
    payload: dict[str, Any] | None,
) -> None:
    """动作端点共用同一条下发路径：差别只在动作名、参数与超时。"""
    r = client.post(f"/api/v1/devices/{DEVICE_ID}/{path}", headers=admin_headers, json=payload)

    assert r.status_code == 200
    assert ready["action"] == action
    assert ready["device_id"] == DEVICE_ID


def test_live_log_reads_from_device(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """现读日志走独立动作，返回原文、存在性与截断标记；缺失文件不是错误。"""
    device_link.register_trusted_device(DEVICE_ID)
    monkeypatch.setattr(manager, "_connections", {DEVICE_ID: object()})
    captured: dict[str, Any] = {}

    async def fake_send(device_id: str, action: str, params: dict[str, Any], timeout_s: float = 0.0) -> dict[str, Any]:
        captured.update(device_id=device_id, action=action, params=params)
        return {
            "status": "ok",
            "reason_code": "",
            "terminal_message": "",
            "data": {
                "content": "line-1\nline-2",
                "kind": "device",
                "lines": 2,
                "truncated": True,
                "present": True,
                "size_bytes": 4096,
            },
        }

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    r = client.get(f"/api/v1/devices/{DEVICE_ID}/logs/live?kind=device&lines=2", headers=admin_headers)

    assert r.status_code == 200
    assert captured["action"] == actions.DEVICE_LOG_TAIL
    assert captured["params"] == {"kind": "device", "lines": 2}
    body = r.json()
    assert body["content"] == "line-1\nline-2"
    assert body["truncated"] is True
    assert body["present"] is True
    assert body["size_bytes"] == 4096
    assert body["read_at"]


def test_live_log_absent_file_is_not_an_error(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """crash.log 只在该设备真正崩溃后才存在；present=false 要能正常返回。"""
    device_link.register_trusted_device(DEVICE_ID)
    monkeypatch.setattr(manager, "_connections", {DEVICE_ID: object()})

    async def fake_send(device_id: str, action: str, params: dict[str, Any], timeout_s: float = 0.0) -> dict[str, Any]:
        return {
            "status": "ok",
            "reason_code": "",
            "terminal_message": "",
            "data": {
                "content": "",
                "kind": "crash",
                "lines": 0,
                "truncated": False,
                "present": False,
                "size_bytes": 0,
            },
        }

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    r = client.get(f"/api/v1/devices/{DEVICE_ID}/logs/live?kind=crash", headers=admin_headers)

    assert r.status_code == 200
    assert r.json()["present"] is False
    assert r.json()["content"] == ""


def test_live_log_rejects_unknown_kind(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.get(f"/api/v1/devices/{DEVICE_ID}/logs/live?kind=whatever", headers=admin_headers)

    assert r.status_code == 422


def test_live_log_maps_device_failure_to_problem(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device_link.register_trusted_device(DEVICE_ID)
    monkeypatch.setattr(manager, "_connections", {DEVICE_ID: object()})

    async def fake_send(device_id: str, action: str, params: dict[str, Any], timeout_s: float = 0.0) -> dict[str, Any]:
        return {
            "status": "failed",
            "reason_code": "device.internal_error",
            "terminal_message": "读文件失败",
            "data": None,
        }

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    r = client.get(f"/api/v1/devices/{DEVICE_ID}/logs/live?kind=device", headers=admin_headers)

    assert r.status_code == 502
    assert r.json()["type"].endswith("/problems/device-log-read-failed")
    assert "读文件失败" in r.json()["detail"]


def test_live_log_rejects_empty_result_data(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    device_link.register_trusted_device(DEVICE_ID)
    monkeypatch.setattr(manager, "_connections", {DEVICE_ID: object()})

    async def fake_send(device_id: str, action: str, params: dict[str, Any], timeout_s: float = 0.0) -> dict[str, Any]:
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    r = client.get(f"/api/v1/devices/{DEVICE_ID}/logs/live?kind=device", headers=admin_headers)

    assert r.status_code == 502
    assert r.json()["type"].endswith("/problems/device-log-read-failed")


def test_clock_set_rejects_malformed_datetime(client: TestClient, admin_headers: dict[str, str]) -> None:
    """datetime 形状在 HTTP 边界即拦（422），不发给设备。"""
    r = client.post("/api/v1/devices/ghost/clock/set", headers=admin_headers, json={"datetime": "nope"})

    assert r.status_code == 422


@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"off_at": "nope", "on_at": "10:55"}, "off_at"),
        ({"off_at": "23:58", "on_at": "25:00"}, "on_at"),
        ({"off_at": "23:58"}, "on_at"),
    ],
)
def test_power_schedule_rejects_malformed_targets(
    client: TestClient, admin_headers: dict[str, str], payload: dict[str, str], field: str
) -> None:
    """HH:MM 形状在 HTTP 边界即拦（422），不发给设备。"""
    r = client.post("/api/v1/devices/ghost/power/schedule", headers=admin_headers, json=payload)

    assert r.status_code == 422


def test_exec_dispatches_command_payload(
    client: TestClient,
    admin_headers: dict[str, str],
    ready: dict[str, Any],
) -> None:
    r = client.post(f"/api/v1/devices/{DEVICE_ID}/exec", headers=admin_headers, json={"command": "id"})

    assert r.status_code == 200
    assert ready["params"] == {"command": "id"}


def test_touch_click_sends_coordinates(
    client: TestClient,
    admin_headers: dict[str, str],
    ready: dict[str, Any],
) -> None:
    r = client.post(f"/api/v1/devices/{DEVICE_ID}/touch", headers=admin_headers, json={"x": 100, "y": 200})

    assert r.status_code == 200
    assert ready["params"] == {"x": 100, "y": 200}


def test_touch_accepts_zero_coordinates(
    client: TestClient,
    admin_headers: dict[str, str],
    ready: dict[str, Any],
) -> None:
    """0 是合法坐标，不能被当成未提供。"""
    r = client.post(f"/api/v1/devices/{DEVICE_ID}/touch", headers=admin_headers, json={"x": 0, "y": 0})

    assert r.status_code == 200
    assert ready["params"] == {"x": 0, "y": 0}


def test_touch_forwards_swipe_parameters(
    client: TestClient,
    admin_headers: dict[str, str],
    ready: dict[str, Any],
) -> None:
    """给了终点与时长就是滑动：三个参数原样下发。"""
    r = client.post(
        f"/api/v1/devices/{DEVICE_ID}/touch",
        headers=admin_headers,
        json={"x": 10, "y": 20, "to_x": 30, "to_y": 40, "duration_ms": 500},
    )

    assert r.status_code == 200
    assert ready["params"] == {"x": 10, "y": 20, "to_x": 30, "to_y": 40, "duration_ms": 500}


@pytest.mark.parametrize("payload", [{}, {"x": 100}, {"y": 200}])
def test_touch_rejects_missing_coordinates(
    client: TestClient,
    admin_headers: dict[str, str],
    ready: dict[str, Any],
    payload: dict[str, int],
) -> None:
    """坐标必填且成对：缺了在 schema 层就被挡下。"""
    r = client.post(f"/api/v1/devices/{DEVICE_ID}/touch", headers=admin_headers, json=payload)

    assert r.status_code == 422


def test_scroll_dispatches_direction(
    client: TestClient,
    admin_headers: dict[str, str],
    ready: dict[str, Any],
) -> None:
    """滚动作用于容器：只下发方向。"""
    r = client.post(f"/api/v1/devices/{DEVICE_ID}/scroll", headers=admin_headers, json={"direction": "backward"})

    assert r.status_code == 200
    assert ready["action"] == actions.UI_SCROLL
    assert ready["params"] == {"direction": "backward"}


def test_scroll_direction_is_closed_set(
    client: TestClient,
    admin_headers: dict[str, str],
    ready: dict[str, Any],
) -> None:
    """方向是闭集：缺了或写错当场挡下，不发给设备。"""
    missing = client.post(f"/api/v1/devices/{DEVICE_ID}/scroll", headers=admin_headers, json={})
    assert missing.status_code == 422

    unknown = client.post(f"/api/v1/devices/{DEVICE_ID}/scroll", headers=admin_headers, json={"direction": "sideways"})
    assert unknown.status_code == 422

    for direction in ("forward", "backward"):
        r = client.post(f"/api/v1/devices/{DEVICE_ID}/scroll", headers=admin_headers, json={"direction": direction})
        assert r.status_code == 200
        assert ready["params"] == {"direction": direction}


def test_key_rejects_unknown_key_name(
    client: TestClient,
    admin_headers: dict[str, str],
    ready: dict[str, Any],
) -> None:
    """按键名是闭集。"""
    r = client.post(f"/api/v1/devices/{DEVICE_ID}/key", headers=admin_headers, json={"key": "volume_up"})

    assert r.status_code == 422


def test_key_dispatches_known_key(
    client: TestClient,
    admin_headers: dict[str, str],
    ready: dict[str, Any],
) -> None:
    r = client.post(f"/api/v1/devices/{DEVICE_ID}/key", headers=admin_headers, json={"key": "home"})

    assert r.status_code == 200
    assert ready["action"] == actions.UI_KEY
    assert ready["params"] == {"key": "home"}


def test_input_requires_channel(
    client: TestClient,
    admin_headers: dict[str, str],
    ready: dict[str, Any],
) -> None:
    """注入通道是必填闭集：两条并列通路由调用方显式选择。"""
    missing = client.post(f"/api/v1/devices/{DEVICE_ID}/input", headers=admin_headers, json={"text": "hello"})
    assert missing.status_code == 422

    unknown = client.post(
        f"/api/v1/devices/{DEVICE_ID}/input", headers=admin_headers, json={"text": "hello", "channel": "telepathy"}
    )
    assert unknown.status_code == 422

    r = client.post(
        f"/api/v1/devices/{DEVICE_ID}/input", headers=admin_headers, json={"text": "hello", "channel": "keys"}
    )
    assert r.status_code == 200
    assert ready["action"] == actions.UI_INPUT
    assert ready["params"] == {"text": "hello", "channel": "keys"}


def test_devices_fall_back_to_natural_name_order_when_sort_orders_tie(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """同序设备回落到名字的自然排序：数字段按数值比（1F < 2F < 10F）。

    存量库升级后所有行的 sort_order 都是 0——那一刻的顺序就是名字自然序，与心跳无关
    （按 last_seen 排会让设备在下拉里随心跳跳动，按字面排会让 10F 排到 2F 前面）。
    """
    from app.core.db import write

    for name in ("10F浴室", "2F浴室", "1F浴室"):
        device_id = f"dev-{name}"
        device_link.register_trusted_device(device_id)
        from app.modules.devices import repository as devices_repository

        devices_repository.set_name(device_id, name)
        write("UPDATE device SET sort_order = 0 WHERE device_id = ?", (device_id,))

    body = client.get("/api/v1/devices", headers=admin_headers).json()

    assert [item["name"] for item in body] == ["1F浴室", "2F浴室", "10F浴室"]


def test_newly_registered_device_appends_after_the_manual_order(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """新设备排到末尾：手动调好的顺序不被后来的登记顶掉。"""
    device_link.register_trusted_device("dev-a")
    device_link.register_trusted_device("dev-b")
    client.put("/api/v1/devices/order", json={"order": ["dev-b", "dev-a"]}, headers=admin_headers)

    device_link.register_trusted_device("dev-c")

    body = client.get("/api/v1/devices", headers=admin_headers).json()
    assert [item["device_id"] for item in body] == ["dev-b", "dev-a", "dev-c"]


def test_device_order_is_manual_and_covers_every_listing(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """展示顺序由管理员手动调整：设备下拉、设备表、统计接口全部照此排列。

    同 sort_order 回落到名字的自然排序（新设备都在 0 时就是字母序），调整后以
    管理员的顺序为准。
    """
    for name in ("1F浴室", "2F浴室", "3F浴室"):
        device_id = f"dev-{name}"
        device_link.register_trusted_device(device_id)
        from app.modules.devices import repository as devices_repository

        devices_repository.set_name(device_id, name)

    # 未调整时回落自然序
    body = client.get("/api/v1/devices", headers=admin_headers).json()
    assert [item["name"] for item in body] == ["1F浴室", "2F浴室", "3F浴室"]

    # 管理员把 3F 排到最前
    ids = [item["device_id"] for item in body]
    resp = client.put("/api/v1/devices/order", json={"order": list(reversed(ids))}, headers=admin_headers)
    assert resp.status_code == 200, resp.json()
    assert resp.json()["order"] == list(reversed(ids)), "PUT 的响应应已是新顺序"
    body = client.get("/api/v1/devices", headers=admin_headers).json()
    assert [item["name"] for item in body] == ["3F浴室", "2F浴室", "1F浴室"]


def test_device_order_rejects_unknown_devices(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """顺序清单里出现未知设备整体拒绝：半排序比不排序更难看。"""
    device_link.register_trusted_device("dev-order-a")

    resp = client.put("/api/v1/devices/order", json={"order": ["dev-order-a", "dev-not-exist"]}, headers=admin_headers)

    assert resp.status_code == 404
    body = client.get("/api/v1/devices", headers=admin_headers).json()
    assert [item["device_id"] for item in body] == ["dev-order-a"]


def test_link_events_record_the_reasons(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """链路事件带原因：online 行记设备回报的上次断开原因，offline 行记服务端自己断的原因。

    hello 的 last_offline_reason / last_offline_at 是 0.4.35 起的新字段；没有它们的老
    设备照常接入（reason 为空串），字段是可选的。
    """
    device_id = "dev-link-reason"
    with device_link.trusted_online(client, device_id, admin_headers):
        # 老设备（hello 不带 last_offline_*）→ reason 空；夹具的信任踢会产生多轮事件，
        # 只看最后一条 online
        rows = (
            db.get_conn()
            .execute("SELECT event, reason FROM device_link_event WHERE device_id = ? ORDER BY id", (device_id,))
            .fetchall()
        )
        assert rows is not None
        assert rows[-1]["event"] == "online"
        assert rows[-1]["reason"] == ""

    # 重连，hello 带上一次断开的原因（dns = 网线/交换机/DHCP 一类）：服务端把它记进
    # 这次 online 事件
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame(device_id, last_offline_reason="dns", last_offline_at=now_rfc3339()))
        frames.recv_until(ws, "link.hello_ack")

        rows = (
            db.get_conn()
            .execute("SELECT event, reason FROM device_link_event WHERE device_id = ? ORDER BY id", (device_id,))
            .fetchall()
        )
        assert rows is not None
        assert rows[-1]["event"] == "online"
        assert rows[-1]["reason"] == "dns"
