"""喷头域：七态派生、快照 diff 与事件、会话开合里程碑、上次使用、GET /doors 与 WS /stream。"""

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core import db
from app.core.clock import rfc3339_after
from app.core.device_clock import load_skew
from app.modules.doors import repository, service
from app.modules.doors.schemas import DoorState


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


def _snapshot(doors: list[dict[str, Any]]) -> dict[str, Any]:
    return {"snapshot_id": str(uuid.uuid4()), "source": "db_watch", "doors": doors}


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({}, DoorState.FREE),
        ({"card_no": 1001, "open_time": "2026-09-30T12:00:00.000Z"}, DoorState.WAITING),
        (
            {"card_no": 1001, "open_time": "2026-09-30T12:00:00.000Z", "in_time": "2026-09-30T12:01:00.000Z"},
            DoorState.BATHING,
        ),
        (
            {
                "card_no": 1001,
                "open_time": "2026-09-30T12:00:00.000Z",
                "close_time": "2026-09-30T12:10:00.000Z",
                "bath_time_s": 300,
            },
            DoorState.PAUSED,
        ),
        ({"status_raw": 2}, DoorState.SELECTED),
        ({"status_raw": 1, "card_no": 123, "in_time": "2026-09-30T12:00:00.000Z"}, DoorState.REPAIR),
        ({"status_raw": 3}, DoorState.DISABLED),
        ({"acc_no": "A001"}, DoorState.WAITING),
    ],
)
def test_derive_state_covers_all_states(raw: dict[str, Any], expected: DoorState) -> None:
    """派生优先级 repair > disabled > 占用三分支 > selected > free；acc_no 单独也算占用。"""
    assert service.derive_state(_door(1, **raw)) == expected


def test_snapshot_updates_live_state_and_records_events(fresh_db: str) -> None:
    """快照链路：状态落库、状态迁移记事件、会话随占用开合。"""
    device_id = "test-device-0002"
    service.process_snapshot(device_id, _snapshot([_door(1), _door(2)]))
    live = repository.get_all_live(device_id)
    assert len(live) == 2
    assert live[1]["state"] == DoorState.FREE.value

    # 时间戳相对当前时刻构造：服务端用"收到快照的时刻"减 inTime 计算出水时长
    assigned = rfc3339_after(-120)
    opened = rfc3339_after(-60)

    service.process_snapshot(device_id, _snapshot([_door(1, card_no=2001, cert_no="C1", open_time=assigned), _door(2)]))
    live = repository.get_all_live(device_id)
    assert live[1]["state"] == DoorState.WAITING.value
    assert live[1]["card_no"] == 2001

    service.process_snapshot(
        device_id, _snapshot([_door(1, card_no=2001, cert_no="C1", open_time=assigned, in_time=opened)])
    )
    assert repository.get_all_live(device_id)[1]["state"] == DoorState.BATHING.value
    assert repository.get_open_session(device_id, 1) is not None

    service.process_snapshot(device_id, _snapshot([_door(1), _door(2)]))
    assert repository.get_open_session(device_id, 1) is None

    conn = db.get_conn()
    events = conn.execute(
        "SELECT from_state, to_state FROM door_state_event WHERE device_id=? AND door_no=1 ORDER BY id",
        (device_id,),
    ).fetchall()
    assert [(e["from_state"], e["to_state"]) for e in events] == [
        (DoorState.FREE.value, DoorState.WAITING.value),
        (DoorState.WAITING.value, DoorState.BATHING.value),
        (DoorState.BATHING.value, DoorState.FREE.value),
    ]
    sessions = conn.execute("SELECT * FROM usage_session WHERE device_id=?", (device_id,)).fetchall()
    assert len(sessions) == 1
    session = sessions[0]
    assert session["card_no"] == 2001
    assert session["ended_at"] is not None
    assert session["opened_at"] is not None
    assert session["flow_s"] >= 55  # 这一趟确实出过水，时长接近一分钟


def test_snapshot_corrects_device_time_by_clock_skew(fresh_db: str) -> None:
    """设备时间字段按已登记的时钟偏差校准为服务器时钟。"""
    device_id = "dev-skew-00001"
    load_skew(device_id, 100.0)  # 设备慢 100 秒
    opened = "2026-09-30T12:00:00.000Z"

    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="C", in_time=opened)]))

    live = repository.get_all_live(device_id)
    assert live[1]["in_time"] == "2026-09-30T12:01:40.000Z"


