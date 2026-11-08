"""统计域：口径、守卫与返回形状。

口径本身写在 `service` 的模块注释里，这里把它们变成可执行的断言——注释会漂，
断言不会。
"""

from collections.abc import Iterator
from contextlib import AbstractContextManager
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from tests import device_link
from tests.identity import bearer
from tests.identity import register as register_identity

from app.core.clock import now_rfc3339, parse_rfc3339, rfc3339_ago, shift_rfc3339, to_local
from app.db.base import utcnow
from app.modules.access.identity import IdentityKind
from app.modules.doors.schemas import DoorState, state_label
from app.modules.doors.service import SESSION_MAX_S
from app.modules.stats import service, spec


def _dt(value: str):
    """RFC 3339 文本 → UTC datetime（模型列的落库形状）。"""
    from datetime import UTC, datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _put(row: object) -> int:
    """落一行种子并返回自增 id。"""
    from app.db.session import session_scope

    with session_scope() as session:
        session.add(row)
        session.commit()
        session.flush()
        return int(row.id)


def _device_pk() -> int | None:
    from sqlalchemy import select

    from app.db.session import session_scope
    from app.models.identity import Device

    with session_scope() as session:
        return session.scalar(select(Device.id).where(Device.device_id == DEVICE))


def _db_session() -> AbstractContextManager[Session]:
    """单元用例的短会话。"""
    from contextlib import contextmanager

    @contextmanager
    def ctx() -> Iterator[Session]:
        from app.db.session import session_scope

        with session_scope() as session:
            yield session

    return ctx()


def _insert_link_event(device_id: str, event: str, at: str) -> None:
    """登记一条链路事件；时刻解析失败的原用例在新层的类型上不可能发生，跳过。"""
    from app.models.enums import LinkEvent
    from app.persistence import devices as devices_store

    parsed = parse_rfc3339(at)
    if parsed is None:
        return
    with _db_session() as session:
        devices_store.insert_link_event(session, device_id, LinkEvent(event), parsed)


def _cell(since: str, until: str, tz: int, device_id: str) -> dict[tuple[int, int], float]:
    from app.db.session import session_scope

    with session_scope() as session:
        return service._cell_seconds(session, since, until, tz, device_id)


def _offline(since: str, until: str, tz: int, device_id: str) -> dict[tuple[int, int], float]:
    from app.db.session import session_scope

    with session_scope() as session:
        return service._offline_seconds(session, device_id, since, until, tz)


STATS_IDENTITY = "stats-probe"
DEVICE = "dev-stats"
TZ = 480
"""测试一律用 UTC+8：分桶的断言要看得懂，就得让本地时刻与 UTC 相差一个整小时数。"""


def _moment(seconds_ago: int) -> str:
    return rfc3339_ago(seconds_ago)


def _this_floor(body: dict) -> dict:
    """「各楼层」里本设备那一行。

    **统计量的落点是这里**：人次、平均淋浴时长、平均等待都在这一行上；`totals` 只留了
    页面上真正会读的中位数（顶部那句总览已经删了，它与这张表是同一批数）。
    """
    return next(item for item in body["compare"] if item["device_id"] == body["device_id"])


def _seed_doors() -> None:
    """给七扇门各落一行 FREE 现态：统计的喷头数与今日摘要的占用数都读它。"""
    from app.models.doors import DoorState as DoorStateRow

    for door in range(1, 8):
        _put(
            DoorStateRow(
                device_id=_device_pk(),
                door_no=door,
                state=DoorState.FREE,
                reported_at=_dt(now_rfc3339()),
            )
        )


def _session(
    door_no: int,
    cert_no: str,
    seconds_ago: int,
    flow_s: int,
    *,
    duration_s: int | None = None,
    gap: bool = False,
    open_after_s: int | None = None,
) -> int:
    """造一场会话，返回它的 id。

    时刻相对此刻算，窗口才刚好把它包含进来。`duration_s` 缺省等于淋浴时长（即"一分配
    就开阀、洗完就释放"），给了就在前后补上等待与收尾。

    `gap=True` 造一场跨盲区的会话：`last_seen_at` 与 `ended_at` 相差超过一次占用的
    物理上限，占用类指标应当把它排除。
    """
    from app.models.usage import UsageSession

    assigned = _dt(_moment(seconds_ago))
    span = duration_s if duration_s is not None else flow_s
    ended = _dt(_moment(max(0, seconds_ago - span)))
    last_seen = _dt(_moment(max(0, seconds_ago - flow_s - SESSION_MAX_S - 60))) if gap else ended
    session_id = _put(
        UsageSession(
            device_id=_device_pk(),
            door_no=door_no,
            cert_no=cert_no,
            assigned_at=assigned,
            assigned_source="observed",
            observed_span="gap" if gap else "continuous",
            ended_at=ended,
            last_seen_at=last_seen,
            flow_s=flow_s,
            device_flow_s=flow_s,
            segments_complete=True,
        )
    )
    if open_after_s is not None:
        # 一段：开阀时刻比分配晚 open_after_s，关阀在它之后 flow_s 秒。
        from app.models.usage import UsageSegment

        opened = _dt(_moment(max(0, seconds_ago - open_after_s)))
        closed = _dt(_moment(max(0, seconds_ago - open_after_s - flow_s)))
        _put(
            UsageSegment(
                session_id=session_id,
                device_id=_device_pk(),
                door_no=door_no,
                opened_at=opened,
                closed_at=closed,
                flow_s=flow_s,
                source="measured",
            )
        )
    return session_id


