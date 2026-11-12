"""门域播报事件：五类迁移、首帧/全貌/重放/盲区/换人的排除。

这些用例只验证 `doors` 产出的事件；事件到语音规则的匹配在 voice 的测试里。
"""

import uuid
from typing import Any

from tests import door_link

from app.core import device_clock
from app.modules.doors import service
from app.modules.doors.events import DoorEvent, DoorEventKind


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
    payload: dict[str, Any] = {"snapshot_id": str(uuid.uuid4()), "source": source, "doors": doors}
    if captured_at is not None:
        payload["captured_at"] = captured_at
    return payload


def _process(
    device_id: str,
    doors: list[dict[str, Any]],
    at: str,
    source: str = service.SOURCE_DB_WATCH,
) -> tuple[DoorEvent, ...]:
    return door_link.feed(device_id, _snapshot(doors, at, source)).events


def _occupied(cert_no: str = "C1", **overrides: object) -> dict[str, Any]:
    return _door(1, card_no=1001, cert_no=cert_no, **overrides)


def _bathing(cert_no: str = "C1") -> dict[str, Any]:
    return _occupied(cert_no, open_time="2026-09-30T12:00:00.000Z", in_time="2026-09-30T12:00:30.000Z")


def _paused(cert_no: str = "C1") -> dict[str, Any]:
    return _occupied(
        cert_no,
        open_time="2026-09-30T12:00:00.000Z",
        close_time="2026-09-30T12:01:00.000Z",
        bath_time_s=30,
    )


T0 = "2026-09-30T12:00:00.000Z"
T1 = "2026-09-30T12:01:00.000Z"
T2 = "2026-09-30T12:02:00.000Z"
T3 = "2026-09-30T12:03:00.000Z"


def test_free_to_waiting_emits_assigned(fresh_db: str) -> None:
    device_id = "voice-event-assigned"
    _process(device_id, [_door(1)], T0)
    events = _process(device_id, [_occupied()], T1)

    assert [event.kind for event in events] == [DoorEventKind.ASSIGNED]
    assert events[0].cert_no == "C1"
    assert events[0].door_no == 1
    assert events[0].event_key == f"session:{events[0].session_id}:assigned"


def test_first_observed_occupancy_emits_nothing(fresh_db: str) -> None:
    device_id = "voice-event-first"
    assert _process(device_id, [_occupied()], T0) == ()


def test_waiting_to_bathing_emits_shower_started(fresh_db: str) -> None:
    device_id = "voice-event-start"
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied()], T1)
    events = _process(device_id, [_bathing()], T2)

    assert [event.kind for event in events] == [DoorEventKind.SHOWER_STARTED]
    assert events[0].cert_no == "C1"
    assert events[0].event_key.startswith("door_event:")


def test_bathing_to_paused_emits_paused(fresh_db: str) -> None:
    device_id = "voice-event-pause"
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied()], T1)
    _process(device_id, [_bathing()], T2)
    events = _process(device_id, [_paused()], T3)

    assert [event.kind for event in events] == [DoorEventKind.PAUSED]


def test_paused_to_bathing_emits_resumed(fresh_db: str) -> None:
    device_id = "voice-event-resume"
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied()], T1)
    _process(device_id, [_bathing()], T2)
    _process(device_id, [_paused()], T3)
    events = _process(device_id, [_bathing()], "2026-09-30T12:04:00.000Z")

    assert [event.kind for event in events] == [DoorEventKind.RESUMED]


def test_waiting_to_free_emits_released_unused(fresh_db: str) -> None:
    device_id = "voice-event-release"
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied()], T1)
    events = _process(device_id, [_door(1)], T2)

    assert [event.kind for event in events] == [DoorEventKind.RELEASED_UNUSED]
    assert events[0].cert_no == "C1"
    assert events[0].event_key.endswith(":released_unused")


def test_bathing_to_free_emits_released_after_shower(fresh_db: str) -> None:
    device_id = "voice-event-release-shower"
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied()], T1)
    _process(device_id, [_bathing()], T2)
    events = _process(device_id, [_door(1)], T3)

    assert [event.kind for event in events] == [DoorEventKind.RELEASED_AFTER_SHOWER]
    assert events[0].event_key.endswith(":released_after_shower")


def test_paused_to_free_emits_released_while_paused(fresh_db: str) -> None:
    device_id = "voice-event-release-paused"
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied()], T1)
    _process(device_id, [_bathing()], T2)
    _process(device_id, [_paused()], T3)
    events = _process(device_id, [_door(1)], "2026-09-30T12:05:00.000Z")

    assert [event.kind for event in events] == [DoorEventKind.RELEASED_WHILE_PAUSED]
    assert events[0].event_key.endswith(":released_while_paused")


def test_free_to_bathing_emits_only_assigned(fresh_db: str) -> None:
    device_id = "voice-event-direct-bathing"
    _process(device_id, [_door(1)], T0)
    events = _process(device_id, [_bathing()], T1)

    assert [event.kind for event in events] == [DoorEventKind.ASSIGNED]


