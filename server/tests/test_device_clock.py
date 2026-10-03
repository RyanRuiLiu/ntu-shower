"""设备钟偏差：平滑、跳变采信、平移，以及没有偏差时的退路。

偏差是"把设备墙钟平移到服务端时间轴"的唯一依据——设备报的 `openTime` / `inTime`
经它换算后就是真实的分配与开阀时刻（见 core/device_clock 的模块注释）。

**符号是「服务端时刻 − 设备时刻」**，所以设备钟慢时它为正、快时为负；平移是一次加法。
"""

import pytest

from app.core import device_clock

#: 跳变阈值。与配置默认值同量级；测试直接传参，不走配置表。
THRESHOLD = 60.0


@pytest.fixture(autouse=True)
def _isolate() -> None:
    """偏差存在模块级字典里，用例之间必须互不影响。"""
    device_clock.clear()
    yield
    device_clock.clear()


def test_first_observation_is_taken_as_is() -> None:
    """首次观测直接采用：没有历史值可平滑，也就无从谈"跳变"。"""
    skew = device_clock.update("dev-1", "2026-10-02T12:00:30.000Z", "2026-10-02T12:00:00.000Z", THRESHOLD)
    assert skew == pytest.approx(-30.0), "设备说 12:00:30 而服务端才 12:00:00：它快 30 秒"


def test_slow_drift_is_smoothed_toward_the_observation() -> None:
    """小变化取 EMA：挪一部分，不一次跳过去。

    设备钟的漂移是 8.5 秒/天，相邻两次观测之间的差只有毫秒级——那点抖动来自网络往返，
    不该原样吃进偏差里。
    """
    device_clock.update("dev-1", "2026-10-02T12:00:30.000Z", "2026-10-02T12:00:00.000Z", THRESHOLD)
    settled = device_clock.update("dev-1", "2026-10-02T12:01:10.000Z", "2026-10-02T12:00:40.000Z", THRESHOLD)
    assert settled == pytest.approx(-30.0), "两次观测一致，值稳定"

    drifted = device_clock.update("dev-1", "2026-10-02T12:02:40.000Z", "2026-10-02T12:02:00.000Z", THRESHOLD)
    assert drifted == pytest.approx(-33.0), "这次观测到 -40，差值 10 小于阈值：-30 + 0.3 × -10"


def test_jump_beyond_threshold_is_taken_immediately() -> None:
    """超过阈值直接采信新值：设备重启归零、被手动改钟都是几十分钟级的跳变，
    继续平滑会让之后几次映射都按错误的偏差算。"""
    device_clock.update("dev-1", "2026-10-02T12:00:30.000Z", "2026-10-02T12:00:00.000Z", THRESHOLD)
    # 设备钟被回拨一小时：它说 11:00:30，而服务端已经 12:00:50
    jumped = device_clock.update("dev-1", "2026-10-02T11:00:30.000Z", "2026-10-02T12:00:50.000Z", THRESHOLD)
    assert jumped == pytest.approx(3620.0), "不取 EMA，直接采用"


def test_unparsable_observation_keeps_the_previous_value() -> None:
    """一次坏观测不该污染已经稳住的偏差。"""
    device_clock.update("dev-1", "2026-10-02T12:00:30.000Z", "2026-10-02T12:00:00.000Z", THRESHOLD)
    for bad in (None, "", "not-a-time", 12345):
        assert device_clock.update("dev-1", bad, "2026-10-02T12:05:00.000Z", THRESHOLD) == pytest.approx(-30.0)


def test_to_server_returns_none_without_a_record() -> None:
    """这台设备还没回过 pong 时平移不了，调用方据此退回观测时刻。

    两种来源的强度不同：平移过来的是事实起点，退回的是观测下界。数据里必须分得清
    （见 doors 的 assigned_source）。
    """
    assert device_clock.to_server("2026-10-02T12:00:00.000Z", "dev-never-seen") is None


def test_to_server_shifts_a_fast_device_clock() -> None:
    """设备钟比服务端快 30 秒：它写着 11:00，那么服务端此刻是 10:59:30。"""
    device_clock.update("dev-1", "2026-10-02T12:00:30.000Z", "2026-10-02T12:00:00.000Z", THRESHOLD)
    assert device_clock.to_server("2026-10-02T11:00:00.000Z", "dev-1") == "2026-10-02T10:59:30.000Z"


def test_to_server_shifts_a_slow_device_clock() -> None:
    """设备钟比服务端慢 30 秒：它写着 12:10，那么服务端此刻是 12:10:30。"""
    device_clock.update("dev-1", "2026-10-02T11:59:30.000Z", "2026-10-02T12:00:00.000Z", THRESHOLD)
    assert device_clock.to_server("2026-10-02T12:10:00.000Z", "dev-1") == "2026-10-02T12:10:30.000Z"


def test_to_server_handles_an_unparsable_moment() -> None:
    """时刻本身坏掉时同样返回 None，调用方走退回那一条。"""
    device_clock.update("dev-1", "2026-10-02T11:59:30.000Z", "2026-10-02T12:00:00.000Z", THRESHOLD)
    assert device_clock.to_server("", "dev-1") is None
    assert device_clock.to_server(None, "dev-1") is None


def test_load_seeds_the_memory_copy() -> None:
    """上线时把持久化的值载入，消除"上线到首次 pong"之间的零偏差窗口。"""
    device_clock.load("dev-1", 42.0)
    assert device_clock.get("dev-1") == pytest.approx(42.0)
    assert device_clock.to_server("2026-10-02T12:00:00.000Z", "dev-1") == "2026-10-02T12:00:42.000Z"


def test_get_defaults_to_zero() -> None:
    """没有记录时为 0，即不平移。"""
    assert device_clock.get("dev-unknown") == 0.0
