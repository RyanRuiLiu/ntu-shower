"""电源计划域的持久化：上报事实与 hold 状态在设备影子行上的往返。

这些是读写原语级的测试：对账判定的行为在 test_power.py，这里只钉"写进去的读得回来、
清掉的就是没了"。
"""

from datetime import UTC, datetime

from app.contracts import HoldSource
from app.db.base import utcnow
from app.db.session import session_scope
from app.modules.devices import crud as devices_store

DEVICE = "dev-persist"


def _register(fresh_db: str) -> None:
    with session_scope() as session:
        devices_store.register(
            session,
            device_id=DEVICE,
            app_version="0.8.0",
            version_code=125,
            os_version="4.4.4",
            capabilities=[],
            ip="127.0.0.1",
            booted_at=utcnow(),
            seen_at=utcnow(),
        )


def test_power_facts_round_trip(fresh_db: str) -> None:
    _register(fresh_db)
    with session_scope() as session:
        assert devices_store.power_facts(session, DEVICE) is None  # 未上报
        assert devices_store.set_power_facts(session, DEVICE, "23:58", "10:55", 480) is True
    with session_scope() as session:
        assert devices_store.power_facts(session, DEVICE) == ("23:58", "10:55", 480)


def test_power_facts_for_unknown_device_is_a_no_op(fresh_db: str) -> None:
    with session_scope() as session:
        assert devices_store.set_power_facts(session, "ghost", "23:58", "10:55", 480) is False
        assert devices_store.power_facts(session, "ghost") is None


def test_hold_lifecycle_round_trip(fresh_db: str) -> None:
    _register(fresh_db)
    started = datetime(2026, 11, 20, 15, 49, 0, tzinfo=UTC)

    with session_scope() as session:
        devices_store.start_hold(session, DEVICE, HoldSource.MANUAL)
    with session_scope() as session:
        hold = devices_store.get_hold(session, DEVICE)
        assert hold is not None
        assert hold["source"] is HoldSource.MANUAL
        assert hold["offset_s"] is None  # 意图已建、尚未施加
        assert hold["assignments"] == []

    with session_scope() as session:
        devices_store.mark_hold_applied(
            session,
            DEVICE,
            660,
            [{"door_no": 3, "cert_no": "9999999001", "done": False, "failed": False}],
            started,
        )
    with session_scope() as session:
        hold = devices_store.get_hold(session, DEVICE)
        assert hold is not None
        assert hold["offset_s"] == 660
        assert hold["assignments"] == [{"door_no": 3, "cert_no": "9999999001", "done": False, "failed": False}]

    with session_scope() as session:
        devices_store.mark_assignment(session, DEVICE, 0, done=True)
    with session_scope() as session:
        hold = devices_store.get_hold(session, DEVICE)
        assert hold is not None
        assert hold["assignments"][0]["done"] is True

    with session_scope() as session:
        devices_store.clear_hold(session, DEVICE)
    with session_scope() as session:
        assert devices_store.get_hold(session, DEVICE) is None


def test_start_hold_is_idempotent(fresh_db: str) -> None:
    _register(fresh_db)
    with session_scope() as session:
        devices_store.start_hold(session, DEVICE, HoldSource.MANUAL)
        devices_store.start_hold(session, DEVICE, HoldSource.AUTOMATED)
    with session_scope() as session:
        hold = devices_store.get_hold(session, DEVICE)
        assert hold is not None
        assert hold["source"] is HoldSource.MANUAL  # 已在 hold 中：第二次是空操作