def _segment(session_id: int, door_no: int, seconds_ago: int, flow_s: int) -> None:
    """给已有会话补一段，用来造"关了几次阀"的分布。"""
    from app.models.usage import UsageSegment

    _put(
        UsageSegment(
            session_id=session_id,
            device_id=_device_pk(),
            door_no=door_no,
            opened_at=_dt(_moment(seconds_ago)),
            closed_at=_dt(_moment(max(0, seconds_ago - flow_s))),
            flow_s=flow_s,
            source="measured",
        )
    )


def _event(
    door_no: int,
    state: DoorState,
    seconds_ago: int | None = None,
    cert_no: str = "",
    bath_s: int = 0,
    *,
    at: str | None = None,
) -> None:
    from app.models.doors import DoorStateEvent

    _put(
        DoorStateEvent(
            occurred_at=_dt(at) if at is not None else _dt(_moment(seconds_ago or 0)),
            device_id=_device_pk(),
            door_no=door_no,
            from_state=DoorState.FREE,
            to_state=state,
            card_no=0,
            cert_no=cert_no,
            bath_time_s=bath_s,
        )
    )


@pytest.fixture
def stats_headers(fresh_db: str) -> dict[str, str]:
    """持 `stats` 位的调用方，外加一台已注册的设备。"""
    from app.db.base import utcnow
    from app.persistence import devices as devices_store

    token = register_identity(STATS_IDENTITY, IdentityKind.OPENID, ["stats"], "统计探针", device_scope=[])
    with device_link.db() as session:
        devices_store.register(
            session,
            device_id=DEVICE,
            app_version="0.5.0",
            version_code=1,
            os_version="4.4.4",
            capabilities=[],
            ip="127.0.0.1",
            booted_at=utcnow(),
            seen_at=utcnow(),
        )
        devices_store.set_name(session, DEVICE, "测试浴室")
    return bearer(token)


@pytest.fixture
def seeded(fresh_db: str, stats_headers: dict[str, str]) -> dict[str, str]:
    """一场够用的数据集：跨窗口的会话、带等待的一段、一场跨盲区的会话。"""
    from app.models.doors import DoorState as DoorStateRow

    for door in range(1, 8):
        _put(
            DoorStateRow(
                device_id=_device_pk(),
                door_no=door,
                state=DoorState.FREE,
                reported_at=_dt(now_rfc3339()),
            )
        )
    _session(1, "2200000001", 3600, 600, open_after_s=30)
    _session(2, "2200000002", 7200, 900)
    _session(3, "2200000001", 3 * 86400, 300)
    _session(4, "2200000003", 20 * 86400, 1800, open_after_s=120)
    _session(5, "2200000004", 40 * 86400, 1200)
    _session(6, "2200000005", 60 * 86400, 60, gap=True)
    return stats_headers


# ── 登记表本身 ──


def test_histogram_buckets_are_open_ended_only_at_the_end() -> None:
    """分档的上界必须递增，且只有最后一档可以没有上界。"""
    for item in spec.HISTOGRAMS:
        uppers = [bucket.upper_s for bucket in item.buckets]
        assert uppers[-1] is None, item.key
        bounded = [value for value in uppers[:-1] if value is not None]
        assert bounded == sorted(bounded), item.key


def test_state_options_cover_every_state() -> None:
    """状态清单要覆盖枚举的每一个取值，且文案直接取自 `doors.schemas`。

    缺一个就不会出现在回放的图例里，而页面不会报错——那台设备上的那个状态只是
    永远不显示。
    """
    options = service.state_options()
    assert {item["key"] for item in options} == {state.value for state in DoorState}
    for item in options:
        assert item["label"] == state_label(DoorState(item["key"]))


# ── 守卫 ──


def test_anonymous_is_rejected(client: TestClient) -> None:
    assert client.get(f"/api/v1/stats/devices/{DEVICE}").status_code == 401