def test_waiting_to_paused_emits_nothing(fresh_db: str) -> None:
    device_id = "voice-event-waiting-paused"
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied()], T1)
    assert _process(device_id, [_paused()], T2) == ()


def test_occupant_change_announces_new_only(fresh_db: str) -> None:
    device_id = "voice-event-change"
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied("C1")], T1)
    events = _process(device_id, [_occupied("C2")], T2)

    assert [event.kind for event in events] == [DoorEventKind.ASSIGNED]
    assert events[0].cert_no == "C2"


def test_startup_and_import_emit_nothing_without_open_time(fresh_db: str) -> None:
    """没有 open_time 时只有观测下界；startup/import 都不补 assigned。"""
    device_id = "voice-event-source"
    _process(device_id, [_door(1)], T0)
    assert _process(device_id, [_occupied()], T1, source=service.SOURCE_STARTUP) == ()
    assert _process(device_id, [_occupied()], T2, source=service.SOURCE_IMPORT) == ()


def test_startup_waiting_with_open_time_emits_assigned(fresh_db: str) -> None:
    """重连全貌帧看到仍在等待开阀、open_time 可平移：补一条真实分配时刻的 assigned。"""
    device_id = "voice-event-startup-waiting"
    device_clock.load(device_id, 0.0)
    _process(device_id, [_door(1)], T0)
    events = _process(device_id, [_occupied(open_time=T1)], T2, source=service.SOURCE_STARTUP)

    assert [event.kind for event in events] == [DoorEventKind.ASSIGNED]
    assert events[0].cert_no == "C1"
    assert events[0].occurred_at == T1
    assert events[0].event_key == f"session:{events[0].session_id}:assigned"


def test_startup_bathing_with_open_time_emits_assigned(fresh_db: str) -> None:
    """重连时已经在淋浴也一样：会话是新发现的，分配事实仍成立。"""
    device_id = "voice-event-startup-bathing"
    device_clock.load(device_id, 0.0)
    _process(device_id, [_door(1)], T0)
    events = _process(device_id, [_bathing()], T1, source=service.SOURCE_STARTUP)

    assert [event.kind for event in events] == [DoorEventKind.ASSIGNED]
    assert events[0].occurred_at == T0


def test_gap_with_open_time_emits_assigned(fresh_db: str) -> None:
    """跨盲区另起一场时，只要 open_time 可平移，仍补 assigned；释放仍不补。"""
    device_id = "voice-event-gap-assigned"
    device_clock.load(device_id, 0.0)
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied("C1")], T1)
    events = _process(
        device_id,
        [_occupied("C2", open_time="2026-09-30T12:20:00.000Z")],
        "2026-09-30T12:21:00.000Z",
    )

    assert [event.kind for event in events] == [DoorEventKind.ASSIGNED]
    assert events[0].cert_no == "C2"
    assert events[0].occurred_at == "2026-09-30T12:20:00.000Z"


def test_import_with_open_time_emits_nothing(fresh_db: str) -> None:
    """历史重放只回放数据，不补播。"""
    device_id = "voice-event-import-open-time"
    device_clock.load(device_id, 0.0)
    _process(device_id, [_door(1)], T0)
    assert _process(device_id, [_occupied(open_time=T1)], T2, source=service.SOURCE_IMPORT) == ()


def test_startup_release_without_gap_emits_release(fresh_db: str) -> None:
    """重连全貌帧发现上一帧还占着、这一帧空了，且间隔未超上限：释放仍是有效生命周期事实。"""
    device_id = "voice-event-startup-release"
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied()], T1)
    events = _process(device_id, [_door(1)], T2, source=service.SOURCE_STARTUP)

    assert [event.kind for event in events] == [DoorEventKind.RELEASED_UNUSED]
    assert events[0].cert_no == "C1"
    assert events[0].occurred_at == T2


def test_startup_assigned_is_not_repeated_by_next_db_watch(fresh_db: str) -> None:
    device_id = "voice-event-startup-once"
    device_clock.load(device_id, 0.0)
    _process(device_id, [_door(1)], T0)
    first = _process(device_id, [_occupied(open_time=T1)], T2, source=service.SOURCE_STARTUP)
    second = _process(device_id, [_occupied(open_time=T1)], T3)

    assert [event.kind for event in first] == [DoorEventKind.ASSIGNED]
    assert second == ()


def test_gap_suppresses_release_and_new_assignment(fresh_db: str) -> None:
    device_id = "voice-event-gap"
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied()], T1)
    # 跨过一次占用的物理上限：旧的会话按盲区收尾，新会话另起，但都不播。
    events = _process(device_id, [_occupied()], "2026-09-30T12:21:00.000Z")
    assert events == ()


def test_event_keys_are_stable_across_duplicate_frames(fresh_db: str) -> None:
    device_id = "voice-event-stable"
    _process(device_id, [_door(1)], T0)
    _process(device_id, [_occupied()], T1)
    first = _process(device_id, [_bathing()], T2)
    second = _process(device_id, [_bathing()], T3)

    assert len(first) == 1
    assert second == ()  # 现态已经是淋浴中，重复帧不再产出迁移
    assert first[0].event_key.startswith("door_event:")
