"""后台任务登记域：四项的读模型、名单增删、以及能力位与作用域的边界。

这一域的判定**不走设备作用域**（名单是 SERVER scope 的配置，不分设备），所以夹具用的
就是空作用域——它同时是"作用域这一维不参与"的回归证明。
"""

import inspect
import string

import pytest
from fastapi.testclient import TestClient

from app.modules.access.identity import IdentityKind
from app.modules.config import store as config_store
from app.modules.config.spec import SPECS_BY_KEY, ConfigKind, ConfigScope
from app.modules.sessions import scheduler as sessions_scheduler
from app.modules.sessions.decision import FEATURES
from app.modules.tasks import service as tasks_service
from app.modules.tasks import spec as tasks_spec
from app.modules.tasks.routes import set_membership as set_membership_endpoint
from app.modules.tasks.spec import TASKS, TASKS_BY_KEY

TASKS_IDENTITY = "tasks-identity-0001"
CERT_NO = "2026000001"
OTHER_CERT_NO = "2026000003"


@pytest.fixture
def tasks_headers(fresh_db: str) -> dict[str, str]:
    """持有 tasks 位的调用方。

    **设备作用域是空的**：这个位没有设备维度，作用域填什么都不该影响判定。填空比填
    `["*"]` 更能证明这一点——填 `*` 的话，判据出错也看不出来。
    """
    from tests.identity import bearer
    from tests.identity import register as register_identity

    token = register_identity(TASKS_IDENTITY, IdentityKind.OPENID, ["tasks"], "测试登记", device_scope=[])
    return bearer(token)


# ── 登记表本身的唯一出处 ──


def test_task_keys_are_derived_from_the_decision_table() -> None:
    """前三个任务的三个键与 `sessions/decision.py` 的判据表逐字相同。

    它们是从那张表派生的。这条防的是"派生被改回手抄"之后两边各自漂——判据按 A 判、
    接口按 B 显示，表现是"登记了却不生效"，而库里看不出任何异常。
    """
    decision_keys = {spec.key for spec in FEATURES}
    outside = [spec.key for spec in TASKS if spec.key not in decision_keys]

    assert outside == ["extend_hours"], "判据表里没有的只该是它——它的判据是设备级的"

    for spec in TASKS:
        source = next(item for item in FEATURES if item.key == spec.key) if spec.key in decision_keys else None
        if source is None:
            continue
        assert spec.enabled_key == source.enabled_key
        assert spec.students_key == source.students_key


def test_task_config_keys_exist_are_server_scoped_and_of_the_right_kind() -> None:
    """每个任务的两份键都在登记表里，类型与归属都对。

    scope 是关键：`*_students` 若是设备域，名单就能按设备各不相同，而"登记"这件事没有
    设备维度——那会让面板上的一个开关对应到不止一份名单。
    """
    for spec in TASKS:
        enabled = SPECS_BY_KEY[spec.enabled_key]
        students = SPECS_BY_KEY[spec.students_key]

        assert spec.label, spec.key
        assert spec.template, spec.key
        assert enabled.kind is ConfigKind.BOOL
        assert students.kind is ConfigKind.TEXT_MAP
        assert enabled.scope is ConfigScope.SERVER
        assert students.scope is ConfigScope.SERVER


def test_every_description_says_it_applies_to_the_registered() -> None:
    """每条说明都写明"只对登记了的人生效"。

    漏掉这一句，用户会读成"这个功能对所有人这样"——而实际上没登记的学号，服务端对他什么
    都不做。这是这套文案里最容易省略、也最要紧的一点：占位符填错了只是数字不准，主体漏了
    是**意思反了**。原先四条都写"开启后"，那是个没有主体的悬空条件。
    """
    for spec in TASKS:
        assert "登记" in spec.template, spec.key


def test_format_duration_reads_like_a_sentence() -> None:
    """秒 → 句子里的中文量词。三支都要走到：整小时、整分钟、以及剩下那些不齐整的。

    默认配置恰好都是整分钟（60 / 300 / 1200 / 3600），所以"4 分 30 秒"那一支只有专门的
    用例才会碰到——而管理员把阈值改成 270 就立刻会碰到它。
    """
    assert tasks_spec.format_duration(7200) == "2小时"
    assert tasks_spec.format_duration(3600) == "1小时"
    assert tasks_spec.format_duration(300) == "5分钟"
    assert tasks_spec.format_duration(60) == "1分钟"
    assert tasks_spec.format_duration(270) == "4分30秒"
    assert tasks_spec.format_duration(3599) == "59分59秒"
    assert tasks_spec.format_duration(45) == "45秒"


