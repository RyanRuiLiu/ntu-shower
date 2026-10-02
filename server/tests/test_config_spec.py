"""两端配置键集合的对账。

服务端的 `spec.CONFIG_SPECS`（scope=device）与设备端的 `RuntimeConfig.appliers`
是同一个契约的两侧实现。任何一侧加了键而另一侧没跟上，表现为"服务端下发了配置，
设备却不认"，静默无效。通过解析设备端源码对账。
"""

import re
from pathlib import Path

import pytest

from app.modules.config.spec import DEVICE_KEYS, SPECS_BY_KEY

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


def test_every_spec_has_a_unique_key() -> None:
    keys = [spec.key for spec in SPECS_BY_KEY.values()]
    assert len(keys) == len(set(keys))


def test_every_spec_has_a_title_and_description() -> None:
    """登记表要生成界面提示与文档，没有说明的键等于没登记。"""
    undescribed = [spec.key for spec in SPECS_BY_KEY.values() if not spec.description or not spec.title]
    assert not undescribed, f"缺少标题或说明的键：{undescribed}"


def test_integer_specs_declare_bounds() -> None:
    """整数键必须给出上下限：没有范围的整数可以配出 0 或负数。"""
    unbounded = [
        spec.key
        for spec in SPECS_BY_KEY.values()
        if spec.kind.value == "int" and (spec.minimum is None or spec.maximum is None)
    ]
    assert not unbounded, f"整数键缺少上下限：{unbounded}"


def test_defaults_are_within_declared_bounds() -> None:
    for spec in SPECS_BY_KEY.values():
        if spec.minimum is not None:
            assert spec.minimum <= spec.default <= (spec.maximum or spec.default), f"{spec.key} 的默认值不在范围内"
