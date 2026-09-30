"""时钟偏差测试：EMA 更新、时间校准。"""

from app.core import device_clock


def test_update_skew_returns_observation() -> None:
    device_time = device_clock.now_rfc3339()
    observed = device_clock.update_skew("dev-c", device_time)
    assert abs(observed) < 5  # 同机测试，偏差应接近 0


def test_correct_rfc3339_shifts_by_skew() -> None:
    device_clock._ema["dev-c2"] = 100.0  # 设备慢 100 秒
    raw = "2026-09-30T12:00:00.000Z"
    corrected = device_clock.correct_rfc3339(raw, "dev-c2")
    assert "T12:01:40" in corrected
    device_clock._ema.pop("dev-c2")


def test_correct_rfc3339_passthrough_empty() -> None:
    assert device_clock.correct_rfc3339(None, "dev-x") is None
    assert device_clock.correct_rfc3339("", "dev-x") == ""