def test_session_owner_change_closes_old_and_opens_new(fresh_db: str) -> None:
    """占用者中途变化：先结旧账再开新账，两个人的时长不并入同一条记录。"""
    device_id = "dev-swap-00001"
    helpers.register_trusted_device(device_id)
    doors = [_door(1, card_no=100, cert_no="A", open_time=rfc3339_after(-30), in_time=rfc3339_after(-25))]
    service.process_snapshot(device_id, _snapshot(doors))
    first = repository.get_open_session(device_id, 1)

    doors[0]["card_no"] = 200
    doors[0]["cert_no"] = "B"
    service.process_snapshot(device_id, _snapshot(doors))

    assert first is not None
    current = repository.get_open_session(device_id, 1)
    assert current is not None
    assert current["id"] != first["id"]
    assert current["cert_no"] == "B"


def test_snapshot_rejects_invalid_and_skips_zero_door_no(fresh_db: str) -> None:
    """doors 缺失或非列表报 door.invalid_snapshot；door_no 非正的条目跳过。"""
    device_id = "dev-bad-snap-1"
    bad = service.process_snapshot(device_id, {"snapshot_id": "s"})
    assert bad["status"] == "error"
    assert bad["error"]["code"] == "door.invalid_snapshot"

    helpers.register_trusted_device(device_id)
    result = service.process_snapshot(device_id, _snapshot([_door(0), _door(1)]))
    assert result == {"status": "ok", "processed": 1}


def test_last_used_ignores_sessions_that_never_opened(fresh_db: str) -> None:
    """「上次使用」只认开过阀的会话：分配后直接结算那种不算。"""
    device_id = "dev-last-used-1"
    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="A", open_time=rfc3339_after(-600))]))
    service.process_snapshot(device_id, _snapshot([_door(1)]))

    assert (device_id, 1) not in repository.latest_usage_by_door()


def test_last_used_keeps_zero_length_bath(fresh_db: str) -> None:
    """开了阀立刻关（出水时长为 0）要算一次使用：判据是 opened_at 而非时长。"""
    device_id = "dev-last-used-2"
    opened = rfc3339_after(-5)
    service.process_snapshot(
        device_id, _snapshot([_door(1, card_no=2, cert_no="B", open_time=rfc3339_after(-20), in_time=opened)])
    )
    service.process_snapshot(device_id, _snapshot([_door(1)]))

    row = repository.latest_usage_by_door()[(device_id, 1)]
    assert row["cert_no"] == "B"
    assert row["opened_at"] == opened


def test_last_used_returns_the_most_recent(fresh_db: str) -> None:
    """同一喷头用过多次时返回开阀时刻最晚的那次。"""
    device_id = "dev-last-used-3"
    for minutes, cert in ((-60, "OLD"), (-10, "NEW")):
        service.process_snapshot(
            device_id,
            _snapshot(
                [
                    _door(
                        1, card_no=1, cert_no=cert, open_time=rfc3339_after(minutes - 5), in_time=rfc3339_after(minutes)
                    )
                ]
            ),
        )
        service.process_snapshot(device_id, _snapshot([_door(1)]))

    row = repository.latest_usage_by_door()[(device_id, 1)]
    assert row["cert_no"] == "NEW"


def test_doors_api_exposes_last_used(fresh_db: str) -> None:
    """喷头列表带"上次使用"三个字段；从没用过的喷头给空值与 0。"""
    device_id = "dev-last-used-4"
    helpers.register_trusted_device(device_id)

    opened = rfc3339_after(-30)
    service.process_snapshot(
        device_id,
        _snapshot(
            [
                _door(1, card_no=7, cert_no="C", open_time=rfc3339_after(-60), in_time=opened),
                _door(2, card_no=8, cert_no="D", open_time=rfc3339_after(-600)),
            ]
        ),
    )
    service.process_snapshot(device_id, _snapshot([_door(1), _door(2)]))  # 两门先后释放

    doors = {d["door_no"]: d for d in repository.list_doors(device_id)}
    assert doors[1]["last_used_at"] == opened
    assert doors[1]["last_used_cert"] == "C"
    assert doors[1]["last_used_s"] > 0
    assert doors[2]["last_used_at"] == ""
    assert doors[2]["last_used_s"] == 0


