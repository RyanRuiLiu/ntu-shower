"""设备时钟偏差维护：server 周期 ping → 设备 pong → 偏差 EMA。

偏差 = 服务器收到 pong 时刻 - 设备侧时间戳（含单向传输误差，EMA 平滑）。
快照中的 open_time/in_time/close_time 为设备时钟，落库前统一校准。
时间字符串的解析与格式化一律经 core.clock（时间的唯一来源）。

## 为什么必须校正而不是改用接收时刻

设备没有 NTP（实测有一台墙钟快 59 分 47 秒，且系统设置里自动对时是关的），它写进
浴室 App 数据库的时间戳全是自己的墙上时钟。那些戳**必须**被采用：它们是唯一能标记
"何时发生"的东西。快照在联网时逐条发送、收到 ack 才删，所以**断网期间积压的快照会在
重连后按序重发**——若改用服务端的接收时刻，整段离线时间的活动会被记成"重连那一秒
发生"，高峰时段这类统计会彻底失真。

所以走校正这条路：保留设备时间戳，减掉偏差。积压不影响它——偏差是设备钟距真值的差，
变化很慢，六小时后重发的那批快照减掉同一个偏差，仍然落回正确的时刻。

## 偏差要持久化

`load_skew` 从库里恢复上次的值。只留在内存里的话，服务每次重启都变为 0——那等于
不校正，设备时间戳被原样写库，与用服务端时钟写的字段混进同一张表，先后顺序就乱了
（实测出现过"结束早于开始 55 分钟"）。
"""

from datetime import datetime, timezone

from app.core import protocol
from app.core.clock import format_rfc3339, now_rfc3339, parse_rfc3339

EMA_ALPHA = 0.3

_ema: dict[str, float] = {}


def load_skew(device_id: str, skew_s: float) -> None:
    """把库里记着的偏差载入内存（设备上线时调）。

    载入而不是"等第一次 pong 回来再算"：设备每次上线后、第一次 pong 到达之前收到的
    快照都会按当前偏差校正，而那时偏差还是 0。载入把这段空白窗口缩到零。
    """
    _ema[device_id] = skew_s


def update_skew(device_id: str, device_time_rfc3339: str, jump_threshold_s: float) -> float:
    """用设备 pong 中的设备时间戳更新偏差；返回更新后的偏差（秒）。

    EMA 平滑滤的是单次往返的抖动（几十毫秒级）。但设备的钟**会跳变**——重启、手动改
    时间、NTP 生效，一次就是几十分钟。那时继续平滑是错的：新观测值才是事实，而按旧
    偏差平滑出来的中间值会让接下来几次校正都算错。差距超过 `jump_threshold_s` 就直接
    取新值，不平滑。
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
    """当前偏差；没有记录时给 0（即不校正）。"""
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
