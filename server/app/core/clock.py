"""统一时间：服务端对外与库内的一切时刻都是 UTC RFC 3339 毫秒字符串（`Z` 后缀）。

本模块是时间的唯一来源——格式化、解析、平移、分桶一律经由此处，其余模块禁止自行
拼接或解析时间字符串（AGENTS.md）。设备侧的对侧实现是
`android/app/src/main/java/com/ryanliu/ntushower/core/clock/Clock.kt`，两端输出同型。

RFC 3339 profile：UTC 用 `Z` 后缀（RFC 3339 §5.6），毫秒精度足以表达本系统的时序
分辨率（设备心跳、命令截止、帧时间）。

**对外输出恒为 UTC**；[to_local] 只服务"按当地时间分桶"这一种场合——分桶要的是
一个本地的小时数，不是又一个字符串。
"""

from datetime import UTC, datetime, timedelta, timezone

_MILLIS = "milliseconds"


def format_rfc3339(moment: datetime) -> str:
    """把 datetime 规范化为 RFC 3339 UTC 毫秒字符串（`Z` 后缀）。"""
    return moment.astimezone(UTC).isoformat(timespec=_MILLIS).replace("+00:00", "Z")


def now_rfc3339() -> str:
    """当前时刻的 RFC 3339 字符串。"""
    return format_rfc3339(datetime.now(UTC))


def rfc3339_after(seconds: float) -> str:
    """当前时刻 + seconds 的 RFC 3339 字符串（命令截止时刻等）。"""
    return format_rfc3339(datetime.now(UTC) + timedelta(seconds=seconds))


def rfc3339_ago(seconds: float) -> str:
    """当前时刻 - seconds 的 RFC 3339 字符串（由运行时长反推开机时刻等）。"""
    return format_rfc3339(datetime.now(UTC) - timedelta(seconds=seconds))


def parse_rfc3339(text: object) -> datetime | None:
    """解析 RFC 3339 字符串；非字符串、空值或格式非法一律返回 None。

    入参声明为 object 而非 str：设备上报的时间字段是未验证的 JSON 值，类型错误必须
    在这一层被吸收，而不是抛到调用栈上。`fromisoformat` 自 3.11 起原生解析 `Z` 后缀
    （本仓库 requires-python >= 3.13），大写 `Z` 合法、小写按标准外形态拒绝——
    RFC 3339 的注记虽允许 `T`/`Z` 小写，本系统两端都只产大写，严格认一种。
    """
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def seconds_between(start: object, end: object) -> int:
    """两个 RFC 3339 时刻之间的整秒数；任一端无法解析时返回 0。

    结果恒为非负：负值只出现在"设备钟被拨动"的数据里，而"时长为负"没有意义，
    调用方也不该为它单独写分支——在这一层收口。
    """
    first = parse_rfc3339(start)
    second = parse_rfc3339(end)
    if first is None or second is None:
        return 0
    return max(0, int((second - first).total_seconds()))


def shift_rfc3339(text: object, seconds: float) -> str | None:
    """把一个 RFC 3339 时刻平移 seconds 秒；无法解析时返回 None。

    与 `rfc3339_after` / `rfc3339_ago` 的区别是基准：那两个以此刻为基准，这个以给定
    时刻为基准。设备钟的偏差校正（core.device_clock）与"时刻减去一段时长"这类换算
    都走它，不要在各处自行拼时间字符串。
    """
    moment = parse_rfc3339(text)
    if moment is None:
        return None
    return format_rfc3339(moment + timedelta(seconds=seconds))


def to_local(value: object, offset_minutes: int) -> datetime | None:
    """把一个 UTC 时刻换算成指定偏移的当地时间；无法解析时返回 None。

    与 [shift_rfc3339] 的区别是**它不回到字符串**：分桶要的是本地的小时与星期，
    再拼一次字符串、再解析一遍纯属绕路。返回的 `datetime` 带着那个固定偏移，所以
    `.hour`、`.weekday()`、`.date()` 拿到的直接就是当地时间。

    偏移用**固定分钟数**而不是 IANA 时区名：服务对象所在的时区不实行夏令时，用固定
    偏移就不必引入 tz 数据库，也不会在切换日算错一小时。偏移的方向是「本地 − UTC」
    （UTC+8 即 480），与 `server_zone_offset_minutes` 一致；JavaScript 的
    `Date.getTimezoneOffset()` 给的是**反的**，调用方传进来之前要取负。
    """
    moment = parse_rfc3339(value)
    if moment is None:
        return None
    return moment.astimezone(timezone(timedelta(minutes=offset_minutes)))
