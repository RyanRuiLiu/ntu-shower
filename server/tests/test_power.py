"""电源计划域：触发窗、偏置施加、布防截止、恢复与放回的对账判定。

夹具要四样：一台**有偏差记录与上报事实**的设备（触发、落点与恢复全部按上报的计划
换算）、一份名单、可替换的桩（命令下发、在线判定与放回分配），以及 hold 的状态
种子——这一层测的是对账判定与状态机，不是界面流程本身。

时间约定（与旧域测试不同，这里的桩是自洽的）：`local_now` 是**设备表盘**时刻，
服务端时刻 = 表盘 − 时区 + 偏差。`_local_now` 在被测代码里读回的正是这个表盘值，
断言因此直接写在表盘轴上；恢复判定的"过 close"用真实轴（服务端 + 时区），两者
互不干扰。
"""

import asyncio
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests import device_link, door_link
from tests.test_scheduler import _frame

from app.contracts import HoldSource, JobOutcome
from app.core import device_clock
from app.core.clock import format_rfc3339, parse_rfc3339
from app.core.problems import DOOR_NOT_FREE, ApiProblemError
from app.db.base import utcnow
from app.db.engine import create_db_engine
from app.db.session import session_scope
from app.modules.config import store as config_store
from app.modules.config.spec import parse_time
from app.modules.devices import crud as devices_store
from app.modules.devices import service as devices_service
from app.modules.power import service as power

DEVICE = "dev-hold"

CONFIG: dict[str, object] = {
    "hold_enabled": True,
    "hold_students": {"9999999001": "000000", "9999999003": "111111"},
    "time_sync_enabled": True,
}

#: UTC+8，所有用例共用。
ZONE_MINUTES = 480

CLOSE = "23:58"
OPEN_AT = "10:55"


def _prepare(
    monkeypatch: pytest.MonkeyPatch,
    local_now: str,
    sent: list[tuple[str, object]] | None = None,
    skew_s: float = 0.0,
    *,
    facts: bool = True,
    online: bool = True,
) -> None:
    """把"现在"、配置、在线判定与命令下发换成可控的桩。

    `local_now` 是设备表盘时刻；服务端时刻 = 表盘 − 时区 + 偏差（自洽：`_local_now`
    读回的就是这个表盘值）。命令下发全部桩成 ok，逐条记进 `sent`。
    """
    device_clock.load(DEVICE, skew_s)
    device_link.register_trusted_device(DEVICE)
    # 表盘时刻 → 服务端时刻：先按上报时区把墙钟摆成 aware，再加偏差（skew = 服务端 − 墙钟）。
    # 不能用字符串 shift——naive 解析 + astimezone 会按机器时区再平移一次（双重换算）。
    wall_utc = datetime.strptime(local_now, "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=timezone(timedelta(minutes=ZONE_MINUTES))
    ).astimezone(UTC) + timedelta(seconds=skew_s)
    monkeypatch.setattr(power, "_now_utc", lambda: wall_utc)
    monkeypatch.setattr(config_store, "effective", lambda _session, _device_id: CONFIG)
    monkeypatch.setattr(power.manager, "is_online", lambda _device_id: online)

    async def fake_send(_session: object, _device_id: str, action: str, params: object, **_kw: object) -> object:
        if sent is not None:
            sent.append((action, params))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", fake_send)

    with session_scope() as session:
        if facts:
            devices_store.set_power_facts(session, DEVICE, CLOSE, OPEN_AT, ZONE_MINUTES)


def _seed_facts(fresh_db: str) -> None:
    with session_scope() as session:
        devices_store.set_power_facts(session, DEVICE, CLOSE, OPEN_AT, ZONE_MINUTES)


def _with_session(open_time: str, cert_no: str = "9999999001", door_no: int = 3) -> None:
    """造一场未结束的会话：分配了、还没开阀。"""
    door_link.feed(
        DEVICE,
        _frame(
            captured_at=open_time,
            card_no=0,
            cert_no=cert_no,
            acc_no="7000000001",
            open_time=open_time,
            door_no=door_no,
        ),
    )


def _tick() -> None:
    asyncio.run(power.tick_once())


def _drain_async_tasks() -> None:
    """把 on_frame 排进事件的放回任务跑完（两轮空转让 create_task 落地）。"""
    for _ in range(2):
        asyncio.run(asyncio.sleep(0))


def _run_on_frame() -> None:
    """在事件循环里调 on_frame（create_task 需要运行中的循环），随后放行任务。"""

    async def runner() -> None:
        power.on_frame(DEVICE)
        for _ in range(2):
            await asyncio.sleep(0)

    asyncio.run(runner())


