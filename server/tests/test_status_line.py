"""楼层状态行：空闲口径、接收方口径与 `status_line.update` 帧。"""

import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests import device_link, frames

from app.core.connections import manager
from app.modules.devices import service as devices_service
from app.modules.doors import service as doors_service
from app.modules.status_line import service as status_line_service


def _snap() -> object:
    """读一轮楼层现态汇总（后台循环的会话边界在这里显式打开）。"""
    from app.db.session import session_scope

    with session_scope() as session:
        return status_line_service._snapshot(session)


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
    from app.db.base import utcnow
    from app.persistence import devices as devices_store

    with device_link.db() as session:
        devices_store.register(
            session,
            device_id=device_id,
            app_version="0.5.0",
            version_code=1,
            os_version="4.4.4",
            capabilities=[],
            ip="127.0.0.1",
            booted_at=utcnow(),
            seen_at=utcnow(),
        )
        devices_store.set_name(session, device_id, name)
        devices_store.set_trusted(session, device_id, trusted)


def test_free_count_only_counts_free_and_follows_device_order(
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`free` 只数空闲；已选中、等待开阀、报修都不算；顺序沿用设备清单。"""
    _register("dev-2f", "2F")
    _register("dev-1f", "1F")
    from app.db.session import session_scope as _scope

    with _scope() as _session:
        devices_service.set_order(_session, ["dev-1f", "dev-2f"])
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
        {"dev-1f": device_link.FakeGatewayWS(), "dev-2f": device_link.FakeGatewayWS()},
    )

    snapshot = _snap()

    assert [floor["name"] for floor in snapshot.payload["floors"]] == ["1F", "2F"]
    assert [floor["free"] for floor in snapshot.payload["floors"]] == [1, 2]
    assert [recipient.device_id for recipient in snapshot.recipients] == ["dev-1f", "dev-2f"]


def test_unavailable_devices_report_no_free_count(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """离线、未信任、在线但没上报过喷头：free 都是 None。

    这三件事不能都显示成 0：离线是不知道，未信任是数据不可用，没上报过是还没有数据。
    接收方只按「已信任且在线」筛选，所以在线但没数据的设备也会收到整份现态。
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
            "dev-untrusted": device_link.FakeGatewayWS(),
            "dev-nodata": device_link.FakeGatewayWS(),
            "dev-online": device_link.FakeGatewayWS(),
        },
    )

    snapshot = _snap()

    by_name = {floor["name"]: floor for floor in snapshot.payload["floors"]}
    assert by_name["4F"]["online"] is False
    assert by_name["4F"]["free"] is None
    assert by_name["5F"]["online"] is False
    assert by_name["5F"]["free"] is None
    assert by_name["6F"]["online"] is True
    assert by_name["6F"]["free"] is None
    assert by_name["7F"]["online"] is True
    assert by_name["7F"]["free"] == 1
    assert [recipient.device_id for recipient in snapshot.recipients] == ["dev-nodata", "dev-online"]


def test_unnamed_device_uses_its_id_as_the_label(fresh_db: str) -> None:
    """没设名称的设备不能从状态行里消失：与网页端同一约定，未命名时显示设备 id。"""
    device_link.register_device_raw("dev-unnamed")
    _report("dev-unnamed", [_door(1)])

    snapshot = _snap()

    assert snapshot.payload["floors"][0]["name"] == "dev-unnamed"


def test_status_line_update_is_pushed_to_trusted_online_device(
    client: TestClient,
    admin_headers: dict[str, str],
    fresh_db: str,
) -> None:
    """已信任且在线的设备会在一个推送周期内收到整份楼层现态。"""
    device_id = "dev-status-line"
    _register(device_id, "2F")
    _report(device_id, [_door(1), _door(2, status_raw=2)])

    with device_link.trusted_online(client, device_id, admin_headers) as ws:
        message = frames.recv_until(ws, "status_line.update")

    assert message["payload"]["floors"] == [
        {"device_id": device_id, "name": "2F", "online": True, "free": 1},
    ]


def test_device_without_door_data_still_receives_the_fleet_status(
    client: TestClient,
    admin_headers: dict[str, str],
    fresh_db: str,
) -> None:
    """刚上线、还没上报喷头的设备也收帧；它自己的楼层是「无数据」，别的楼层照常可见。"""
    device_id = "dev-status-line-nodata"
    _register(device_id, "6F")

    with device_link.trusted_online(client, device_id, admin_headers) as ws:
        message = frames.recv_until(ws, "status_line.update")

    assert message["payload"]["floors"] == [
        {"device_id": device_id, "name": "6F", "online": True, "free": None},
    ]
