"""跨端契约生成物与来源的一致性。

契约的唯一来源是服务端的 Python 定义（动作名、状态取值、配置键），另两端的常量
由 `tools/sync_contracts.py` 生成。"改了来源却忘了重新生成"是这套做法的唯一失效
方式：生成物与重新生成的结果逐字节相同才算过。`test_action_names.py` 等对账测试
防的是"两端内容不一致"，这条防的是"根本没人去改另一端"。
"""

from pathlib import Path

import pytest
from tools import sync_contracts

from app.core.actions import ALL_ACTIONS

REPO_ROOT = Path(__file__).resolve().parents[2]


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def test_generated_artifacts_exist() -> None:
    """生成物入库而非构建期生成：文件不见了本身就是问题。"""
    missing = [_rel(path) for path in sync_contracts.artifacts() if not path.is_file()]
    assert not missing, f"生成物缺失（跑 `python -m tools.sync_contracts --write`）：{missing}"


@pytest.mark.parametrize("path", sorted(sync_contracts.artifacts(), key=_rel))
def test_artifact_matches_generated_content(path: Path) -> None:
    """磁盘上的内容必须与重新生成的结果逐字节一致。"""
    expected = sync_contracts.artifacts()[path]
    assert path.is_file(), f"{_rel(path)} 缺失（跑 `python -m tools.sync_contracts --write`）"

    actual = path.read_text(encoding="utf-8")
    assert actual == expected, f"{_rel(path)} 与生成结果不一致——改了契约来源却没重新生成？"


def test_artifacts_carry_the_generated_mark() -> None:
    """每个生成物都要声明自己是生成的，否则会被当成手写文件直接改。"""
    for path, content in sync_contracts.artifacts().items():
        assert sync_contracts.GENERATED_MARK in content, _rel(path)


def test_every_action_reaches_the_kotlin_artifact() -> None:
    """动作清单必须完整落到 Kotlin 里：生成逻辑漏项时"内容一致"仍会通过，要单独盯。"""
    content = sync_contracts.artifacts()[sync_contracts.KOTLIN_ACTIONS]
    for action in ALL_ACTIONS:
        assert f'"{action}"' in content, f"生成物里没有动作：{action}"


def test_message_types_come_from_the_schema_directory() -> None:
    """消息类型登记（服务端 + 设备）必须与 schema 文件集一致：schema 是唯一出处。"""
    from app.core.message_types import ALL as SERVER_ALL

    schemas = {path.stem for path in sync_contracts.SCHEMA_DIR.glob("*.json")} - {"envelope"}
    assert set(SERVER_ALL) == schemas
    kotlin = sync_contracts.artifacts()[sync_contracts.KOTLIN_MESSAGE_TYPES]
    for stem in schemas:
        assert f'"{stem}"' in kotlin, f"设备登记表里没有消息类型：{stem}"


def test_wire_types_are_never_written_as_raw_strings() -> None:
    """网关与各域的信封 type 一律引用登记表常量：裸字符串与登记表漂移时，
    服务端会不认识自己刚发的消息——源头禁掉。"""
    import re

    wire_types = sorted(sync_contracts._schema_message_types())
    pattern = re.compile("|".join(f'"{re.escape(t)}"' for t in wire_types))
    # actions.py 持有桥动作的**动作名**（bridge.open/close 以裸消息下发，动作名即
    # 消息名——双登记，同值由 test_bridge_action_names_match_their_message_types 钉住）
    offenders: list[str] = []
    for path in (REPO_ROOT / "server" / "app").rglob("*.py"):
        if path.name in ("message_types.py", "actions.py"):
            continue
        matched = pattern.findall(path.read_text(encoding="utf-8"))
        if matched:
            offenders.append(f"{_rel(path)}: {matched}")
    assert not offenders, f"信封 type 出现裸字符串（改用 app.core.message_types 常量）：{offenders}"


def test_bridge_action_names_match_their_message_types() -> None:
    """桥动作用裸消息下发（无命令信封）：动作登记表与消息登记表各自持有同名值，
    双登记不可漂移——同值由这里钉住。"""
    from app.core import actions, message_types

    assert actions.BRIDGE_OPEN == message_types.BRIDGE_OPEN
    assert actions.BRIDGE_CLOSE == message_types.BRIDGE_CLOSE


def test_every_message_type_has_a_server_side_consumer() -> None:
    """每个消息类型在服务端都有消费者（发送方或接收方）：schema 只登记没人用的
    消息，是"看起来有协议、实际没实现"的静默缺口。"""
    from app.core.message_types import ALL as SERVER_ALL

    for wire_type in SERVER_ALL:
        constant = wire_type.replace(".", "_").upper()
        consumers = 0
        for path in (REPO_ROOT / "server" / "app").rglob("*.py"):
            if path.name == "message_types.py":
                continue
            if constant in path.read_text(encoding="utf-8"):
                consumers += 1
        assert consumers >= 1, f"消息类型没有服务端消费者：{wire_type}"
