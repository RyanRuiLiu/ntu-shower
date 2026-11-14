"""后台任务看板：四项的读模型与登记表的唯一出处。

名单的登记入口只有管理台配置页；本域剩下的是登记表本身与概览页看板，
不再有登记端点。
"""

from fastapi.testclient import TestClient

from app.modules.config.spec import SPECS_BY_KEY, ConfigKind, ConfigScope
from app.modules.jobs.spec import JOBS, JOBS_BY_KEY
from app.modules.sessions import decision

CERT_NO = "2026000001"


# ── 登记表本身的唯一出处 ──


def test_task_keys_are_derived_from_the_decision_table() -> None:
    """前三个任务的三个键与 `sessions/decision.py` 的判据表逐字相同。

    它们是从那张表派生的。这条防的是"派生被改回手抄"之后两边各自漂——判据按 A 判、
    看板按 B 显示，表现是"开关开了却不生效"，而库里看不出任何异常。
    """
    decision_keys = {spec.key for spec in decision.JOBS}
    outside = [spec.key for spec in JOBS if spec.key not in decision_keys]

    assert outside == ["extend_hours"], "判据表里没有的只该是它——它的判据是设备级的"

    for spec in JOBS:
        source = next(item for item in decision.JOBS if item.key == spec.key) if spec.key in decision_keys else None
        if source is None:
            continue
        assert spec.enabled_key == source.enabled_key
        assert spec.students_key == source.students_key


def test_task_config_keys_exist_are_server_scoped_and_of_the_right_kind() -> None:
    """每个任务的两份键都在登记表里，类型与归属都对。

    scope 是关键：`*_students` 若是设备域，名单就能按设备各不相同，而名单登记这件事
    没有设备维度——那会让面板上的一个开关对应到不止一份名单。
    """
    for spec in JOBS:
        enabled = SPECS_BY_KEY[spec.enabled_key]
        students = SPECS_BY_KEY[spec.students_key]

        assert spec.label, spec.key
        assert enabled.kind is ConfigKind.BOOL
        assert students.kind is ConfigKind.TEXT_MAP
        assert enabled.scope is ConfigScope.SERVER
        assert students.scope is ConfigScope.SERVER


def test_extend_hours_is_not_in_the_decision_table() -> None:
    """它不在判据表里是有意的：它的判据是设备级的，塞进去要放一个不成立的形状。"""
    assert "extend_hours" not in {spec.key for spec in decision.JOBS}
    assert JOBS_BY_KEY["extend_hours"].enabled_key == "extend_hours_enabled"
    assert JOBS_BY_KEY["extend_hours"].students_key == "extend_hours_students"


# ── 当前态看板（admin）──


def test_status_reports_tasks_extending_and_scheduled(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """当前态的形状：四任务（开关与名单数）、延长中的设备、调度器的即时计划。

    新库上四任务全关、无延长、无排定——看板在"系统闲着"时也必须能如实显示闲着。
    """
    resp = client.get("/api/v1/jobs/status", headers=admin_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert [task["key"] for task in data["tasks"]] == [
        "renew_wait",
        "renew_session",
        "auto_settle",
        "extend_hours",
    ]
    assert all(task["enabled"] is False and task["students_count"] == 0 for task in data["tasks"])
    assert data["extending"] == []
    assert data["scheduled"] == []


def test_status_includes_the_extending_device(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """延长中的设备是"哪台设备钟是拨快着的"唯一可见面：表里有行，看板就报。"""
    from datetime import UTC, datetime

    from app.db.session import session_scope
    from app.persistence import devices as devices_store
    from app.persistence import extend_hours as extend_repository

    shifted_at = datetime(2026, 10, 5, 15, 49, 30, tzinfo=UTC)
    with session_scope() as session:
        devices_store.register(
            session,
            device_id="dev-status-ext",
            app_version="0.4.13",
            version_code=57,
            os_version="4.4.4",
            capabilities=[],
            ip="127.0.0.1",
            booted_at=shifted_at,
            seen_at=shifted_at,
        )
        extend_repository.record(session, "dev-status-ext", shifted_at, [{"door_no": 3, "cert_no": CERT_NO}])

    data = client.get("/api/v1/jobs/status", headers=admin_headers).json()

    extending = data["extending"]
    assert len(extending) == 1
    row = extending[0]
    assert row["device_id"] == "dev-status-ext"
    assert row["shift_minutes"] == 15
    assert row["shifted_at"] == "2026-10-05T15:49:30.000Z"
    assert row["total"] == 1
    assert row["reassigned_count"] == 0
    assert row["pending"] == [{"door_no": 3, "cert_no": CERT_NO}]
    assert row["mcu_off_at"] == ""  # 没经过滚动：尚无武装的截止
    assert row["rearm_interval_s"] == 120
    assert row["idle"] is True


def test_status_requires_admin(client: TestClient, ops_headers: dict[str, str], fresh_db: str) -> None:
    """当前态是管理面的问题：非 admin 位调不动它。"""
    resp = client.get("/api/v1/jobs/status", headers=ops_headers)

    assert resp.status_code == 403