def test_doors_api_is_public_empty_and_filtered(client: TestClient, fresh_db: str) -> None:
    """GET /doors 公开可查，支持按设备过滤；DoorView 带设备在场与名称。"""
    assert client.get("/api/v1/doors").json() == []

    device_id = "dev-doors-1"
    helpers.register_trusted_device(device_id)
    service.process_snapshot(device_id, _snapshot([_door(2)]))

    doors = client.get("/api/v1/doors", params={"device_id": device_id}).json()
    assert len(doors) == 1
    view = doors[0]
    assert view["door_no"] == 2
    assert view["state"] == "free"
    assert view["device_online"] is False
    assert view["device_name"] == ""
    assert {"device_id", "door_no", "state", "card_no", "cert_no", "reported_at"} <= set(view)
    assert {"last_used_at", "last_used_s", "last_used_cert"} <= set(view)

    assert client.get("/api/v1/doors").json()[0]["device_id"] == device_id  # 不带参数列全部设备


def test_doors_api_device_online_follows_connections(
    client: TestClient,
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """device_online 来自内存连接表，与档案是否存在无关。"""
    device_id = "dev-doors-online"
    service.process_snapshot(device_id, _snapshot([_door(1)]))

    assert client.get("/api/v1/doors").json()[0]["device_online"] is False

    from app.core.connections import manager

    monkeypatch.setattr(manager, "_connections", {device_id: object()})
    assert client.get("/api/v1/doors").json()[0]["device_online"] is True


def test_stream_door_update_carries_full_state_matching_rest(client: TestClient, fresh_db: str) -> None:
    """door.update 载荷是该设备完整现态行，与 GET /doors 逐字节同构（单一来源证明）。"""
    device_id = "dev-stream-door"
    with client.websocket_connect("/api/v1/stream") as ws:
        service.process_snapshot(device_id, _snapshot([_door(1, card_no=1001, cert_no="2530110052")]))
        message = json.loads(ws.receive_text())

    assert message["type"] == "door.update"
    payload = message["payload"]
    assert set(payload) == {"device_id", "at", "doors"}
    assert payload["device_id"] == device_id
    rest_rows = client.get("/api/v1/doors", params={"device_id": device_id}).json()
    assert payload["doors"] == rest_rows


def test_stream_pushes_device_update(client: TestClient, fresh_db: str) -> None:
    """设备经网关接入即向订阅者推 device.update（精简载荷）。"""
    device_id = "dev-stream-device"
    with client.websocket_connect("/api/v1/stream") as ws, client.websocket_connect("/gateway/device") as device_ws:
        device_ws.send_text(helpers.hello_frame(device_id))
        helpers.recv_until(device_ws, "link.hello_ack")
        message = json.loads(ws.receive_text())

    assert message["type"] == "device.update"
    payload = message["payload"]
    assert set(payload) == {"device_id", "name", "online", "at"}
    assert payload["device_id"] == device_id
    assert payload["online"] is True


def test_stream_with_device_filter_only_pushes_that_device(client: TestClient, fresh_db: str) -> None:
    """带 device_id 参数时服务端只推该设备的事件（握手期过滤）。"""
    target = "dev-stream-target"
    other = "dev-stream-other"
    with client.websocket_connect(f"/api/v1/stream?device_id={target}") as ws:
        service.process_snapshot(target, _snapshot([_door(1)]))  # 先推一条同步订阅状态
        first = json.loads(ws.receive_text())
        assert first["payload"]["device_id"] == target

        service.process_snapshot(other, _snapshot([_door(1)]))
        service.process_snapshot(target, _snapshot([_door(2)]))
        second = json.loads(ws.receive_text())

    assert second["type"] == "door.update"
    assert second["payload"]["device_id"] == target
    assert [door["door_no"] for door in second["payload"]["doors"]] == [1, 2]  # 完整现态，不因 other 事件混入


def test_stream_client_close_ends_session_cleanly(client: TestClient, fresh_db: str) -> None:
    """客户端主动关闭：服务端探测到帧即退出推送循环，收尾不残留。"""
    with client.websocket_connect("/api/v1/stream") as ws:
        service.process_snapshot("dev-stream-close", _snapshot([_door(1)]))
        json.loads(ws.receive_text())  # 确认推送通道活着
        ws.close()
