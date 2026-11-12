"""按设备聚合洗浴数据，以及个人统计与时间点回放。

## 口径

每条都跟着实现走；改口径先改这里。

1. **窗口按 `assigned_at` 划**：会话落在 `[since, until)` 内才计入。不按 `ended_at`——
   那列可空（进行中的会话没有），拿可空列筛时间会漏行，`idx_usage_session_time`
   也是按 `assigned_at` 建的。

2. **淋浴时长取 `flow_s`**（段之和）。`usage_session` 的列注释写着它是"这个人总共洗了
   多久"的不联表入口；`device_flow_s` 是设备自己报的数，留着供对照，不进统计。

3. **不按 `assigned_source` 过滤**。那列注释写"`observed` 的跨度天然偏小"，但实测近
   30 天 2661 场会话里 `inferred` 只有 22 场——按它过滤等于把统计打成空的（0.8%）。
   而 `observed` 偏小的是"从真实分配到我们首次看见"，量级就是设备上报间隔（样本里
   10 秒级），对分钟级的统计无影响。

4. **占用与等待类排除盲区过大的会话**（见 [_usable_for_occupancy]）。这是正确性规则，
   不是性能取舍——实测近 30 天它几乎不过滤（`gap` 21 场、盲区超上限 0 场），但留着。

5. **等待是满员时段**（[_full_wait_seconds]）：喷头全被占着的那几段，从"最后一个空位
   被占"到"第一个空位空出来"，每一段算一次。它量的是"挤不进去"，取值是占用口径。
   **不是"分配后多久开阀"**——那段时间里喷头已经归使用者了，没有任何人在等它。
   窗口末尾仍在满员的那一段不算：它还没结束。

6. **分桶按调用方给的时区偏移**（`clock.to_local`）。同一场会话在不同时区下会落到
   不同的小时格里，这是必然的——"他几点淋浴"这句话只在某个时区下成立。

7. **除「会话数」一榜与占用类指标外，每一项统计都只计出过水的会话**（[_flowed]）。
   `flow_s = 0` 是"分配了喷头却没出水"——测试行为，不是淋浴。生产数据里它占 16%，
   近 7 天窗口高达 32%；把它算进"平均淋浴"，全表均值会从 296.9 秒掉到 248.3 秒。
   占用类（热力图、高峰时段、等待时长）**不过滤**：占着喷头就算占用，
   有没有出水是另一回事。

8. **热力图的分母剔除设备离线的时段**（[_cell_seconds]）。离线期间没有帧，占用
   分子天然是空的；分母若按墙钟照算，每次掉线之后整张图都系统性偏低。分子同时
   **截到窗口末端**（[_heat]）：会话按 `assigned_at` 入窗，结束可能越过窗口末端，
   溢出的秒数会落进分母不存在的格上，把那一格顶过"全忙"。

9. **热力图的标题、副标、图例、逐格说明与说明行初始内容全部由服务端下发**
   （`spec.HEAT_TITLE` 等）。它们是描述文案与口径的表述，收在服务端一处——改字、
   改措辞不要求客户端发版，也不存在两份文案各自漂移的可能。客户端不为任何一句
   热力图文字持有底稿。

## 一次请求里只查一遍

会话表与段表各查一次，其余维度都从这两份结果推。今日（[today]）是窗口的子集，本地
筛一遍即可；楼层对比里本设备那一行由主流程传进去，不重算——一台设备时重算等于把整套
statistics 白做一次，而它对请求耗时的影响不会报错，只会慢一倍。
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from itertools import pairwise
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import format_rfc3339, now_rfc3339, parse_rfc3339, rfc3339_ago, seconds_between, to_local
from app.models.enums import DoorState
from app.modules.devices.schemas import LINK_OFFLINE
from app.modules.doors.schemas import state_label
from app.modules.doors.service import SESSION_MAX_S
from app.persistence import devices as devices_store
from app.persistence import doors as doors_store

from . import spec
from .schemas import (
    CompareRow,
    DoorUsageRow,
    HistogramRow,
    HistogramView,
    LabeledOption,
    LeaderboardRow,
    LeaderboardView,
    MineView,
    ReplayDoor,
    ReplayDoorLast,
    ReplayView,
    StatsTotals,
    StatsView,
    TodaySummary,
)

# ── 取数 ─────────────────────────────────────────────


def _window(days: int) -> tuple[str, str]:
    """[since, until)。until 取此刻，所以这是滚动窗口，两端都不落在小时边界上。"""
    return rfc3339_ago(days * 86400), now_rfc3339()


def _sessions(session: Session, device_id: str, since: str, until: str) -> list[dict[str, Any]]:
    """窗口内这台设备的会话，按分配时刻升序。"""
    return doors_store.sessions_in_window(session, device_id, since, until)


def _segments_by_session(session: Session, device_id: str, since: str, until: str) -> dict[int, list[dict[str, Any]]]:
    """会话 id → 它的段（按开阀时刻升序）。

    一次取全窗口内的段再分组，比每场会话各查一次少两千多次往返。
    """
    rows = doors_store.segments_in_session_window(session, device_id, since, until)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["session_id"])].append(row)
    return grouped


def _usable_for_occupancy(row: dict[str, Any]) -> bool:
    """这一场能不能进占用与等待类指标（口径 4）。"""
    if str(row["observed_span"]) != "continuous":
        return False
    return seconds_between(row["last_seen_at"], row["ended_at"]) <= SESSION_MAX_S


def _flowed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """出过水的会话（`flow_s > 0`）。

    **除「会话数」一榜外，每一项统计的输入都从这里过。** 它与上面那个占用判据是
    **两套口径，不是一条**：占了喷头就算占用（热力图、高峰时段都按这个），
    而"洗过"要求真的出过水。

    `flow_s = 0` 是"分配了喷头却没出水"——从空闲变到选中、再到等待开阀，最后没淋浴。
    生产数据里它占 16%，近 7 天窗口高达 32%；近 30 天有 70 个学号的两种会话数不相等，
    最极端的一个是 89 场会话、只有 30 次出水。混在一起算，"来得勤"和"洗得多"就成了
    同一件事，而页面上看不出区别。
    """
    return [row for row in rows if int(row["flow_s"]) > 0]


def _door_count(session: Session, device_id: str) -> int:
    return doors_store.door_count(session, device_id)


def _device_name(session: Session, device_id: str) -> str:
    record = devices_store.get(session, device_id)
    return str(record["name"]) if record is not None else ""


# ── 时间与数值 ───────────────────────────────────────


def _day_start(moment: str, offset_minutes: int) -> str:
    """`moment` 所在地那一天的 0 点，换算回 UTC 字符串。

    走 `to_local` + `format_rfc3339` 两步而不是自行拼接：时刻换算一律经 `core/clock`
    （AGENTS.md），而拼错一个零或少一个 Z 都不会报错。
    """
    local = to_local(moment, offset_minutes)
    if local is None:
        return moment
    return format_rfc3339(local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC))


def _average(values: list[int]) -> int:
    return round(sum(values) / len(values)) if values else 0


def _median(values: list[int]) -> int:
    return round(statistics.median(values)) if values else 0


def _bucket_index(value: int, buckets: tuple[spec.HistogramBucket, ...]) -> int:
    """值落进第几档。上界为 None 的那一档收剩下的全部。"""
    for index, bucket in enumerate(buckets):
        if bucket.upper_s is None or value <= bucket.upper_s:
            return index
    return len(buckets) - 1


def _add_span(seconds: dict[tuple[int, int], float], start: datetime, end: datetime) -> None:
    """把 [start, end) 的秒数按 (星期, 小时) 格累加，跨格自动切开。

    一场从 21:40 到 22:10 的会话，两格各占一部分；整场归给起点那一格会使后一格
    少算。会话占用与设备离线共用这条路径，切法才一致。
    """
    cursor = start
    while cursor < end:
        edge = min(cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1), end)
        column = cursor.hour - spec.HOUR_START
        if 0 <= column < spec.HOUR_COUNT:
            seconds[(cursor.weekday(), column)] += (edge - cursor).total_seconds()
        cursor = edge


def _until_dt(until: str) -> datetime:
    """统计窗口右端点（RFC 3339 文本）转 aware datetime；解析失败按纪元处理。"""
    parsed = parse_rfc3339(until)
    return parsed if parsed is not None else datetime.min.replace(tzinfo=UTC)


def _offline_seconds(
    session: Session, device_id: str, since: str, until: str, offset_minutes: int
) -> dict[tuple[int, int], float]:
    """窗口内设备离线的秒数，按 (星期, 小时) 格累计。

    状态从窗口之前最近的一条事件起步（`devices.link_events` 取 `until` 前全部事件）：
    设备在窗口开始前就已掉线、直到窗口内才上线，那段同样是离线。
    """
    seconds: dict[tuple[int, int], float] = defaultdict(float)
    start = to_local(since, offset_minutes)
    end = to_local(until, offset_minutes)
    if start is None or end is None:
        return seconds
    offline_at: datetime | None = None
    for row in devices_store.link_events(session, device_id, before=_until_dt(until)):
        moment = to_local(str(row["occurred_at"]), offset_minutes)
        if moment is None:
            continue
        if str(row["event"]) == LINK_OFFLINE:
            if offline_at is None:
                offline_at = moment
            continue
        if offline_at is not None:
            _add_span(seconds, max(offline_at, start), min(moment, end))
            offline_at = None
    if offline_at is not None:
        _add_span(seconds, max(offline_at, start), end)
    return seconds


def _cell_seconds(
    session: Session, since: str, until: str, offset_minutes: int, device_id: str
) -> dict[tuple[int, int], float]:
    """窗口内每个 (星期, 小时) 格**实际经过**的秒数——热力图的分母。

    墙钟累计再剔掉设备离线的那部分（口径 8）：离线期间没有帧，占用分子是空的，
    分母若照算，每次掉线之后整张图都系统性偏低。

    不用"该星期几出现过几天 × 3600"：窗口两端通常落在半天中间，那种算法会把首尾
    两天各自的不足一天都当成整天。实测 09-27 16:11 到 10-04 00:00 那个窗口，周日
    被算成 2 天，而它实际只覆盖 7.8 小时加 0 小时——近 7 天的口径因此整体偏低。
    """
    seconds: dict[tuple[int, int], float] = defaultdict(float)
    start = to_local(since, offset_minutes)
    end = to_local(until, offset_minutes)
    if start is None or end is None:
        return seconds
    _add_span(seconds, start, end)
    for key, offline in _offline_seconds(session, device_id, since, until, offset_minutes).items():
        seconds[key] = max(0.0, seconds[key] - offline)
    return seconds


# ── 分桶 ─────────────────────────────────────────────


def _heat(
    session: Session, rows: list[dict[str, Any]], since: str, until: str, offset_minutes: int, device_id: str
) -> list[list[float]]:
    """`[星期][小时]` 的平均占用喷头数。

    按会话的占用区间与每个小时格求重叠秒数，再除以那一格在窗口内实际经过的秒数。

    **逐格累加而不是整场算进起点那一格**：一场从 21:40 到 22:10 的会话，两格各占
    一部分；整场归给 21 点会使 22 点那一格少算。

    **会话的结束截到窗口末端**：按 `assigned_at` 入窗的会话可能结束在 `until`／
    `settled` 之后，溢出的秒数会落进分母不存在的格上，把那一格顶过"全忙"。

    取值是**小数**：一小时内的占用喷头数随时在变，"平均占用"本来就带小数。**不要取整**
    ——0.38 抹成 0 之后，那一格看起来像"一直没人"，而它其实一直有人。
    """
    seconds: dict[tuple[int, int], float] = defaultdict(float)
    limit = to_local(until, offset_minutes)
    for row in rows:
        if not _usable_for_occupancy(row):
            continue
        start = to_local(row["assigned_at"], offset_minutes)
        end = to_local(row["ended_at"], offset_minutes)
        if start is None or end is None or end <= start:
            continue
        # 截断后不会倒挂：start < limit 由查询的 assigned_at < until 保证。
        if limit is not None:
            end = min(end, limit)
        _add_span(seconds, start, end)

    elapsed = _cell_seconds(session, since, until, offset_minutes, device_id)
    return [
        [
            round(seconds[(weekday, column)] / elapsed[(weekday, column)], 2) if elapsed[(weekday, column)] else 0.0
            for column in range(spec.HOUR_COUNT)
        ]
        for weekday in range(7)
    ]


# ── 各维度 ───────────────────────────────────────────


def _heat_level(share: float) -> int:
    """一个格子的色阶：**"相对于本设备峰值"到颜色**的那一层映射。

    0 是没人，最高档是本设备最忙的时段。分档界限取自 `spec.HEAT_LEVELS`。

    **它属于服务端**：什么算"忙"是这一格的口径，与榜名、口径注解、副列取哪个字段同类。
    留在客户端就成了第二份——服务端调了分档、客户端不跟着改，页面上只是颜色悄悄变了，
    而两处都不会报错。
    """
    if share <= 0:
        return 0
    for index, upper in enumerate(spec.HEAT_LEVELS):
        if share < upper:
            return index + 1
    return len(spec.HEAT_LEVELS) + 1


def _heat_levels(heat: list[list[float]]) -> list[list[int]]:
    """把热力矩阵映射成色阶矩阵，与 `heat` 同形。

    **分母是本图自己的峰值，不是喷头数**（见 `spec.HEAT_LEVELS`）：相对刻度下每台设备
    都跟自己比，换设备不用调任何门槛。

    峰值取整个矩阵的最大格；**空矩阵或全为 0 时**（新设备、窗口内没有会话）峰值是 0，
    那时每格都归 0 档——不判这一条就会除零。
    """
    peak = max((value for row in heat for value in row), default=0.0)
    if peak <= 0:
        return [[0] * len(row) for row in heat]
    return [[_heat_level(value / peak) for value in row] for row in heat]


def _format_busy(value: float) -> str:
    """0.38 → "0.4"，1.0 → "1"：一位小数，去掉无意义的尾零。"""
    text = f"{value:.1f}".rstrip("0").rstrip(".")
    return text or "0"


def _heat_cell_text(busy: float, weekday: int, column: int, door_count: int) -> str:
    """一格怎么说，逐格说明与说明行的"现在"共用这一条成文路径。"""
    at = f"{spec.WEEKDAY_LABELS[weekday]} {spec.HOUR_START + column} 时"
    if busy <= 0:
        return f"{at} · 没有人"
    share = round(busy / door_count * 100) if door_count > 0 else 0
    return f"{at} · 平均占用 {_format_busy(busy)}/{door_count}（{share}%）"


def _heat_texts(heat: list[list[float]], door_count: int) -> list[list[str]]:
    """每个格子的说明文字，**由服务端成文**，与 `heat` 同形。

    时刻、绝对占用、占容量的比例——一格怎么念是这一格的口径，与档名、榜注解同类；
    留在客户端就成了第二份，改一处另一处不跟着动，而两处都不会报错。

    **占用留一位小数、并给出占容量的比例**：格值多在 0.x–1.x 之间，取整会把 0.4
    念成 0，而颜色正是按那个 0.4 分档的——两个数出自同一个值却对不上。整格为 0
    时直接说"没有人"：那是平均口径下唯一做得到的绝对判断。
    """
    return [
        [_heat_cell_text(busy, weekday, column, door_count) for column, busy in enumerate(row)]
        for weekday, row in enumerate(heat)
    ]


def _heat_now_text(heat: list[list[float]], until: str, offset_minutes: int, door_count: int) -> str:
    """说明行的初始内容：**"现在"那一格**，整句由服务端成文。

    请求时刻落在服务时段内，就给"现在"那格的说明；之外没有"现在"可指，给整句
    引导。哪一刻算"现在"由请求时刻回答（客户端的展示基准与服务端同源），客户端
    不持有这两句话的任何一份。

    打开页面后时间继续走，这句话不会自己更新——它与本响应里每个数字一样，是
    拉取那一刻的读数；刷新即重取。
    """
    moment = to_local(until, offset_minutes)
    if moment is not None:
        column = moment.hour - spec.HOUR_START
        if 0 <= column < spec.HOUR_COUNT:
            weekday = moment.weekday()
            return f"{spec.HEAT_NOW_PREFIX}{_heat_cell_text(heat[weekday][column], weekday, column, door_count)}"
    return spec.HEAT_HINT


def _copy(days: int) -> dict[str, str]:
    """下发 `spec.STATS_COPY` 整表。

    `{days}` 在这里填——窗口天数是请求参数，服务端知道；其余占位符是运行时值，
    留给客户端用 shower-utils 的格式化结果填入。
    """
    return {key: text.replace("{days}", str(days)) for key, text in spec.STATS_COPY.items()}


def _totals(flowed: list[dict[str, Any]]) -> StatsTotals:
    """窗口内的汇总。**只给出页面上真正会读的那一个**。

    人次、平均淋浴时长、平均等待都在「各楼层」那张表里（含本设备那一行），不在这里
    再存一份：同一概念两份来源，改一处另一处不跟着动，页面上只会显示出两个值。

    只统计出过水的会话：没淋浴的那次算进均值会把它拉向 0，而页面上看不出是哪些会话
    拉低的——`flow_median_s` 同理。
    """
    return {"flow_median_s": _median([int(row["flow_s"]) for row in flowed])}


def _today_summary(session: Session, device_id: str, flowed: list[dict[str, Any]], offset_minutes: int) -> TodaySummary:
    """今日截至现在。进行中的会话不计入人次与均值——它们的时长还没走完。

    `flowed` 是窗口内的**全部**出水会话，比今日宽；今日是它的子集，本地筛一遍即可。
    口径是**出水**：没淋浴的那次不算洗过。
    """
    since, until = _day_start(now_rfc3339(), offset_minutes), now_rfc3339()
    flows = [int(row["flow_s"]) for row in flowed if row["ended_at"] != "" and since <= str(row["assigned_at"]) < until]
    bathing = doors_store.door_count_in_state(session, device_id, DoorState.BATHING.value)
    return {
        "as_of": until,
        "served": len(flows),
        "bathing": bathing,
        "flow_avg_s": _average(flows),
    }


def _histogram_value(key: str, row: dict[str, Any], segments: dict[int, list[dict[str, Any]]]) -> int:
    """这一场落进这个分布时该用的值。

    两个分布各取一个量：淋浴时长直接读 `flow_s`；关阀次数**数的是段**——一段就是
    "从开阀到关阀"（`reference.md` 4.4），所以每段结束都关了一次阀，**不去减那个一**。

    **只数有 `closed_at` 的段**：在途的那一段还没关阀（同处写着"`closed_at` 为空 =
    尚未关阀"）。实测当前的线上数据里这种段占 0，但"从淋浴中直接结算"那一刻真的到来时，
    把在途段也算成一次关阀，就会凭空多出一次——而页面上看不出来。
    """
    if key == "flow":
        return int(row["flow_s"])
    return sum(1 for part in segments.get(int(row["id"]), []) if part["closed_at"])


def _histograms(flowed: list[dict[str, Any]], segments: dict[int, list[dict[str, Any]]]) -> list[HistogramView]:
    """两个分布：淋浴时长与关阀次数。分档边界取自 `spec.HISTOGRAMS`。

    **两个分布都只统计出过水的会话**：没淋浴的那次 `flow_s` 为 0，会整批落进最低那一档
    ——于是最低档在说着"有很多很快就洗完的"，而实际是"根本没淋"。
    """
    views: list[HistogramView] = []
    for item in spec.HISTOGRAMS:
        counts = [0] * len(item.buckets)
        for row in flowed:
            counts[_bucket_index(_histogram_value(item.key, row, segments), item.buckets)] += 1
        views.append(
            {
                "key": item.key,
                "title": item.title,
                "note": item.note,
                "rows": [
                    HistogramRow(label=bucket.label, count=counts[index]) for index, bucket in enumerate(item.buckets)
                ],
            }
        )
    return views


def _door_rows(flowed: list[dict[str, Any]]) -> list[DoorUsageRow]:
    """每个喷头的使用次数与平均淋浴时长。

    **只统计出过水的会话**："使用次数"问的是洗了几次，分配了却没出水的那次不算用过。
    """
    by_door: dict[int, list[int]] = defaultdict(list)
    for row in flowed:
        by_door[int(row["door_no"])].append(int(row["flow_s"]))
    return [
        DoorUsageRow(door_no=door_no, sessions_flowed=len(flows), flow_avg_s=_average(flows))
        for door_no, flows in sorted(by_door.items())
    ]


def _full_wait_seconds(rows: list[dict[str, Any]], door_count: int) -> list[int]:
    """一次**等待**的时长（秒）：**从"最后一个空位被占"到"第一个空位空出来"**。

    喷头全满的那一段，就是"想来的人挤不进去"的那一段。空位一出现，这一次等待结束；
    下一刻再占满，是另一次。这是**设备级**的量。

    **它量的不是"分配后多久开阀"**：那段时间里喷头已经归使用者了，没有任何人在等它。
    那个量不存在于统计里（只在状态页与回放上以"等待开阀"的状态出现），所以别把两者
    当同一个数——混起来会让读者以为有人在排队。

    窗口末尾仍然满员的那一段**不算**：它还没结束，算进去就是把"到此刻为止"当成一整次。
    等待次数就是本函数返回的条数。
    """
    events: list[tuple[str, int]] = []
    for row in rows:
        events.append((str(row["assigned_at"]), 1))
        events.append((str(row["ended_at"]), -1))
    # 同一时刻先记释放再记分配：释放与分配落在同一秒时，空位确实出现过（哪怕只有一瞬），
    # 那一次等待到这一刻就该结束，而下一刻开始的是一次新的。
    events.sort(key=lambda item: (item[0], item[1]))

    waits: list[int] = []
    occupied = 0
    full_since: str | None = None
    for at, delta in events:
        occupied += delta
        if occupied >= door_count:
            if full_since is None:
                full_since = at
        elif full_since is not None:
            waits.append(seconds_between(full_since, at))
            full_since = None
    return waits


# ── 榜单 ─────────────────────────────────────────────


def _by_cert(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """按学号分组，跳过没有学号的会话。"""
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        cert = str(row["cert_no"])
        if cert:
            grouped[cert].append(row)
    return grouped


def _streak_days(rows: list[dict[str, Any]], offset_minutes: int) -> int:
    """最长的连续使用天数（按当地日期）。同一天淋浴两次算一天。"""
    days = sorted({local.date() for row in rows if (local := to_local(row["assigned_at"], offset_minutes))})
    if not days:
        return 0
    longest = current = 1
    for previous, following in pairwise(days):
        current = current + 1 if (following - previous).days == 1 else 1
        longest = max(longest, current)
    return longest


def _mean_start_minute(rows: list[dict[str, Any]], offset_minutes: int) -> int:
    """平均开始时刻，当地 0 点起的分钟数。"""
    minutes = [
        local.hour * 60 + local.minute
        for row in rows
        if (local := to_local(row["assigned_at"], offset_minutes)) is not None
    ]
    return round(sum(minutes) / len(minutes)) if minutes else 0


def _leaderboards(rows: list[dict[str, Any]], offset_minutes: int) -> list[LeaderboardView]:
    """全部榜，按 `spec.BOARDS` 的顺序。**排序在服务端做**，`rank` 就是最终名次。

    `rows` 传**全部会话**，所以这里先按 `_flowed` 分成两拨：「会话数」一榜答的是"占了
    喷头几次"，用全部；其余榜用真正洗过澡的那拨——一场水都没出过的人不该出现在
    "洗得多／洗得晚／连续多"里，以 0 排在榜尾看着像"他洗得很少"，实际是"他没洗过"。

    按人排与按场排的两类**在同一个循环里出**，因为界面上它们就是一列页签。行形状合成
    一种（按人的榜填 `door_no=None`、`at=""`），客户端一个循环就能渲染——分成两种形状
    就得让客户端逐榜判断，而那个判断写错了不报错。
    """
    values: dict[str, dict[str, int]] = {}
    for cert, items in _by_cert(rows).items():
        flowed = _flowed(items)
        values[cert] = {
            "sessions": len(items),
            "sessions_flowed": len(flowed),
            "flow": sum(int(row["flow_s"]) for row in flowed),
            "avg": _average([int(row["flow_s"]) for row in flowed]),
            # **分配时长**均值（分配 → 释放），含没出水的那几次——与 "sessions" 同口径：
            # 会话数那一榜的副列要它，"来了几次"与"每次占多久"是一对。
            # 还在占着的那些没有释放时刻、算不出长度，不进均值。
            # 键名与契约字段一致（`assigned_avg_s` 就是 `LeaderboardRow.assigned_avg_s`）：
            # 副列的名字由 `spec` 给，两处对不上的话排序会静默退化成"没有副列"。
            "assigned_avg_s": _average(
                [seconds_between(row["assigned_at"], row["ended_at"]) for row in items if row["ended_at"] is not None]
            ),
            "streak": _streak_days(flowed, offset_minutes),
            "latest": _mean_start_minute(flowed, offset_minutes),
        }

    singles = _flowed(rows)
    everyone = list(values.items())
    washed = [(cert, row) for cert, row in values.items() if row["sessions_flowed"] > 0]
    views: list[LeaderboardView] = []
    for item in spec.BOARDS:
        views.append(
            {
                "key": item.key,
                "title": item.title,
                "note": item.note,
                "detail": item.detail,
                "detail_label": item.detail_label,
                "order": item.orders[0].key,
                "orders": [{"key": order.key, "label": order.label} for order in item.orders],
                # **两个方向都算好一起给**：切换时不发请求，「切榜不重拉」这条才继续成立。
                "rows": {order.key: _board_rows(item, order.key, everyone, washed, singles) for order in item.orders},
            }
        )
    return views


#: 契约字段名 → 按人统计里的键。**只有 `flow_avg_s` 一处不同名**：契约上它叫
#: `flow_avg_s`，按人统计里叫 `avg`（那个名字来自 `BOARDS` 里 `avg` 这个榜的 key）。
#: 主列与副列都按名字取数，对不上的话副列排序会静默退化成"没有副列"。
_FIELD_ALIAS = {"flow_avg_s": "avg"}


def _board_rows(
    item: spec.LeaderboardSpec,
    order: str,
    everyone: list[tuple[str, dict[str, int]]],
    washed: list[tuple[str, dict[str, int]]],
    singles: list[dict[str, Any]],
) -> list[LeaderboardRow]:
    """一个榜在一个方向上的行。

    **方向是榜的一个参数，不是两个榜**：`desc` 从多到少、`asc` 从少到多。每个榜都给两个
    方向（`spec.BOARDS`），界面上的切换按钮位置因此固定。

    **主列相同时按副列排**：副列是这一榜的另一半（次数榜看每次占多久、时长榜看出水
    次数），主列打平时它才是有信息量的那一个。不排的话先后由查询返回的顺序决定，同一批
    数据两次请求可能给出不同的先后——页面上只是名次悄悄变了。

    「平均淋浴时长」**不设最低会话数**：出过水就上榜。有快就有慢，样本薄是他自己的事；
    设了门槛，读者在页面上只会看到"榜上少了几个人"而不知道为什么。
    """

    def value_of(row: dict[str, int], field: str) -> int:
        return row.get(_FIELD_ALIAS.get(field, field), 0)

    if item.kind == "single":
        return [
            LeaderboardRow(
                rank=rank,
                cert_no=str(row["cert_no"]),
                value=int(row["flow_s"]),
                door_no=int(row["door_no"]),
                at=str(row["assigned_at"]),
                # 按场排的榜，一行是"某一场"而不是某个人，所以这三个数没有意义。
                sessions=None,
                sessions_flowed=None,
                flow_avg_s=None,
                assigned_avg_s=None,
            )
            # 时长相同就按喷头号定序：否则先后由查询返回的顺序决定。
            for rank, row in enumerate(
                sorted(
                    singles,
                    key=lambda row: (int(row["flow_s"]), int(row["door_no"])),
                    reverse=order == "desc",
                )[: spec.LEADERBOARD_SIZE],
                start=1,
            )
        ]
    # 「会话数」是唯一含未出水会话的榜；其余榜只列真正洗过澡的人。
    source = everyone if item.key == "sessions" else washed
    ordered_people = sorted(
        source,
        key=lambda pair: (value_of(pair[1], item.key), value_of(pair[1], item.detail)),
        reverse=order == "desc",
    )
    return [
        LeaderboardRow(
            rank=rank,
            cert_no=cert,
            value=row[item.key],
            door_no=None,
            at="",
            sessions=row["sessions"],
            sessions_flowed=row["sessions_flowed"],
            flow_avg_s=row["avg"],
            assigned_avg_s=row["assigned_avg_s"],
        )
        for rank, (cert, row) in enumerate(ordered_people[: spec.LEADERBOARD_SIZE], start=1)
    ]


# ── 楼层对比 ─────────────────────────────────────────


def _peak_hours(heat: list[list[float]]) -> tuple[int, int]:
    """高峰时段的起止小时，由热力矩阵推出。

    先取各小时（跨全部星期）的平均占用的最高者，再向两侧吸收不低于其 90% 的相邻
    小时，合成一个时段——高峰未必恰为单个小时，可能是"21–23 时"。

    **它必须与热力图同源**：页面上那张图与"高峰 21–23 时"这句话若各算各的，读者会
    发现两者对不上，而两处都不会报错。
    """
    per_hour = [sum(heat[weekday][column] for weekday in range(7)) / 7 for column in range(spec.HOUR_COUNT)]
    top = max(range(spec.HOUR_COUNT), key=lambda column: per_hour[column])
    threshold = per_hour[top] * 0.9
    low = high = top
    while low - 1 >= 0 and per_hour[low - 1] >= threshold:
        low -= 1
    while high + 1 < spec.HOUR_COUNT and per_hour[high + 1] >= threshold:
        high += 1
    return spec.HOUR_START + low, spec.HOUR_START + high


def _compare_rows(
    session: Session, current: CompareRow, since: str, until: str, settled: str, offset_minutes: int
) -> list[CompareRow]:
    """各设备的轻量汇总，供楼层对比。

    **顺序就是设备登记的展示顺序**（`devices.list_all`，与管理台、设备下拉同一来源）
    ——切了设备去看对比，行不该因为"是你选的这一台"就跳到最前面。

    **本设备那一行由调用方传进来**（它已在主流程里算过），在这里放回它登记顺序上的
    自然位置。不传的话这里要把它重算一遍——会话表、段表、热力矩阵各再来一次，而
    热力矩阵是整套统计里最贵的一项。

    两个右端点各有用途，不要合成一个：`until` 用于计数（含今天已发生的那些），
    `settled`（今天的 0 点）用于高峰——那个只能从已完整过去的日子推，
    与主视图的热力图同一口径，否则"高峰 20–22 时"会和页面上那张图对不上。
    """
    rows: list[CompareRow] = []
    for record in devices_store.list_all(session):
        device_id = str(record["device_id"])
        if device_id == current["device_id"]:
            rows.append(current)
            continue
        sessions = _sessions(session, device_id, since, until)
        flowed = _flowed(sessions)
        usable = [row for row in sessions if _usable_for_occupancy(row)]
        low, high = _peak_hours(_heat(session, usable, since, settled, offset_minutes, device_id))
        # 只查一次喷头数：行里要用、等待算法也要用，两次 COUNT 是白走一趟库。
        door_count = _door_count(session, device_id)
        rows.append(
            CompareRow(
                device_id=device_id,
                name=str(record["name"]),
                door_count=door_count,
                sessions_flowed=len(flowed),
                flow_avg_s=_average([int(row["flow_s"]) for row in flowed]),
                wait_avg_s=_average(_full_wait_seconds(usable, door_count)),
                peak_start=low,
                peak_end=high,
            )
        )
    return rows


# ── 对外 ─────────────────────────────────────────────


def state_options() -> list[LabeledOption]:
    """状态取值的展示名，供回放的 `state` 字段配套。

    文案取自 `doors.schemas.state_label`——那里是机器可读的唯一来源，与 `DoorState`
    枚举同处一文件。本函数只把它换成本域的契约形状，不另起中文。
    """
    return [LabeledOption(key=state.value, label=state_label(state)) for state in DoorState]


def device_stats(session: Session, device_id: str, days: int, tz_offset_minutes: int) -> StatsView:
    """一台设备一次装载。切设备与切窗口重拉，其余在客户端本地切。"""
    since, until = _window(days)
    rows = _sessions(session, device_id, since, until)
    flowed = _flowed(rows)
    segments = _segments_by_session(session, device_id, since, until)
    usable = [row for row in rows if _usable_for_occupancy(row)]
    # 喷头数只查一次：响应里要给、各楼层本设备那一行要给、等待算法也要给。
    door_count = _door_count(session, device_id)
    # 热力图与高峰时段只看**已完整过去的日子**。今天还没走完，把它算进分母会让今天
    # 所在那一列按"零占用"参与平均——窗口越短偏得越狠（近 7 天时今天占分母的七分之一）。
    # 今天的数据由 TodaySummary 单独回答。
    settled = _day_start(until, tz_offset_minutes)
    heat = _heat(session, usable, since, settled, tz_offset_minutes, device_id)
    low, high = _peak_hours(heat)
    return {
        "device_id": device_id,
        "name": _device_name(session, device_id),
        "door_count": door_count,
        "since": since,
        "until": until,
        "tz_offset_minutes": tz_offset_minutes,
        "hour_start": spec.HOUR_START,
        "hour_count": spec.HOUR_COUNT,
        "states": state_options(),
        "totals": _totals(flowed),
        "today": _today_summary(session, device_id, flowed, tz_offset_minutes),
        "heat": heat,
        "heat_levels": _heat_levels(heat),
        "heat_title": spec.HEAT_TITLE,
        "heat_note": spec.HEAT_NOTE.format(days=days),
        "heat_legend": [LabeledOption(key=str(index), label=label) for index, label in enumerate(spec.HEAT_LEGEND)],
        "heat_texts": _heat_texts(heat, door_count),
        "heat_now_text": _heat_now_text(heat, until, tz_offset_minutes, door_count),
        "copy": _copy(days),
        "histograms": _histograms(flowed, segments),
        "doors": _door_rows(flowed),
        "leaderboards": _leaderboards(rows, tz_offset_minutes),
        "compare": _compare_rows(
            session,
            CompareRow(
                device_id=device_id,
                name=_device_name(session, device_id),
                door_count=door_count,
                sessions_flowed=len(flowed),
                flow_avg_s=_average([int(row["flow_s"]) for row in flowed]),
                wait_avg_s=_average(_full_wait_seconds(usable, door_count)),
                peak_start=low,
                peak_end=high,
            ),
            since,
            until,
            settled,
            tz_offset_minutes,
        ),
    }


def mine_stats(session: Session, cert_no: str, days: int, tz_offset_minutes: int) -> MineView:
    """个人统计。**跨设备**——同一个人可能在不同楼层淋浴，所以不与主视图同端点。

    它跟着统计窗口变：切到"近 7 天"，这里的每一个数都要是 7 天的。
    **只统计出过水的会话**：没淋浴的那几次不该出现在"次数"、"总时长"、"平均淋浴"里，
    也不该参与"比多少人洗得快"——那是同一套口径的另一面。
    """
    since, until = _window(days)
    rows = doors_store.sessions_of_cert(session, cert_no, since, until)
    mine = _flowed(rows)

    flows = [int(row["flow_s"]) for row in mine]
    doors: dict[int, int] = defaultdict(int)
    hours: dict[int, int] = defaultdict(int)
    for row in mine:
        doors[int(row["door_no"])] += 1
        local = to_local(row["assigned_at"], tz_offset_minutes)
        if local is not None:
            hours[local.hour] += 1
    last = mine[-1] if mine else None
    return {
        "cert_no": cert_no,
        "sessions_flowed": len(mine),
        "flow_total_s": sum(flows),
        "flow_avg_s": _average(flows),
        "flow_longest_s": max(flows) if flows else 0,
        "door_no": max(doors, key=lambda door: doors[door]) if doors else None,
        "hour_mode": max(hours, key=lambda hour: hours[hour]) if hours else None,
        "faster_than_pct": _faster_than_percent(session, cert_no, mine, since, until),
        "last_at": str(last["assigned_at"]) if last is not None else "",
        "last_flow_s": int(last["flow_s"]) if last is not None else 0,
    }


def _faster_than_percent(session: Session, cert_no: str, mine: list[dict[str, Any]], since: str, until: str) -> int:
    """比百分之多少的人淋浴时长短。**按人比**——这句话的主语是人，不是会话数。

    别人的水平用一个聚合查询取，**不把他们的行拉回来**：算一个百分位只要各人的均值，
    而拉行会把窗口内的全部会话读进内存（实测两千余行）只为求一个数。

    样本少于两人时给 0：只有一个人时"比 100% 的人快"没有信息量，显示在页面上还会
    被当成结论。
    """
    if not mine:
        return 0
    averages = doors_store.average_flow_by_cert(session, since, until, exclude_cert=cert_no)
    if not averages:
        return 0
    mine_average = _average([int(row["flow_s"]) for row in mine])
    shorter = sum(1 for average in averages.values() if average > mine_average)
    return round(shorter * 100 / len(averages))


def device_replay(session: Session, device_id: str, day: str, tz_offset_minutes: int) -> ReplayView:
    """某一天的状态变化序列，客户端按滑块位置重放。

    一次拉一天（实测 162 条、10.5 KB）而不是按时刻查：后者要扫到那一刻为止的全部
    历史事件（实测 36–55 ms），而滑块每移动一格就触发一次，那样每次拖动都会卡。

    `day` 是**当地日期**。它对应的 UTC 区间随调用方的时区偏移移动——同一句"10 月 3 日"
    在东八区与在 UTC 指的不是同一段二十四小时。
    """
    local_midnight = datetime.fromisoformat(day).replace(tzinfo=timezone(timedelta(minutes=tz_offset_minutes)))
    start = format_rfc3339(local_midnight.astimezone(UTC))
    end = format_rfc3339((local_midnight + timedelta(days=1)).astimezone(UTC))
    rows = doors_store.events_in_window(session, device_id, start, end)
    return {
        "device_id": device_id,
        "date": day,
        "tz_offset_minutes": tz_offset_minutes,
        "hour_start": spec.HOUR_START,
        "hour_count": spec.HOUR_COUNT,
        "events": [
            ReplayDoor(
                door_no=int(row["door_no"]),
                state=DoorState(str(row["to_state"])),
                at=str(row["occurred_at"]),
                cert_no=str(row["cert_no"]),
                bath_time_s=int(row["bath_time_s"]),
            )
            for row in rows
        ],
        "last_closed": _last_closed_by_door(session, device_id, start),
    }


def _last_closed_by_door(session: Session, device_id: str, before: str) -> list[ReplayDoorLast]:
    """每扇门在 `before` 之前的最后一次关阀。

    **为什么不在客户端从当天的事件里推**：那一次发生在更早的日子，而回放一次只拉一天
    ——"上次使用"在当天没有事件可推的门上会恒为空，而页面上看不出来。

    段表没有学号，所以联 `usage_session` 取那一次是谁。`row_number()` 按关阀时刻倒序，
    取每门第一条；`closed_at` 为空的行是在途段（尚未关阀），不能算"上一次关阀"。
    """
    rows = doors_store.last_closed_by_door(session, device_id, before)
    return [
        ReplayDoorLast(door_no=int(row["door_no"]), closed_at=str(row["closed_at"]), cert_no=str(row["cert_no"]))
        for row in rows
    ]
