"""喷头域：七态派生、帧 diff 与事件、会话开合里程碑、上次使用、GET /doors 与 WS /stream。"""

import json
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core import db, device_clock
from app.core.clock import rfc3339_after
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


def _snapshot(
    doors: list[dict[str, Any]],
    captured_at: str | None = None,
    source: str = service.SOURCE_DB_WATCH,
) -> dict[str, Any]:
    """构造一帧。

    `captured_at` 是帧的采集时刻，也是推导里唯一的时刻来源。不给时服务端退回本机
    接收时刻——那对"隔了多久"这类断言没有意义（两帧在测试里只差几毫秒），所以凡是要
    验时长的用例都得显式给它。

    `source` 区分事件帧与全貌帧，后者只刷新现态、不派生（见 service 的常量说明）。
    """
    payload: dict[str, Any] = {"snapshot_id": str(uuid.uuid4()), "source": source, "doors": doors}
    if captured_at is not None:
        payload["captured_at"] = captured_at
    return payload


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
    """帧链路：状态落库、状态迁移记事件、会话随占用开合。"""
    device_id = "test-device-0002"
    service.process_snapshot(device_id, _snapshot([_door(1), _door(2)]))
    live = repository.get_all_live(device_id)
    assert len(live) == 2
    assert live[1]["state"] == DoorState.FREE.value

    # 三帧的采集时刻由测试显式给出。设备报的 open_time/in_time 只表达"有没有"，
    # 它们的值一律不参与计算——那是设备的墙上时钟，实测偏差可达 24 小时。
    t0 = "2026-09-30T12:00:00.000Z"
    t1 = "2026-09-30T12:01:00.000Z"
    t2 = "2026-09-30T12:02:00.000Z"
    opened = "2026-09-30T12:00:30.000Z"

    service.process_snapshot(device_id, _snapshot([_door(1, card_no=2001, cert_no="C1"), _door(2)], t0))
    live = repository.get_all_live(device_id)
    assert live[1]["state"] == DoorState.WAITING.value
    assert live[1]["card_no"] == 2001
    assert live[1]["reported_at"] == t0

    service.process_snapshot(device_id, _snapshot([_door(1, card_no=2001, cert_no="C1", in_time=opened)], t1))
    assert repository.get_all_live(device_id)[1]["state"] == DoorState.BATHING.value
    assert repository.get_open_session(device_id, 1) is not None

    service.process_snapshot(device_id, _snapshot([_door(1), _door(2)], t2))
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
    assert session["flow_s"] == 60  # t1 到 t2 那一段：上一帧还在出水，计入
    assert session["assigned_at"] == t0  # 这台设备没回过 pong、无偏差可平移，起点落在本帧
    # 设备从没报过 bath_time_s，我们却观测到 60 秒出水——两边对不上，完整性为假。
    # 它表达的正是这件事：我们的段之和与设备的累计值不一致。
    assert session["segments_complete"] == 0

    segments = conn.execute("SELECT * FROM usage_segment WHERE session_id=?", (session["id"],)).fetchall()
    assert len(segments) == 1
    assert segments[0]["opened_at"] == t1  # 无偏差可平移，开阀时刻退回首次观测到出水的那一帧
    assert segments[0]["flow_s"] == session["flow_s"]  # 会话时长就是段之和


def test_live_time_fields_are_on_the_server_axis(fresh_db: str) -> None:
    """现态的三个时间字段是**服务端轴**上的值，不是设备报的原话。

    设备那口钟的读数只保留在原始帧里。这台设备还没有偏差记录（帧比首次 pong 先到），
    所以换算不出来，字段是空的：给空值比给设备轴的原值安全。
    """
    device_id = "dev-rawtime-01"
    opened = "2026-09-30T12:00:00.000Z"

    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="C", in_time=opened)]))

    live = repository.get_all_live(device_id)
    assert live[1]["in_time"] == ""
    # 原话仍然完整地留在原始层里：现态是解释过的视图，原始帧才是设备说过什么。
    raw = db.get_conn().execute("SELECT doors FROM door_snapshot WHERE device_id = ?", (device_id,)).fetchone()
    assert opened in raw["doors"]


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