def test_description_template_has_no_unfilled_placeholder(fresh_db: str) -> None:
    """模板里的每个占位符都取得到值，且填完不留残渣。

    这条防"改了文案忘了改取值"：`str.format` 缺键会抛 `KeyError`，但那要等到用户请求到达
    才发生——这条把它提前到门禁里。反过来，模板里多一个没人用的取值也算漂了。
    """
    formatter = string.Formatter()

    for spec in TASKS:
        config = config_store.global_effective()
        values = spec.fill(config)
        names = {name for _literal, name, _spec, _conv in formatter.parse(spec.template) if name}

        assert names, f"{spec.key} 的文案里没有占位符，数值不就成了写死的？"
        assert names == set(values), f"{spec.key} 的占位符与取值对不上：{names} vs {set(values)}"
        assert "{" not in spec.template.format(**values)


def test_description_follows_the_configured_threshold(fresh_db: str) -> None:
    """改了阈值，用户读到的说明跟着变。

    这就是"文案里不写死数值"的全部意义：`auto_settle_pause_threshold_s` 是可配的，若文案里
    印着"60 秒"，管理员改成 120 之后用户读到的就是假的，而两处都不会报错。
    """
    before = tasks_service.describe(TASKS_BY_KEY["auto_settle"], config_store.global_effective())
    assert "1分钟" in before

    config_store.put_global_overrides({"auto_settle_pause_threshold_s": 120})
    after = tasks_service.describe(TASKS_BY_KEY["auto_settle"], config_store.global_effective())

    assert "2分钟" in after
    assert "1分钟" not in after


def test_extend_hours_is_not_in_the_decision_table() -> None:
    """它不在判据表里是有意的：它的判据是设备级的，塞进去要放一个不成立的形状。"""
    assert "extend_hours" not in {spec.key for spec in FEATURES}
    assert TASKS_BY_KEY["extend_hours"].enabled_key == "extend_hours_enabled"
    assert TASKS_BY_KEY["extend_hours"].students_key == "extend_hours_students"


# ── 守卫 ──


def test_listing_needs_the_capability(client: TestClient, ops_headers: dict[str, str]) -> None:
    """有身份但没有 tasks 位：403 capability-denied。"""
    r = client.get("/api/v1/tasks", params={"cert_no": CERT_NO}, headers=ops_headers)

    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/capability-denied")


def test_anonymous_cannot_list(client: TestClient) -> None:
    r = client.get("/api/v1/tasks", params={"cert_no": CERT_NO})

    assert r.status_code == 401


def test_cert_no_is_required(client: TestClient, tasks_headers: dict[str, str]) -> None:
    """学号必填：服务端不知道调用方是谁（openid 不绑学号），没它就没有可回答的对象。"""
    r = client.get("/api/v1/tasks", headers=tasks_headers)

    assert r.status_code == 422


# ── 读模型 ──


def test_listing_reports_every_task(client: TestClient, tasks_headers: dict[str, str]) -> None:
    """四项都列出来；没登记过的学号 joined 全为假，管理员没开时 enabled 全为假。"""
    r = client.get("/api/v1/tasks", params={"cert_no": CERT_NO}, headers=tasks_headers)

    assert r.status_code == 200
    body = r.json()
    assert [item["key"] for item in body] == [spec.key for spec in TASKS]
    assert all(item["label"] for item in body)
    assert all(item["description"] for item in body)
    assert all(item["joined"] is False for item in body)
    assert all(item["enabled"] is False for item in body)


def test_listing_reports_the_administrators_switch(client: TestClient, tasks_headers: dict[str, str]) -> None:
    """`enabled` 如实反映配置，且只是**读**——它决定登记的那个人到底会不会被服务。"""
    config_store.put_global_overrides({"auto_settle_enabled": True})

    body = client.get("/api/v1/tasks", params={"cert_no": CERT_NO}, headers=tasks_headers).json()
    states = {item["key"]: item["enabled"] for item in body}

    assert states["auto_settle"] is True
    assert states["renew_wait"] is False


# ── 增删名单里的一行 ──


def test_joining_writes_the_roster_and_leaving_removes_it(client: TestClient, tasks_headers: dict[str, str]) -> None:
    """登记把 `学号=密码` 写进那份名单，取消登记删掉它。"""
    joined = client.put(
        "/api/v1/tasks/auto_settle",
        json={"cert_no": CERT_NO, "password": "000000", "joined": True},
        headers=tasks_headers,
    )

    assert joined.status_code == 200
    assert joined.json()["joined"] is True
    assert config_store.global_effective()["auto_settle_students"] == {CERT_NO: "000000"}

    left = client.put(
        "/api/v1/tasks/auto_settle",
        json={"cert_no": CERT_NO, "password": "000000", "joined": False},
        headers=tasks_headers,
    )

    assert left.status_code == 200
    assert left.json()["joined"] is False
    assert config_store.global_effective()["auto_settle_students"] == {}


