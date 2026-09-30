"""管理面远程调试端点：debug.exec 与 screen.capture 的守卫与动作转发。

动作名是跨端契约（服务端 payload.action ↔ 设备端 AgentService.dispatchCommand），
因此这里逐个锁定，避免任一侧改名后链路静默失效。
"""

from fastapi.testclient import TestClient
from pytest import MonkeyPatch

from app.gateway import manager as manager_module

ADMIN_HEADERS = {"X-Admin-Token": "test-token"}
DEVICE_ID = "dev-debug-1"
GHOST_ID = "dev-ghost-1"


def _trust_device() -> None:
    from app.modules.devices import repository as devices_repository

    devices_repository.register(DEVICE_ID, "0.3.2", 9, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted(DEVICE_ID, True)


def _capture_command(monkeypatch: MonkeyPatch) -> dict:
    """用假实现顶替命令往返，只观察端点下发的 action 与参数。"""
    from app.modules.sessions import service as sessions_service

    captured: dict = {}

    async def fake_send(device_id: str, action: str, params: dict, timeout_s: float | None = None) -> dict:
        captured.update(device_id=device_id, action=action, params=params, timeout_s=timeout_s)
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": {"image_base64": "ZGF0YQ=="}}

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    return captured


def test_debug_endpoints_reject_unknown_device(client: TestClient) -> None:
    assert client.post(f"/admin/devices/{GHOST_ID}/exec", headers=ADMIN_HEADERS, json={"command": "id"}).status_code == 404
    assert client.post(f"/admin/devices/{GHOST_ID}/screen", headers=ADMIN_HEADERS).status_code == 404


def test_debug_endpoints_reject_offline_device(client: TestClient) -> None:
    """已注册且已信任但不在线：409（不能把命令投进虚空）。"""
    from app.modules.devices import repository as devices_repository

    devices_repository.register(GHOST_ID, "0.3.2", 9, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted(GHOST_ID, True)

    assert client.post(f"/admin/devices/{GHOST_ID}/screen", headers=ADMIN_HEADERS).status_code == 409


def test_exec_dispatches_debug_exec_action(client: TestClient, monkeypatch) -> None:
    _trust_device()
    monkeypatch.setattr(manager_module.manager, "_connections", {DEVICE_ID: object()})
    captured = _capture_command(monkeypatch)

    r = client.post(f"/admin/devices/{DEVICE_ID}/exec", headers=ADMIN_HEADERS, json={"command": "id"})

    assert r.status_code == 200
    assert captured["action"] == "debug.exec"
    assert captured["params"] == {"command": "id"}


def test_screen_capture_dispatches_screen_action(client: TestClient, monkeypatch) -> None:
    """全屏截图链路：管理面 → 设备端 screen.capture（AgentService 识别的动作名）。"""
    _trust_device()
    monkeypatch.setattr(manager_module.manager, "_connections", {DEVICE_ID: object()})
    captured = _capture_command(monkeypatch)

    r = client.post(f"/admin/devices/{DEVICE_ID}/screen", headers=ADMIN_HEADERS)

    assert r.status_code == 200
    assert captured["action"] == "screen.capture"
    assert captured["params"] == {}
    assert r.json()["data"]["image_base64"] == "ZGF0YQ=="
