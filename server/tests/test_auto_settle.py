"""自动结算：pick_auto_settle_candidate 纯函数的取舍规则与扫描入口的在线门槛。"""

import asyncio
from typing import Any

import pytest

from app.core.clock import rfc3339_ago
from app.modules.doors import repository as doors_repository
from app.modules.doors import service as doors_service
from app.modules.sessions import auto_settle

THRESHOLD_S = 600
STUDENT_CONFIG: dict[str, Any] = {
    "auto_settle_enabled": True,
    "auto_settle_threshold_s": THRESHOLD_S,
    "auto_settle_students": {"99990052": "000000"},
}


def _seed_open_session(device_id: str, door_no: int, cert_no: str, assigned_seconds_ago: int) -> None:
    """直接在 usage_session 里开一条指定分配时刻的会话。

    `assigned_source` 取 observed：这条是测试直接造出来的，没有经过推导，也就没有
    设备报的 open_time 可平移——那正是"观测下界"的语义。
    """
    doors_repository.open_session(
        device_id,
        door_no,
        900 + door_no,
        cert_no,
        rfc3339_ago(assigned_seconds_ago),
        doors_service.SESSION_ASSIGNED_OBSERVED,
    )


def test_pick_candidate_requires_enabled(fresh_db: str) -> None:
    _seed_open_session("dev-as-1", 1, "99990052", 30 * 60)

    disabled = {**STUDENT_CONFIG, "auto_settle_enabled": False}
    assert auto_settle.pick_auto_settle_candidate(disabled) is None
    assert auto_settle.pick_auto_settle_candidate(STUDENT_CONFIG) is not None


def test_pick_candidate_requires_listed_student(fresh_db: str) -> None:
    """名单外学员不自动结算。"""
    _seed_open_session("dev-as-1", 1, "99990052", 30 * 60)

    config = {**STUDENT_CONFIG, "auto_settle_students": {"other": "x"}}
    assert auto_settle.pick_auto_settle_candidate(config) is None


def test_pick_candidate_requires_threshold_reached(fresh_db: str) -> None:
    """分配时长不足阈值不产生候选。"""
    _seed_open_session("dev-as-1", 1, "99990052", 5 * 60)

    assert auto_settle.pick_auto_settle_candidate(STUDENT_CONFIG) is None


def test_pick_candidate_returns_full_tuple_when_hit(fresh_db: str) -> None:
    """命中时返回 (device_id, cert_no, password, duration_s)，密码随候选取出。"""
    _seed_open_session("dev-as-1", 2, "99990052", 30 * 60)

    hit = auto_settle.pick_auto_settle_candidate(STUDENT_CONFIG)

    assert hit is not None
    assert hit[0] == "dev-as-1"
    assert hit[1] == "99990052"
    assert hit[2] == "000000"
    assert hit[3] >= 30 * 60


def test_pick_candidate_ignores_non_map_students(fresh_db: str) -> None:
    config = {**STUDENT_CONFIG, "auto_settle_students": "not-a-map"}
    assert auto_settle.pick_auto_settle_candidate(config) is None


def test_pick_candidate_with_no_open_sessions(fresh_db: str) -> None:
    assert auto_settle.pick_auto_settle_candidate(STUDENT_CONFIG) is None


def test_duration_s_bad_input_returns_zero() -> None:
    assert auto_settle._duration_s("not-a-time") == 0
    assert auto_settle._duration_s("") == 0


def test_scan_once_skips_when_no_candidate(fresh_db: str) -> None:
    assert asyncio.run(auto_settle.scan_once()) is False


def test_scan_once_skips_offline_device(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """命中的候选设备离线：不下发，返回 False。"""
    _seed_open_session("dev-as-off", 1, "99990052", 30 * 60)
    monkeypatch.setattr(auto_settle.config_store, "effective", lambda _device_id: STUDENT_CONFIG)

    assert asyncio.run(auto_settle.scan_once()) is False


def test_scan_once_settles_hit_and_records_audit(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """命中候选且设备在线：下发结算命令，回执 ok 后扫描返回 True 并留审计。"""
    import json

    from tests import helpers

    from app.core.connections import manager
    from app.modules.audit import repository as command_audit
    from app.modules.sessions import service

    device_id = "dev-as-hit"
    _seed_open_session(device_id, 1, "99990052", 30 * 60)
    monkeypatch.setattr(auto_settle.config_store, "effective", lambda _device_id: STUDENT_CONFIG)
    fake = helpers.FakeGatewayWS()
    monkeypatch.setattr(manager, "_connections", {device_id: fake})

    async def flow() -> bool:
        task = asyncio.create_task(auto_settle.scan_once())
        await asyncio.sleep(0.05)
        command = json.loads(fake.sent[0])
        assert command["payload"]["action"] == "session.settle"
        service.resolve(
            str(command["id"]),
            {"status": "ok", "reason_code": "", "terminal_message": "已结算", "data": None},
        )
        return await task

    assert asyncio.run(flow()) is True
    rows = command_audit.list_commands(device_id=device_id)
    assert rows[0]["action"] == "session.settle"
    assert rows[0]["status"] == "ok"


def test_start_and_stop_manage_background_task() -> None:
    """start 起后台扫描任务、stop 收掉；重复 start 不叠加任务。"""

    async def flow() -> None:
        auto_settle.start()
        first = auto_settle._task
        assert first is not None
        await asyncio.sleep(0.1)  # 首轮扫描立即执行一次（空库，无候选）
        auto_settle.start()
        assert auto_settle._task is first  # 已在运行时不替换
        auto_settle.stop()
        assert auto_settle._task is None
        await asyncio.sleep(0.05)
        assert first.cancelled() or first.done()

    asyncio.run(flow())