def test_snapshot_records_missing_doors_and_skips_nonpositive_door_no(fresh_db: str) -> None:
    """doors 缺失照收照落（记成空数组）；door_no 非正的条目跳过。

    缺失的 doors 不是协议错误，而是"设备这一帧没有可用的门数据"这条事实：原始层要
    留下它，派生跳过。回错误会让设备端不删发件箱那一行，把队列堵死。
    """
    device_id = "dev-bad-snap-1"
    empty = service.process_snapshot(device_id, {"snapshot_id": "s"})
    assert empty.processed == 0
    assert empty.events == ()
    recorded = db.get_conn().execute("SELECT doors FROM door_snapshot WHERE snapshot_id = ?", ("s",)).fetchall()
    assert [row["doors"] for row in recorded] == ["[]"]

    helpers.register_trusted_device(device_id)
    result = service.process_snapshot(device_id, _snapshot([_door(0), _door(1)]))
    assert result.processed == 1


def test_last_used_ignores_sessions_that_never_opened(fresh_db: str) -> None:
    """「上次使用」只认开过阀的会话：分配后直接结算那种不算。"""
    device_id = "dev-last-used-1"
    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="A", open_time=rfc3339_after(-600))]))
    service.process_snapshot(device_id, _snapshot([_door(1)]))

    assert (device_id, 1) not in repository.latest_usage_by_door()


def test_last_used_keeps_zero_length_bath(fresh_db: str) -> None:
    """开了阀立刻关（淋浴时长为 0）也要算一次使用：「上次使用」的判据是开阀时刻落过值，
    不是时长。统计层的"算不算用过"另有判据（`flow_s > 0`），两者不要混。"""
    device_id = "dev-last-used-2"
    t0 = "2026-09-30T12:00:00.000Z"
    service.process_snapshot(
        device_id, _snapshot([_door(1, card_no=2, cert_no="B", in_time="2026-09-30T12:00:00.000Z")], t0)
    )
    service.process_snapshot(device_id, _snapshot([_door(1)], "2026-09-30T12:00:00.500Z"))

    row = repository.latest_usage_by_door()[(device_id, 1)]
    assert row["cert_no"] == "B"
    assert row["opened_at"] == t0  # 无偏差可平移，开阀时刻退回本帧
    assert row["flow_s"] == 0  # 不足一秒，没有可计入的出水


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

    t0 = "2026-09-30T12:00:00.000Z"
    t1 = "2026-09-30T12:01:00.000Z"
    service.process_snapshot(
        device_id,
        _snapshot(
            [
                _door(1, card_no=7, cert_no="C", in_time="2026-09-30T12:00:30.000Z"),
                _door(2, card_no=8, cert_no="D"),
            ],
            t0,
        ),
    )
    service.process_snapshot(device_id, _snapshot([_door(1), _door(2)], t1))  # 两门先后释放

    doors = {d["door_no"]: d for d in repository.list_doors(device_id)}
    # last_used_at 是开阀时刻，无偏差可平移时退回首次观测到出水的那一帧
    assert doors[1]["last_used_at"] == t0
    assert doors[1]["last_used_cert"] == "C"
    assert doors[1]["last_used_s"] == 60  # t0 到 t1：上一帧还在出水，计入
    assert doors[2]["last_used_at"] == ""
    assert doors[2]["last_used_s"] == 0


