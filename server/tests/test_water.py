"""水控板直控路由：admin 守卫、逻辑门号透传、超时与参数校验。

设备动作换成桩：真下发要一条在线设备链路与真实板子，那是设备端
`WaterBoardClientTest` / `WaterHandlerTest` 的活。这里只盯 HTTP 面的形状与守卫。
"""

import pytest
from fastapi.testclient import TestClient

from app.core import actions
from app.modules.water import routes

DEVICE = "dev-water"

OK_RESULT: dict[str, object] = {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}


def _stub_action(
    monkeypatch: pytest.MonkeyPatch,
    sent: list[tuple[str, str, dict[str, int], float]],
) -> None:
    async def fake(
        _session: object,
        device_id: str,
        action: str,
        params: dict[str, int],
        timeout_s: float,
        **_kw: object,
    ) -> object:
        sent.append((device_id, action, params, timeout_s))
        return OK_RESULT

    monkeypatch.setattr(routes, "device_action", fake)


def test_open_passes_only_the_logical_door(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, dict[str, int], float]] = []
    _stub_action(monkeypatch, sent)

    response = client.post(
        f"/api/v1/devices/{DEVICE}/water/open",
        json={"door_no": 3},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json() == OK_RESULT
    assert sent == [(DEVICE, actions.WATER_OPEN, {"door_no": 3}, routes.WATER_ACTION_TIMEOUT_S)]


def test_close_passes_only_the_logical_door(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, dict[str, int], float]] = []
    _stub_action(monkeypatch, sent)

    response = client.post(
        f"/api/v1/devices/{DEVICE}/water/close",
        json={"door_no": 7},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert sent == [(DEVICE, actions.WATER_CLOSE, {"door_no": 7}, routes.WATER_ACTION_TIMEOUT_S)]


def test_status_sends_no_business_params(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, dict[str, int], float]] = []
    _stub_action(monkeypatch, sent)

    response = client.get(f"/api/v1/devices/{DEVICE}/water", headers=admin_headers)

    assert response.status_code == 200
    assert sent == [(DEVICE, actions.WATER_STATUS, {}, routes.WATER_ACTION_TIMEOUT_S)]


@pytest.mark.parametrize("path", ["water/open", "water/close"])
def test_water_endpoints_need_admin(client: TestClient, path: str) -> None:
    response = client.post(f"/api/v1/devices/{DEVICE}/{path}", json={"door_no": 1})
    assert response.status_code == 401


def test_water_status_needs_admin(client: TestClient) -> None:
    response = client.get(f"/api/v1/devices/{DEVICE}/water")
    assert response.status_code == 401


@pytest.mark.parametrize("door_no", [0, -1])
def test_door_no_must_be_positive(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    door_no: int,
) -> None:
    sent: list[tuple[str, str, dict[str, int], float]] = []
    _stub_action(monkeypatch, sent)

    response = client.post(
        f"/api/v1/devices/{DEVICE}/water/open",
        json={"door_no": door_no},
        headers=admin_headers,
    )

    assert response.status_code == 422
    assert sent == []


def test_door_no_is_required(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[tuple[str, str, dict[str, int], float]] = []
    _stub_action(monkeypatch, sent)

    response = client.post(f"/api/v1/devices/{DEVICE}/water/close", json={}, headers=admin_headers)

    assert response.status_code == 422
    assert sent == []
