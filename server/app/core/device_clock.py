"""设备时钟偏差维护：server 周期 ping → 设备 pong → 偏差 EMA。

偏差 = 服务器收到 pong 时刻 - 设备侧时间戳（含单向传输误差，EMA 平滑）。
快照中的 open_time/in_time/close_time 为设备时钟，落库前统一校准。
时间字符串的解析与格式化一律经 core.clock（时间的唯一来源）。
"""

from datetime import datetime, timezone

from app.core import protocol
from app.core.clock import format_rfc3339, now_rfc3339, parse_rfc3339

EMA_ALPHA = 0.3

_ema: dict[str, float] = {}


def update_skew(device_id: str, device_time_rfc3339: str) -> float:
    """用设备 pong 中的设备时间戳更新偏差；返回本次观测偏差（秒）。"""
    device_moment = parse_rfc3339(device_time_rfc3339)
    if device_moment is None:
        return get_skew(device_id)
    observed = datetime.now(timezone.utc).timestamp() - device_moment.timestamp()
    prev = _ema.get(device_id)
    _ema[device_id] = observed if prev is None else prev + EMA_ALPHA * (observed - prev)
    return _ema[device_id]


def get_skew(device_id: str) -> float:
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