def test_paused_keeps_this_segments_open_time(fresh_db: str) -> None:
    """已暂停时 `in_time` 仍给出**本段**的开阀时刻——不是空串，也不是别的段。

    设备在关阀那一刻清空了 `inTime`（`bathTime` 的累加与它同一条 SQL），帧里于是没有开阀
    时刻。但这一段是什么时候开的，是服务端记下过的事实（段表的 `opened_at`）：`in_time`
    的含义本来就是"当前这一段的开阀时刻"，与阀此刻开着还是关着无关——"此刻在不在出水"
    由 `state` 回答。它一空，管理台与小程序那一格就都跟着空。

    **分两段是有意的**：只验一段的话，"拿 `close_time − bath_time_s` 反推"与"取成第一
    段"这两种错法都看不出来。`bath_time_s` 是多段之和，反推出来会落在两段之间。
    """
    device_id = "dev-paused-open-time"
    t0 = "2026-09-30T12:00:00.000Z"  # 分配并开阀
    t1 = "2026-09-30T12:01:30.000Z"  # 第一段关阀（90 秒）
    t1b = "2026-09-30T12:03:00.000Z"  # 仍在同一段暂停里：关阀之后又来了一帧上报
    t2 = "2026-09-30T12:05:00.000Z"  # 第二段开阀
    t3 = "2026-09-30T12:07:30.000Z"  # 第二段关阀；bath_time_s 已累到 240
    device_clock.load(device_id, 0.0)

    service.process_snapshot(device_id, _snapshot([_door(1, acc_no="A001", open_time=t0, in_time=t0)], t0))
    service.process_snapshot(
        device_id, _snapshot([_door(1, acc_no="A001", open_time=t0, close_time=t1, bath_time_s=90)], t1)
    )
    live = repository.get_door_live(device_id, 1)
    assert live["state"] == "paused"
    assert live["in_time"] == t0, "第一段：这一段的开阀时刻"

    # 暂停会一直持续到结算，关阀之后每一帧都还要答得出（真机上这一段是几分钟到几十分钟）。
    # 只验关阀那一帧的话，"答案只活一帧"这种错法看不出来——而那正是运维实际看到的帧。
    service.process_snapshot(
        device_id, _snapshot([_door(1, acc_no="A001", open_time=t0, close_time=t1, bath_time_s=90)], t1b)
    )
    assert repository.get_door_live(device_id, 1)["in_time"] == t0, "同一段暂停的后续帧，答案不变"

    service.process_snapshot(
        device_id, _snapshot([_door(1, acc_no="A001", open_time=t0, in_time=t2, bath_time_s=90)], t2)
    )
    service.process_snapshot(
        device_id, _snapshot([_door(1, acc_no="A001", open_time=t0, close_time=t3, bath_time_s=240)], t3)
    )
    live = repository.get_door_live(device_id, 1)
    assert live["state"] == "paused"
    assert live["in_time"] == t2, "第二段：本段的开阀时刻，不是第一段的，也不是反推出来的"
    assert repository.list_doors(device_id)[0]["in_time"] == t2, "接口上给的是同一个值"


def test_waiting_still_has_no_open_time(fresh_db: str) -> None:
    """等待开阀时 `in_time` 仍为空：本会话还没有过任何一段，这个时刻不存在。

    与上一条成对。它挡住的是把"当前这一段"实现成"这扇门最近的一段"——上一场用过、
    本场还没开阀时，那种写法会把**上一场**的开阀时刻填进来。
    """
    device_id = "dev-waiting-open-time"
    t0 = "2026-09-30T12:00:00.000Z"
    t1 = "2026-09-30T12:02:00.000Z"
    device_clock.load(device_id, 0.0)

    # 先完整用一场：开阀 → 释放
    service.process_snapshot(device_id, _snapshot([_door(1, acc_no="A001", open_time=t0, in_time=t0)], t0))
    service.process_snapshot(device_id, _snapshot([_door(1, acc_no="A001", open_time=t0, in_time=t0)], t1))
    service.process_snapshot(device_id, _snapshot([_door(1)], "2026-09-30T12:03:00.000Z"))
    assert repository.list_doors(device_id)[0]["state"] == "free"

    # 再分配：新的一场，还没开阀
    service.process_snapshot(
        device_id,
        _snapshot([_door(1, acc_no="B002", open_time="2026-09-30T12:10:00.000Z")], "2026-09-30T12:10:00.000Z"),
    )
    view = repository.list_doors(device_id)[0]
    assert view["state"] == "waiting"
    assert view["in_time"] == "", "上一场开过阀，与本场无关"


