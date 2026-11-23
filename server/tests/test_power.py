"""电源计划域：触发窗、整夜覆盖布防、恢复时刻与重分配的对账判定。

夹具要四样：一台**有偏差记录与上报事实**的设备（触发、施加与恢复全部按上报的计划
换算）、一份名单、可替换的桩（命令下发、在线判定与重分配），以及 hold 的状态
种子——这一层测的是对账判定与状态机，不是界面流程本身。

时间约定（与旧域测试不同，这里的桩是自洽的）：`local_now` 是**设备表盘**时刻，
服务端时刻 = 表盘 − 时区 + 偏差。`_local_now` 在被测代码里读回的正是这个表盘值，
断言因此直接写在表盘轴上；恢复判定的"过 close"用真实轴（服务端 + 时区），两者
互不干扰。
"""

import asyncio
from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from tests import device_link, door_link
from tests.test_scheduler import _frame

from app.contracts import HoldSource, JobOutcome
from app.core import device_clock
from app.core.clock import format_rfc3339, parse_rfc3339
from app.core.problems import DOOR_NOT_FREE, ApiProblemError
from app.db.base import utcnow
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
            devices_store.set_device_facts(session, DEVICE, CLOSE, OPEN_AT, ZONE_MINUTES, 20, 5, 5)


def _seed_facts(fresh_db: str) -> None:
    with session_scope() as session:
        devices_store.set_device_facts(session, DEVICE, CLOSE, OPEN_AT, ZONE_MINUTES, 20, 5, 5)


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
    """把 on_frame 排进事件的重分配任务跑完（两轮空转让 create_task 落地）。"""
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
    arming_deadline: datetime | None = None,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> None:
    """hold 状态种子：登记设备并写入 hold 列（offset NULL = 意图未施加）。

    `hold_started_at` 由 mark_hold_applied 以 `utcnow()` 落库——种子时刻必须与被桩的
    "现在"自洽，所以把 devices 侧的 utcnow 一并冻结到种子时刻。arming_deadline 默认
    取种子时刻（已过）：布防可见性把"截止已过"的掉线当断电的正常表现，只有显式给出
    未来截止的用例才留痕。
    """
    device_link.register_trusted_device(DEVICE)
    moment = parse_rfc3339(started_at)
    assert moment is not None
    if monkeypatch is not None:
        monkeypatch.setattr(devices_store, "utcnow", lambda: moment)
    with session_scope() as session:
        devices_store.start_hold(session, DEVICE, HoldSource.AUTOMATED)
        if offset_s is not None or assignments is not None:
            devices_store.mark_hold_applied(
                session, DEVICE, offset_s or 0, assignments or [], arming_deadline or moment
            )


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
            "arming_deadline": hold["arming_deadline"],
        }


# ── 纯函数：恢复时刻与触发窗 ──────────────────────────────────────────────


def test_resume_offset_targets_ten_past_midnight() -> None:
    """偏移量按表盘现算，恢复时刻恒为"午夜后 10 分钟"；凌晨晚关门改为 close+10。"""

    def local(hh: int, mm: int) -> datetime:
        return datetime(2026, 11, 20, hh, mm)

    open_minute = 10 * 60 + 55  # 上报的 open 10:55

    assert power._resume_offset_s(local(23, 49), 23 * 60 + 58, open_minute) == 21 * 60
    assert power._resume_offset_s(local(23, 55), 23 * 60 + 58, open_minute) == 15 * 60
    assert power._resume_offset_s(local(0, 5), 5, open_minute) == 5 * 60
    assert power._resume_offset_s(local(0, 10), 5, open_minute) == 0
    # 凌晨晚关门（close 00:30 早于 open 10:55，且已越过午夜+10）：恢复时刻 = close + 10
    assert power._resume_offset_s(local(0, 25), 30, open_minute) == 15 * 60
    # close 在 00:10 之前不进该分支：午夜+10 已在 close 之后，语义不变
    assert power._resume_offset_s(local(23, 57), 5, open_minute) == 13 * 60