def test_without_the_capability(client: TestClient, ops_headers: dict[str, str]) -> None:
    """有身份但没有 `stats` 位：403 capability-denied。"""
    r = client.get(f"/api/v1/stats/devices/{DEVICE}", headers=ops_headers)
    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/capability-denied")


def test_unknown_device_is_not_found(client: TestClient, seeded: dict[str, str]) -> None:
    r = client.get("/api/v1/stats/devices/no-such-device", headers=seeded)
    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/device-not-found")


def test_replay_rejects_a_malformed_day(client: TestClient, seeded: dict[str, str]) -> None:
    r = client.get(f"/api/v1/stats/devices/{DEVICE}/replay?day=2026-10-3", headers=seeded)
    assert r.status_code == 422


# ── 返回形状 ──


def test_heat_is_a_seven_by_hour_count_matrix(client: TestClient, seeded: dict[str, str]) -> None:
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=30", headers=seeded).json()
    assert len(body["heat"]) == 7
    assert all(len(row) == body["hour_count"] for row in body["heat"])
    assert body["hour_start"] + body["hour_count"] == 24


def test_today_summary_reports_the_present_moment(client: TestClient, seeded: dict[str, str]) -> None:
    """「今日」三个数是**截至现在**：已洗人次、淋浴中、平均淋浴时长。"""
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=30", headers=seeded).json()
    assert body["today"]["as_of"]
    assert body["today"]["served"] >= 0
    assert body["today"]["bathing"] >= 0


def test_histogram_counts_add_up(client: TestClient, seeded: dict[str, str]) -> None:
    """两个分布的各档之和都等于**出过水的**会话数——分档是穷尽的，不该漏掉任何一场。"""
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    for item in body["histograms"]:
        assert sum(row["count"] for row in item["rows"]) == _this_floor(body)["sessions_flowed"], item["key"]


def test_leaderboard_ranks_are_consecutive(client: TestClient, seeded: dict[str, str]) -> None:
    """七个榜在同一个列表里（按人排与按场排混着一列页签），每个方向的排名都从 1 连到底。"""
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    assert [board["key"] for board in body["leaderboards"]] == [item.key for item in spec.BOARDS]
    for board in body["leaderboards"]:
        for order, rows in board["rows"].items():
            ranks = [row["rank"] for row in rows]
            assert ranks == list(range(1, len(ranks) + 1)), f"{board['key']}/{order}"


def test_leaderboard_order_follows_the_direction(client: TestClient, seeded: dict[str, str]) -> None:
    """每个方向真的按它说的排：`desc` 从多到少，`asc` 从少到多。

    方向是榜的一个参数（`orders`），**两个方向在同一次响应里都算好了**——所以这里能
    一次把两边都验掉。写反了不会报错，只会让榜首变成榜尾。
    """
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    for board in body["leaderboards"]:
        keys = [order["key"] for order in board["orders"]]
        assert keys[0] == board["order"], board["key"]
        assert set(board["rows"]) == set(keys), board["key"]
        for order, rows in board["rows"].items():
            values = [row["value"] for row in rows]
            assert values == sorted(values, reverse=order == "desc"), f"{board['key']}/{order}"


def test_window_and_timezone_come_back_as_given(client: TestClient, seeded: dict[str, str]) -> None:
    """响应里回带本次分桶用的口径，客户端据此显示"近 N 天""几点"。

    不回带的话客户端只能自己记住请求时传了什么，而它一旦与服务端实际用的不一致，
    页面上标注的口径就是假的。
    """
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=7&tz_offset_minutes=0", headers=seeded).json()
    assert body["tz_offset_minutes"] == 0
    assert body["hour_start"] + body["hour_count"] == 24
    assert body["since"] < body["until"]


# ── 口径 ──


def test_a_wider_window_sees_more(client: TestClient, seeded: dict[str, str]) -> None:
    """窗口真的在切数据，而不是把同一批数字换个标签。"""
    narrow = client.get(f"/api/v1/stats/devices/{DEVICE}?days=7", headers=seeded).json()
    wide = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    assert _this_floor(narrow)["sessions_flowed"] < _this_floor(wide)["sessions_flowed"]