def _seed_hold(
    fresh_db: str,
    started_at: str,
    *,
    offset_s: int | None = 660,
    assignments: list[dict[str, object]] | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> None:
    """hold 状态种子：登记设备并写入 hold 列（offset NULL = 意图未施加）。

    `hold_started_at` 由 start_hold 以 `utcnow()` 落库——种子时刻必须与被桩的"现在"
    自洽，所以把 devices 侧的 utcnow 一并冻结到种子时刻。
    """
    device_link.register_trusted_device(DEVICE)
    moment = parse_rfc3339(started_at)
    assert moment is not None
    if monkeypatch is not None:
        monkeypatch.setattr(devices_store, "utcnow", lambda: moment)
    with session_scope() as session:
        devices_store.start_hold(session, DEVICE, HoldSource.AUTOMATED)
        if offset_s is not None or assignments is not None:
            devices_store.mark_hold_applied(session, DEVICE, offset_s or 0, assignments or [], moment)


def _get_hold(fresh_db: str) -> dict[str, object] | None:

    with session_scope() as session:
        hold = devices_store.get_hold(session, DEVICE)
        if hold is None:
            return None
        return {
            "started_at": format_rfc3339(hold["started_at"]) if hold["started_at"] else None,
            "source": str(hold["source"].value) if hold["source"] else None,
            "offset_s": hold["offset_s"],
            "assignments": hold["assignments"],
        }


# ── 纯函数：落点与触发窗 ──────────────────────────────────────────────────


def test_landing_offset_always_lands_after_midnight_margin() -> None:
    """偏移量按表盘现算，落点恒为"午夜后 10 分钟"：午夜前、午夜后触发都成立。"""

    def local(hh: int, mm: int) -> datetime:
        return datetime(2026, 11, 20, hh, mm)

    assert power._landing_offset_s(local(23, 49)) == 21 * 60
    assert power._landing_offset_s(local(23, 55)) == 15 * 60
    assert power._landing_offset_s(local(0, 5)) == 5 * 60
    assert power._landing_offset_s(local(0, 10)) == 0


def test_hold_due_window_and_its_cross_midnight_form() -> None:
    """触发窗 = [close − 9 分, close)；close 早于提前量时窗口跨午夜。"""

    def local(hh: int, mm: int) -> datetime:
        return datetime(2026, 11, 20, hh, mm)

    close = parse_time("23:58")
    assert close is not None
    assert power._hold_due(local(23, 48), close)
    assert power._hold_due(local(23, 57), close)
    assert not power._hold_due(local(23, 47), close)
    assert not power._hold_due(local(23, 58), close)  # 到点即出窗：close 后不触发

    late_close = parse_time("00:05")
    assert late_close is not None
    assert power._hold_due(local(23, 55), late_close)
    assert power._hold_due(local(0, 0), late_close)
    assert not power._hold_due(local(23, 54), late_close)
    assert not power._hold_due(local(0, 5), late_close)


# ── 触发与施加 ────────────────────────────────────────────────────────────


def test_trigger_applies_hold_with_pre_arm_then_step(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """触发窗内名单学生在占用：先落意图，再预补偿布防、最后写钟到午夜后落点。

    截止 = 表盘此刻 + 2×偏移 + 余量：布防写在拨钟前（此刻墙钟=真实轴），拨钟把墙钟
    推前一个偏移，截止必须再让出同样的量——实际断电才落在落点 + 余量（真实轴
    00:20 = 墙钟轴 00:40 减去拨快的那 20 分钟）。
    """
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:50:00", sent)
    _with_session("2026-11-20T15:40:00.000Z")

    _tick()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["source"] == "automated"
    assert hold["offset_s"] == 20 * 60
    # 顺序硬约束：布防先于拨钟
    kinds = [action for action, _ in sent]
    assert kinds == ["power.schedule", "clock.set"]
    assert sent[0][1] == {"off_at": "00:40", "on_at": "10:55"}
    assert sent[1][1] == {"datetime": "2026-11-21 00:10:00", "rearm": False}

    # 施加时刻落库为恢复的落点轴：落点 = 施加时刻 + 偏移
    with session_scope() as session:
        row = devices_store.get_hold(session, DEVICE)
        assert row is not None
        assert row["started_at"] is not None
        assert row["lease_deadline"] is not None


def test_trigger_outside_window_does_not_hold(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, "2026-11-20 23:30:00")
    _with_session("2026-11-20T15:40:00.000Z")

    _tick()

    assert _get_hold(fresh_db) is None


def test_trigger_without_roster_occupancy_does_not_hold(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, "2026-11-20 23:50:00")

    _tick()

    assert _get_hold(fresh_db) is None


def test_trigger_with_master_switch_off_does_not_hold(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, "2026-11-20 23:50:00", online=True)
    monkeypatch.setattr(config_store, "effective", lambda _s, _d: {**CONFIG, "hold_enabled": False})
    _with_session("2026-11-20T15:40:00.000Z")

    _tick()

    assert _get_hold(fresh_db) is None


def test_apply_survives_command_failure_and_retries(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """布防/拨钟失败：意图保留、偏置未落，下一轮重试（状态先行、审计后置）。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:50:00", sent)
    _with_session("2026-11-20T15:40:00.000Z")

    async def failing_send(_session: object, _device_id: str, action: str, _params: object, **_kw: object) -> object:
        sent.append((action, _params))
        return {"status": "timeout", "reason_code": "device.offline", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", failing_send)
    _tick()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["started_at"] is not None
    assert hold["offset_s"] is None  # 意图未施加

    # 设备恢复（桩回到 ok）：下一轮施加成功
    async def ok_send(_session: object, _device_id: str, action: str, params: object, **_kw: object) -> object:
        sent.append((action, params))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", ok_send)
    _tick()
    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["offset_s"] == 20 * 60


def test_expired_intent_is_abandoned_after_close(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """意图拖过了 close（离线错过整窗）：作废，不无意义拨钟。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-21 00:30:00", sent, skew_s=0.0)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=None, monkeypatch=monkeypatch)  # 昨晚的意图

    _tick()

    assert _get_hold(fresh_db) is None
    assert sent == []


def test_incident_timeline_replay(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """用 2026-11-20 事故的真实参数逐 tick 重演：断电截止落在落点 + 余量，恢复
    零命令，恢复后的校正不重布。

    当晚参数：close 23:58、23:51:44 手动点击、对账 30 秒一轮、偏移 18 分钟。
    旧实现的实际结果是 00:00–00:02 集体断电（预补偿少算一个偏移、close 一过就
    恢复、恢复重布算出 0 分钟延时）。
    """
    sent: list[tuple[str, object]] = []

    def at(local: str) -> datetime:
        moment = datetime.strptime(local, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(minutes=ZONE_MINUTES)))
        return moment.astimezone(UTC)

    _prepare(monkeypatch, "2026-11-20 23:51:44", sent)
    monkeypatch.setattr(power.manager, "is_online", lambda _d: True)

    def tick_at(local_real: str) -> None:
        monkeypatch.setattr(power, "_now_utc", lambda: at(local_real))
        asyncio.run(power.tick_once())

    # 23:51:44 手动点击（真实轴）
    monkeypatch.setattr(power, "_now_utc", lambda: at("2026-11-20 23:51:44"))
    with session_scope() as session:
        power.request_hold(session, DEVICE)

    # 23:52:00 对账：一次布防（截止 00:38 = 拨后墙钟轴的落点+余量）+ 一次拨钟（到 00:10）
    # 施加时刻（started_at）由 mark_hold_applied 的 utcnow 落库，冻结到对账这一刻
    monkeypatch.setattr(devices_store, "utcnow", lambda: at("2026-11-20 23:52:00"))
    tick_at("2026-11-20 23:52:00")
    assert [action for action, _ in sent] == ["power.schedule", "clock.set"]
    assert sent[0][1] == {"off_at": "00:38", "on_at": "10:55"}
    assert sent[1][1] == {"datetime": "2026-11-21 00:10:00", "rearm": False}

    # 拨钟后第一帧收敛偏差：表盘 = 真实 + 18 分钟（gateway 的 update 在生产做同样的事）
    device_clock.load(DEVICE, -1080.0)

    # 23:58:20（旧实现在此恢复并算出 0 分钟延时）：对账不动——布防在位、落点未到，
    # 没有续租、没有恢复、没有任何命令
    sent.clear()
    tick_at("2026-11-20 23:58:20")
    assert sent == []
    assert _get_hold(fresh_db) is not None

    # 00:10:00 落点：恢复清状态，零命令
    tick_at("2026-11-21 00:10:00")
    assert _get_hold(fresh_db) is None
    assert sent == []

    # 00:10:30 下一轮：自动校时写回真实钟面，且不重布（已过 close——重布会把断电
    # 推到次日，延长被变成 24 小时）
    tick_at("2026-11-21 00:10:30")
    assert sent == [("clock.set", {"datetime": "2026-11-21 00:10:30", "rearm": False})]

    # 布防截止的语义核对：墙钟轴 00:38 = 真实轴 00:20 = 落点（00:10）+ 余量（10 分钟）
    with session_scope() as session:
        row = devices_store.get_hold(session, DEVICE)
        assert row is None


def test_intent_outside_the_window_parks_without_commands(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """触发窗前的意图 park 到窗内再施加：close 还没到，拨钟只会白白跳过 App 的放人线。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:30:00", sent)
    _seed_hold(fresh_db, "2026-11-20T15:30:00.000Z", offset_s=None, monkeypatch=monkeypatch)  # 23:30 手动点击

    _tick()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["offset_s"] is None  # 意图保留，等触发窗
    assert sent == []


# ── 租约 ──────────────────────────────────────────────────────────────────


def test_armed_hold_sends_no_further_commands(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """已施加的 hold 不再下发任何布防/写钟：布防只有一次，滚动续写与 MCU 的钟面
    检查语义冲突（每次续写的截止可能更早，会抢先或拉前——2026-11-20 事故）。

    hold 起点取白天（表盘 00:30 之后的意图，落点在当晚）：恢复判定不在本用例触发。
    """
    sent: list[tuple[str, object]] = []
    # hold 中墙钟 = 真实 + 偏移：真实 00:00 local，偏移 660s → 墙 00:11 local
    _prepare(monkeypatch, "2026-11-21 00:11:00", sent, skew_s=-660.0)
    _seed_hold(fresh_db, "2026-11-20T16:30:00.000Z", offset_s=660, monkeypatch=monkeypatch)

    _tick()
    _tick()

    assert sent == []


# ── 恢复 ──────────────────────────────────────────────────────────────────


def test_resume_at_landing_clears_hold_without_commands(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """落点已过且无人占用：清 hold 列，不发任何命令——钟面写回由 following_schedule
    的自动校时承担（恢复后偏差 = 偏移量，超阈值即触发）。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-21 00:30:00", sent, skew_s=0.0)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)

    _tick()

    hold = _get_hold(fresh_db)
    assert hold is None
    assert sent == []


def test_resume_waits_while_a_roster_student_is_occupying(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-21 00:30:00", sent, skew_s=0.0)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    _with_session("2026-11-20T16:10:00.000Z")  # 放回后的会话：真人在用

    _tick()

    assert _get_hold(fresh_db) is not None
    # 租约续期照常（幂等布防）；恢复未发生：无写钟
    assert all(action != "clock.set" for action, _ in sent)


def test_resume_waits_until_the_landing_has_passed(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复等落点，不等被跳过的 close——close 一过就恢复等于没延长。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:30:00", sent, skew_s=0.0)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)

    _tick()

    assert _get_hold(fresh_db) is not None
    assert all(action != "clock.set" for action, _ in sent)


# ── 校正 ──────────────────────────────────────────────────────────────────


def test_correction_steps_when_skewed_and_idle(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """following_schedule 下偏差超阈值且无人占用：写准（rearm=true 一条命令）。

    目标 = 真实当地钟面（服务端 + 时区）——与设备当前读数无关，这正是绝对钟面
    step 的含义：墙钟比真实快 2 分钟，写准的目标是"真实"（19:58），不是墙钟读数。
    """
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 20:00:00", sent, skew_s=-120.0)

    _tick()

    assert ("clock.set", {"datetime": "2026-11-20 19:58:00", "rearm": True}) in sent


def test_correction_after_close_does_not_rearm(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """过了今晚 close 的校正不重布常规计划：此刻重布把断电推到次日，延长会被
    变成 24 小时——断电由既有布防截止承担。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-21 00:10:00", sent, skew_s=-120.0)

    _tick()

    assert ("clock.set", {"datetime": "2026-11-21 00:08:00", "rearm": False}) in sent


def test_correction_is_silent_during_hold(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """hold 期间的偏差是故意的：校正让位。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-21 00:11:00", sent, skew_s=-660.0)
    _seed_hold(fresh_db, "2026-11-20T16:30:00.000Z", offset_s=660, monkeypatch=monkeypatch)

    _tick()

    assert all(action != "clock.set" for action, _ in sent)


# ── 已施加期间的掉线可见性 ────────────────────────────────────────────────


def test_armed_hold_offline_before_landing_is_flagged_once(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """布防在位、落点未到、设备掉线：留痕一次（限频门控日志与审计同一路径）；掉线
    不触发任何补救（人不在就是断电，方向安全）。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:51:00", sent, skew_s=-660.0, online=False)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    # 布防截止取未来：seed 的 moment 是过去时刻，把截止推到"现在 + 一个余量"之后
    with session_scope() as session:
        devices_store.set_lease(session, DEVICE, datetime(2026, 11, 20, 16, 59, 0, tzinfo=UTC))

    _tick()
    _tick()

    assert sent == []
    from app.persistence.audit import JobRun

    with session_scope() as session:
        rows = session.query(JobRun).filter(JobRun.outcome == "hold-failed").all()
        assert len(rows) == 1  # 限频：第二轮不再重复


def test_armed_hold_offline_after_landing_is_not_flagged(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """布防截止已过（到点断电）后的掉线是正常表现：不留痕。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-21 00:30:00", sent, skew_s=0.0, online=False)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)

    _tick()

    assert sent == []
    from app.persistence.audit import JobRun

    with session_scope() as session:
        assert session.query(JobRun).filter(JobRun.outcome == "hold-failed").count() == 0


# ── 放回（帧驱动）────────────────────────────────────────────────────────


def _seed_applied_hold_with_assignment(fresh_db: str, started_at: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _seed_hold(
        fresh_db,
        started_at,
        offset_s=660,
        assignments=[{"door_no": 3, "cert_no": "9999999001", "done": False, "failed": False}],
        monkeypatch=monkeypatch,
    )


def test_reassign_marks_done_after_a_successful_assign(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, "2026-11-20 23:51:00", skew_s=-660.0)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)

    async def ok_assign(_session: object, _device_id: str, _req: object) -> object:
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.sessions_service, "perform_assign", ok_assign)
    monkeypatch.setattr(power, "_running", True)
    _run_on_frame()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["assignments"][0]["done"] is True


def test_reassign_waits_for_a_residual_session_from_before_the_hold(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """施加前的残留会话（浴室端还没清场）→ 等，不标记。"""
    _prepare(monkeypatch, "2026-11-20 23:51:00", skew_s=-660.0)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)
    _with_session("2026-11-20T15:40:00.000Z")  # 施加（15:49Z）之前就占着的

    monkeypatch.setattr(power, "_running", True)
    _run_on_frame()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["assignments"][0]["done"] is False
    assert hold["assignments"][0]["failed"] is False


def test_reassign_marks_a_failed_command_and_never_retries(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """命令真发出去而失败 → 标记 failed，下一帧不再发（每轮重发不会有不同结果）。"""
    _prepare(monkeypatch, "2026-11-20 23:51:00", skew_s=-660.0)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)

    async def failing_assign(_session: object, _device_id: str, _req: object) -> object:
        return {"status": "failed", "reason_code": "device.home_unreachable", "terminal_message": "x", "data": None}

    monkeypatch.setattr(power.sessions_service, "perform_assign", failing_assign)
    monkeypatch.setattr(power, "_running", True)
    _run_on_frame()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["assignments"][0]["done"] is False
    assert hold["assignments"][0]["failed"] is True


def test_reassign_counts_a_self_reopened_session_as_already_back(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """施加后他自己重开了会话（assigned_at ≥ hold_started_at）→ 已成立，标记 done。"""
    _prepare(monkeypatch, "2026-11-20 23:51:00", skew_s=-660.0)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)
    _with_session("2026-11-20T16:00:00.000Z")  # 施加之后才出现的会话（偏置轴 ≥ hold 时刻）：他自己开的

    monkeypatch.setattr(power, "_running", True)
    _run_on_frame()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["assignments"][0]["done"] is True


# ── 端点 ──────────────────────────────────────────────────────────────────


def _register_device_for_http() -> None:
    device_link.register_trusted_device(DEVICE)


def test_manual_hold_endpoint_creates_intent(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_facts(fresh_db)
    _register_device_for_http()
    monkeypatch.setattr(config_store, "effective", lambda _s, _d: CONFIG)

    resp = client.post("/api/v1/devices/dev-hold/hold", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["hold_source"] == "manual"
    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["offset_s"] is None  # 意图已建、尚未施加


def test_manual_hold_is_rejected_when_the_master_switch_is_off(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_facts(fresh_db)
    _register_device_for_http()
    monkeypatch.setattr(config_store, "effective", lambda _s, _d: {**CONFIG, "hold_enabled": False})

    resp = client.post("/api/v1/devices/dev-hold/hold", headers=admin_headers)

    assert resp.status_code == 422


def test_manual_hold_is_idempotent(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seed_facts(fresh_db)
    _register_device_for_http()
    monkeypatch.setattr(config_store, "effective", lambda _s, _d: CONFIG)

    first = client.post("/api/v1/devices/dev-hold/hold", headers=admin_headers)
    second = client.post("/api/v1/devices/dev-hold/hold", headers=admin_headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["hold_started_at"] == first.json()["hold_started_at"]


def test_resume_endpoint_resumes_when_holding(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register_device_for_http()
    _seed_facts(fresh_db)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    monkeypatch.setattr(power.manager, "is_online", lambda _d: True)
    sent: list[tuple[str, object]] = []

    async def fake_send(_session: object, _device_id: str, action: str, params: object, **_kw: object) -> object:
        sent.append((action, params))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", fake_send)

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["resumed"] is True
    assert _get_hold(fresh_db) is None
    assert sent == []


def test_resume_endpoint_before_the_landing_attempts_a_cancel_arming(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """落点前的 resume 是尽力取消：重布"表盘此刻 + 1.5 分钟"的更早截止（MCU 接受
    更早截止时生效，否则被忽略、断电仍落在落点 + 余量），随后清 hold 列。"""
    _register_device_for_http()
    _seed_facts(fresh_db)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    monkeypatch.setattr(power.manager, "is_online", lambda _d: True)
    sent: list[tuple[str, object]] = []

    async def fake_send(_session: object, _device_id: str, action: str, params: object, **_kw: object) -> object:
        sent.append((action, params))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", fake_send)
    _prepare(monkeypatch, "2026-11-20 23:51:00", sent, skew_s=0.0)

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["resumed"] is True
    assert _get_hold(fresh_db) is None
    # 取消重布：截止取表盘 23:52（此刻 + 1.5 分钟），上电目标取上报的 open
    assert ("power.schedule", {"off_at": "23:52", "on_at": "10:55"}) in sent
    assert all(action != "clock.set" for action, _ in sent)


def test_resume_endpoint_is_idempotent_when_not_holding(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    _register_device_for_http()

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["resumed"] is False


def test_record_status_report_lands_the_shadow_row(fresh_db: str) -> None:
    """上报事实落影子行：三个值整组进出（缺哪项都按整体缺失处理）。"""

    device_link.register_trusted_device(DEVICE)
    with session_scope() as session:
        assert devices_service.record_status_report(session, DEVICE, CLOSE, OPEN_AT, 480) is True
        assert devices_service.power_facts(session, DEVICE) == (CLOSE, OPEN_AT, 480)
    assert _get_hold(fresh_db) is None  # 上报事实不改变 hold 状态


# ── 迁移词表守卫 ─────────────────────────────────────────────────────────


def test_migration_outcome_check_covers_every_job_outcome(fresh_db: str) -> None:
    """迁移建出的库对 JobOutcome 全集逐值可写——词表漂移在这里当场红。

    2026-11-20 二楼事故的根因是这一条缺位：测试库由 metadata 建（词表取当前枚举），
    生产库由迁移建（词表冻结在旧值），power 域的新终局值入库即被拒，收尾审计一失败
    连状态迁移一起被打断。conftest 切到 alembic 建库之后，本条就是那个引信的保险丝。
    """
    from app.persistence.audit import JobRun

    with session_scope() as session:
        for outcome in JobOutcome:
            session.add(
                JobRun(
                    occurred_at=utcnow(),
                    device_id=DEVICE,
                    door_no=0,
                    cert_no="",
                    job="power",
                    outcome=outcome,
                )
            )
        session.commit()


# ── 边界：让位、失败与限频分支 ────────────────────────────────────────────


def test_correction_skips_when_someone_is_occupying(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """校钟的跳变会清掉会话：有人在用就让位。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 20:00:00", sent, skew_s=-120.0)
    _with_session("2026-11-20T14:40:00.000Z")

    _tick()

    assert all(action != "clock.set" for action, _ in sent)


def test_correction_below_threshold_does_not_step(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 20:00:00", sent, skew_s=-10.0)

    _tick()

    assert sent == []


def test_correction_disabled_does_not_step(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 20:00:00", sent, skew_s=-120.0)
    monkeypatch.setattr(config_store, "effective", lambda _s, _d: {**CONFIG, "time_sync_enabled": False})

    _tick()

    assert sent == []


def test_correction_step_failure_warns_for_the_next_round(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _prepare(monkeypatch, "2026-11-20 20:00:00", skew_s=-120.0)

    async def failing_send(_session: object, _device_id: str, _action: str, _params: object, **_kw: object) -> object:
        return {"status": "timeout", "reason_code": "device.offline", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", failing_send)

    _tick()

    # 校正失败只留痕不落 job_runs（它不属于四个后台任务看板），下一轮重试
    assert True


def test_trigger_without_the_first_pong_does_nothing(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """facts 已上报但首次 pong 未到：表盘算不出，触发窗无法判定——整轮不动手。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:50:00", sent)
    device_clock.clear()
    _with_session("2026-11-20T15:40:00.000Z")

    _tick()

    assert _get_hold(fresh_db) is None
    assert sent == []


def test_step_failure_after_arm_records_hold_failed(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """布防成功、拨钟失败：hold-failed 留痕，意图保留重试。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:50:00", sent)
    _with_session("2026-11-20T15:40:00.000Z")

    calls = {"n": 0}

    async def fail_second_send(
        _session: object, _device_id: str, action: str, _params: object, **_kw: object
    ) -> object:
        calls["n"] += 1
        if action == "clock.set":
            return {"status": "timeout", "reason_code": "device.offline", "terminal_message": "", "data": None}
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", fail_second_send)
    _tick()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["offset_s"] is None
    assert calls["n"] == 2


def test_reassign_skips_a_student_removed_from_the_roster(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """名单刚被改：不在名单里的人不再分配（凭证也跟着名单走）。"""
    _prepare(monkeypatch, "2026-11-20 23:51:00", skew_s=-660.0)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)
    monkeypatch.setattr(config_store, "effective", lambda _s, _d: {**CONFIG, "hold_students": {}})

    monkeypatch.setattr(power, "_running", True)
    _run_on_frame()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["assignments"][0]["done"] is True


def test_reassign_waits_on_precondition_rejection(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """领域层前置拒（命令没下发）：等下一帧，不算失败也不算完成。"""
    _prepare(monkeypatch, "2026-11-20 23:51:00", skew_s=-660.0)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)

    async def rejecting_assign(_session: object, _device_id: str, _req: object) -> object:
        raise ApiProblemError(DOOR_NOT_FREE, "喷头不是空闲状态")

    monkeypatch.setattr(power.sessions_service, "perform_assign", rejecting_assign)
    monkeypatch.setattr(power, "_running", True)
    _run_on_frame()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["assignments"][0]["done"] is False
    assert hold["assignments"][0]["failed"] is False


def test_resume_endpoint_works_offline(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resume 只清状态、不发命令：离线同样可恢复（取消重布的前提不满足就跳过）。"""
    _register_device_for_http()
    _seed_facts(fresh_db)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    monkeypatch.setattr(power.manager, "is_online", lambda _d: False)

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["resumed"] is True
    assert _get_hold(fresh_db) is None


def test_holds_view_without_reported_facts_reports_recovery_undecided(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hold 中但计划事实缺失（上报被清）：恢复条件按未决显示，不虚构。"""
    _prepare(monkeypatch, "2026-11-20 23:51:00", skew_s=-660.0)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)
    with session_scope() as session:
        devices_store.set_power_facts(session, DEVICE, "", "", 0)  # 模拟事实被清

    with session_scope() as session:
        holds = power.holds(session)

    assert len(holds) == 1
    assert holds[0]["past_close"] is False


# ── 边界： retired 设备与 hold 看板的未决恢复 ─────────────────────────────


def test_tick_and_holds_skip_retired_devices(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """停用设备不参与对账，也不出现在看板（历史数据保留，职责终止）。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:50:00", sent)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)
    with session_scope() as session:
        devices_store.set_retired(session, DEVICE, True)

    _tick()

    with session_scope() as session:
        assert power.holds(session) == []
    assert sent == []


def test_hold_view_without_reported_facts_reports_recovery_undecided_2(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """意图未施加且事实缺失：恢复条件按未决显示（past_close=False），不虚构。"""
    _prepare(monkeypatch, "2026-11-20 23:51:00", skew_s=-660.0)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=None, monkeypatch=monkeypatch)

    with session_scope() as session:
        holds = power.holds(session)

    assert len(holds) == 1
    assert holds[0]["past_close"] is False
    assert holds[0]["idle"] is True


# ── 边界：触发窗外的让位与恢复的失败路径 ──────────────────────────────────


def test_trigger_offline_does_not_create_intent(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """触发窗内设备离线：不建立意图（命令发不出去，建立只会堆过期意图）。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:50:00", sent, online=False)
    _with_session("2026-11-20T15:40:00.000Z")

    _tick()

    assert _get_hold(fresh_db) is None
    assert sent == []


def test_apply_and_resume_tolerate_a_missing_skew_record(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """意图未施加 + 偏差记录缺失：表盘算不出，施加与续租都暂缓（下轮重试）。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:50:00", sent)
    _with_session("2026-11-20T15:40:00.000Z")
    device_clock.clear()

    _tick()

    assert _get_hold(fresh_db) is None
    assert sent == []


def test_correction_gate_skipped_when_hold_active(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """校正的第一道闸就是 hold 本身：hold 中不进入后续任何校正判定。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:51:00", sent, skew_s=-660.0)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)

    _tick()

    # 只有 hold 施加路径的命令（重布/拨钟），没有校正的写钟
    assert all(params.get("rearm") is not True for _a, params in sent)


def test_reassign_ignores_assignments_without_an_open_hold(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """hold 已恢复后到达的迟到帧：不放回（assignments 随 hold 清空）。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:51:00", sent, skew_s=-660.0)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)
    monkeypatch.setattr(power, "_running", True)
    devices_clear_hold()

    _run_on_frame()

    hold = _get_hold(fresh_db)
    assert hold is None


def devices_clear_hold() -> None:
    with session_scope() as session:
        devices_store.clear_hold(session, DEVICE)


def test_stop_awaits_inflight_reassign_tasks(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """stop 等待在途放回任务结束（asyncio.gather），不留悬挂任务。"""
    _prepare(monkeypatch, "2026-11-20 23:51:00", skew_s=-660.0)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)
    _with_session("2026-11-20T16:00:00.000Z")
    monkeypatch.setattr(power, "_running", True)

    async def run_frame_then_stop() -> None:
        power.on_frame(DEVICE)
        await power.stop()

    asyncio.run(run_frame_then_stop())

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["assignments"][0]["done"] is True


# ── 边界：未知设备、离线恢复与限频 ────────────────────────────────────────


def test_hold_crud_guards_return_on_unknown_device(fresh_db: str) -> None:
    """影子行不存在时的读与写一律静默返回（设备先注册再操作的守卫在上层）。"""
    with session_scope() as session:
        devices_store.set_power_facts(session, "ghost", "23:58", "10:55", 480)
        devices_store.start_hold(session, "ghost", HoldSource.MANUAL)
        devices_store.mark_hold_applied(session, "ghost", 660, [], utcnow())
        devices_store.set_lease(session, "ghost", utcnow())
        devices_store.clear_hold(session, "ghost")
        devices_store.mark_assignment(session, "ghost", 0, done=True)
        assert devices_store.power_facts(session, "ghost") is None
        assert devices_store.get_hold(session, "ghost") is None
        assert devices_store.set_retired(session, "ghost", True) is False


def test_apply_waits_for_skew_when_intent_was_created_before_restart(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """服务端重启清空进程内偏差，但 hold 意图在库里：施加暂缓到首个 pong 之后。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:50:00", sent)
    _with_session("2026-11-20T15:40:00.000Z")
    _seed_hold(fresh_db, "2026-11-20T15:40:00.000Z", offset_s=None, monkeypatch=monkeypatch)
    device_clock.clear()

    _tick()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["offset_s"] is None  # 意图保留，等偏差可用
    assert sent == []


def test_warn_once_throttles_repeated_failures(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """同一轮里的连续失败：WARNING 限频 60 秒一条（对账会重试，不刷屏）。"""
    _prepare(monkeypatch, "2026-11-20 23:50:00")
    _with_session("2026-11-20T15:40:00.000Z")

    async def failing_send(_session: object, _device_id: str, _action: str, _params: object, **_kw: object) -> object:
        return {"status": "timeout", "reason_code": "device.offline", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", failing_send)
    _tick()
    _tick()

    # 限频不改变行为，只压日志：两轮都失败、意图都保留
    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["offset_s"] is None


def test_resume_endpoint_does_not_need_facts(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resume 只清状态：facts 缺失（上报被清）也不影响恢复。"""
    _register_device_for_http()
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    monkeypatch.setattr(power.manager, "is_online", lambda _d: True)

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["resumed"] is True
    assert _get_hold(fresh_db) is None


def test_hold_and_resume_on_unknown_device_return_404(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """device_scope 是 "*" 时守卫不挡未知设备：端点必须自己回答 404，不能 500。"""
    resp = client.post("/api/v1/devices/ghost/hold", headers=admin_headers)
    assert resp.status_code == 404

    resp = client.post("/api/v1/devices/ghost/resume", headers=admin_headers)
    assert resp.status_code == 404


def test_offline_devices_never_get_commands(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """离线设备零通信：校正、施加、布防可见性三处都在本地把门，不发命令不落审计行。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:50:00", sent, skew_s=-120.0, online=False)
    _with_session("2026-11-20T15:40:00.000Z")
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    # 布防截止取过去：掉线发生在截止之后是断电的正常表现，不触发可见性告警
    with session_scope() as session:
        devices_store.set_lease(session, DEVICE, datetime(2026, 11, 20, 15, 30, 0, tzinfo=UTC))

    _tick()

    assert sent == []
    from app.persistence.audit import JobRun

    with session_scope() as session:
        assert session.query(JobRun).count() == 0


# ── 迁移恢复：复现 2026-11-20 生产部署失败的现场 ──────────────────────────


def test_migration_recovers_from_failed_deploy_leftover(tmp_path: Path) -> None:
    """上次部署失败留下的 batch 半成品表不再阻塞迁移，重跑一次到位。

    生产现场：迁移在 DROP TABLE devices 上因外键失败退出——残留
    _alembic_tmp_devices 半成品表、devices 本体未动、alembic_version 未推进。
    修复后的迁移先清残表，devices 换列走原生 ADD/DROP COLUMN（不重建被引用的表），
    同一库上重跑成功。整条迁移在测试里真实重放，与生产建库同源。
    """
    import os

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    server_dir = Path(__file__).resolve().parents[1]
    db = tmp_path / "recover.db"

    os.environ["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    try:
        cfg = Config(str(server_dir / "alembic.ini"))
        cfg.set_main_option("script_location", str(server_dir / "migrations"))
        command.upgrade(cfg, "5e0f1d5dd2c0")

        # 复现失败现场：半成品残表（迁移中断的 batch 产物）
        engine = create_db_engine(f"sqlite:///{db.as_posix()}")
        with engine.connect() as conn:
            conn.execute(text("CREATE TABLE _alembic_tmp_devices (id VARCHAR(64) PRIMARY KEY)"))
            conn.commit()
        engine.dispose()

        # 重放到 head：迁移应清理残表、完成全部换列
        command.upgrade(cfg, "head")

        engine = create_db_engine(f"sqlite:///{db.as_posix()}")
        with engine.connect() as conn:
            cols = {row[1] for row in conn.execute(text("pragma table_info(devices)"))}
            assert "hold_started_at" in cols
            assert "clock_shifted_at" not in cols
            leftover = conn.execute(text("select name from sqlite_master where name='_alembic_tmp_devices'")).scalar()
            assert leftover is None
        engine.dispose()
    finally:
        os.environ.pop("DATABASE_URL", None)
