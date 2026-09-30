"""覆盖缺口补测：桥接关闭/清理路径、自动结算循环生命周期、杂项边界。"""

import asyncio
import json
import uuid

from fastapi.testclient import TestClient

from app.gateway import manager as manager_module
from app.modules.bridge.manager import AdbBridge
from app.modules.sessions import auto_settle


def _admin(client: TestClient) -> dict:
    return {"X-Admin-Token": "test-token"}


def _hello(device_id: str) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": "link.hello",
            "id": str(uuid.uuid4()),
            "ts": "2026-09-30T12:00:00.000Z",
            "payload": {
                "device_id": device_id,
                "app_version": "0.2.5",
                "version_code": 7,
                "os_version": "4.4.4",
                "capabilities": [],
            },
        }
    )


def _connect_trusted(client: TestClient, device_id: str):
    from app.modules.devices import repository as devices_repository

    devices_repository.register(device_id, "0.2.5", 7, "4.4.4", [], "127.0.0.1")
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        while json.loads(ws.receive_text()).get("type") != "link.hello_ack":
            pass
    client.post(f"/admin/devices/{device_id}/trust", headers=_admin(client))
    ws = client.websocket_connect("/gateway/device").__enter__()
    ws.send_text(_hello(device_id))
    while json.loads(ws.receive_text()).get("type") != "link.hello_ack":
        pass
    return ws


class FakeGatewayWS:
    async def send_text(self, data: str) -> bool:
        return True


def test_bridge_close_with_active_session(client: TestClient, monkeypatch) -> None:
    """桥接活跃时 adb/close 返回 True 并清理；重复关闭返回 False。"""
    from app.modules.devices import repository as devices_repository

    device_id = "dev-bridge-close"
    devices_repository.register(device_id, "0.2.5", 7, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted(device_id, True)
    monkeypatch.setattr(manager_module.manager, "_connections", {device_id: FakeGatewayWS()})

    client.post(f"/admin/devices/{device_id}/adb/open", headers=_admin(client))
    # 设备 WS 未连接的桥也能被管理面关闭（回收监听）
    assert client.post(f"/admin/devices/{device_id}/adb/close", headers=_admin(client)).json()["closed"] is True
    assert client.post(f"/admin/devices/{device_id}/adb/close", headers=_admin(client)).json()["closed"] is False


def test_bridge_manager_units() -> None:
    bridge = AdbBridge("dev-u", 15655)
    assert bridge.ready is False
    asyncio.run(bridge.close())  # 幂等：未开启时 close 不抛
    asyncio.run(bridge.close())


def test_auto_settle_loop_lifecycle(client: TestClient) -> None:
    """后台循环 start/stop 幂等且可停。"""
    auto_settle.start()
    auto_settle.start()  # 重复 start 幂等
    auto_settle.stop()
    auto_settle.stop()  # 重复 stop 幂等


def test_auto_settle_scan_offline_device(client: TestClient, monkeypatch) -> None:
    """命中的候选设备离线 → 不下发，返回 False。"""
    from datetime import datetime, timedelta, timezone

    from app.modules.devices import repository as devices_repository

    devices_repository.register("dev-as-off", "0.2.5", 7, "4.4.4", [], "127.0.0.1")
    started = (
        (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )
    devices_repository.set_trusted("dev-as-off", True)

    from app.modules.doors import repository as doors_repository

    doors_repository.open_session("dev-as-off", 1, 901, "99990052", started)

    config = {
        "auto_settle_enabled": True,
        "auto_settle_threshold_s": 600,
        "auto_settle_students": {"99990052": "000000"},
    }
    monkeypatch.setattr(auto_settle.config_store, "effective", lambda _d: config)
    # 网关无连接 → 设备离线
    import asyncio

    assert asyncio.run(auto_settle.scan_once()) is False


def test_duration_s_bad_input() -> None:
    assert auto_settle._duration_s("not-a-time") == 0
    assert auto_settle._duration_s("") == 0


def test_now_epoch_s() -> None:
    from app.core.clock import now_epoch_s

    assert isinstance(now_epoch_s(), int)


def test_validation_error_problem_json(client: TestClient) -> None:
    """422 校验错误同样走 problem+json（RFC 9457 覆盖所有错误响应）。"""
    r = client.post("/api/v1/sessions/assign", json={"door_no": 0})
    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert {"type", "title", "status", "detail"} <= set(body)


def test_devices_repository_capabilities_non_list(client: TestClient) -> None:
    """capabilities 存成非数组时读取降级为空列表（防御坏数据）。"""
    from app.modules.devices import repository as devices_repository

    devices_repository.register("dev-bad-cap", "0.2.5", 7, "4.4.4", {"not": "a list"}, "x")
    device = devices_repository.get("dev-bad-cap")
    assert device["capabilities"] == []


def test_devices_service_offline_marks_row(client: TestClient) -> None:
    from app.modules.devices import repository as devices_repository
    from app.modules.devices import service as devices_service

    devices_repository.register("dev-off-2", "0.2.5", 7, "4.4.4", [], "127.0.0.1")
    devices_service.set_device_offline("dev-off-2")
    assert devices_repository.get("dev-off-2")["online"] is False


def test_is_trusted_unknown_device_false(client: TestClient) -> None:
    from app.modules.devices import service as devices_service

    assert devices_service.is_trusted("ghost-device") is False


def test_devices_repository_capabilities_corrupt_json(client: TestClient) -> None:
    """capabilities 列被写入非法 JSON 时读取安全降级为空列表。"""
    from app.core.db import get_conn
    from app.modules.devices import repository as devices_repository

    devices_repository.register("dev-corrupt", "0.2.5", 7, "4.4.4", [], "127.0.0.1")
    get_conn().execute("UPDATE devices SET capabilities = '{broken' WHERE device_id = 'dev-corrupt'")
    get_conn().commit()
    assert devices_repository.get("dev-corrupt")["capabilities"] == []
