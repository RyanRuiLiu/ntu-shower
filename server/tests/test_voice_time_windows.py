"""语音规则的每周时段：跨天、星期边界、覆盖长度与摘要。"""

from datetime import datetime

import pytest

from app.modules.voice import schemas, time_windows


def _window(value: dict[str, object]) -> time_windows.Window:
    return time_windows.window_from_mapping(value)


def _at(day: int, hour: int, minute: int = 0) -> datetime:
    # 2026-01-05 是周一；day 取 5..11 可覆盖周一到周日
    return datetime(2026, 1, 4 + day, hour, minute)


def test_same_day_window_includes_start_excludes_end() -> None:
    window = _window({"days": [1], "start": "09:00", "end": "17:00"})
    windows = [window]
    assert time_windows.match(windows, _at(1, 9, 0))
    assert time_windows.match(windows, _at(1, 16, 59))
    assert not time_windows.match(windows, _at(1, 8, 59))
    assert not time_windows.match(windows, _at(1, 17, 0))
    assert not time_windows.match(windows, _at(2, 10, 0))


def test_cross_midnight_belongs_to_start_day() -> None:
    windows = [_window({"days": [5], "start": "22:00", "end": "02:00"})]
    assert time_windows.match(windows, _at(5, 23, 0))
    assert time_windows.match(windows, _at(6, 1, 59))
    assert not time_windows.match(windows, _at(6, 2, 0))
    assert not time_windows.match(windows, _at(5, 21, 59))
    assert not time_windows.match(windows, _at(6, 23, 0))


def test_sunday_night_wraps_to_monday() -> None:
    windows = [_window({"days": [7], "start": "22:00", "end": "02:00"})]
    assert time_windows.match(windows, _at(7, 23, 0))
    assert time_windows.match(windows, _at(1, 1, 0))
    assert not time_windows.match(windows, _at(1, 2, 0))


def test_multiple_windows_are_or() -> None:
    windows = [
        _window({"days": [1], "start": "07:00", "end": "08:00"}),
        _window({"days": [1], "start": "18:00", "end": "19:00"}),
    ]
    assert time_windows.match(windows, _at(1, 7, 30))
    assert time_windows.match(windows, _at(1, 18, 30))
    assert not time_windows.match(windows, _at(1, 12, 0))


def test_coverage_overlap_and_summary() -> None:
    monday = _window({"days": [1], "start": "09:00", "end": "10:00"})
    tuesday = _window({"days": [2], "start": "09:00", "end": "10:00"})
    friday = _window({"days": [5], "start": "22:00", "end": "02:00"})
    assert time_windows.coverage([monday]) == 60
    assert time_windows.overlap([monday], [tuesday]) is False
    assert time_windows.overlap([monday], [monday]) is True
    assert time_windows.summary([]) == "不限"
    assert time_windows.summary([friday]) == "周五 22:00–次日 02:00"
    assert time_windows.summary([monday, tuesday]) == "周一 09:00–10:00、周二 09:00–10:00"
    weekdays = [
        _window({"days": [1, 2, 3, 4, 5], "start": "07:00", "end": "08:00"}),
        _window({"days": [1, 2, 3, 4, 5], "start": "18:00", "end": "19:00"}),
    ]
    assert time_windows.summary(weekdays) == "周一至周五 07:00–08:00、周一至周五 18:00–19:00"


def test_window_validation_rejects_bad_shapes() -> None:
    with pytest.raises(ValueError, match="至少选择一天"):
        _window({"days": [], "start": "09:00", "end": "10:00"})
    with pytest.raises(ValueError, match="星期取值"):
        _window({"days": [8], "start": "09:00", "end": "10:00"})
    with pytest.raises(ValueError, match="HH:MM"):
        _window({"days": [1], "start": "9:00", "end": "10:00"})
    with pytest.raises(ValueError, match="不能相同"):
        _window({"days": [1], "start": "09:00", "end": "09:00"})


def test_time_window_schema_normalizes_days_and_rejects_same_span() -> None:
    window = schemas.TimeWindow(days=[3, 1, 3], start="22:00", end="06:00")
    assert window.days == [1, 3]
    assert window.to_window().start_minute == 22 * 60
    with pytest.raises(ValueError, match="不能相同"):
        schemas.TimeWindow(days=[1], start="06:00", end="06:00")