def test_doors_api_is_public_empty_and_filtered(client: TestClient, fresh_db: str) -> None:
    """GET /doors 公开可查，支持按设备过滤；行只含门域字段（设备事实在设备资源）。"""
    assert client.get("/api/v1/doors").json() == []

    device_id = "dev-doors-1"
    helpers.register_trusted_device(device_id)
    service.process_snapshot(device_id, _snapshot([_door(2)]))

    doors = client.get("/api/v1/doors", params={"device_id": device_id}).json()
    assert len(doors) == 1
    view = doors[0]
    assert view["door_no"] == 2
    assert view["state"] == "free"
    assert {"device_id", "door_no", "state", "card_no", "cert_no", "reported_at"} <= set(view)
    assert {"last_used_at", "last_used_s", "last_used_cert"} <= set(view)
    # 设备域事实不进喷头行：设备在不在线、叫什么由 /devices 与 device.update 回答
    assert "device_online" not in view
    assert "device_name" not in view

    assert client.get("/api/v1/doors").json()[0]["device_id"] == device_id  # 不带参数列全部设备


def test_stream_door_update_carries_full_state_matching_rest(client: TestClient, fresh_db: str) -> None:
    """door.update 载荷是该设备完整现态行，与 GET /doors 逐字节同构（单一来源证明）。"""
    device_id = "dev-stream-door"
    with client.websocket_connect("/api/v1/stream") as ws:
        service.process_snapshot(device_id, _snapshot([_door(1, card_no=1001, cert_no="99990052")]))
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


def test_startup_snapshot_keeps_events_out_but_still_runs_the_session(fresh_db: str) -> None:
    """全貌帧（`source=startup`）不记迁移，但会话照常派生。

    它与"状态是否变化"无关——重连与开机都会推一遍，而设备侧状态可能一直没变，拿它写
    `door_state_event` 会记出一串从未在那一刻发生的迁移。**会话是另一回事**：那一帧带着
    设备当时的完整读数，占用中的门能从 `open_time` 平移出真实起点。曾经连会话一起跳过，
    代价是"重连时正有人洗着"的那一场整场消失。
    """
    device_id = "dev-startup-1"
    device_clock.load(device_id, 0.0)
    # 起始是空闲：这一帧走 db_watch，且它不涉及占用
    service.process_snapshot(device_id, _snapshot([_door(1)], "2026-09-30T12:00:00.000Z"))

    # 全貌帧里喷头显示被占着、正在出水。它是"当前是什么样"，不是"刚刚有人分配进来"
    service.process_snapshot(
        device_id,
        _snapshot(
            [
                _door(
                    1,
                    card_no=1,
                    cert_no="A",
                    open_time="2026-09-30T12:01:00.000Z",
                    in_time="2026-09-30T12:04:00.000Z",
                )
            ],
            "2026-09-30T12:05:00.000Z",
            service.SOURCE_STARTUP,
        ),
    )

    assert repository.get_all_live(device_id)[1]["state"] == DoorState.BATHING.value  # 现态确实刷新了
    assert db.get_conn().execute("SELECT COUNT(*) FROM door_state_event").fetchone()[0] == 0  # 但没有迁移
    session = repository.get_open_session(device_id, 1)
    assert session is not None, "会话照常开出来"
    assert session["assigned_at"] == "2026-09-30T12:01:00.000Z"  # 起点由 open_time 平移
    segment = db.get_conn().execute("SELECT * FROM usage_segment WHERE session_id = ?", (session["id"],)).fetchone()
    assert segment is not None, "全貌帧同样要开段——它挡住的只是事件流水"
    assert segment["opened_at"] == "2026-09-30T12:04:00.000Z"  # 开阀时刻由 in_time 平移


def test_startup_snapshot_closes_the_session_it_finds_abandoned(fresh_db: str) -> None:
    """全貌帧发现门已经空了，就把开着的那场结掉。

    设备离线期间人走了，重连时看到的是收尾后的样子。不结的话那场会话永远挂在"进行中"，
    每一个"当前占用"的统计都多算一条。

    **结束时刻落在我们最后看见它的那一帧**，不是重连这一刻：两帧隔了 30 分钟，超过一次
    占用的物理上限，中间必然发生过一次我们没看见的释放。按重连时刻结尾会晚整整半小时，
    而且那种偏晚是系统性的——离线越久偏得越多。
    """
    device_id = "dev-startup-2"
    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="A")], "2026-09-30T12:00:00.000Z"))

    service.process_snapshot(
        device_id,
        _snapshot([_door(1)], "2026-09-30T12:30:00.000Z", service.SOURCE_STARTUP),
    )

    assert repository.get_open_session(device_id, 1) is None
    row = db.get_conn().execute("SELECT * FROM usage_session WHERE device_id = ?", (device_id,)).fetchone()
    assert row["ended_at"] == "2026-09-30T12:00:00.000Z"
    assert row["observed_span"] == "gap"  # 中间那半小时我们没看见


