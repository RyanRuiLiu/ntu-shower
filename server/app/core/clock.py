"""统一时间：服务端对外输出的时间一律为 UTC RFC 3339 毫秒精度。

本模块是时间的唯一来源——其余模块禁止自行拼接或解析时间字符串，
格式化、解析、偏移计算一律经由此处（AGENTS.md）。

RFC 3339 profile：UTC 用 "Z" 后缀（RFC 3339 §5.6），毫秒精度足以
表达本系统的时序分辨率（设备心跳、命令截止、快照时间）。
"""

from datetime import datetime, timedelta, timezone

_MILLIS = "milliseconds"


def format_rfc3339(moment: datetime) -> str:
    """把 datetime 规范化为 RFC 3339 UTC 毫秒字符串（Z 后缀）。"""
    return moment.astimezone(timezone.utc).isoformat(timespec=_MILLIS).replace("+00:00", "Z")


def now_rfc3339() -> str:
    """当前时刻的 RFC 3339 字符串。"""
    return format_rfc3339(datetime.now(timezone.utc))


def now_epoch_s() -> int:
    """当前时刻的 Unix 秒。"""
    return int(datetime.now(timezone.utc).timestamp())


def rfc3339_after(seconds: float) -> str:
    """当前时刻 + seconds 的 RFC 3339 字符串（命令截止时刻等）。"""
    return format_rfc3339(datetime.now(timezone.utc) + timedelta(seconds=seconds))


def rfc3339_ago(seconds: float) -> str:
    """当前时刻 - seconds 的 RFC 3339 字符串（由运行时长反推开机时刻等）。"""
    return format_rfc3339(datetime.now(timezone.utc) - timedelta(seconds=seconds))


def parse_rfc3339(text: object) -> datetime | None:
    """解析 RFC 3339 字符串；非字符串、空值或格式非法一律返回 None。

    入参声明为 object 而非 str：设备上报的时间字段是未验证的 JSON 值，
    类型错误必须在这一层被吸收，而不是抛到调用栈上。

    Python 3.10 的 fromisoformat 尚不识别 "Z" 后缀，故先替换为显式偏移。
    """
    if not isinstance(text, str) or not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def seconds_between(start: object, end: object) -> int:
    """两个 RFC 3339 时刻之间的整秒数；任一端无法解析时返回 0。

    结果恒为非负。设备时钟与服务端之间即便做过偏差校正，仍可能算出负值，而
    "时长为负"没有意义，调用方也不该为它单独写分支——在这一层收口。
    """
    first = parse_rfc3339(start)
    second = parse_rfc3339(end)
    if first is None or second is None:
        return 0
    return max(0, int((second - first).total_seconds()))


def shift_rfc3339(text: object, seconds: float) -> str | None:
    """把一个 RFC 3339 时刻平移 seconds 秒；无法解析时返回 None。

    与 `rfc3339_after` / `rfc3339_ago` 的区别是基准：那两个以此刻为基准，这个以给定
    时刻为基准。设备钟的偏差校正（core/device_clock）与"时刻减去一段时长"这类换算
    都走它，不要在各处自行拼时间字符串。
    """
    moment = parse_rfc3339(text)
    if moment is None:
        return None
    return format_rfc3339(moment + timedelta(seconds=seconds))
