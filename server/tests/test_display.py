"""楼层状态行：空闲口径、接收方筛选与 `display.status` 帧。"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core.connections import manager
from app.modules.devices import repository as devices_repository
from app.modules.devices import service as devices_service
from app.modules.display import service as display_service
from app.modules.doors import service as doors_service


def _door(door_no: int, **overrides: object) -> dict[str, Any]:
    door: dict[str, Any] = {
        "door_no": door_no,
        "card_no": 0,
        "cert_no": "",
        "acc_no": "",
        "open_time": None,
        "in_time": None,
        "close_time": None,
        "bath_time_s": 0,
        "status_raw": 0,
    }
    door.update(overrides)
    return door


def _report(device_id: str, doors: list[dict[str, Any]]) -> None:
    doors_service.process_snapshot(device_id, {"snapshot_id": str(uuid.uuid4()), "source": "db_watch", "doors": doors})


def _register(device_id: str, name: str, *, trusted: bool = True) -> None:
    devices_repository.register(device_id, "0.5.0", 1, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")
    devices_repository.set_name(device_id, name)
    devices_repository.set_trusted(device_id, trusted)


def test_free_count_only_counts_free_and_follows_device_order(
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`free` 只数空闲；已选中、等待开阀、报修都不算；顺序沿用设备清单。"""
    _register("dev-2f", "2F")
    _register("dev-1f", "1F")
    devices_service.set_order(["dev-1f", "dev-2f"])
    _report(
        "dev-1f",
        [
            _door(1),
            _door(2, status_raw=2),
            _door(3, card_no=1001, cert_no="C", acc_no="A"),
            _door(4, status_raw=1),
        ],
    )
    _report("dev-2f", [_door(1), _door(2)])
    monkeypatch.setattr(
        manager,
        "_connections",
        {"dev-1f": helpers.FakeGatewayWS(), "dev-2f": helpers.FakeGatewayWS()},
    )

    snapshot = display_service._snapshot()

    assert [floor["name"] for floor in snapshot.payload["floors"]] == ["1F", "2F"]
    assert [floor["free"] for floor in snapshot.payload["floors"]] == [1, 2]
    assert [recipient.device_id for recipient in snapshot.recipients] == ["dev-1f", "dev-2f"]


def test_unavailable_devices_report_no_free_count(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """离线、未信任、在线但没上报过喷头：free 都是 None，且只有可用的上报设备收帧。

    这三件事不能都显示成 0：离线是不知道，未信任是数据不可用，没上报过是还没有数据。
    """
    _register("dev-offline", "4F")
    _report("dev-offline", [_door(1)])
    _register("dev-untrusted", "5F", trusted=False)
    _report("dev-untrusted", [_door(1)])
    _register("dev-nodata", "6F")
    _register("dev-online", "7F")
    _report("dev-online", [_door(1)])
    monkeypatch.setattr(
        manager,
        "_connections",
        {
            "dev-untrusted": helpers.FakeGatewayWS(),
            "dev-nodata": helpers.FakeGatewayWS(),
            "dev-online": helpers.FakeGatewayWS(),
        },
    )

    snapshot = display_service._snapshot()

    by_name = {floor["name"]: floor for floor in snapshot.payload["floors"]}
    assert by_name["4F"]["online"] is False
    assert by_name["4F"]["free"] is None
    assert by_name["5F"]["online"] is False
    assert by_name["5F"]["free"] is None
    assert by_name["6F"]["online"] is True
    assert by_name["6F"]["free"] is None
    assert by_name["7F"]["online"] is True
    assert by_name["7F"]["free"] == 1
    assert [recipient.device_id for recipient in snapshot.recipients] == ["dev-online"]


def test_unnamed_device_uses_its_id_as_the_label(fresh_db: str) -> None:
    """没设名称的设备不能从状态行里消失：与网页端同一约定，未命名时显示设备 id。"""
    devices_repository.register("dev-unnamed", "0.5.0", 1, "4.4.4", [], "127.0.0.1", "2026-06-01T08:00:00.000Z")
    _report("dev-unnamed", [_door(1)])

    snapshot = display_service._snapshot()

    assert snapshot.payload["floors"][0]["name"] == "dev-unnamed"


def test_display_status_is_pushed_to_trusted_reporting_device(
    client: TestClient,
    admin_headers: dict[str, str],
    fresh_db: str,
) -> None:
    """已信任、在线、上报过喷头的设备会在一个推送周期内收到整份楼层现态。"""
    device_id = "dev-status-line"
    _register(device_id, "2F")
    _report(device_id, [_door(1), _door(2, status_raw=2)])

    with helpers.trusted_online(client, device_id, admin_headers) as ws:
        message = helpers.recv_until(ws, "display.status")

    assert message["payload"]["floors"] == [
        {"device_id": device_id, "name": "2F", "online": True, "free": 1},
    ]
