"""两端设备命令动作名的对账。

服务端的 `core/actions.ALL_ACTIONS` 与设备端的 `SupportedActions.ALL` 是同一个
契约的两侧实现。任何一侧改了名字而另一侧没跟上，都会表现为"服务端把命令发出去，
设备端回 unknown_action"——而且不报错，只是那条命令永远不生效。

这里通过解析设备端源码来对账，而不是让 Kotlin 导出一份清单：前者在 Python 侧
就能跑，后者要额外的构建与产物搬运。模式与 `test_config_spec.py` 一致。

## 为什么两边各有一份清单

Kotlin 与 Python 无法共享代码。能做到的是：**各自只有一处定义**，
再用这个测试把两份钉在一起。
"""

import re
from pathlib import Path

import pytest

from app.core.actions import ALL_ACTIONS

#: 设备端动作清单的位置（相对仓库根）。
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

#: ALL 集合的声明起点
_ALL_SET = "val ALL: Set<String> = setOf("


def _android_constants() -> dict[str, str]:
    """常量名 → 动作名。"""
    return dict(_CONST.findall(_read_source()))


def _android_all() -> set[str]:
    """`ALL` 集合里的动作名（集合里写的是常量名，需经常量表换算）。"""
    source = _read_source()
    body = source.split(_ALL_SET, 1)
    if len(body) != 2:
        pytest.fail(f"SupportedActions.kt 里找不到 `{_ALL_SET}`，解析逻辑需要同步更新")
    names = re.findall(r"^\s*([A-Z_]+),", body[1], re.MULTILINE)

    constants = _android_constants()
    missing = [n for n in names if n not in constants]
    if missing:
        pytest.fail(f"ALL 里引用了未定义的常量：{missing}")
    return {constants[n] for n in names}


def _read_source() -> str:
    return ANDROID_ACTIONS.read_text(encoding="utf-8")


def test_android_actions_file_exists() -> None:
    assert ANDROID_ACTIONS.is_file(), f"设备端动作清单位置变了：{ANDROID_ACTIONS}"


def test_action_names_match_between_server_and_android() -> None:
    """两端认识的动作必须完全一致。

    少一个：服务端可能下发设备不认识的动作（设备回 unknown_action）。
    多一个：设备宣告了做不到的能力，服务端的"下发前比对"形同虚设。
    """
    server_actions = set(ALL_ACTIONS)
    android_actions = _android_all()

    only_server = sorted(server_actions - android_actions)
    only_android = sorted(android_actions - server_actions)

    assert not only_server, f"服务端有但设备端没有：{only_server}"
    assert not only_android, f"设备端有但服务端没有：{only_android}"


def test_server_actions_are_unique() -> None:
    """清单里不该有重复（重复会让"多了还是少了"的判断失真）。"""
    assert len(ALL_ACTIONS) == len(set(ALL_ACTIONS))


def test_actions_follow_the_object_dot_action_form() -> None:
    """命名规则：`<对象>.<动作>`。

    对象是**被操作的东西**，不是代码模块，也不是用途。反例是历史上的
    `debug.exec`——`debug` 是用途，同一个能力在 HTTP 侧却叫
    `/devices/{id}/exec`（device 域），一个能力两个名字。
    """
    for action in ALL_ACTIONS:
        assert re.fullmatch(r"[a-z]+\.[a-z_]+", action), f"不符合 <对象>.<动作>：{action}"


def test_no_action_uses_a_purpose_word_as_its_object() -> None:
    """挡住"用用途词当对象"这类命名再次出现。"""
    purposes = {"debug", "misc", "util", "helper", "temp"}
    for action in ALL_ACTIONS:
        obj = action.split(".", 1)[0]
        assert obj not in purposes, f"对象用了用途词：{action}"


def test_every_action_is_documented() -> None:
    """每个动作都要有说明——它是跨端契约，不是内部细节。"""
    import app.core.actions as module

    for action in ALL_ACTIONS:
        matches = [
            name
            for name, value in vars(module).items()
            if value == action and not name.startswith("_") and name.isupper()
        ]
        assert matches, f"没有对应的常量定义：{action}"
        assert module.__doc__, "模块缺少说明"


def test_every_action_belongs_to_a_channel() -> None:
    """每个动作都必须登记执行通道。

    通道决定服务端的并发限流粒度（见 `core/actions.py` 的执行通道一节）：
    漏登记会让 `send_command_and_wait` 查表时抛 KeyError——那是运行期才发现的问题。
    """
    from app.core.actions import ACTION_CHANNELS

    missing = sorted(set(ALL_ACTIONS) - set(ACTION_CHANNELS))
    assert not missing, f"这些动作没有登记执行通道：{missing}"

    unknown = sorted(set(ACTION_CHANNELS) - set(ALL_ACTIONS))
    assert not unknown, f"通道表里有多余的动作：{unknown}"


def test_channel_names_are_defined_constants() -> None:
    """通道取值必须是已定义的常量——写错字符串会让限流悄悄失效。"""
    import app.core.actions as module

    known = {module.CHANNEL_AUTOMATION, module.CHANNEL_CAPTURE, module.CHANNEL_MAINTENANCE}
    for action, channel in module.ACTION_CHANNELS.items():
        assert channel in known, f"{action} 的通道不认识：{channel}"


def test_ui_actions_share_the_automation_channel_with_sessions() -> None:
    """界面注入必须与分配/结算同通道。

    设备端它们共用同一条串行队列——并发操作界面会让节点定位互相错位。这里守住
    "服务端不许把它们分到不同通道"，那等于允许两路界面操作同时下发。
    """
    import app.core.actions as module

    for action in (module.UI_TOUCH, module.UI_SCROLL, module.UI_KEY, module.UI_INPUT):
        assert module.ACTION_CHANNELS[action] == module.CHANNEL_AUTOMATION, action
    for action in (module.SESSION_ASSIGN, module.SESSION_SETTLE):
        assert module.ACTION_CHANNELS[action] == module.CHANNEL_AUTOMATION, action
