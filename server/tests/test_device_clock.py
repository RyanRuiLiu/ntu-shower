"""时钟偏差测试：EMA 更新、跳变检测、偏差载入、时间校准。"""

from app.core import device_clock

#: 跳变阈值（秒）。测试里显式传，不读配置——这条测试只关心算法本身。
THRESHOLD_S = 300.0


def test_update_skew_returns_observation() -> None:
    device_time = device_clock.now_rfc3339()
    observed = device_clock.update_skew("dev-c", device_time, THRESHOLD_S)
    assert abs(observed) < 5  # 同机测试，偏差应接近 0


def test_update_skew_jumps_instead_of_smoothing_on_large_change() -> None:
    """设备的钟被改过时直接取新值，不平滑。

    平滑是为了滤掉单次往返的抖动（几十毫秒）。而设备重启、手动改时间这类跳变是几十分钟
    级的——继续按旧值平滑的话，接下来几次校正都会算错，而新观测值才是事实。
    """
    device_id = "dev-jump"
    device_clock._ema[device_id] = 10.0  # 先立一个"设备慢 10 秒"的旧值

    # 设备报的时间戳落在 2020 年：观测偏差是五年多，远超阈值
    observed = device_clock.update_skew(device_id, "2020-01-01T00:00:00.000Z", THRESHOLD_S)

    assert observed > THRESHOLD_S, "巨大跳变必须整体取新值，而不是按 EMA 平滑"
    device_clock._ema.pop(device_id, None)


def test_update_skew_smooths_small_jitter() -> None:
    """阈值以内的抖动仍走 EMA——那是平滑存在的理由。"""
    device_id = "dev-jitter"
    device_clock._ema[device_id] = 100.0

    # 本次观测接近 0（同机测试），与旧值 100 相差 100 秒，仍在阈值内
    device_clock.update_skew(device_id, device_clock.now_rfc3339(), THRESHOLD_S)

    assert 0.0 < device_clock._ema[device_id] < 100.0, "应朝新观测收敛，而不是整体替换"
    device_clock._ema.pop(device_id, None)


def test_load_skew_restores_from_storage() -> None:
    """从库恢复的偏差立刻可用于校正，不必等第一次 pong。"""
    device_id = "dev-restore"
    device_clock._ema.pop(device_id, None)

    device_clock.load_skew(device_id, 3590.0)
    corrected = device_clock.correct_rfc3339("2026-09-30T12:00:00.000Z", device_id)

    assert "T12:59:50" in corrected, "载入的偏差必须立即生效"
    device_clock._ema.pop(device_id, None)


def test_correct_rfc3339_shifts_by_skew() -> None:
    device_clock._ema["dev-c2"] = 100.0  # 设备慢 100 秒
    raw = "2026-09-30T12:00:00.000Z"
    corrected = device_clock.correct_rfc3339(raw, "dev-c2")
    assert "T12:01:40" in corrected
    device_clock._ema.pop("dev-c2")


def test_correct_rfc3339_passthrough_empty() -> None:
    assert device_clock.correct_rfc3339(None, "dev-x") is None
    assert device_clock.correct_rfc3339("", "dev-x") == ""
