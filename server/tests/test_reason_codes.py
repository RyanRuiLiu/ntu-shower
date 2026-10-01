"""失败原因码的三端对账。

`reason_code` 是跨端契约：设备端产生、服务端转发、网页端据此分支。任何一端写了
一个没登记的码，另一端的分支就会**静默失效**——表现是"提示不对"，不是报错，
没有人会去留意。

登记表在 `app/core/reason_codes.py`。
"""

import re
from pathlib import Path

import pytest

from app.core.actions import ALL_ACTIONS
from app.core.reason_codes import MESSAGES, PREFIX_OWNERS, REGISTRY

ANDROID_SRC = (
    Path(__file__).resolve().parents[2] / "android" / "app" / "src" / "main" / "java" / "com" / "ryanliu" / "ntushower"
)

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "protocol" / "schema"

#: 原因码字面量。只认已登记前缀，所以 `link.hello`、`session.command` 这类**消息类型**
#: 不会被误抓；`ui.dump`、`ui.touch` 这类**动作名**会命中，下面用 `ALL_ACTIONS` 减掉。
#: 允许数字是为了 `a11y`——它是 accessibility 的行业缩写，不是随手起的。
_CODE = re.compile(r'"((?:proto|device|server|command|door|ui|camera|screen)\.[a-z0-9_]+)"')


def _message_types() -> set[str]:
    """协议的消息类型（`door.snapshot` 这类）。它们不是原因码，只是同名前缀。"""
    return {path.stem for path in SCHEMA_DIR.glob("*.json")}


def _device_codes() -> set[str]:
    """设备端源码里出现过、且既不是动作名也不是消息类型的原因码。

    排除 core/log：那里写的是**事件名**（command.received 这类痕迹代号，与
    服务端 log_event 的事件同一套命名），不是原因码——形状相同、语义不同。
    """
    found: set[str] = set()
    for path in ANDROID_SRC.rglob("*.kt"):
        if path.parent.name in ("audit", "log"):
            continue
        found |= set(_CODE.findall(path.read_text(encoding="utf-8")))
    return found - set(ALL_ACTIONS) - _message_types()


def test_registry_has_no_duplicates() -> None:
    """重复的码会让"这个码是什么意思"有两个答案。"""
    codes = [reason.code for reason in REGISTRY]
    assert len(codes) == len(set(codes)), f"重复登记：{sorted(c for c in codes if codes.count(c) > 1)}"


def test_every_reason_has_a_chinese_message() -> None:
    """中文说明不能空：它是前端在人面前唯一能显示的内容。"""
    for reason in REGISTRY:
        assert reason.message.strip(), f"{reason.code} 没有说明"
        assert any("\u4e00" <= ch <= "\u9fff" for ch in reason.message), f"{reason.code} 的说明不是中文"


def test_every_prefix_is_registered() -> None:
    """前缀必须表达"问题归谁"。

    反例是历史上用执行通道名当前缀的 `automation.*`——通道是服务端的限流划分，
    换一种排队策略它就会变，而原因码不该跟着变。
    """
    for reason in REGISTRY:
        prefix = reason.code.split(".", 1)[0]
        assert prefix in PREFIX_OWNERS, f"{reason.code} 的前缀未登记归属"


def test_reason_codes_use_dot_namespace() -> None:
    for reason in REGISTRY:
        assert re.fullmatch(r"[a-z][a-z0-9]*\.[a-z0-9_]+", reason.code), f"不符合 <前缀>.<情况>：{reason.code}"


def test_device_codes_are_all_registered() -> None:
    """设备端写下的每个码都要在登记表里。

    这条是命门：设备端加一个新的失败分支时忘了登记，这里就会红。
    """
    unknown = sorted(_device_codes() - set(MESSAGES))
    assert not unknown, f"设备端用了未登记的原因码：{unknown}（登记表见 app/core/reason_codes.py）"


def test_no_reason_is_registered_but_unused() -> None:
    """登记了却没人用的码是噪声，会让后来的人以为那条通路存在。

    允许服务端独有的码（`proto.*` / `command.unknown_id` / `door.*`）不在设备端出现。
    """
    device_only_prefixes = {"ui", "camera", "screen"}
    unused = sorted(
        reason.code
        for reason in REGISTRY
        if reason.code.split(".", 1)[0] in device_only_prefixes and reason.code not in _device_codes()
    )
    assert not unused, f"登记了但设备端从未产生：{unused}"


@pytest.mark.parametrize("reason_code", sorted(MESSAGES))
def test_describe_returns_registered_message(reason_code: str) -> None:
    from app.core.reason_codes import describe

    assert describe(reason_code) == MESSAGES[reason_code]


def test_describe_falls_back_to_the_code_itself() -> None:
    """未登记的码回显它自己——让人看得见漏网的那一条，而不是显示空白。"""
    from app.core.reason_codes import describe

    assert describe("who.knows") == "who.knows"
