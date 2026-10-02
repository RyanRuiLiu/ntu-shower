"""设备时钟偏差维护：服务端周期 ping，设备 pong 回带时间戳，偏差做 EMA 平滑。

设备无 NTP，快照中的 open_time/in_time/close_time 全是它的墙上时钟，而那些
时间戳必须采用：快照断网期间积压、重连后按序补发，若改用服务端接收时刻，
整段离线时间的活动会被记成"重连那一秒发生"。方案是保留设备时间戳、减掉偏差；
偏差变化很慢，积压快照减同一个偏差仍落回正确时刻。

偏差持久化在设备档案（clock_skew_s），设备上线时载入：只留内存的话，服务重启
后回到 0，等于不校正，设备时间戳会与服务器时钟写的字段混排错序。

时间字符串的解析与格式化一律经 core.clock。
"""

from datetime import datetime, timezone

from app.core import protocol
from app.core.clock import format_rfc3339, now_rfc3339, parse_rfc3339

EMA_ALPHA = 0.3

_ema: dict[str, float] = {}


def load_skew(device_id: str, skew_s: float) -> None:
    """把库里记着的偏差载入内存，消除上线到首次 pong 之间的零偏差窗口。"""
    _ema[device_id] = skew_s


def update_skew(device_id: str, device_time_rfc3339: str, jump_threshold_s: float) -> float:
    """用设备 pong 中的时间戳更新偏差，返回更新后的值（秒）。

    EMA 平滑滤单次往返的抖动；单次观测与当前值之差超过 jump_threshold_s 时直接
    采用新值：设备重启、手动改时间、NTP 生效都是几十分钟级的跳变，继续平滑会让
    之后几次校正按错误的偏差计算。
    """
    device_moment = parse_rfc3339(device_time_rfc3339)
    if device_moment is None:
        return get_skew(device_id)
    observed = datetime.now(timezone.utc).timestamp() - device_moment.timestamp()
    prev = _ema.get(device_id)
    if prev is None or abs(observed - prev) > jump_threshold_s:
        _ema[device_id] = observed
    else:
        _ema[device_id] = prev + EMA_ALPHA * (observed - prev)
    return _ema[device_id]


def get_skew(device_id: str) -> float:
    """当前偏差；没有记录时为 0（即不校正）。"""
    return _ema.get(device_id, 0.0)


def correct_rfc3339(time_rfc3339: str | None, device_id: str) -> str | None:
    """将设备时钟的 RFC 3339 时间按偏差校准为服务器时钟；空值原样返回。"""
    moment = parse_rfc3339(time_rfc3339)
    if moment is None:
        return time_rfc3339
    corrected = datetime.fromtimestamp(moment.timestamp() + get_skew(device_id), timezone.utc)
    return format_rfc3339(corrected)


def ping_payload() -> str:
    """网关周期 ping 的报文（设备 pong 后由 update_skew 消化）。

    ping/pong 对称：payload.sender_time 均为发送方时间戳。
    """
    return protocol.envelope("link.ping", {"sender_time": now_rfc3339()})
