"""两端设备命令动作名的对账。

服务端的 `core/actions.ALL_ACTIONS` 与设备端的 `SupportedActions.ALL` 是同一个
契约的两侧实现，任何一侧改名而另一侧没跟上，表现为设备回 unknown_action。通过
解析设备端源码对账，而不是让 Kotlin 导出清单。Kotlin 与 Python 无法共享代码，
能做到的是各自只有一处定义，再用这个测试把两份钉在一起。
"""

import re
from pathlib import Path

import pytest

from app.core import actions
from app.core.actions import ACTION_CHANNELS, ALL_ACTIONS

ANDROID_ACTIONS = (
    Path(__file__).resolve().parents[2]
    / "android"
    / "app"
    / "src"
    / "main"
    / "java"
    / "com"
    / "ryanliu"
    / "ntushower"
    / "transport"
    / "SupportedActions.kt"
)

#: 常量定义，形如 `const val SESSION_ASSIGN = "session.assign"`
_CONST = re.compile(r'^\s*const val ([A-Z_]+)\s*=\s*"([^"]+)"', re.MULTILINE)

_ALL_SET = "val ALL: Set<String> = setOf("


def _read_source() -> str:
    return ANDROID_ACTIONS.read_text(encoding="utf-8")


def _android_constants() -> dict[str, str]:
    """常量名 → 动作名。"""
    return dict(_CONST.findall(_read_source()))


def _android_all() -> set[str]:
    """`ALL` 集合里的动作名（集合里写的是常量名，需经常量表换算）。"""
    body = _read_source().split(_ALL_SET, 1)
    if len(body) != 2:
        pytest.fail(f"SupportedActions.kt 里找不到 `{_ALL_SET}`，解析逻辑需要同步更新")
    names = re.findall(r"^\s*([A-Z_]+),", body[1], re.MULTILINE)

    constants = _android_constants()
    missing = [name for name in names if name not in constants]
    if missing:
        pytest.fail(f"ALL 里引用了未定义的常量：{missing}")
    return {constants[name] for name in names}


def test_android_actions_file_exists() -> None:
    assert ANDROID_ACTIONS.is_file(), f"设备端动作清单位置变了：{ANDROID_ACTIONS}"


def test_action_names_match_between_server_and_android() -> None:
    """两端认识的动作必须完全一致。"""
    server_actions = set(ALL_ACTIONS)
    android_actions = _android_all()

    only_server = sorted(server_actions - android_actions)
    only_android = sorted(android_actions - server_actions)

    assert not only_server, f"服务端有但设备端没有：{only_server}"
    assert not only_android, f"设备端有但服务端没有：{only_android}"


def test_server_actions_are_unique() -> None:
    assert len(ALL_ACTIONS) == len(set(ALL_ACTIONS))


def test_actions_follow_the_object_dot_action_form() -> None:
    """命名规则 `<对象>.<动作>`：对象是被操作的东西，不是代码模块或用途。"""
    for action in ALL_ACTIONS:
        assert re.fullmatch(r"[a-z]+\.[a-z_]+", action), f"不符合 <对象>.<动作>：{action}"


def test_no_action_uses_a_purpose_word_as_its_object() -> None:
    purposes = {"debug", "misc", "util", "helper", "temp"}
    for action in ALL_ACTIONS:
        obj = action.split(".", 1)[0]
        assert obj not in purposes, f"对象用了用途词：{action}"


def test_every_action_has_a_constant() -> None:
    """每个动作都要有登记常量——它是跨端契约，不是内部细节。"""
    for action in ALL_ACTIONS:
        matches = [
            name
            for name, value in vars(actions).items()
            if value == action and not name.startswith("_") and name.isupper()
        ]
        assert matches, f"没有对应的常量定义：{action}"


def test_every_action_belongs_to_a_channel() -> None:
    """每个动作都必须登记执行通道，通道表里也不能有多余动作。"""
    missing = sorted(set(ALL_ACTIONS) - set(ACTION_CHANNELS))
    assert not missing, f"这些动作没有登记执行通道：{missing}"

    unknown = sorted(set(ACTION_CHANNELS) - set(ALL_ACTIONS))
    assert not unknown, f"通道表里有多余的动作：{unknown}"


def test_channel_names_are_defined_constants() -> None:
    """通道取值必须是已定义的常量——写错字符串会让限流失效。"""
    known = {
        actions.CHANNEL_AUTOMATION,
        actions.CHANNEL_CAPTURE,
        actions.CHANNEL_MAINTENANCE,
        actions.CHANNEL_VOICE,
    }
    for action, channel in actions.ACTION_CHANNELS.items():
        assert channel in known, f"{action} 的通道不认识：{channel}"


def test_ui_actions_share_the_automation_channel_with_sessions() -> None:
    """界面注入必须与分配/结算同通道：并发操作界面会让节点定位互相错位。"""
    for action in (actions.UI_TOUCH, actions.UI_SCROLL, actions.UI_KEY, actions.UI_INPUT):
        assert actions.ACTION_CHANNELS[action] == actions.CHANNEL_AUTOMATION, action
    for action in (actions.SESSION_ASSIGN, actions.SESSION_SETTLE):
        assert actions.ACTION_CHANNELS[action] == actions.CHANNEL_AUTOMATION, action