def test_last_seen_stops_at_the_last_occupied_frame(fresh_db: str) -> None:
    """`last_seen_at` 停在最后一次确认占着的那一帧，结束帧不动它。

    它把"结束时刻可不可信"变成一个判据：`ended_at` 与它的差就是观测盲区。
    """
    device_id = "dev-lastseen-1"
    t0 = "2026-09-30T12:00:00.000Z"
    t1 = "2026-09-30T12:01:00.000Z"
    t2 = "2026-09-30T12:02:00.000Z"

    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="A")], t0))
    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="A", in_time=t0)], t1))
    service.process_snapshot(device_id, _snapshot([_door(1)], t2))

    row = db.get_conn().execute("SELECT * FROM usage_session WHERE device_id = ?", (device_id,)).fetchone()
    assert row["assigned_at"] == t0
    assert row["ended_at"] == t2
    assert row["last_seen_at"] == t1  # 不是 t2——结束那一帧恰恰证明它已经不占了


def test_gap_beyond_session_limit_is_not_counted(fresh_db: str) -> None:
    """两帧间隔超过会话上限：中间必然发生过一次没看见的释放，那段出水不计。

    设备满 SESSION_MAX_S 会强制释放，所以横跨这个间隔的"同一段水"不可能存在——宁可
    少算，也不能把两段接成一段。
    """
    device_id = "dev-gap-1"
    t0 = "2026-09-30T12:00:00.000Z"
    far = "2026-09-30T12:30:00.000Z"  # 与 t0 相隔 1800 秒 > SESSION_MAX_S

    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="A", in_time=t0)], t0))
    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="A", in_time=t0)], far))

    row = db.get_conn().execute("SELECT flow_s FROM usage_session WHERE device_id = ?", (device_id,)).fetchone()
    assert row["flow_s"] == 0


def test_session_survives_process_restart(fresh_db: str) -> None:
    """进程重启不丢状态：会话的延续全靠库里的两处落点。

    处理一帧用到的一切都在表里——`door_live` 给出上一帧的状态与时刻，`usage_session`
    给出进行中的会话与已累计的出水。处理过程本身没有内存状态（只剩一把按设备分的锁，
    而锁不承载语义），所以"重启"不是一件需要恢复的事，只是下一帧照常处理。

    这个用例同时钉住"重启后不会把同一次占用重开成两条会话"。
    """
    device_id = "dev-restart-1"
    t0 = "2026-09-30T12:00:00.000Z"
    t1 = "2026-09-30T12:01:00.000Z"

    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="A", in_time=t0)], t0))

    # 模拟重启：清掉进程内的一切。库不动——这正是状态所在之处。
    service._device_locks.clear()

    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="A", in_time=t0)], t1))

    conn = db.get_conn()
    rows = conn.execute("SELECT * FROM usage_session WHERE device_id = ?", (device_id,)).fetchall()
    assert len(rows) == 1, "重启不该把同一次占用重开成两条会话"
    assert rows[0]["assigned_at"] == t0, "起点仍是原来那一帧"
    assert rows[0]["flow_s"] == 60, "上一帧在出水的证据跨重启仍然有效"


def test_door_no_must_be_a_strict_in_range_integer(fresh_db: str) -> None:
    """门号必须是 1..48 的严格整数；不截断、不宽容。

    门号决定"这是哪个喷头"。`3.7` 截成 `3` 会把一条坏数据伪装成 3 号喷头的真实读数，
    脏账记到别人头上——其余数值字段可以宽容，这一个不行。
    """
    device_id = "dev-door-no"
    result = service.process_snapshot(
        device_id,
        _snapshot([_door(1), _door("2"), _door(3.0), _door(3.7), _door(0), _door(49), _door("abc")]),
    )
    assert result.processed == 3  # 只有 1、"2"、3.0 合法
    assert sorted(repository.get_all_live(device_id)) == [1, 2, 3]