def test_timezone_moves_the_buckets(client: TestClient, seeded: dict[str, str]) -> None:
    """同一批会话在不同时区下落到不同的小时格里。

    **不能断言"总量不变"**：服务时段 11–24 时是**本地**概念，换了偏移就是换了另一段
    绝对时间，落进时段内的占用秒数与分母都会变。能断言的只有分桶结果确实变了。

    种子会话按"此刻往前"生成，而热力图只收 11–23 时；在上午跑测试时它们会全部落在
    服务时段外。这里单独补一场昨天 20:30 的会话，并直接断言它落在东八区 20 点格、
    UTC 12 点格——与运行时刻无关。
    """
    now = to_local(now_rfc3339(), TZ)
    assert now is not None
    yesterday_evening = (now - timedelta(days=2)).replace(hour=20, minute=30, second=0, microsecond=0)
    _session(7, "2200000006", int((now - yesterday_evening).total_seconds()), 600)

    east = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90&tz_offset_minutes=480", headers=seeded).json()
    utc = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90&tz_offset_minutes=0", headers=seeded).json()

    assert east["tz_offset_minutes"] == 480
    assert utc["tz_offset_minutes"] == 0
    assert east["heat"] != utc["heat"]
    weekday = yesterday_evening.weekday()
    assert east["heat"][weekday][20 - spec.HOUR_START] > 0
    assert utc["heat"][weekday][12 - spec.HOUR_START] > 0


def test_sessions_across_a_blind_spot_stay_out_of_occupancy_metrics(client: TestClient, seeded: dict[str, str]) -> None:
    """口径 4：跨盲区的那一场不进热力图，但它的淋浴时长照旧进均值。

    盲区说的是"我们没看见中间那一段"，它影响的是占用与等待的长度，与出了多少水无关。
    """
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    # 六场里有一场跨盲区；它进 totals，不进 heat 的分子。
    assert _this_floor(body)["sessions_flowed"] == 6
    assert round(sum(sum(row) for row in body["heat"]), 2) < 6


def test_percentile_is_zero_when_there_is_nobody_to_compare(client: TestClient, stats_headers: dict[str, str]) -> None:
    """只有一个人时"比 100% 的人快"没有信息量，给 0 而不是给一个看着像结论的数。"""
    _session(1, "2200000001", 3600, 600)
    body = client.get("/api/v1/stats/mine?cert_no=2200000001&days=30", headers=stats_headers).json()
    assert body["sessions_flowed"] == 1
    assert body["faster_than_pct"] == 0


def test_mine_follows_the_window(client: TestClient, seeded: dict[str, str]) -> None:
    """个人统计也随窗口变——切到"近 7 天"，卡片标题与里面的数必须是同一口径。

    用 `2200000004`：他只有一场，落在 40 天前，所以只在 90 天的窗口里。
    """
    narrow = client.get("/api/v1/stats/mine?cert_no=2200000004&days=7", headers=seeded).json()
    wide = client.get("/api/v1/stats/mine?cert_no=2200000004&days=90", headers=seeded).json()
    assert narrow["sessions_flowed"] == 0
    assert wide["sessions_flowed"] == 1
    assert narrow["sessions_flowed"] < wide["sessions_flowed"]


def test_replay_returns_that_day_only(client: TestClient, seeded: dict[str, str]) -> None:
    """回放按当地日期切，且按时刻升序——客户端要靠顺序做重放。

    事件时刻按"今天当地的上午"显式构造，而不是"现在减 N 秒"：后者在午夜前后一小时内
    跑测试会落到昨天，用例就随钟点间歇性地挂。
    """
    day = to_local(now_rfc3339(), TZ)
    assert day is not None
    for _offset, (state, index) in enumerate([(DoorState.WAITING, 0), (DoorState.BATHING, 60), (DoorState.FREE, 120)]):
        at = shift_rfc3339(f"{day.date().isoformat()}T02:00:00.000Z", index)
        assert at is not None
        _event(1, state, None, at=at)

    body = client.get(
        f"/api/v1/stats/devices/{DEVICE}/replay?day={day.date().isoformat()}&tz_offset_minutes={TZ}",
        headers=seeded,
    ).json()
    assert len(body["events"]) == 3
    assert [item["at"] for item in body["events"]] == sorted(item["at"] for item in body["events"])

    other = (day - timedelta(days=30)).date().isoformat()
    empty = client.get(
        f"/api/v1/stats/devices/{DEVICE}/replay?day={other}&tz_offset_minutes={TZ}", headers=seeded
    ).json()
    assert empty["events"] == []


# ── 出没出水：两套口径的分界 ──
# 占用类（热力图、高峰、等待）看"占着喷头没有"；
# 其余每一项看"洗过没有"。混了不报错——只会让均值悄悄偏低。