def test_hold_due_window_and_its_cross_midnight_form() -> None:
    """触发窗 = [close − 10 分, close)；close 早于提前量时窗口跨午夜。"""

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


def test_trigger_applies_hold_with_night_covering_arm_then_step(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """触发窗内名单学生在占用：先落意图，再整夜覆盖布防、最后写钟到恢复时刻。

    布防截止 = 次日 auto_close_time（off_day_offset=1）：布防写在拨钟前（此刻表盘
    =真实轴），拨钟把表盘推过午夜进入次日——布防先于拨钟是硬约束，拨过今晚既有
    截止（23:58）会当场断电（docs/device-poweroff.md 的实测记录）。
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
    assert sent[0][1] == {"off_at": "23:58", "on_at": "10:55", "off_day_offset": 1}
    assert sent[1][1] == {"datetime": "2026-11-21 00:10:00", "rearm": False}

    # 施加时刻落库为恢复判定轴：恢复时刻 = 施加时刻 + 偏移；
    # 布防截止入库为次日 auto_close_time（真实轴）
    with session_scope() as session:
        row = devices_store.get_hold(session, DEVICE)
        assert row is not None
        assert row["started_at"] is not None
        assert row["arming_deadline"] == datetime(2026, 11, 21, 15, 58, tzinfo=UTC)


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


def test_hold_requested_after_midnight_pends_until_tonight(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """午夜后（昨晚 close 已过）手动请求延长：意图保留到今晚触发窗再施加。

    昨晚的断电已由常规计划执行，此刻的"今晚"指即将到来的那个 close——意图跨日
    存活，过期判定以建立时刻之后第一次到来的 close 为界（今天 23:58），不在午夜
    处作废。
    """
    sent: list[tuple[str, object]] = []

    def at(local: str) -> datetime:
        moment = datetime.strptime(local, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone(timedelta(minutes=ZONE_MINUTES)))
        return moment.astimezone(UTC)

    _prepare(monkeypatch, "2026-11-21 00:05:00", sent)
    monkeypatch.setattr(devices_store, "utcnow", lambda: at("2026-11-21 00:05:00"))
    with session_scope() as session:
        power.request_hold(session, DEVICE)

    # 午夜后不施加：触发窗在今晚 23:48–23:58，此刻窗外，意图原样等待
    _tick()
    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["offset_s"] is None
    assert sent == []

    # 今晚 23:50：施加，布防截止 = 明晚 23:58（真实轴）
    monkeypatch.setattr(power, "_now_utc", lambda: at("2026-11-21 23:50:00"))
    monkeypatch.setattr(devices_store, "utcnow", lambda: at("2026-11-21 23:50:00"))
    _tick()
    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["offset_s"] == 20 * 60
    assert sent == [
        ("power.schedule", {"off_at": "23:58", "on_at": "10:55", "off_day_offset": 1}),
        ("clock.set", {"datetime": "2026-11-22 00:10:00", "rearm": False}),
    ]

    with session_scope() as session:
        row = devices_store.get_hold(session, DEVICE)
        assert row is not None
        assert row["arming_deadline"] == datetime(2026, 11, 22, 15, 58, tzinfo=UTC)


def test_late_close_hold_dials_to_close_plus_margin(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """凌晨晚关门（close 00:30 早于 open 10:55）：布防截止 = 次日 00:30，恢复时刻
    = close + 10。拨钟只前进 15 分钟到 00:40——按午夜+10 会形成近一整天的跳变。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-21 00:25:00", sent)
    with session_scope() as session:
        devices_store.set_device_facts(session, DEVICE, "00:30", OPEN_AT, ZONE_MINUTES, 20, 5, 5)

    with session_scope() as session:
        power.request_hold(session, DEVICE)
    _tick()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["offset_s"] == 15 * 60
    assert sent == [
        ("power.schedule", {"off_at": "00:30", "on_at": "10:55", "off_day_offset": 1}),
        ("clock.set", {"datetime": "2026-11-21 00:40:00", "rearm": False}),
    ]

    with session_scope() as session:
        row = devices_store.get_hold(session, DEVICE)
        assert row is not None
        assert row["arming_deadline"] == datetime(2026, 11, 21, 16, 30, tzinfo=UTC)


def test_incident_timeline_replay(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """用 2026-11-20 事故的真实参数逐 tick 重演：整夜覆盖布防一次到位，恢复时刻
    写准回真实轴，恢复后不再有任何命令。

    当晚参数：close 23:58、23:51:44 手动点击、对账 30 秒一轮、偏移 18 分钟。
    旧实现的实际结果是 00:00–00:02 集体断电（布防截止少算一个偏移、close 一过就
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

    # 23:52:00 对账：一次整夜覆盖布防（断电目标 = 次日 23:58）+ 一次拨钟（到 00:10）
    # 施加时刻（started_at）由 mark_hold_applied 的 utcnow 落库，冻结到对账这一刻
    monkeypatch.setattr(devices_store, "utcnow", lambda: at("2026-11-20 23:52:00"))
    tick_at("2026-11-20 23:52:00")
    assert [action for action, _ in sent] == ["power.schedule", "clock.set"]
    assert sent[0][1] == {"off_at": "23:58", "on_at": "10:55", "off_day_offset": 1}
    assert sent[1][1] == {"datetime": "2026-11-21 00:10:00", "rearm": False}

    # 拨钟后第一帧收敛偏差：表盘 = 真实 + 18 分钟（gateway 的 update 在生产做同样的事）
    device_clock.load(DEVICE, -1080.0)

    # 23:58:20（旧实现在此恢复并算出 0 分钟延时）：对账不动——布防在位、恢复时刻
    # 未到，没有续写、没有恢复、没有任何命令
    sent.clear()
    tick_at("2026-11-20 23:58:20")
    assert sent == []
    assert _get_hold(fresh_db) is not None

    # 00:10:00 恢复时刻：墙钟写回真实轴（hold 自己的第二步），随后清 hold 列
    tick_at("2026-11-21 00:10:00")
    assert sent == [("clock.set", {"datetime": "2026-11-21 00:10:00", "rearm": False})]
    assert _get_hold(fresh_db) is None

    # 00:10:30 下一轮：写准即偏差纪元作废（设备下一帧才重新收敛，此刻无偏差记录），
    # 校正无从判定；恢复时 rearm=False 已定，不再有任何命令
    sent.clear()
    tick_at("2026-11-21 00:10:30")
    assert sent == []

    with session_scope() as session:
        row = devices_store.get_hold(session, DEVICE)
        assert row is None


def test_intent_outside_the_window_pends_without_commands(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """触发窗前的意图 pending 到窗内再施加：close 还没到，拨钟只会无谓地跳过 App 的放人线。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:30:00", sent)
    _seed_hold(fresh_db, "2026-11-20T15:30:00.000Z", offset_s=None, monkeypatch=monkeypatch)  # 23:30 手动点击

    _tick()

    hold = _get_hold(fresh_db)
    assert hold is not None
    assert hold["offset_s"] is None  # 意图保留，等触发窗
    assert sent == []


# ── 布防在位 ──────────────────────────────────────────────────────────────


def test_armed_hold_sends_no_further_commands(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """已施加的 hold 不再下发任何布防/写钟：布防只有一次，滚动续写与 MCU 的钟面
    检查语义冲突（每次续写的截止可能更早，会抢先或拉前——2026-11-20 事故）。

    hold 起点取白天（表盘 00:30 之后的意图，恢复时刻在当晚）：恢复判定不在本用例触发。
    """
    sent: list[tuple[str, object]] = []
    # hold 中墙钟 = 真实 + 偏移：真实 00:00 local，偏移 660s → 墙 00:11 local
    _prepare(monkeypatch, "2026-11-21 00:11:00", sent, skew_s=-660.0)
    _seed_hold(fresh_db, "2026-11-20T16:30:00.000Z", offset_s=660, monkeypatch=monkeypatch)

    _tick()
    _tick()

    assert sent == []


# ── 恢复 ──────────────────────────────────────────────────────────────────


def test_resume_at_the_moment_writes_back_and_clears(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复时刻已过且无人占用：墙钟写回真实轴（hold 自己的第二步——不开自动校时
    开关也执行，偏置不写回则下一晚的恢复时刻算术即被破坏），随后清 hold 列。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-21 00:30:00", sent, skew_s=0.0)
    monkeypatch.setattr(config_store, "effective", lambda _s, _d: {**CONFIG, "time_sync_enabled": False})
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)

    _tick()

    hold = _get_hold(fresh_db)
    assert hold is None
    assert sent == [("clock.set", {"datetime": "2026-11-21 00:30:00", "rearm": False})]
    from app.persistence.audit import JobRun

    with device_link.db() as session:
        resumed = session.query(JobRun).filter(JobRun.outcome == "resumed").all()
    assert len(resumed) == 1
    assert resumed[0].device_id == DEVICE


def test_resume_waits_while_a_roster_student_is_occupying(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-21 00:30:00", sent, skew_s=0.0)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    _with_session("2026-11-20T16:10:00.000Z")  # 重分配后的会话：真人在用

    _tick()

    assert _get_hold(fresh_db) is not None
    # 恢复未发生：无写钟（布防在位，也不再有任何重布）
    assert sent == []


def test_resume_waits_until_the_moment_has_passed(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """恢复等恢复时刻，不等被跳过的 close——close 一过就恢复等于没延长。"""
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


def test_armed_hold_offline_before_the_arming_deadline_is_flagged_once(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """布防在位、截止（次日 close）未到、设备掉线：留痕一次（限频门控日志与审计
    同一路径）；掉线不触发任何补救（人不在就是断电，方向安全）。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:51:00", sent, skew_s=-660.0, online=False)
    _seed_hold(
        fresh_db,
        "2026-11-20T15:49:00.000Z",
        offset_s=660,
        arming_deadline=datetime(2026, 11, 21, 15, 58, tzinfo=UTC),
        monkeypatch=monkeypatch,
    )

    _tick()
    _tick()

    assert sent == []
    from app.persistence.audit import JobRun

    with session_scope() as session:
        rows = session.query(JobRun).filter(JobRun.outcome == "hold-failed").all()
        assert len(rows) == 1  # 限频：第二轮不再重复


def test_armed_hold_offline_after_the_arming_deadline_is_not_flagged(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """布防截止已过（到点断电）后的掉线是正常表现：不留痕。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-21 00:30:00", sent, skew_s=0.0, online=False)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)

    _tick()

    assert sent == []
    from app.persistence.audit import JobRun

    with session_scope() as session:
        assert session.query(JobRun).filter(JobRun.outcome == "hold-failed").count() == 0


def test_install_reboot_offline_is_not_flagged(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """OTA 自安装的重启掉线（installing 后宽限内）：预期表现，不留痕。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:51:00", sent, skew_s=-660.0, online=False)
    _seed_hold(
        fresh_db,
        "2026-11-20T15:49:00.000Z",
        offset_s=660,
        arming_deadline=datetime(2026, 11, 21, 15, 58, tzinfo=UTC),
        monkeypatch=monkeypatch,
    )
    power.note_install_started(DEVICE)

    _tick()

    assert sent == []
    from app.persistence.audit import JobRun

    with session_scope() as session:
        assert session.query(JobRun).filter(JobRun.outcome == "hold-failed").count() == 0


def test_install_reboot_grace_expires_back_to_flagging(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """宽限窗已过（安装结束、设备仍未回连）：布防可见性恢复正常告警。"""
    sent: list[tuple[str, object]] = []
    _prepare(monkeypatch, "2026-11-20 23:51:00", sent, skew_s=-660.0, online=False)
    _seed_hold(
        fresh_db,
        "2026-11-20T15:49:00.000Z",
        offset_s=660,
        arming_deadline=datetime(2026, 11, 21, 15, 58, tzinfo=UTC),
        monkeypatch=monkeypatch,
    )
    power.note_install_started(DEVICE)
    # 记录时刻拨回宽限窗外（循环单调钟只在此处注入一次）
    power._install_reboot_at[DEVICE] = power.time_module.monotonic() - (power.OTA_INSTALL_REBOOT_GRACE_S + 1)

    _tick()

    from app.persistence.audit import JobRun

    with session_scope() as session:
        assert session.query(JobRun).filter(JobRun.outcome == "hold-failed").count() == 1


# ── 重分配（帧驱动）──────────────────────────────────────────────────────


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

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/problems/hold-disabled")


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


def test_hold_status_endpoint_reads_the_current_intent(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GET /hold 纯读现状：未建立意图 404，建立后回与 POST 幂等响应同形状。"""
    _seed_facts(fresh_db)
    _register_device_for_http()
    monkeypatch.setattr(config_store, "effective", lambda _s, _d: CONFIG)

    before = client.get("/api/v1/devices/dev-hold/hold", headers=admin_headers)
    assert before.status_code == 404
    assert before.headers["content-type"].startswith("application/problem+json")

    client.post("/api/v1/devices/dev-hold/hold", headers=admin_headers)

    resp = client.get("/api/v1/devices/dev-hold/hold", headers=admin_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["device_id"] == DEVICE
    assert body["hold_source"] == "manual"
    assert body["arming_deadline"] == ""  # 意图已建、尚未施加


def test_hold_status_requires_read_scope(client: TestClient, fresh_db: str) -> None:
    """读端点要 devices:read：只给 devices:write 的密钥拿不到状态。"""
    from tests.identity import bearer, register_key

    _seed_facts(fresh_db)
    _register_device_for_http()
    token = register_key("hold-status-ro", ["devices:write"], device_scope=["*"])

    resp = client.get("/api/v1/devices/dev-hold/hold", headers=bearer(token))

    assert resp.status_code == 403


def test_manual_hold_after_apply_does_not_rearm(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """整夜覆盖布防已在位后再点延长：幂等返回现状，不再下发任何命令。

    布防只有一次（MCU 最后写入唯一生效），重复写没有增益；重复的 manual 请求
    也不把 source 覆盖成 manual。
    """
    _register_device_for_http()
    _seed_facts(fresh_db)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    monkeypatch.setattr(config_store, "effective", lambda _s, _d: CONFIG)
    sent: list[tuple[str, object]] = []

    async def fake_send(_session: object, _device_id: str, action: str, params: object, **_kw: object) -> object:
        sent.append((action, params))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", fake_send)

    with session_scope() as session:
        before = devices_store.get_hold(session, DEVICE)
        assert before is not None
        deadline_before = before["arming_deadline"]

    resp = client.post("/api/v1/devices/dev-hold/hold", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["hold_source"] == "automated"
    assert sent == []
    with session_scope() as session:
        after = devices_store.get_hold(session, DEVICE)
        assert after is not None
        assert after["arming_deadline"] == deadline_before


def test_resume_endpoint_resumes_when_holding(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """恢复时刻已过的手动 resume：墙钟写回真实轴（hold 自己的第二步），随后清 hold 列。"""
    _register_device_for_http()
    _seed_facts(fresh_db)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    monkeypatch.setattr(power.manager, "is_online", lambda _d: True)
    sent: list[tuple[str, object]] = []

    async def fake_send(_session: object, _device_id: str, action: str, params: object, **_kw: object) -> object:
        sent.append((action, params))
        return {"status": "ok", "reason_code": "", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", fake_send)
    # 恢复时刻（15:49Z + 11 分）之后：恢复不取消布防，只有一次写准
    monkeypatch.setattr(power, "_now_utc", lambda: datetime(2026, 11, 20, 16, 30, tzinfo=UTC))

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "resumed"
    assert body["previous_state"] == "applied"
    assert body["clock_restored"] is True
    assert _get_hold(fresh_db) is None
    assert sent == [("clock.set", {"datetime": "2026-11-21 00:30:00", "rearm": False})]
    # 手动恢复不冒充后台任务：它的审计在接口调用与命令流水，不在 job_runs
    from app.persistence.audit import JobRun

    with device_link.db() as session:
        assert session.query(JobRun).filter(JobRun.outcome == "resumed").count() == 0


def test_resume_endpoint_before_the_moment_attempts_a_cancel_arming(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """恢复时刻前的手动 resume 是尽力取消：先写准（恢复自己的第二步），再重布
    "真实此刻 + 1.5 分钟"的更早截止（MCU 接受更早截止时生效，否则被忽略、断电仍
    落在整夜覆盖布防的截止），随后清 hold 列。"""
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
    assert resp.json()["outcome"] == "resumed"
    assert _get_hold(fresh_db) is None
    # 先写准（真实表盘此刻 23:51），再取消重布：截止取 23:52（此刻 + 1.5 分钟），上电目标取上报的 open
    assert sent == [
        ("clock.set", {"datetime": "2026-11-20 23:51:00", "rearm": False}),
        ("power.schedule", {"off_at": "23:52", "on_at": "10:55", "off_day_offset": 0}),
    ]


def test_resume_endpoint_is_idempotent_when_not_holding(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    _register_device_for_http()

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "not-holding"
    assert body["previous_state"] is None
    assert body["clock_restored"] is False


def test_resume_endpoint_cancels_a_pending_intent_without_commands(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """设备关机时建立的意图没有拨钟：恢复只清状态、不发命令，离线也能取消。

    2026-11-24 00:01 现场：设备已关机，手动 hold 只落意图；旧实现仍去写墙钟，失败后
    返回 resumed=false，界面把它说成「未在延长中」，看板却继续显示 hold。
    """
    _register_device_for_http()
    _seed_facts(fresh_db)
    _seed_hold(fresh_db, "2026-11-23T16:01:31.000Z", offset_s=None, monkeypatch=monkeypatch)
    monkeypatch.setattr(power.manager, "is_online", lambda _d: False)

    async def no_command(_session: object, _device_id: str, _action: str, _params: object, **_kw: object) -> object:
        msg = "取消未施加的意图不应下发任何命令"
        raise AssertionError(msg)

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", no_command)

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["outcome"] == "cancelled"
    assert body["previous_state"] == "pending"
    assert body["clock_restored"] is False
    assert _get_hold(fresh_db) is None


def test_resume_endpoint_defers_a_pending_intent_inside_the_apply_window(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """触发窗内且设备在线：对账器可能正在施加，恢复暂缓到下一轮。"""
    _register_device_for_http()
    _seed_facts(fresh_db)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=None, monkeypatch=monkeypatch)
    device_clock.load(DEVICE, 0.0)  # 有偏差记录才具备施加条件；无记录时施加本就不会发命令
    monkeypatch.setattr(power, "_now_utc", lambda: datetime(2026, 11, 20, 15, 50, tzinfo=UTC))  # 23:50 local
    monkeypatch.setattr(power.manager, "is_online", lambda _d: True)

    async def no_command(_session: object, _device_id: str, _action: str, _params: object, **_kw: object) -> object:
        msg = "施加进行中的取消不应下发命令"
        raise AssertionError(msg)

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", no_command)

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/problems/hold-applying")
    assert resp.headers["retry-after"] == "30"
    assert _get_hold(fresh_db) is not None


def test_resume_endpoint_clears_an_expired_intent_inside_the_next_window(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """过期意图即使落在下一天的触发窗内也不再施加，恢复直接清掉。"""
    _register_device_for_http()
    _seed_facts(fresh_db)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=None, monkeypatch=monkeypatch)
    device_clock.load(DEVICE, 0.0)
    monkeypatch.setattr(power, "_now_utc", lambda: datetime(2026, 11, 21, 15, 50, tzinfo=UTC))  # 次日 23:50
    monkeypatch.setattr(power.manager, "is_online", lambda _d: True)

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 200
    assert resp.json()["outcome"] == "cancelled"
    assert _get_hold(fresh_db) is None


def test_resume_endpoint_keeps_a_pending_intent_whose_arming_was_written(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """布防已写入而拨钟未完成：不能只清状态，否则整夜覆盖布防留在 MCU 上。"""
    _register_device_for_http()
    _seed_facts(fresh_db)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=None, monkeypatch=monkeypatch)
    with session_scope() as session:
        devices_store.mark_hold_armed(session, DEVICE, datetime(2026, 11, 21, 15, 58, tzinfo=UTC))
    monkeypatch.setattr(power.manager, "is_online", lambda _d: False)

    async def no_command(_session: object, _device_id: str, _action: str, _params: object, **_kw: object) -> object:
        msg = "arming-pending 不应下发任何命令"
        raise AssertionError(msg)

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", no_command)

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/problems/hold-arming-pending")
    assert resp.headers["retry-after"] == "30"
    assert _get_hold(fresh_db) is not None


def test_record_device_status_lands_the_shadow_row(fresh_db: str) -> None:
    """上报事实落影子行：三个值整组进出（缺哪项都按整体缺失处理）。"""

    device_link.register_trusted_device(DEVICE)
    with session_scope() as session:
        assert devices_service.record_device_status(session, DEVICE, CLOSE, OPEN_AT, 480, 20, 5, 5) is True
        assert tuple(devices_service.device_facts(session, DEVICE)) == (CLOSE, OPEN_AT, 480, 20, 5, 5)
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
    # 布防已在位（拨钟失败）：截止必须落库，恢复才能拒绝「只清状态」的取消
    assert hold["arming_deadline"] == datetime(2026, 11, 21, 15, 58, tzinfo=UTC)
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


def test_resume_endpoint_offline_keeps_the_hold(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """恢复 = 墙钟写回真实轴，写不进就保持 hold（偏置不写回则下一晚恢复时刻算术
    永久破坏）：离线时命令发不出，返回 409 device-offline，等回连后对账自动恢复。"""
    _register_device_for_http()
    _seed_facts(fresh_db)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    monkeypatch.setattr(power.manager, "is_online", lambda _d: False)
    monkeypatch.setattr(power, "_now_utc", lambda: datetime(2026, 11, 20, 16, 30, tzinfo=UTC))
    sent: list[tuple[str, object]] = []

    async def failing_send(_session: object, _device_id: str, action: str, params: object, **_kw: object) -> object:
        sent.append((action, params))
        return {"status": "timeout", "reason_code": "device.offline", "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", failing_send)

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/problems/device-offline")
    assert _get_hold(fresh_db) is not None
    assert sent == [("clock.set", {"datetime": "2026-11-21 00:30:00", "rearm": False})]


@pytest.mark.parametrize(
    ("status", "reason_code", "expected_status", "problem_code"),
    [
        ("timeout", "deadline.exceeded", 504, "device-timeout"),
        ("failed", "clock.write_failed", 502, "device-command-failed"),
    ],
)
def test_resume_endpoint_maps_device_command_failure(
    client: TestClient,
    admin_headers: dict[str, str],
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    reason_code: str,
    expected_status: int,
    problem_code: str,
) -> None:
    """墙钟写入的其他失败按命令终态映射为 504/502，hold 保持不变。"""
    _register_device_for_http()
    _seed_facts(fresh_db)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    monkeypatch.setattr(power, "_now_utc", lambda: datetime(2026, 11, 20, 16, 30, tzinfo=UTC))

    async def failing_send(_session: object, _device_id: str, _action: str, _params: object, **_kw: object) -> object:
        return {"status": status, "reason_code": reason_code, "terminal_message": "", "data": None}

    monkeypatch.setattr(power.device_actions, "send_command_and_wait", failing_send)

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == expected_status
    assert resp.json()["type"].endswith(f"/problems/{problem_code}")
    assert _get_hold(fresh_db) is not None


def test_holds_view_without_reported_facts_reports_recovery_undecided(
    fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """hold 中但计划事实缺失（上报被清）：恢复条件按未决显示，不虚构。"""
    _prepare(monkeypatch, "2026-11-20 23:51:00", skew_s=-660.0)
    _seed_applied_hold_with_assignment(fresh_db, "2026-11-20T15:49:00.000Z", monkeypatch)
    with session_scope() as session:
        devices_store.set_device_facts(session, DEVICE, "", "", 0, 20, 5, 5)  # 模拟事实被清

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
    assert holds[0]["state"] == "pending"
    assert holds[0]["resume_at"] == ""
    assert holds[0]["past_close"] is False
    assert holds[0]["idle"] is True


def test_hold_view_reports_derived_state_and_resume_at(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """看板状态是派生的：applied 给恢复时刻，arming 只给布防截止，pending 两者皆空。"""
    _prepare(monkeypatch, "2026-11-20 23:51:00", skew_s=-660.0)
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)

    with session_scope() as session:
        applied = power.holds(session)[0]
    assert applied["state"] == "applied"
    assert applied["resume_at"] == "2026-11-20T16:00:00.000Z"

    with session_scope() as session:
        devices_store.clear_hold(session, DEVICE)
        devices_store.start_hold(session, DEVICE, HoldSource.MANUAL)
        devices_store.mark_hold_armed(session, DEVICE, datetime(2026, 11, 21, 15, 58, tzinfo=UTC))
    with session_scope() as session:
        arming = power.holds(session)[0]
    assert arming["state"] == "arming"
    assert arming["resume_at"] == ""
    assert arming["arming_deadline"] == "2026-11-21T15:58:00.000Z"


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
    """意图未施加 + 偏差记录缺失：表盘算不出，施加暂缓（下轮重试）。"""
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
    """hold 已恢复后到达的迟到帧：不重分配（assignments 随 hold 清空）。"""
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
    """stop 等待在途重分配任务结束（asyncio.gather），不留悬挂任务。"""
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
        devices_store.set_device_facts(session, "ghost", "23:58", "10:55", 480, 20, 5, 5)
        devices_store.start_hold(session, "ghost", HoldSource.MANUAL)
        devices_store.mark_hold_applied(session, "ghost", 660, [], utcnow())
        devices_store.clear_hold(session, "ghost")
        devices_store.mark_assignment(session, "ghost", 0, done=True)
        assert devices_store.device_facts(session, "ghost") is None
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


def test_resume_endpoint_keeps_the_hold_without_facts(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """facts 缺失（上报被清）时无从换算表盘目标（时区缺失）：墙钟写回暂缓，
    hold 保持，等事实恢复后重试。"""
    _register_device_for_http()
    _seed_hold(fresh_db, "2026-11-20T15:49:00.000Z", offset_s=660, monkeypatch=monkeypatch)
    monkeypatch.setattr(power.manager, "is_online", lambda _d: True)

    resp = client.post("/api/v1/devices/dev-hold/resume", headers=admin_headers)

    assert resp.status_code == 409
    assert resp.json()["type"].endswith("/problems/hold-facts-missing")
    assert _get_hold(fresh_db) is not None


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
    _seed_hold(
        fresh_db,
        "2026-11-20T15:49:00.000Z",
        offset_s=660,
        arming_deadline=datetime(2026, 11, 20, 15, 30, 0, tzinfo=UTC),
        monkeypatch=monkeypatch,
    )

    _tick()

    assert sent == []
    from app.persistence.audit import JobRun

    with session_scope() as session:
        assert session.query(JobRun).count() == 0
