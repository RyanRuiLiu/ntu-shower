"""语音规则的匹配优先级：纯计算，无 IO。

独立成模块：persistence（matching_rules 排序）与 service（重名检查）都要用，
放任何一边都会造成循环导入。
"""

from . import time_windows as time_rules


def rule_priority(
    devices: list[str], certs: list[str], time_windows: list[time_rules.Window]
) -> tuple[int, int, int, int, int, int]:
    """规则匹配优先级：设备 → 学号 → 集合大小 → 时段。

    前三项沿用原模型：设备具体先于学号具体，小集合先于大集合。时间只在前四项
    完全相同时参与：有时段比不限时段紧，一周覆盖分钟数更少的时段更紧。负号让降序
    排序把更紧的规则排在前面；规则 id 不参与优先级。
    """
    device_specific = 0 if "*" in devices else 1
    cert_specific = 0 if "*" in certs else 1
    device_width = len(devices) if device_specific else 0
    cert_width = len(certs) if cert_specific else 0
    time_specific = 1 if time_windows else 0
    time_coverage = time_rules.coverage(time_windows)
    return (device_specific, cert_specific, -device_width, -cert_width, time_specific, -time_coverage)