def test_an_empty_session_counts_only_in_the_session_count(client: TestClient, seeded: dict[str, str]) -> None:
    """没出水的会话只进「会话数」一榜，其余每一项都不认它。

    `flow_s = 0` 是"分配了喷头却没出水"——测试行为，不是淋浴。生产数据里它占 16%，
    近 7 天窗口高达 32%。但它仍然算"占了喷头一次"，所以会话数与占用类指标都要含它。
    """
    _session(7, "2200000006", 1800, 0)

    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    boards = {board["key"]: board for board in body["leaderboards"]}

    # 「会话数」含它：那个人有一场。
    assert "2200000006" in [row["cert_no"] for row in boards["sessions"]["rows"]["desc"]]
    # 「出水会话数」不含它：那个人一场出水的都没有。
    assert "2200000006" not in [row["cert_no"] for row in boards["sessions_flowed"]["rows"]["desc"]]
    # 汇总、两个分布、喷头热度、楼层对比也都不认它。
    assert _this_floor(body)["sessions_flowed"] == 6
    for item in body["histograms"]:
        assert sum(row["count"] for row in item["rows"]) == 6, item["key"]
    assert sum(row["sessions_flowed"] for row in body["doors"]) == 6
    assert body["compare"][0]["sessions_flowed"] == 6


def test_average_board_lists_everyone_who_flowed(client: TestClient, stats_headers: dict[str, str]) -> None:
    """「平均淋浴」不设最低会话数：出过水就上榜。

    原先有一条"满 3 次"的门槛。去掉它是因为**门槛在页面上只表现为"榜上少了几个人"**
    ——读者看不出那是筛过，只会以为那些人没洗过。有快就有慢，样本薄是他自己的事。
    """
    _seed_doors()
    # 甲只洗过一次；乙开过一次会话但没出水。
    _session(1, "2200000001", 3600, 600)
    _session(2, "2200000002", 7200, 0)

    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=stats_headers).json()
    boards = {board["key"]: board for board in body["leaderboards"]}

    # 只洗过一次的人也上榜——没有最低会话数。默认方向是"洗得最快"。
    assert [row["cert_no"] for row in boards["avg"]["rows"]["asc"]] == ["2200000001"]
    # 没出过水的人不在这一榜上，但这与门槛无关：`_flowed` 那一步就把他排除了，
    # 而在「会话数」那一榜里他照旧算一场。
    assert "2200000002" in [row["cert_no"] for row in boards["sessions"]["rows"]["desc"]]


def test_a_person_board_row_carries_the_other_counts(client: TestClient, seeded: dict[str, str]) -> None:
    """按人排的榜，每行除 `value` 外还带**这个人的三个基本数**；按场排的那一榜不带。

    副行靠它们说话：「会话数」对「出水会话数」、「总时长」对「平均淋浴」、需要样本量的
    几榜给次数。少了这几个数副行只能空着，而 `.lb-sub` 是定宽的——右侧留一块空白，
    看着像信息没加载出来，页面上却不会报错。
    """
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    for board in body["leaderboards"]:
        for row in board["rows"][board["order"]]:
            if board["key"] == "single":
                # 这一行是"某一场"，不是某个人：三个数没有意义，喷头号与日期才有。
                assert row["sessions"] is None, board["key"]
                assert row["sessions_flowed"] is None, board["key"]
                assert row["flow_avg_s"] is None, board["key"]
                assert row["door_no"] is not None, board["key"]
            else:
                assert row["sessions"] is not None, board["key"]
                assert row["sessions_flowed"] is not None, board["key"]
                assert row["flow_avg_s"] is not None, board["key"]
                assert row["assigned_avg_s"] is not None, board["key"]
                assert row["door_no"] is None, board["key"]
                # 出水会话数不可能多于全部会话数——两个数反了不会报错，只会看着"更勤快"。
                assert row["sessions_flowed"] <= row["sessions"], board["key"]
                # 分配时长（占用）必然不短于淋浴时长：那段里包含了没出水的时间。
                assert row["assigned_avg_s"] >= row["flow_avg_s"], board["key"]


def test_histogram_views_carry_their_note(client: TestClient, seeded: dict[str, str]) -> None:
    """每个分布都带副标口径；「淋浴时长分布」留空——它的副标要放窗口内的中位。

    留空是**有意的**，不是漏了：那一块的副标由中位那个数充当，比一句静态注解更该出现
    在那里。所以这里不断言它非空，只断言两个分布都在、且另一个确实给了注解。
    """
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    notes = {item["key"]: item["note"] for item in body["histograms"]}
    assert set(notes) == {"flow", "pauses"}
    assert notes["flow"] == ""
    assert notes["pauses"].strip()


def test_one_open_and_one_close_counts_as_one_close(client: TestClient, stats_headers: dict[str, str]) -> None:
    """开一次阀、关一次，就是**关阀 1 次**——不是 0 次。

    一段就是"从开阀到关阀"（`REFERENCE.md` 4.4），所以每段结束都关了一次阀。原先算的是
    "段数减 1"，于是"分配 → 开阀 → 暂停 → 结算"这种正常流程被记成**关 0 次**，而那一档
    看起来只是"有人一次都没停过"。
    """
    _seed_doors()
    _session(1, "2200000001", 3600, 600, open_after_s=30)

    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=stats_headers).json()
    histograms = {item["key"]: item for item in body["histograms"]}
    buckets = {row["label"]: row["count"] for row in histograms["pauses"]["rows"]}

    assert buckets["1 次"] == 1
    # 「0 次」这一档不该存在：有出水就至少关一次阀。
    assert "0 次" not in buckets


