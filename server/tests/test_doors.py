"""doors 模块测试：状态派生、事件流水、洗浴会话开合。"""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.modules.doors import repository, service


@pytest.fixture
def client(fresh_db):
    from fastapi.testclient import TestClient

    return TestClient(create_app())


def _door(door_no: int, **overrides) -> dict:
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
    assert service.derive_state(_door(1)) == service.STATE_FREE


def test_repair_priority_over_occupied() -> None:
    door = _door(1, status_raw=1, card_no=123, in_time="2026-09-29T12:00:00.000Z")
    assert service.derive_state(door) == service.STATE_REPAIR


def test_disabled() -> None:
    assert service.derive_state(_door(1, status_raw=3)) == service.STATE_DISABLED


def test_waiting_entered_but_not_started() -> None:
    door = _door(1, card_no=1001, open_time="2026-09-29T12:00:00.000Z")
    assert service.derive_state(door) == service.STATE_WAITING


def test_bathing_when_in_time_set() -> None:
    door = _door(1, card_no=1001, open_time="2026-09-29T12:00:00.000Z", in_time="2026-09-29T12:01:00.000Z")
    assert service.derive_state(door) == service.STATE_BATHING


def test_paused_after_close() -> None:
    door = _door(
        1,
        card_no=1001,
        open_time="2026-09-29T12:00:00.000Z",
        close_time="2026-09-29T12:10:00.000Z",
        bath_time_s=300,
    )
    assert service.derive_state(door) == service.STATE_PAUSED


def test_selected_rare_external_state() -> None:
    assert service.derive_state(_door(1, status_raw=2)) == service.STATE_SELECTED


def test_accno_alone_occupies() -> None:
    assert service.derive_state(_door(1, acc_no="A001")) == service.STATE_WAITING


# ── 快照处理：现态 / 事件 / 会话 ──


def test_snapshot_flow(client: TestClient) -> None:
    device_id = "test-device-0002"
    service.process_snapshot(device_id, _snapshot([_door(1), _door(2)]))

    # 首次快照：落现态，无事件
    live = repository.get_all_live(device_id)
    assert len(live) == 2 and live[1]["state"] == service.STATE_FREE

    # 第二次：1 号门被占用进门 → 事件 free→waiting + 会话开启
    service.process_snapshot(
        device_id,
        _snapshot([_door(1, card_no=2001, cert_no="C1", open_time="2026-09-29T12:30:00.000Z"), _door(2)]),
    )
    live = repository.get_all_live(device_id)
    assert live[1]["state"] == service.STATE_WAITING and live[1]["card_no"] == 2001

    # 进门后开水 → bathing，同一会话延续
    service.process_snapshot(
        device_id,
        _snapshot(
            [
                _door(
                    1,
                    card_no=2001,
                    cert_no="C1",
                    open_time="2026-09-29T12:30:00.000Z",
                    in_time="2026-09-29T12:31:00.000Z",
                )
            ]
        ),
    )
    live = repository.get_all_live(device_id)
    assert live[1]["state"] == service.STATE_BATHING
    assert repository.get_open_session(device_id, 1) is not None

    # 出门释放 → 会话收尾
    service.process_snapshot(device_id, _snapshot([_door(1), _door(2)]))
    live = repository.get_all_live(device_id)
    assert live[1]["state"] == service.STATE_FREE

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
        (service.STATE_FREE, service.STATE_WAITING),
        (service.STATE_WAITING, service.STATE_BATHING),
        (service.STATE_BATHING, service.STATE_FREE),
    ]
    sessions = get_conn().execute("SELECT * FROM usage_session WHERE device_id=?", (device_id,)).fetchall()
    assert len(sessions) == 1
    s = sessions[0]
    assert s["card_no"] == 2001 and s["ended_at"] is not None and s["end_reason"] == "checkout"


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
    r = client.post(f"/admin/devices/{device_id}/trust", headers={"X-Admin-Token": "test-token"})
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
