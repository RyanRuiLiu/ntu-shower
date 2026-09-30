"""doors 模块测试：状态派生、事件流水、洗浴会话开合。"""

import pytest
from fastapi.testclient import TestClient

from app.core.clock import rfc3339_after
from app.main import create_app
from app.modules.doors import repository, service
from app.modules.doors.schemas import DoorState


@pytest.fixture
def client(fresh_db: str) -> TestClient:
    return TestClient(create_app())


def _door(door_no: int, **overrides: object) -> dict:
    door = {
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


def _snapshot(doors: list[dict], snapshot_id: str = "11111111-1111-4111-8111-111111111111") -> dict:
    return {"snapshot_id": snapshot_id, "source": "db_watch", "doors": doors}


# ── derive_state ──


def test_free() -> None:
    assert service.derive_state(_door(1)) == DoorState.FREE


def test_repair_priority_over_occupied() -> None:
    door = _door(1, status_raw=1, card_no=123, in_time="2026-09-29T12:00:00.000Z")
    assert service.derive_state(door) == DoorState.REPAIR


def test_disabled() -> None:
    assert service.derive_state(_door(1, status_raw=3)) == DoorState.DISABLED


def test_waiting_entered_but_not_started() -> None:
    door = _door(1, card_no=1001, open_time="2026-09-29T12:00:00.000Z")
    assert service.derive_state(door) == DoorState.WAITING


def test_bathing_when_in_time_set() -> None:
    door = _door(1, card_no=1001, open_time="2026-09-29T12:00:00.000Z", in_time="2026-09-29T12:01:00.000Z")
    assert service.derive_state(door) == DoorState.BATHING


def test_paused_after_close() -> None:
    door = _door(
        1,
        card_no=1001,
        open_time="2026-09-29T12:00:00.000Z",
        close_time="2026-09-29T12:10:00.000Z",
        bath_time_s=300,
    )
    assert service.derive_state(door) == DoorState.PAUSED


def test_selected_rare_external_state() -> None:
    assert service.derive_state(_door(1, status_raw=2)) == DoorState.SELECTED


def test_accno_alone_occupies() -> None:
    assert service.derive_state(_door(1, acc_no="A001")) == DoorState.WAITING


# ── 快照处理：现态 / 事件 / 会话 ──


def test_snapshot_flow(client: TestClient) -> None:
    device_id = "test-device-0002"
    service.process_snapshot(device_id, _snapshot([_door(1), _door(2)]))

    # 首次快照：落现态，无事件
    live = repository.get_all_live(device_id)
    assert len(live) == 2
    assert live[1]["state"] == DoorState.FREE.value

    # 时间戳必须相对当前时刻构造：服务端算"截至此刻的出水时长"用的是**收到快照的
    # 时刻**减去设备的 inTime，写死一个过去的日期会算出几天几夜。
    assigned = rfc3339_after(-120)
    opened = rfc3339_after(-60)

    # 第二次：1 号门被占用进门 → 事件 free→waiting + 会话开启
    service.process_snapshot(
        device_id,
        _snapshot([_door(1, card_no=2001, cert_no="C1", open_time=assigned), _door(2)]),
    )
    live = repository.get_all_live(device_id)
    assert live[1]["state"] == DoorState.WAITING.value
    assert live[1]["card_no"] == 2001

    # 进门后开水 → bathing，同一会话延续
    service.process_snapshot(
        device_id,
        _snapshot([_door(1, card_no=2001, cert_no="C1", open_time=assigned, in_time=opened)]),
    )
    live = repository.get_all_live(device_id)
    assert live[1]["state"] == DoorState.BATHING.value
    assert repository.get_open_session(device_id, 1) is not None

    # 出门释放 → 会话收尾
    service.process_snapshot(device_id, _snapshot([_door(1), _door(2)]))
    live = repository.get_all_live(device_id)
    assert live[1]["state"] == DoorState.FREE.value

    session = repository.get_open_session(device_id, 1)
    assert session is None

    # 事件与落库核验
    from app.core.db import get_conn

    events = (
        get_conn()
        .execute(
            "SELECT from_state, to_state FROM door_state_event WHERE device_id=? AND door_no=1 ORDER BY id",
            (device_id,),
        )
        .fetchall()
    )
    assert [(e["from_state"], e["to_state"]) for e in events] == [
        (DoorState.FREE.value, DoorState.WAITING.value),
        (DoorState.WAITING.value, DoorState.BATHING.value),
        (DoorState.BATHING.value, DoorState.FREE.value),
    ]
    sessions = get_conn().execute("SELECT * FROM usage_session WHERE device_id=?", (device_id,)).fetchall()
    assert len(sessions) == 1
    s = sessions[0]
    assert s["card_no"] == 2001
    assert s["ended_at"] is not None
    # 这一趟确实出过水：opened_at 有值（区别于"分配后从没开机"），时长接近一分钟。
    # 这两个字段是"算不算一次使用"的全部依据——会话表不记结束原因，因为设备在用户
    # 主动出门和三种超时释放下写的是同一套清零语句，服务端没有依据区分。
    assert s["opened_at"] is not None
    assert s["flow_s"] >= 55


def test_snapshot_via_websocket(client: TestClient) -> None:
    device_id = "test-device-0003"
    import json as jsonlib
    import uuid as uuidlib

    snapshot_id = str(uuidlib.uuid4())
    hello = {
        "v": 1,
        "type": "link.hello",
        "id": str(uuidlib.uuid4()),
        "ts": "2026-09-29T12:00:00.000Z",
        "payload": {"device_id": device_id, "app_version": "0.1.0", "os_version": "4.4.4", "capabilities": []},
    }

    # 第一次连接：建档（未信任，此时设备收不到 ack）
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(jsonlib.dumps(hello))
        ack = jsonlib.loads(ws.receive_text())
        assert ack["payload"]["trusted"] is False

    # 后台信任
    r = client.patch(f"/ops/devices/{device_id}", headers={"X-Ops-Token": "test-token"}, json={"trusted": True})
    assert r.status_code == 200

    # 重新连接：已信任，快照应收到 ack
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(jsonlib.dumps(hello))
        jsonlib.loads(ws.receive_text())  # hello_ack (trusted=true)
        ws.send_text(
            jsonlib.dumps(
                {
                    "v": 1,
                    "type": "door.snapshot",
                    "id": str(uuidlib.uuid4()),
                    "ts": "2026-09-29T12:00:01.000Z",
                    "payload": _snapshot([_door(1)], snapshot_id),
                }
            )
        )
        ack = jsonlib.loads(ws.receive_text())
        assert ack["type"] == "door.snapshot_ack"
        assert ack["payload"]["snapshot_id"] == snapshot_id


# ── 上次使用 ──


def test_last_used_ignores_sessions_that_never_opened(client: TestClient) -> None:
    """「上次使用」只认**开过阀**的会话。

    分配后直接结算那种（从没出过水）不算——这是需求里点名要排除的场景。
    """
    device_id = "dev-last-used-1"

    # 一次完整的"进门又马上走"：有分配、有时间，但从没开阀
    service.process_snapshot(
        device_id,
        _snapshot([_door(1, card_no=1, cert_no="A", open_time=rfc3339_after(-600))]),
    )
    service.process_snapshot(device_id, _snapshot([_door(1)]))

    assert (device_id, 1) not in repository.latest_usage_by_door(), "从没开过阀的会话不该算作一次使用"


def test_last_used_keeps_zero_length_bath(client: TestClient) -> None:
    """开了阀立刻关（出水时长接近 0）**要算**一次使用。

    它与"从没出过水"在设备的数据库里长得一样（`bathTime` 都是 0），区别只在
    `inTime` 有没有出现过——所以判据是 `opened_at` 而不是时长。
    """
    device_id = "dev-last-used-2"
    opened = rfc3339_after(-5)

    service.process_snapshot(
        device_id,
        _snapshot([_door(1, card_no=2, cert_no="B", open_time=rfc3339_after(-20), in_time=opened)]),
    )
    service.process_snapshot(device_id, _snapshot([_door(1)]))

    row = repository.latest_usage_by_door()[(device_id, 1)]
    assert row["cert_no"] == "B"
    assert row["opened_at"] == opened


def test_last_used_returns_the_most_recent(client: TestClient) -> None:
    """同一个喷头用过多次时，返回**开阀时刻最晚**的那次，而不是最早或最后写入的。"""
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


def test_doors_api_exposes_last_used(client: TestClient) -> None:
    """管理面的喷头列表要带上"上次使用"三个字段；从没用过的喷头给空值与 0。"""
    from app.modules.devices import repository as devices_repository

    device_id = "dev-last-used-4"
    devices_repository.register(device_id, "0.3.19", 26, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted(device_id, True)

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
    # 1 号门洗着；2 号门是"进门后没开水"的占用态
    service.process_snapshot(device_id, _snapshot([_door(1), _door(2)]))

    doors = {d["door_no"]: d for d in repository.list_doors(device_id)}
    assert doors[1]["last_used_at"] == opened
    assert doors[1]["last_used_cert"] == "C"
    assert doors[2]["last_used_at"] == "", "2 号喷头从没开过阀，不该有上次使用"
