"""两端配置键集合的对账。

服务端的 `spec.CONFIG_SPECS`（scope=device）与设备端的 `RuntimeConfig.appliers`
是同一个契约的两侧实现。任何一侧加了键而另一侧没跟上，表现为"服务端下发了配置，
设备却不认"，静默无效。通过解析设备端源码对账。
"""

import re
from pathlib import Path

import pytest

from app.modules.config.spec import (
    CONFIG_SPECS,
    DEVICE_KEYS,
    GROUPS,
    SPECS_BY_KEY,
    TIME,
    WEEKDAYS,
    parse_time,
    parse_weekdays,
)

ANDROID_RUNTIME_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "ryanliu"
    / "ntushower"
    / "core"
    / "config"
    / "RuntimeConfig.kt"
)

#: appliers 映射里的一行，形如 `"key" to { c -> ... }`
_APPLIER_KEY = re.compile(r'^\s*"([a-z0-9_]+)"\s+to\s+\{', re.MULTILINE)


def _device_keys() -> set[str]:
    """从 RuntimeConfig.kt 的 appliers 映射里提取设备端认识的键。"""
    source = ANDROID_RUNTIME_CONFIG.read_text(encoding="utf-8")
    block = source.split("private val appliers", 1)
    if len(block) != 2:
        pytest.fail("RuntimeConfig.kt 里找不到 appliers 映射，解析逻辑需要同步更新")
    return set(_APPLIER_KEY.findall(block[1]))


def test_device_config_file_exists() -> None:
    assert ANDROID_RUNTIME_CONFIG.is_file(), f"设备端配置实现位置变了：{ANDROID_RUNTIME_CONFIG}"


def test_device_keys_match_between_server_and_android() -> None:
    """两端认识的键必须完全一致。"""
    server_keys = set(DEVICE_KEYS)
    device_keys = _device_keys()

    missing_on_device = sorted(server_keys - device_keys)
    extra_on_device = sorted(device_keys - server_keys)

    assert not missing_on_device, f"服务端会下发但设备端不认识的键：{missing_on_device}"
    assert not extra_on_device, f"设备端认识但登记表里没有的键：{extra_on_device}"


def test_device_keys_are_all_scoped_to_device() -> None:
    """下发给设备的键必须都标了 scope=device，否则会被 device_effective 过滤掉。"""
    for key in DEVICE_KEYS:
        assert SPECS_BY_KEY[key].scope.value == "device"


def test_each_group_holds_one_scope_and_sits_in_the_matching_section() -> None:
    """一组里的键必须同属一个 scope，且与它所在的分区相符。

    分区的划分依据就是"这个键去哪"：服务端那些在服务器上运行、不下发；设备那些会推到
    设备。一组混两种 scope，页面上那张卡片就同时属于两个页签，而后续所有按去处做的判断
    （要不要推送、能不能单台覆盖）都会在它身上失效——所以这里连"混"都不许。
    """
    for group in GROUPS:
        keys = [spec for spec in CONFIG_SPECS if spec.group == group.key]
        assert keys, f"分组 {group.key} 一个键都没有"
        scopes = {spec.scope.value for spec in keys}
        assert len(scopes) == 1, f"分组 {group.key} 混了两种 scope：{sorted(scopes)}"
        assert group.section == scopes.pop(), f"分组 {group.key} 的分区与它的 scope 不符"


def test_every_spec_belongs_to_a_declared_group() -> None:
    """每个键都要挂在一个声明过的分组里，否则它在配置页上没有卡片可渲染。"""
    declared = {group.key for group in GROUPS}
    for spec in CONFIG_SPECS:
        assert spec.group in declared, f"{spec.key} 挂在未声明的分组 {spec.group}"


def test_every_spec_has_a_unique_key() -> None:
    keys = [spec.key for spec in SPECS_BY_KEY.values()]
    assert len(keys) == len(set(keys))


def test_every_spec_has_a_title_and_description() -> None:
    """登记表要生成界面提示与文档，没有说明的键等于没登记。"""
    undescribed = [spec.key for spec in SPECS_BY_KEY.values() if not spec.description or not spec.title]
    assert not undescribed, f"缺少标题或说明的键：{undescribed}"


def test_integer_specs_declare_bounds() -> None:
    """整数键必须声明下限：没有下限的整数可以配出 0 或负数。

    上限允许省略——阈值类键没有天然上界（如自动校时的触发阈值，设得越大越接近
    "永不触发"），强行给一个反而限制使用。
    """
    unbounded = [spec.key for spec in SPECS_BY_KEY.values() if spec.kind.value == "int" and spec.minimum is None]
    assert not unbounded, f"整数键缺少下限：{unbounded}"


def test_defaults_are_within_declared_bounds() -> None:
    """默认值必须落在自己声明的范围内。

    时刻键的默认值是 `HH:MM` 文本，而范围用自 0 点起的分钟数表达，所以先经 [parse_time]
    换算——与校验用的是同一个函数，不在测试里另写一份。
    """
    for spec in SPECS_BY_KEY.values():
        if spec.minimum is None:
            continue
        default: object = spec.default
        if spec.kind is TIME:
            assert isinstance(spec.default, str), f"{spec.key} 的默认值应当是 HH:MM 文本"
            default = parse_time(spec.default)
        assert isinstance(default, int), f"{spec.key} 的默认值不是合法时刻：{spec.default!r}"
        assert spec.minimum <= default <= (spec.maximum or default), f"{spec.key} 的默认值不在范围内"


def test_text_choices_default_is_declared() -> None:
    """声明了封闭取值集的键，默认值必须就是其中一个取值。"""
    for spec in SPECS_BY_KEY.values():
        if spec.choices is not None:
            assert spec.default in spec.choices, f"{spec.key} 的默认值不在 choices 里：{spec.default!r}"


def test_weekday_default_is_canonical() -> None:
    """星期键的默认值必须是规范形式，且能被解析成七天。"""
    spec = SPECS_BY_KEY["notice_weekdays"]

    assert spec.kind is WEEKDAYS
    assert isinstance(spec.default, str)
    assert parse_weekdays(spec.default) == (1, 2, 3, 4, 5, 6, 7)
    assert spec.default == "1,2,3,4,5,6,7"


def test_parse_weekdays_normalizes_and_rejects() -> None:
    """去重、升序、去空白；空项、越界、非数字一律不给值。"""
    assert parse_weekdays(" 3, 1, 3 ") == (1, 3)
    assert parse_weekdays("1,2,3,4,5,6,7") == (1, 2, 3, 4, 5, 6, 7)

    for bad in ("", " ", "0", "8", "1,8", "1,,2", "1,a", "mon"):
        assert parse_weekdays(bad) is None, bad


def test_text_choices_machinery_rejects_unknown_value() -> None:
    """文本键的封闭取值集校验机制（`notice_align` 使用）。"""
    from app.modules.config.spec import ConfigKind
    from app.modules.config.store import _check_value

    assert _check_value("k", ConfigKind.TEXT, None, None, ("a", "b"), "a") == "a"
    with pytest.raises(ValueError, match="只允许"):
        _check_value("k", ConfigKind.TEXT, None, None, ("a", "b"), "c")
