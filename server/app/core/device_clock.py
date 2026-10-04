"""设备钟偏差维护：把设备报的墙上时刻平移到服务端时间轴。

## 它解决什么

帧里的 `open_time` / `in_time` 是**这次会话从什么时候开始**的唯一线索。而设备那口
钟的绝对值不可信：无 NTP，自然漂移 8.5 秒/天，还会被运维回拨一小时（代价是把自动
释放推后）。

但**偏差本身是稳定的**。一次会话压在 20 分钟以内（设备满 `maxbatchtime` 就强制释放），
而 20 分钟里的漂移只有 0.03 秒；跳变只有 23:5x 那一处，而设备 23:53 会自动释放所有
喷头——所以任何一次会话都不可能跨过它。**窗口内偏差是常量**，减掉它，相对关系就还原了。

这也意味着"设备钟准不准"完全不影响结果：推算用的是"设备钟读数 + 偏差"，两个量都来自
同一口钟，偏移量在加减中抵消。

## 偏差从哪来

两条通路，取的都是"设备墙钟此刻的读数"与"服务端此刻"之差：

- **帧载荷里的 `device_clock`**（与 `captured_at` 取自同一瞬间）——主力。两个读数只隔一个
  调用栈，精度是毫秒级；
- **`link.pong` 的 `device_time`**——两个读数隔一个网络往返，精度是秒级。它负责在设备空闲
  （不上报业务帧）时把已经稳住的偏差维持住。

**"偏差从哪来"只有这一处**：架构文档只讲它在两条轴上的位置，不复述这段。

## 为什么持久化

从设备上线到首次 pong 之间有一小段窗口。只留内存的话，服务重启后偏差回到 0，等于这段
时间不平移——而"我们刚接手、那边还被占着"恰恰是这段窗口里最常发生的事。

时间字符串的解析与格式化一律经 core.clock。
"""

from app.core.clock import parse_rfc3339, shift_rfc3339

#: EMA 平滑系数。偏差变化很慢，取小一点滤掉单次往返的抖动。
EMA_ALPHA = 0.3

#: 设备 → 偏差（秒）。**符号是「服务端时刻 − 设备时刻」**：设备钟慢时为正，于是平移
#: 只需一次加法（见 [to_server]）。写成反号会让每个调用点都多一个减号，那是出错的地方。
_skew: dict[str, float] = {}


def load(device_id: str, skew_s: float) -> None:
    """把持久化的偏差载入内存，消除上线到首次 pong 之间的零偏差窗口。"""
    _skew[device_id] = skew_s


def update(device_id: str, device_time: object, received_at: object, jump_threshold_s: float) -> float:
    """用设备报的墙上时刻更新偏差，返回更新后的值。

    `received_at` 由调用方在收到那一帧的入口处取好传进来，**不在这里另取一次**——那会
    把它推后一个函数调用的时间，而这里的精度单位本来就是秒。

    单次观测与当前值之差超过 `jump_threshold_s` 时**直接采信新值**：设备重启、被手动
    改钟都是几十分钟级的跳变，继续平滑会让之后几次映射都按错误的偏差来算。

    解析不了（缺字段、格式坏）就保持原值——一次坏观测不该污染已经稳住的偏差。
    """
    observed_moment = parse_rfc3339(device_time)
    arrived = parse_rfc3339(received_at)
    if observed_moment is None or arrived is None:
        return get(device_id)

    # 服务端时刻减设备时刻（符号约定见 _skew）
    observed = arrived.timestamp() - observed_moment.timestamp()
    previous = _skew.get(device_id)
    if previous is None or abs(observed - previous) > jump_threshold_s:
        _skew[device_id] = observed
    else:
        _skew[device_id] = previous + EMA_ALPHA * (observed - previous)
    return _skew[device_id]


def get(device_id: str) -> float:
    """当前偏差；没有记录时为 0。"""
    return _skew.get(device_id, 0.0)


def has(device_id: str) -> bool:
    """这台设备有没有偏差记录。

    与 [get] 的差别只在"有没有"这一点：`get` 返回 0.0 时，分不清"从没量到过"与
    "偏差恰好为零"。要据偏差推算设备钟的调用方必须先问这一句——拿 0 当偏差用，
    等价于假定设备钟与我们对齐，而那正是这台没有 NTP 的设备靠不住的地方。
    """
    return device_id in _skew


def to_server(device_time: object, device_id: str) -> str | None:
    """把设备钟上的一个时刻平移到服务端时间轴。

    这台设备还没有偏差记录时返回 None——调用方据此退回观测时刻。**两种来源的强度不同**：
    平移过来的是事实起点，退回的是观测下界，数据里要分得清（见 doors 的 assigned_source）。
    """
    if device_id not in _skew:
        return None
    return shift_rfc3339(device_time, _skew[device_id])


def clear() -> None:
    """清空内存中的偏差（测试隔离用）。"""
    _skew.clear()
