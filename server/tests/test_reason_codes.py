"""失败原因码的对账。

`reason_code` 是跨端契约：设备端产生、服务端转发、网页端据此分支。任何一端写了
一个没登记的码，另一端的分支就会静默失效。登记表在 `app/core/reason_codes.py`。
"""

import re
from pathlib import Path

import pytest

from app.core.actions import ALL_ACTIONS
from app.core.reason_codes import MESSAGES, REGISTRY, describe

ANDROID_SRC = (
    Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "main" / "java" / "com" / "ryanliu" / "ntushower"
)

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "protocol" / "schema"

#: 原因码命名空间清单（显式）：前缀表达"问题归谁"，而非代码模块。
#: proto 帧本身不合法；device 设备侧；server 服务端侧；command 命令生命周期；
#: door 业务规则；ui 界面操作；camera 与 screen 采集。不在此列即登记错误。
NAMESPACES: tuple[str, ...] = (
    "proto",
    "device",
    "server",
    "command",
    "door",
    "ui",
    "camera",
    "screen",
)
NAMESPACE_SET = frozenset(NAMESPACES)

#: 设备专属的命名空间：这些码只应由设备端产生。
DEVICE_ONLY_NAMESPACES = frozenset({"ui", "camera", "screen"})

#: 原因码字面量。只认已登记命名空间，消息类型（`door.snapshot`）与动作名（`ui.dump`）
#: 会命中，后面用消息类型清单与 ALL_ACTIONS 减掉。允许数字是为了 `a11y`。
_CODE = re.compile('"((?:' + "|".join(NAMESPACES) + r')\.[a-z0-9_]+)"')


def _message_types() -> set[str]:
    """协议的消息类型。它们不是原因码，只是同名前缀。"""
    return {path.stem for path in SCHEMA_DIR.glob("*.json")}


def _device_codes() -> set[str]:
    """设备端源码里出现过、且既不是动作名也不是消息类型的原因码。

    排除 core/log：那里写的是事件名（与 log_event 的事件同一套命名），形状相同、
    语义不同。
    """
    found: set[str] = set()
    for path in ANDROID_SRC.rglob("*.kt"):
        if path.parent.name in ("audit", "log"):
            continue
        found |= set(_CODE.findall(path.read_text(encoding="utf-8")))
    return found - set(ALL_ACTIONS) - _message_types()


def test_registry_has_no_duplicates() -> None:
    codes = [reason.code for reason in REGISTRY]
    assert len(codes) == len(set(codes)), f"重复登记：{sorted(code for code in codes if codes.count(code) > 1)}"


def test_every_reason_has_a_chinese_message() -> None:
    """中文说明不能空：它是前端在人面前唯一能显示的内容。"""
    for reason in REGISTRY:
        assert reason.message.strip(), f"{reason.code} 没有说明"
        assert any("\u4e00" <= ch <= "\u9fff" for ch in reason.message), f"{reason.code} 的说明不是中文"


def test_every_prefix_belongs_to_a_namespace() -> None:
    """前缀必须表达"问题归谁"，且登记在命名空间清单里。"""
    for reason in REGISTRY:
        prefix = reason.code.split(".", 1)[0]
        assert prefix in NAMESPACE_SET, f"{reason.code} 的前缀未登记归属"


def test_reason_codes_use_dot_namespace() -> None:
    for reason in REGISTRY:
        assert re.fullmatch(r"[a-z][a-z0-9]*\.[a-z0-9_]+", reason.code), f"不符合 <前缀>.<情况>：{reason.code}"


def test_device_codes_are_all_registered() -> None:
    """设备端写下的每个码都要在登记表里；漏登记的分支会静默失效。"""
    unknown = sorted(_device_codes() - set(MESSAGES))
    assert not unknown, f"设备端用了未登记的原因码：{unknown}"


def test_no_device_only_reason_is_registered_but_unused() -> None:
    """设备专属命名空间（ui/camera/screen）的码登记了就必须有设备端产生它。"""
    unused = sorted(
        reason.code
        for reason in REGISTRY
        if reason.code.split(".", 1)[0] in DEVICE_ONLY_NAMESPACES and reason.code not in _device_codes()
    )
    assert not unused, f"登记了但设备端从未产生：{unused}"


@pytest.mark.parametrize("reason_code", sorted(MESSAGES))
def test_describe_returns_registered_message(reason_code: str) -> None:
    assert describe(reason_code) == MESSAGES[reason_code]


def test_describe_falls_back_to_the_code_itself() -> None:
    """未登记的码回显它自己：让人看得见漏网的那一条，而不是显示空白。"""
    assert describe("who.knows") == "who.knows"
