"""后台任务看板：四项的读模型与登记表的唯一出处。

名单的登记入口只有管理台配置页；本域剩下的是登记表本身与概览页看板，
不再有登记端点。
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.contracts import HoldSource
from app.modules.config.spec import SPECS_BY_KEY, ConfigKind, ConfigScope
from app.modules.jobs.spec import _TITLES, JOBS
from app.modules.sessions import decision

CERT_NO = "9999000001"


# ── 登记表本身的唯一出处 ──


def test_job_keys_are_derived_from_the_decision_table() -> None:
    """前三个任务的键与 `sessions/decision.py` 的判据表逐字相同。

    它们是从那张表派生的。这条防的是"派生被改回手抄"之后两边各自漂——判据按 A 判、
    看板按 B 显示，表现是"开关开了却不生效"，而库里看不出任何异常。
    """
    decision_keys = {spec.key for spec in decision.JOBS}
    entries = {key: (enabled, students) for key, enabled, students in JOBS}
    outside = set(entries) - decision_keys

    assert outside == {"power"}, "判据表里没有的只该是它——它的判据是设备级的"

    for key, enabled_key, students_key in JOBS:
        source = next(item for item in decision.JOBS if item.key == key) if key in decision_keys else None
        if source is None:
            continue
        assert enabled_key == source.enabled_key
        assert students_key == source.students_key


def test_job_config_keys_exist_are_server_scoped_and_of_the_right_kind() -> None:
    """每个任务的两份键都在登记表里，类型与归属都对。

    scope 是关键：`*_students` 若是设备域，名单就能按设备各不相同，而名单登记这件事
    没有设备维度——那会让面板上的一个开关对应到不止一份名单。
    """
    for key, enabled_key, students_key in JOBS:
        enabled = SPECS_BY_KEY[enabled_key]
        students = SPECS_BY_KEY[students_key]

        assert _TITLES[key], key
        assert enabled.kind is ConfigKind.BOOL
        assert students.kind is ConfigKind.TEXT_MAP
        assert enabled.scope is ConfigScope.SERVER
        assert students.scope is ConfigScope.SERVER


def test_power_is_not_in_the_decision_table() -> None:
    """它不在判据表里是有意的：它的判据是设备级的，塞进去要放一个不成立的形状。"""
    assert "power" not in {spec.key for spec in decision.JOBS}
    entries = {key: (enabled, students) for key, enabled, students in JOBS}
    assert entries["power"] == ("hold_enabled", "hold_students")


def test_frontend_groups_scheduled_rows_by_the_job_field() -> None:
    """概览页看板按 `job` 字段分组排定动作（ScheduledAction.job）。

    jobs 改名时服务端字段从 task/feature 定为 job，前端漏改的表现是四个任务段
    永远显示"当前没有排定的动作"——静默且无人报错，这里钉住消费的字段名。
    """
    source = (Path(__file__).resolve().parents[1] / "web" / "views" / "overview.js").read_text(encoding="utf-8")
    assert "row.job" in source
    assert "row.task" not in source


# ── 当前态看板（admin）──


def test_status_reports_tasks_holds_and_scheduled(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """当前态的形状：四任务（开关与名单数）、hold 中的设备、调度器的即时计划。

    新库上四任务全关、无 hold、无排定——看板在"系统闲着"时也必须能如实显示闲着。
    """
    resp = client.get("/api/v1/jobs/status", headers=admin_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert [job["key"] for job in data["jobs"]] == [
        "renew_wait",
        "renew_session",
        "auto_settle",
        "power",
    ]
    assert all(job["enabled"] is False and job["students_count"] == 0 for job in data["jobs"])
    assert data["holds"] == []
    assert data["scheduled"] == []


def test_status_includes_the_holding_device(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """hold 中的设备是"哪台设备钟是偏置着的"唯一可见面：表里有状态，看板就报。"""
    from app.db.session import session_scope
    from app.modules.devices import crud as devices_store

    started_at = datetime(2026, 10, 5, 15, 49, 30, tzinfo=UTC)
    with session_scope() as session:
        devices_store.register(
            session,
            device_id="dev-status-hold",
            app_version="0.8.0",
            version_code=125,
            os_version="4.4.4",
            capabilities=[],
            ip="127.0.0.1",
            booted_at=started_at,
            seen_at=started_at,
        )
        devices_store.start_hold(session, "dev-status-hold", HoldSource.AUTOMATED)
        devices_store.mark_hold_applied(
            session,
            "dev-status-hold",
            660,
            [{"door_no": 3, "cert_no": CERT_NO, "done": False, "failed": False}],
            started_at + timedelta(minutes=10),
        )

    data = client.get("/api/v1/jobs/status", headers=admin_headers).json()

    holds = data["holds"]
    assert len(holds) == 1
    row = holds[0]
    assert row["device_id"] == "dev-status-hold"
    assert row["hold_source"] == "automated"
    assert row["hold_started_at"] != ""  # 意图建立时刻由 start_hold 落库（真实钟）
    assert row["hold_offset_s"] == 660
    assert row["total"] == 1
    assert row["reassigned_count"] == 0
    assert row["pending"] == [{"door_no": 3, "cert_no": CERT_NO}]
    assert row["arming_deadline"] == "2026-10-05T15:59:30.000Z"
    assert row["idle"] is True


def test_status_requires_admin(client: TestClient, ops_headers: dict[str, str], fresh_db: str) -> None:
    """当前态是管理面的问题：非 admin 位调不动它。"""
    resp = client.get("/api/v1/jobs/status", headers=ops_headers)

    assert resp.status_code == 403
