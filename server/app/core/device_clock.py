"""设备时钟偏差维护：server 周期 ping → 设备 pong → 偏差 EMA。

偏差 = 服务器收到 pong 时刻 - 设备侧时间戳（含单向传输误差，EMA 平滑）。
快照中的 open_time/in_time/close_time 为设备时钟，落库前统一校准。
"""

from datetime import datetime, timezone

from app.core import protocol
from app.core.clock import now_rfc3339

_ema: dict[str, float] = {}
EMA_ALPHA = 0.3


def update_skew(device_id: str, device_time_rfc3339: str) -> float:
    """用设备 pong 中的设备时间戳更新偏差；返回本次观测偏差（秒）。"""
    device_epoch = _parse(device_time_rfc3339)
    if device_epoch is None:
        return _ema.get(device_id, 0.0)
    now_epoch = datetime.now(timezone.utc).timestamp()
    observed = now_epoch - device_epoch
    prev = _ema.get(device_id)
    _ema[device_id] = observed if prev is None else prev + EMA_ALPHA * (observed - prev)
    return _ema[device_id]


def get_skew(device_id: str) -> float:
    return _ema.get(device_id, 0.0)


def correct_rfc3339(time_rfc3339: str | None, device_id: str) -> str | None:
    """将设备时钟的 RFC3339 时间按偏差校准为服务器时钟；空值原样返回。"""
    if not time_rfc3339:
        return time_rfc3339
    epoch = _parse(time_rfc3339)
    if epoch is None:
        return time_rfc3339
    corrected = datetime.fromtimestamp(epoch + get_skew(device_id), timezone.utc)
    return corrected.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse(time_rfc3339: str) -> float | None:
    try:
        return datetime.fromisoformat(time_rfc3339.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def ping_payload(device_id: str) -> str:
    """网关周期 ping 的报文（设备 pong 后由 update_skew 消化）。

    ping/pong 对称：payload.sender_time 均为发送方时间戳。
    """
    return protocol.envelope("link.ping", {"sender_time": now_rfc3339()})