def test_each_task_has_its_own_roster(client: TestClient, tasks_headers: dict[str, str]) -> None:
    """四份名单各自独立：同一个人可以只开其中一个。"""
    client.put(
        "/api/v1/tasks/auto_settle",
        json={"cert_no": CERT_NO, "password": "000000", "joined": True},
        headers=tasks_headers,
    )

    effective = config_store.global_effective()
    assert effective["auto_settle_students"] == {CERT_NO: "000000"}
    assert effective["renew_wait_students"] == {}
    assert effective["renew_session_students"] == {}
    assert effective["extend_hours_students"] == {}


def test_leaving_only_removes_that_student(client: TestClient, tasks_headers: dict[str, str]) -> None:
    """名单是共享的：删一个人不动别人。

    这条钉的是"读—改—写拿到的是整份名单、只动自己那一行"，而不是整份替换。
    """
    config_store.put_global_overrides({"auto_settle_students": {OTHER_CERT_NO: "111111"}})

    client.put(
        "/api/v1/tasks/auto_settle",
        json={"cert_no": CERT_NO, "password": "000000", "joined": True},
        headers=tasks_headers,
    )
    assert config_store.global_effective()["auto_settle_students"] == {OTHER_CERT_NO: "111111", CERT_NO: "000000"}

    client.put(
        "/api/v1/tasks/auto_settle",
        json={"cert_no": CERT_NO, "password": "000000", "joined": False},
        headers=tasks_headers,
    )
    assert config_store.global_effective()["auto_settle_students"] == {OTHER_CERT_NO: "111111"}


def test_joining_never_touches_the_administrators_switch(client: TestClient, tasks_headers: dict[str, str]) -> None:
    """登记碰不到 `*_enabled`：那个开关是管理员的，登记与生效是两层。

    顺带钉住 `update_global` 那个陷阱——`augment` 只回传名单那一项的话，这个开关会因为
    "没出现在输入里"被 `_as_overrides` 当成等于默认值而剔掉，表现是"我加个人进名单，
    自动结算被关了"。
    """
    config_store.put_global_overrides({"auto_settle_enabled": True})

    client.put(
        "/api/v1/tasks/auto_settle",
        json={"cert_no": CERT_NO, "password": "000000", "joined": True},
        headers=tasks_headers,
    )

    effective = config_store.global_effective()
    assert effective["auto_settle_enabled"] is True
    assert effective["auto_settle_students"] == {CERT_NO: "000000"}


def test_joining_is_allowed_while_the_switch_is_off(client: TestClient, tasks_headers: dict[str, str]) -> None:
    """管理员没开也允许登记——这正是界面第三态"已登记 · 待管理员开启"的来源。"""
    assert config_store.global_effective()["renew_session_enabled"] is False

    joined = client.put(
        "/api/v1/tasks/renew_session",
        json={"cert_no": CERT_NO, "password": "000000", "joined": True},
        headers=tasks_headers,
    )

    assert joined.status_code == 200
    assert joined.json()["enabled"] is False
    assert joined.json()["joined"] is True


def test_roster_entries_are_trimmed_at_both_ends(client: TestClient, tasks_headers: dict[str, str]) -> None:
    """学号与密码两端空格去掉——与判据那边的 `roster` 必须一致。

    两处解释成不同的键，表现是"配了却不生效"，而配置页上那一行看起来完全正确。
    """
    client.put(
        "/api/v1/tasks/auto_settle",
        json={"cert_no": f"  {CERT_NO}  ", "password": "  000000  ", "joined": True},
        headers=tasks_headers,
    )

    assert config_store.global_effective()["auto_settle_students"] == {CERT_NO: "000000"}


def test_unknown_task_is_not_found(client: TestClient, tasks_headers: dict[str, str]) -> None:
    r = client.put(
        "/api/v1/tasks/no_such_task",
        json={"cert_no": CERT_NO, "password": "000000", "joined": True},
        headers=tasks_headers,
    )

    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/task-not-found")


def test_blank_credentials_are_rejected(client: TestClient, tasks_headers: dict[str, str]) -> None:
    """空学号或空密码在门口挡掉：它们只会往名单里写一行永远匹配不上的东西。"""
    for payload in (
        {"cert_no": "", "password": "000000", "joined": True},
        {"cert_no": CERT_NO, "password": "", "joined": True},
    ):
        r = client.put("/api/v1/tasks/auto_settle", json=payload, headers=tasks_headers)
        assert r.status_code == 422, payload