def test_duplicate_door_no_in_one_frame_keeps_the_last(fresh_db: str) -> None:
    """同一帧里重复的门号取最后一条。

    设备按门号整行读取，重复只可能来自拼装错误，取后者与"后来者覆盖"的直觉一致。
    一帧只处理一次——否则"甲"会先开一条会话、"乙"再触发换人，同一帧里凭空多出一个
    人和一条账。
    """
    device_id = "dev-dup-door"
    service.process_snapshot(device_id, _snapshot([_door(1)]))
    service.process_snapshot(
        device_id, _snapshot([_door(1, card_no=1, cert_no="甲"), _door(1, card_no=2, cert_no="乙")])
    )

    live = repository.get_all_live(device_id)
    assert live[1]["cert_no"] == "乙"

    # 去重之后只开一条会话。不去重的话"甲"会先开一条、"乙"再触发换人结旧开新——同一帧
    # 里凭空多出一个人和一条账。
    sessions = db.get_conn().execute("SELECT cert_no FROM usage_session WHERE device_id = ?", (device_id,)).fetchall()
    assert [row["cert_no"] for row in sessions] == ["乙"]


def test_numeric_fields_are_clamped_not_trusted(fresh_db: str) -> None:
    """负卡号归零、出水秒数夹进 [0, 一天]。

    负卡号会凭空造出一个占用者（占用判据是"卡号非 0"）；出水秒数的负值与天文数字都只
    可能是脏数据。夹到一天而不是设备上限——那个上限由配置下发、随时可能变大。
    """
    device_id = "dev-clamp"
    service.process_snapshot(device_id, _snapshot([_door(1, card_no=-5, cert_no="A", bath_time_s=-1)]))
    assert repository.get_all_live(device_id)[1]["state"] == DoorState.FREE.value

    service.process_snapshot(device_id, _snapshot([_door(1, card_no=1, cert_no="A", bath_time_s=99_999_999)]))
    assert repository.get_all_live(device_id)[1]["bath_time_s"] == 86400


def test_occupant_falls_back_to_acc_no_when_cert_no_is_blank(fresh_db: str) -> None:
    """证件号为空时占用者退回账户号。

    退回只覆盖极端输入（实测两者始终同时有值），但少了它，一条只有 `acc_no` 的帧会被
    当成"无人认领"，按人统计时凭空多出一个空身份。
    """
    device_id = "dev-occupant"
    service.process_snapshot(device_id, _snapshot([_door(1, card_no=0, cert_no="", acc_no="1010209501")]))

    session = repository.get_open_session(device_id, 1)
    assert session is not None
    assert session["cert_no"] == "1010209501"


def test_derivation_failure_returns_ok_and_keeps_the_raw_frame(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """派生失败在 service 内部止住：返回 ok、原始帧在库。

    这是回执语义的上半句：帧已经收下，派生可以事后从原始层重算，回执让设备删掉
    发件箱那一行才是对的。
    """

    failure = RuntimeError("boom")

    def boom(_door: object) -> object:
        raise failure

    monkeypatch.setattr(service, "derive_state", boom)

    result = service.process_snapshot("dev-derive-fail", _snapshot([_door(1)]))

    assert result.processed == 0
    assert result.events == ()
    row = db.read_one("SELECT COUNT(*) AS n FROM door_snapshot WHERE device_id = ?", ("dev-derive-fail",))
    assert row is not None
    assert row["n"] == 1


def test_raw_insert_failure_propagates_for_the_gateway_to_withhold_the_ack(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """原始帧落不下去就抛错：网关据此扣下回执，设备保留那一行稍后重发。

    这是回执语义的下半句——ack 与「原始帧已落库」严格等价，磁盘满、库锁超时
    都不会让一帧事实从世界上消失。
    """

    failure = RuntimeError("disk full")

    def boom(*_args: object, **_kwargs: object) -> object:
        raise failure

    monkeypatch.setattr(repository, "insert_snapshot", boom)

    with pytest.raises(RuntimeError, match="disk full"):
        service.process_snapshot("dev-raw-fail", _snapshot([_door(1)]))
