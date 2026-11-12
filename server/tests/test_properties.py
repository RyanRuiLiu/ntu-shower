"""属性测试试点（Hypothesis）。

手写用例断言的是「具体输入的期望输出」；属性断言的是「任意输入下不变量成立」。
这里只收两条**已经有出处**的不变量，让它们从样本覆盖变成通用断言：

- `derive_state` 对任意输入的满射与报修/停用优先级——出处：`doors/service.py`
  模块注释（"对任意输入的满射……不存在算不出状态的门读数"、优先级原因）与
  `docs/reference.md` 不变量节；
- `app/core/clock.py` 的规范化往返与非负时长——出处：模块注释（"统一时间：服务端
  对外输出的时间一律为 UTC RFC 3339 毫秒精度"；"结果恒为非负"）。

deadline=None：默认 200ms/例在 CI 的 coverage 追踪下会抖动——hypothesis 文档记载的
CI 噪声源。正确性不因此放宽：失败照样收缩出反例，只是不再按单例耗时误报。

pytestmark timeout=60：全局单测上限 5 秒（pyproject），其目标是拦「等待不会到达的事件」
的用例；属性用例的耗时随机器速度浮动（CI 的 coverage 追踪 + fork 比本地慢数倍），
本模块放宽到 60 秒并收紧 max_examples=50 封住总时长。硬杀代价实测过：worker 直接
os._exit，coverage 数据丢失（87% < 96%）连带整条 CI 失败。

若试点证明价值，再扩到其他纯函数；若只添噪声，删这一个文件即可整体回退。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.core import clock
from app.core.protocol import as_int
from app.models.enums import DoorState
from app.modules.doors.service import RAW_STATUS_DISABLED, RAW_STATUS_REPAIR, derive_state

pytestmark = pytest.mark.timeout(60)

#: as_int 只接受 int/float/str，其余取默认值——构造 status_raw 时只给这三类。
STATUS_SCALARS = st.one_of(st.integers(), st.floats(allow_nan=False), st.text(max_size=8))

DOORS = st.fixed_dictionaries(
    {
        "status_raw": STATUS_SCALARS,
        "card_no": st.one_of(st.none(), STATUS_SCALARS),
        "acc_no": st.one_of(st.none(), st.text(max_size=16), st.integers()),
        "in_time": st.one_of(st.none(), st.text(max_size=40)),
        "close_time": st.one_of(st.none(), st.text(max_size=40)),
        "bath_time_s": st.one_of(st.none(), STATUS_SCALARS),
    }
)

#: 带时区的时刻：naive 时刻 + 整分钟偏移在测试体内合成。不走 st.datetimes(timezones=…)：
#  那条路径要对 datetime 边界反复过滤重试，是文档标注的昂贵策略，在 CI 的追踪下会
#  拖垮单测 5 秒上限（2026-11-10 实测：gw1 被硬杀）。
AWARE_DATETIMES = st.tuples(st.datetimes(), st.integers(-14 * 60, 14 * 60)).map(
    lambda pair: pair[0].replace(tzinfo=timezone(timedelta(minutes=pair[1])))
)


@settings(deadline=None, max_examples=50)
@given(DOORS)
def test_derive_state_is_total(door: dict) -> None:
    """满射：任何一帧都落在七态之内，不存在算不出状态的门读数。"""
    assert derive_state(door) in set(DoorState)


@settings(deadline=None, max_examples=50)
@given(DOORS)
def test_repair_and_disabled_take_priority(door: dict) -> None:
    """报修/停用排在占用判定之前：status 命中 1/3 即返回，不看占用字段。

    as_int 对 int 恒等（协议层的约定），status_raw 给原始 int 即可精确命中。
    """
    door["status_raw"] = RAW_STATUS_REPAIR
    assert derive_state(door) is DoorState.REPAIR
    door["status_raw"] = RAW_STATUS_DISABLED
    assert derive_state(door) is DoorState.DISABLED


@settings(deadline=None, max_examples=50)
@given(STATUS_SCALARS)
def test_as_int_accepts_scalars_and_never_raises(value: object) -> None:
    """as_int 的吸收性：三类 JSON 标量进来要么得 int 要么取默认，绝不抛异常。"""
    result = as_int(value)
    assert isinstance(result, int)
    if isinstance(value, int):
        assert result == value


@settings(deadline=None, max_examples=50)
@given(AWARE_DATETIMES)
def test_rfc3339_format_is_a_fixed_point_of_parse(moment: datetime) -> None:
    """规范化往返：format 的输出再 parse 再 format 得到同一字符串（毫秒精度即规范形）。

    不直接断言 parse(format(dt)) == dt，是因为 format 截断到毫秒——截断本身在
    "再 format 一次是否不变"里被隐含验证，且不需要在测试里复写截断算式。
    """
    text = clock.format_rfc3339(moment)
    assert clock.format_rfc3339(clock.parse_rfc3339(text)) == text


@settings(deadline=None, max_examples=50)
@given(st.text(max_size=64))
def test_shift_by_zero_canonicalizes(text: str) -> None:
    """shift(text, 0) == format(parse(text))：平移零秒就是规范化本身。"""
    shifted = clock.shift_rfc3339(text, 0)
    parsed = clock.parse_rfc3339(text)
    if parsed is None:
        assert shifted is None
    else:
        assert shifted == clock.format_rfc3339(parsed)


@settings(deadline=None, max_examples=50)
@given(AWARE_DATETIMES, AWARE_DATETIMES)
def test_seconds_between_is_never_negative(start: datetime, end: datetime) -> None:
    """时长恒为非负（clock.py：调用方不该为负值单独写分支）。"""
    first = clock.format_rfc3339(start)
    second = clock.format_rfc3339(end)
    assert clock.seconds_between(first, second) >= 0
    assert clock.seconds_between(second, first) >= 0


@settings(deadline=None, max_examples=50)
@given(AWARE_DATETIMES, st.integers(-(10**6), 10**6))
def test_shift_moves_the_moment_by_exactly_seconds(moment: datetime, seconds: int) -> None:
    """平移基准是被给时刻而非此刻：shift(text, s) == format(解析值 + s 秒)。

    与期望比较走规范形而非裸 datetime：format 截断到毫秒，裸比较会把截断误判成
    平移误差。
    """
    text = clock.format_rfc3339(moment)
    shifted = clock.shift_rfc3339(text, seconds)
    expected = clock.format_rfc3339(moment.astimezone(UTC) + timedelta(seconds=seconds))
    assert shifted == expected