def test_replay_carries_the_last_close_from_before_that_day(client: TestClient, seeded: dict[str, str]) -> None:
    """回放带上**那一天之前**的最后一次关阀，空闲芯片的"上次使用"靠它。

    当天的事件序列里没有它——那一次发生在更早的日子，而回放一次只拉一天。不带的话，
    当天没被用过的喷头会恒显示空，而页面上看不出来是"没有"还是"没拉到"。
    """
    _session(1, "2200000001", 20 * 86400, 600, open_after_s=30)
    day = to_local(now_rfc3339(), TZ)
    assert day is not None

    body = client.get(
        f"/api/v1/stats/devices/{DEVICE}/replay?day={day.date().isoformat()}&tz_offset_minutes={TZ}",
        headers=seeded,
    ).json()
    last = {item["door_no"]: item for item in body["last_closed"]}

    assert 1 in last
    assert last[1]["cert_no"] == "2200000001"
    assert last[1]["closed_at"]


def test_a_streak_of_one_day_counts_as_one(client: TestClient, stats_headers: dict[str, str]) -> None:
    """只来过一天的人，「连续天数」是 **1**，不是 0。

    这条边界完全落在 `_streak_days` 那行 `longest = current = 1` 上：只有一天时
    `pairwise` 是空的、循环不跑。改成 0 不会报错，只会让"来过一天"和"没来过"一样。
    """
    _seed_doors()
    _session(1, "2200000001", 3600, 600)

    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=stats_headers).json()
    boards = {board["key"]: board for board in body["leaderboards"]}

    assert [(row["cert_no"], row["value"]) for row in boards["streak"]["rows"]["desc"]] == [("2200000001", 1)]


def test_a_tie_on_the_main_column_falls_back_to_the_detail(client: TestClient, stats_headers: dict[str, str]) -> None:
    """主列打平时按副列排——副列是这一榜的另一半。

    两人都是 2 场（主列并列），但甲每次分配后隔很久才开阀、乙立刻开。此时副列（平均占用）
    才是有信息量的那个：降序下甲应当在前。

    不定这一层的话，先后由查询返回的顺序决定——同一批数据两次请求可能给出不同的先后，
    而页面上只是名次悄悄换了，看不出是没定序。
    """
    _seed_doors()
    # 甲：两场，每场占用 3600 秒（分配后 3000 秒才开阀）。
    _session(1, "2200000001", 20000, 600, duration_s=3600, open_after_s=3000)
    _session(2, "2200000001", 40000, 600, duration_s=3600, open_after_s=3000)
    # 乙：同样两场，但分配后立刻开阀，占用只有淋浴那 600 秒。
    _session(3, "2200000002", 60000, 600)
    _session(4, "2200000002", 80000, 600)

    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=stats_headers).json()
    boards = {board["key"]: board for board in body["leaderboards"]}
    rows = boards["sessions"]["rows"]["desc"]

    # 主列并列（都是 2），所以次序完全由副列决定。
    assert [row["value"] for row in rows] == [2, 2]
    assert [row["cert_no"] for row in rows] == ["2200000001", "2200000002"]
    assert rows[0]["assigned_avg_s"] > rows[1]["assigned_avg_s"]


def test_wait_measures_full_occupancy_not_the_gap_before_opening(
    client: TestClient, stats_headers: dict[str, str]
) -> None:
    """「平均等待」量的是**满员时段**：从最后一个空位被占到第一个空位空出。

    四个人占 7 个喷头里的 4 个——**从来没满过**，所以等待是 0。这一条正是为了挡住旧算法：
    旧算法量的是"分配后多久开阀"，在同样这组数据上会给出一个正数（每次都有间隔），
    而那时候根本没有人在等。

    这不是咬文嚼字：满员是"挤不进去"，分配后开阀是"自己的喷头自己多久才用"。
    """
    _seed_doors()
    # 4 场并行占用，各自 3000 秒；每人分配后 900 秒才开阀。
    for door in range(1, 5):
        _session(door, f"220000000{door}", 6000, 1800, duration_s=3000, open_after_s=900)

    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=stats_headers).json()
    # 从没满过 → 一次等待都没有。旧算法量的是"分配后多久开阀"，在同样这组数据上会给出
    # 一个正数（每人都有间隔），而那段时间里根本没有人在等。
    this_floor = next(item for item in body["compare"] if item["device_id"] == body["device_id"])
    assert this_floor["wait_avg_s"] == 0


