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
