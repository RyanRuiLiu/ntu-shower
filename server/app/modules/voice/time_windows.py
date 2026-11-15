"""语音规则的每周生效时段。

时间是规则的又一条约束：只有事件发生在任一时段内才允许播报。时段按**每周重复**
的当地时间解释，不处理节假日和具体日期；站点不实行夏令时，所以当地时间由固定
偏移换算（与 `server_zone_offset_minutes` 同一口径）。

跨天时段（`start > end`）的 `days` 指**开始那一天**：周五 22:00–02:00 覆盖周五
22:00 到周六 02:00。开始时刻包含、结束时刻不包含。一个窗口展开成一周的分钟集合，
匹配、覆盖长度和重叠判断都用同一份集合，避免三处各写一套边界规则。
"""

import json
import re
from dataclasses import dataclass
from datetime import datetime

MINUTES_PER_DAY = 24 * 60
MINUTES_PER_WEEK = 7 * MINUTES_PER_DAY
MAX_TIME_WINDOWS_PER_RULE = 8

TIME_PATTERN = r"^([01]\d|2[0-3]):([0-5]\d)$"
_TIME_RE = re.compile(TIME_PATTERN)
_DAY_LABELS = ("一", "二", "三", "四", "五", "六", "日")

ERR_TIME = "时间必须是 HH:MM"
ERR_DAYS = "星期取值必须是 1–7"
ERR_DAYS_EMPTY = "至少选择一天"
ERR_WINDOW = "时段必须是对象"
ERR_SPAN = "开始与结束时刻不能相同"
ERR_WINDOWS = "time_windows 必须是数组"


@dataclass(frozen=True)
class Window:
    days: tuple[int, ...]
    start_minute: int
    end_minute: int

    @property
    def start(self) -> str:
        return format_time(self.start_minute)

    @property
    def end(self) -> str:
        return format_time(self.end_minute)


def parse_time(value: object) -> int:
    """`HH:MM` → 当天分钟数；格式不合法时抛 ValueError。"""
    if not isinstance(value, str):
        raise TypeError(ERR_TIME)
    match = _TIME_RE.fullmatch(value)
    if match is None:
        raise ValueError(ERR_TIME)
    return int(match.group(1)) * 60 + int(match.group(2))


def format_time(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def normalize_days(values: object) -> tuple[int, ...]:
    """星期取值 1（周一）–7（周日），去重排序；空集合没有意义。"""
    if not isinstance(values, list) or not values:
        raise ValueError(ERR_DAYS_EMPTY)
    days: list[int] = []
    for value in values:
        if not isinstance(value, int) or not 1 <= value <= 7:
            raise ValueError(ERR_DAYS)
        if value not in days:
            days.append(value)
    return tuple(sorted(days))


def window_from_mapping(value: object) -> Window:
    if not isinstance(value, dict):
        raise TypeError(ERR_WINDOW)
    days = normalize_days(value.get("days"))
    start = parse_time(value.get("start"))
    end = parse_time(value.get("end"))
    if start == end:
        raise ValueError(ERR_SPAN)
    return Window(days=days, start_minute=start, end_minute=end)


def parse_json(value: object) -> list[Window]:
    """从数据库 JSON 列解析；空字符串/`[]` 表示不限时段。"""
    if value is None or value == "":
        return []
    raw = json.loads(value) if isinstance(value, str) else value
    if not isinstance(raw, list):
        raise TypeError(ERR_WINDOWS)
    return [window_from_mapping(item) for item in raw]


def dump(windows: list[Window]) -> str:
    return json.dumps(
        [{"days": list(window.days), "start": window.start, "end": window.end} for window in windows],
        ensure_ascii=False,
    )


def weekly_minutes(window: Window) -> frozenset[int]:
    """一个窗口覆盖的分钟集合；跨天按开始日展开到下一日。"""
    minutes: set[int] = set()
    for day in window.days:
        base = (day - 1) * MINUTES_PER_DAY
        if window.start_minute < window.end_minute:
            minutes.update(range(base + window.start_minute, base + window.end_minute))
        else:
            minutes.update(range(base + window.start_minute, base + MINUTES_PER_DAY))
            next_base = (day % 7) * MINUTES_PER_DAY
            minutes.update(range(next_base, next_base + window.end_minute))
    return frozenset(minutes)


def match(windows: list[Window], local: datetime) -> bool:
    """无窗口表示不限；否则任一时段包含该本地时刻即命中。"""
    if not windows:
        return True
    minute = (local.isoweekday() - 1) * MINUTES_PER_DAY + local.hour * 60 + local.minute
    return any(minute in weekly_minutes(window) for window in windows)


def coverage(windows: list[Window]) -> int:
    """一周实际覆盖的分钟数；越短表示约束越紧。"""
    minutes: set[int] = set()
    for window in windows:
        minutes.update(weekly_minutes(window))
    return len(minutes)


def overlap(left: list[Window], right: list[Window]) -> bool:
    left_minutes: set[int] = set()
    for window in left:
        left_minutes.update(weekly_minutes(window))
    return any(left_minutes & weekly_minutes(window) for window in right)


def day_label(days: tuple[int, ...]) -> str:
    sorted_days = sorted(days)
    if sorted_days == list(range(1, 8)):
        return "每天"
    if sorted_days == [1, 2, 3, 4, 5]:
        return "周一至周五"
    if len(sorted_days) > 1 and sorted_days == list(range(sorted_days[0], sorted_days[-1] + 1)):
        return f"周{_DAY_LABELS[sorted_days[0] - 1]}至周{_DAY_LABELS[sorted_days[-1] - 1]}"
    return "、".join(f"周{_DAY_LABELS[day - 1]}" for day in sorted_days)


def summary(windows: list[Window]) -> str:
    if not windows:
        return "不限"
    parts = []
    for window in windows:
        span = (
            f"{window.start}–次日 {window.end}"
            if window.start_minute > window.end_minute
            else f"{window.start}–{window.end}"
        )
        parts.append(f"{day_label(window.days)} {span}")
    return "、".join(parts)