# ── 自己的身份记录 ──


def test_own_identity_exposes_the_capabilities(client: TestClient, tasks_headers: dict[str, str]) -> None:
    """客户端据此回答"面板显不显示"——一次 200 说完，不必拿 403 反推。"""
    r = client.get("/api/v1/identities/me", headers=tasks_headers)

    assert r.status_code == 200
    body = r.json()
    assert body["identity"] == TASKS_IDENTITY
    assert body["capabilities"] == ["tasks"]
    assert body["device_scope"] == []


def test_own_identity_needs_an_identity(client: TestClient) -> None:
    r = client.get("/api/v1/identities/me")

    assert r.status_code == 401


def test_own_identity_is_not_the_roster(client: TestClient, ops_headers: dict[str, str]) -> None:
    """`/identities/me` 不是名单：没有 admin 位也能读自己那一条，读不到别人的。"""
    r = client.get("/api/v1/identities/me", headers=ops_headers)

    assert r.status_code == 200
    assert r.json()["identity"] == "test-identity"
    assert "identities" not in r.json()


# ── 写入口为什么必须是 async ──


def test_reload_needs_a_running_event_loop(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """`sessions_scheduler.reload()` 只能在事件循环上调用。

    这就是 `PUT /api/v1/tasks/{task}` 必须是 `async def` 的依据：同步端点在 FastAPI 里跑
    线程池，那里没有运行中的循环。把这条钉成测试，而不是注释里的一句提醒——写成 `def`
    的话请求会 500，而单看代码看不出来。
    """
    monkeypatch.setattr(sessions_scheduler, "_workers", {})
    worker = sessions_scheduler._worker("dev-reload")
    monkeypatch.setattr(sessions_scheduler, "_running", True)
    monkeypatch.setattr(sessions_scheduler, "_workers", {"dev-reload": worker})

    with pytest.raises(RuntimeError, match="no running event loop"):
        sessions_scheduler.reload()


def test_membership_endpoint_is_a_coroutine_function() -> None:
    """写入口必须是协程函数。

    它写完名单要调 `sessions_scheduler.reload()`，而那个取 `asyncio.get_running_loop()`
    （见上一条）。FastAPI 把同步端点丢进线程池，那里没有运行中的循环——改成 `def` 就是
    请求 500，而单看代码看不出来。这条把它钉住。

    **不从 `app.routes` 找那个端点**：这个 FastAPI 版本把 included router 包成一个
    `_IncludedRouter` 条目，顶层遍历不到子路由（`test_api_contract.py` 之所以从
    `app.openapi()` 反查，也是同一个原因）。而 `@router.put` 装饰器返回的就是原函数，
    所以直接查模块里那一个即可。
    """
    assert inspect.iscoroutinefunction(set_membership_endpoint)


# ── 当前态看板（admin）──


def test_status_reports_tasks_extending_and_scheduled(
    client: TestClient, admin_headers: dict[str, str], fresh_db: str
) -> None:
    """当前态的形状：四任务（开关与名单数）、延长中的设备、调度器的即时计划。

    新库上四任务全关、无延长、无排定——看板在"系统闲着"时也必须能如实显示闲着。
    """
    resp = client.get("/api/v1/background-tasks/status", headers=admin_headers)

    assert resp.status_code == 200
    data = resp.json()
    assert [task["key"] for task in data["tasks"]] == [
        "renew_wait",
        "renew_session",
        "auto_settle",
        "extend_hours",
    ]
    assert all(task["enabled"] is False and task["roster"] == 0 for task in data["tasks"])
    assert data["extending"] == []
    assert data["scheduled"] == []


def test_status_includes_the_extending_device(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    """延长中的设备是"哪台设备钟是拨快着的"唯一可见面：表里有行，看板就报。"""
    from app.modules.extend_hours import repository as extend_repository

    extend_repository.record("dev-status-ext", "2026-10-05T15:49:30.000Z", [{"door_no": 3, "cert_no": CERT_NO}])

    data = client.get("/api/v1/background-tasks/status", headers=admin_headers).json()

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
    assert row["reassigned_count"] == 0


def test_status_requires_admin(client: TestClient, fresh_db: str, tasks_headers: dict[str, str]) -> None:
    """当前态是管理面的问题：tasks 位调不动它。"""
    resp = client.get("/api/v1/background-tasks/status", headers=tasks_headers)

    assert resp.status_code == 403