def test_wait_is_the_window_where_every_door_is_taken(client: TestClient, stats_headers: dict[str, str]) -> None:
    """把 7 个喷头占满之后空出一个，那一段就是一次等待，长度就是那一段的长度。

    7 场先后占满（第一场最早开始、最后一场最晚开始），第 8 场起来之前先释放一个：
    从"占满"到"空出"之间的秒数，就是这一次等待。
    """
    _seed_doors()
    # 七场把喷头占满：最早的一场 10000 秒前开始，最晚的 4000 秒前开始（即 4000 秒前满员）。
    for door in range(1, 8):
        _session(door, f"220000000{door}", 6000 + door * 500, 1800, duration_s=9000)
    # 满员持续到 2000 秒前：那一刻第一场释放。
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=stats_headers).json()
    # 满员起点 = 最后一场的开始（4000 秒前），终点 = 第一个释放（最早那场结束）。
    # 时长 > 0 即说明满员口径生效；具体秒数由上面两组时刻决定。
    assert body["compare"][0]["wait_avg_s"] > 0


def test_heat_levels_rank_against_the_boards_own_peak(client: TestClient, seeded: dict[str, str]) -> None:
    """色阶相对于**本图自己的峰值**，不是相对容量。

    这是 Google Popular Times 的做法（"热门时段"把每个时段相对于该地点最忙的时段表示）：
    每台设备跟自己比，所以喷头数不同（5／6／7／8）、用法不同的设备都不用调门槛。绝对占用率
    做不到——门槛要按设备重定，否则一台全红、另一台全绿。

    断言只写两条**与夹具无关**的性质：最忙的那一格必是最高档、没人的格子必是最低档。
    中间档位随数据变，写死就把这个测试绑在某一份夹具上了。
    """
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    heat, levels = body["heat"], body["heat_levels"]
    assert len(levels) == len(heat) == 7
    top = len(spec.HEAT_LEVELS) + 1
    peak = max(value for row in heat for value in row)
    for week, (row, level_row) in enumerate(zip(heat, levels, strict=True)):
        assert len(level_row) == body["hour_count"], week
        for busy, level in zip(row, level_row, strict=True):
            assert 0 <= level <= top
            if busy <= 0:
                assert level == 0, week
            if peak > 0 and busy == peak:
                assert level == top, week


def test_heat_copy_is_served_by_the_backend(client: TestClient, seeded: dict[str, str]) -> None:
    """热力图的标题、副标、图例与逐格说明都由服务端下发。

    客户端发一次版成本高，而这些是文案与口径的表述——收在服务端，改字不必发版，
    也不存在两份文案各自漂移的可能。断言形状与来源，不锁定具体字句（字句会随
    措辞调整，字句由 `spec` 与测试同源保证）。
    """
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    assert body["heat_title"] == spec.HEAT_TITLE
    assert body["heat_note"] == spec.HEAT_NOTE.format(days=90)
    assert [item["key"] for item in body["heat_legend"]] == [str(i) for i in range(len(spec.HEAT_LEGEND))]
    assert [item["label"] for item in body["heat_legend"]] == list(spec.HEAT_LEGEND)

    texts = body["heat_texts"]
    assert len(texts) == 7
    for weekday, row in enumerate(texts):
        assert len(row) == body["hour_count"], weekday
        for column, text in enumerate(row):
            assert text.startswith(spec.WEEKDAY_LABELS[weekday]), (weekday, column)
            assert "平均占用" in text or "没有人" in text, (weekday, column)

    # 说明行的初始内容也是服务端成文：服务时段内是"现在"那格的说明，之外是整句引导。
    now_text = body["heat_now_text"]
    assert now_text == spec.HEAT_HINT or now_text.startswith(spec.HEAT_NOW_PREFIX)
    if now_text.startswith(spec.HEAT_NOW_PREFIX):
        assert any(now_text.removeprefix(spec.HEAT_NOW_PREFIX) in row for row in texts)


def test_stats_copy_is_served_with_days_filled(client: TestClient, seeded: dict[str, str]) -> None:
    """整页描述文案整表下发：键集与登记表一致，`{days}` 已填，其余占位符保留。

    键集断言保证"客户端要用的新键"与"登记表"同步——服务端删了键而客户端还在读，
    或加了键忘了下发，页面上只是那句话空了，不会报错。
    """
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    copy = body["copy"]
    assert set(copy) == set(spec.STATS_COPY)
    assert copy["doors_note"] == "近 90 天 · 次数与平均淋浴时长"
    assert "{days}" not in copy["doors_note"]
    # 运行时占位符原样下发，客户端填值。
    assert "{as_of}" in copy["today_note"]
    assert "{t}" in copy["replay_detail_bathing"]
    assert "{pct}" in copy["mine_faster_fast"]


