"""管理面远程调试端点：设备维护与界面操作的守卫与动作转发。

动作名是跨端契约（服务端 payload.action ↔ 设备端 AgentService.dispatchCommand），
因此这里逐个锁定，避免任一侧改名后链路静默失效。

界面操作那一组（ui.*）还会校验参数形态——触摸只认坐标，`x` 与 `y` 必填；曾经有过的
`index` 已删除（理由见 `devices/schemas.py` 的 `DeviceUiTouchRequest`）。
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from app.core import actions
from app.gateway import manager as manager_module

OPS_HEADERS = {"X-Ops-Token": "test-token"}
DEVICE_ID = "dev-debug-1"
GHOST_ID = "dev-ghost-1"


def _trust_device() -> None:
    from app.modules.devices import repository as devices_repository

    devices_repository.register(DEVICE_ID, "0.3.2", 9, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted(DEVICE_ID, True)


def _online(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_module.manager, "_connections", {DEVICE_ID: object()})


def _capture_command(monkeypatch: pytest.MonkeyPatch) -> dict:
    """用假实现顶替命令往返，只观察端点下发的 action 与参数。"""
    from app.modules.sessions import service as sessions_service

    captured: dict = {}

    async def fake_send(device_id: str, action: str, params: dict, timeout_s: float | None = None) -> dict:
        captured.update(device_id=device_id, action=action, params=params, timeout_s=timeout_s)
        return {
            "status": "ok",
            "reason_code": "",
            "terminal_message": "",
            "data": {"image_base64": "ZGF0YQ==", "tree": {"elements": []}},
        }

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    return captured


def _ready(monkeypatch: pytest.MonkeyPatch) -> dict:
    _trust_device()
    _online(monkeypatch)
    return _capture_command(monkeypatch)


def test_debug_endpoints_reject_unknown_device(client: TestClient) -> None:
    assert client.post(f"/ops/devices/{GHOST_ID}/exec", headers=OPS_HEADERS, json={"command": "id"}).status_code == 404
    assert client.post(f"/ops/devices/{GHOST_ID}/capture", headers=OPS_HEADERS).status_code == 404
    assert client.post(f"/ops/devices/{GHOST_ID}/restart", headers=OPS_HEADERS).status_code == 404
    assert client.post(f"/ops/devices/{GHOST_ID}/uninstall", headers=OPS_HEADERS).status_code == 404
    assert client.post(f"/ops/devices/{GHOST_ID}/dump", headers=OPS_HEADERS).status_code == 404


def test_debug_endpoints_reject_offline_device(client: TestClient) -> None:
    """已注册且已信任但不在线：409（不能把命令投进虚空）。"""
    from app.modules.devices import repository as devices_repository

    devices_repository.register(GHOST_ID, "0.3.2", 9, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted(GHOST_ID, True)

    assert client.post(f"/ops/devices/{GHOST_ID}/capture", headers=OPS_HEADERS).status_code == 409


def test_exec_dispatches_device_exec_action(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/exec", headers=OPS_HEADERS, json={"command": "id"})

    assert r.status_code == 200
    assert captured["action"] == actions.DEVICE_EXEC
    assert captured["params"] == {"command": "id"}


def test_screen_capture_dispatches_screen_action(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """全屏截图链路：管理面 → 设备端 screen.capture（AgentService 识别的动作名）。"""
    captured = _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/capture", headers=OPS_HEADERS)

    assert r.status_code == 200
    assert captured["action"] == actions.SCREEN_CAPTURE
    assert captured["params"] == {}
    assert r.json()["data"]["image_base64"] == "ZGF0YQ=="


@pytest.mark.parametrize(
    ("path", "action"),
    [
        ("restart", actions.DEVICE_RESTART),
        ("uninstall", actions.DEVICE_UNINSTALL),
        ("dump", actions.UI_DUMP),
    ],
)
def test_maintenance_and_tree_endpoints_dispatch(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    action: str,
) -> None:
    """无参端点：动作名对上即可，参数恒为空。"""
    captured = _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/{path}", headers=OPS_HEADERS)

    assert r.status_code == 200
    assert captured["action"] == action
    assert captured["params"] == {}


def test_touch_by_coordinates(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/touch", headers=OPS_HEADERS, json={"x": 100, "y": 200})

    assert r.status_code == 200
    assert captured["params"] == {"x": 100, "y": 200}


def test_touch_accepts_zero_coordinates(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """0 是合法坐标，不能因为"看起来像默认值"就被当成未提供。"""
    captured = _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/touch", headers=OPS_HEADERS, json={"x": 0, "y": 0})

    assert r.status_code == 200
    assert captured["params"] == {"x": 0, "y": 0}


def test_touch_rejects_missing_coordinates(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """坐标是必填：缺了在 schema 层就被挡下，不发无意义的往返。"""
    _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/touch", headers=OPS_HEADERS, json={})

    assert r.status_code == 422


def test_touch_forwards_swipe_parameters(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """给了终点与时长就是滑动：三个参数都要原样下发。"""
    captured = _ready(monkeypatch)

    r = client.post(
        f"/ops/devices/{DEVICE_ID}/touch",
        headers=OPS_HEADERS,
        json={"x": 10, "y": 20, "to_x": 30, "to_y": 40, "duration_ms": 500},
    )

    assert r.status_code == 200
    assert captured["params"] == {"x": 10, "y": 20, "to_x": 30, "to_y": 40, "duration_ms": 500}


def test_scroll_dispatches_direction(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """滚动作用于容器：只下发方向，不下发坐标。"""
    captured = _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/scroll", headers=OPS_HEADERS, json={"direction": "backward"})

    assert r.status_code == 200
    assert captured["action"] == actions.UI_SCROLL
    assert captured["params"] == {"direction": "backward"}


def test_scroll_defaults_to_forward(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/scroll", headers=OPS_HEADERS, json={})

    assert r.status_code == 200
    assert captured["params"] == {"direction": "forward"}


def test_scroll_rejects_unknown_direction(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """方向是闭集：写错当场挡下，不发给设备。"""
    _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/scroll", headers=OPS_HEADERS, json={"direction": "sideways"})

    assert r.status_code == 422


def test_touch_rejects_incomplete_coordinates(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """只给 x 不给 y：坐标不完整，同样拒绝。"""
    _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/touch", headers=OPS_HEADERS, json={"x": 100})

    assert r.status_code == 422


def test_key_and_text_dispatch_their_payload(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/key", headers=OPS_HEADERS, json={"key": "home"})
    assert r.status_code == 200
    assert captured["action"] == actions.UI_KEY
    assert captured["params"] == {"key": "home"}

    r = client.post(f"/ops/devices/{DEVICE_ID}/input", headers=OPS_HEADERS, json={"text": "hello"})
    assert r.status_code == 200
    assert captured["action"] == actions.UI_INPUT
    assert captured["params"] == {"text": "hello"}


def test_key_rejects_unknown_key_name(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """按键名是闭集：写错在入口就被挡下，不发给设备。"""
    _ready(monkeypatch)

    r = client.post(f"/ops/devices/{DEVICE_ID}/key", headers=OPS_HEADERS, json={"key": "volume_up"})

    assert r.status_code == 422


def test_debug_endpoints_require_ops_token(client: TestClient) -> None:
    for call in _ALL_DEBUG_CALLS:
        assert call(client, {}).status_code == 401


def test_patch_device_updates_only_provided_fields(client: TestClient) -> None:
    """只改传了的字段：改名不应顺手把信任位抹掉。"""
    from app.modules.devices import repository as devices_repository

    devices_repository.register(DEVICE_ID, "0.3.2", 9, "4.4.4", [], "127.0.0.1")

    client.patch(f"/ops/devices/{DEVICE_ID}", headers=OPS_HEADERS, json={"trusted": True})
    renamed = client.patch(f"/ops/devices/{DEVICE_ID}", headers=OPS_HEADERS, json={"name": "二楼浴室"})

    assert renamed.status_code == 200
    assert renamed.json()["name"] == "二楼浴室"
    assert renamed.json()["trusted"] is True


def test_patch_device_with_empty_body_is_a_noop(client: TestClient) -> None:
    """空请求体：什么都不改，返回当前档案而不是报错。"""
    from app.modules.devices import repository as devices_repository

    devices_repository.register(DEVICE_ID, "0.3.2", 9, "4.4.4", [], "127.0.0.1")

    r = client.patch(f"/ops/devices/{DEVICE_ID}", headers=OPS_HEADERS, json={})

    assert r.status_code == 200
    assert r.json()["device_id"] == DEVICE_ID


#: 全部调试端点，用于统一校验鉴权。新增端点时补一行。
_ALL_DEBUG_CALLS: tuple[Callable[..., object], ...] = (
    lambda c, h: c.post(f"/ops/devices/{DEVICE_ID}/exec", headers=h, json={"command": "id"}),
    lambda c, h: c.post(f"/ops/devices/{DEVICE_ID}/capture", headers=h),
    lambda c, h: c.post(f"/ops/devices/{DEVICE_ID}/restart", headers=h),
    lambda c, h: c.post(f"/ops/devices/{DEVICE_ID}/uninstall", headers=h),
    lambda c, h: c.post(f"/ops/devices/{DEVICE_ID}/dump", headers=h),
    lambda c, h: c.post(f"/ops/devices/{DEVICE_ID}/touch", headers=h, json={"x": 0, "y": 0}),
    lambda c, h: c.post(f"/ops/devices/{DEVICE_ID}/key", headers=h, json={"key": "home"}),
    lambda c, h: c.post(f"/ops/devices/{DEVICE_ID}/input", headers=h, json={"text": "x"}),
)