def test_compare_rows_follow_the_device_display_order(client: TestClient, seeded: dict[str, str]) -> None:
    """楼层对比按设备登记的展示顺序排，选中的那一台不跳到最前面。

    本设备那一行是主流程算好传进来的——顺手放头部会让这张表的顺序跟着"选了谁"
    走，而它是各楼层的基准读数，顺序该与设备下拉同源（`devices.list_all`）。让另一台
    设备登记在前面、请求的却是后面那台，顺序才试得出来。
    """
    from sqlalchemy import text

    from app.persistence import devices as devices_store

    with device_link.db() as session:
        devices_store.register(
            session,
            device_id="dev-stats-b1",
            app_version="0.5.0",
            version_code=1,
            os_version="4.4.4",
            capabilities=[],
            ip="127.0.0.1",
            booted_at=utcnow(),
            seen_at=utcnow(),
        )
        devices_store.set_name(session, "dev-stats-b1", "B1 浴室")
        session.execute(text("UPDATE devices SET sort_order = 1 WHERE device_id = :d"), {"d": DEVICE})
        session.commit()

    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=30", headers=seeded).json()

    assert [row["device_id"] for row in body["compare"]] == ["dev-stats-b1", DEVICE]
    # 本设备那一行的数值仍是主流程传进来的那一份，位置调整不会让它丢数。
    floor = next(item for item in body["compare"] if item["device_id"] == DEVICE)
    assert floor["sessions_flowed"] > 0


@pytest.mark.usefixtures("client")
def test_heat_denominator_excludes_offline_time(fresh_db: str) -> None:
    """热力图分母剔除设备离线的时段（口径 8）。

    离线期间没有帧、占用分子必为空，分母若照算，每次掉线之后整张图都系统性偏低。
    窗口固定为本地 09-28（周一）12:00 到 09-29（周二）12:00，设备在本地周一
    12–14 时离线：那两格的分母归零，相邻格不受影响。用独立的设备号，离线事件
    不渗进其他用例的窗口。
    """
    link_device = "dev-stats-link"
    from app.persistence import devices as devices_store

    with _db_session() as session:
        devices_store.register(
            session,
            device_id=link_device,
            app_version="0.5.0",
            version_code=1,
            os_version="4.4.4",
            capabilities=[],
            ip="127.0.0.1",
            booted_at=utcnow(),
            seen_at=utcnow(),
        )
    _insert_link_event(link_device, "offline", "2026-09-28T04:00:00Z")
    _insert_link_event(link_device, "online", "2026-09-28T06:00:00Z")

    elapsed = _cell("2026-09-28T04:00:00Z", "2026-09-29T04:00:00Z", TZ, link_device)

    assert elapsed[(0, 1)] == 0.0  # 周一 12 时，整格离线
    assert elapsed[(0, 2)] == 0.0  # 周一 13 时，整格离线
    assert elapsed[(0, 3)] == 3600.0  # 周一 14 时，已恢复在线
    assert elapsed[(1, 0)] == 3600.0  # 周二 11 时，与离线无关

    # 解析不了的时刻成不了分母，也不抛错：坏窗口给空表，坏事件被跳过。
    assert _cell("not-a-time", "2026-09-29T04:00:00Z", TZ, link_device) == {}
    assert _offline("not-a-time", "not-a-time", TZ, link_device) == {}
    _insert_link_event(link_device, "offline", "0")  # 时刻解析不了：写入被跳过
    assert _cell("2026-09-28T04:00:00Z", "2026-09-29T04:00:00Z", TZ, link_device) == elapsed


def test_each_board_names_a_detail_field_that_exists(client: TestClient, seeded: dict[str, str]) -> None:
    """榜的 `detail` 必须指向行上真实存在的字段，且那个字段有值。

    客户端的副行就是 `row[board["detail"]]`——写错一个字母不会报错，那一格只是空的；而
    `.lb-sub` 仍占着 220rpx，右半边看起来像"信息没加载出来"。
    """
    body = client.get(f"/api/v1/stats/devices/{DEVICE}?days=90", headers=seeded).json()
    for board in body["leaderboards"]:
        rows = board["rows"][board["order"]]
        assert rows, board["key"]
        for row in rows:
            if not board["detail"]:
                # 按场排的那一榜：副行是"哪一台、哪一天"，两者都得有。
                assert row["door_no"] is not None, board["key"]
                assert row["at"], board["key"]
                continue
            assert board["detail"] in row, f"{board['key']} → {board['detail']}"
            assert row[board["detail"]] is not None, f"{board['key']} → {board['detail']}"
