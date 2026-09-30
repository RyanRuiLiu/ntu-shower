"""跨端契约的生成物是否与来源一致。

契约的唯一来源是服务端的 Python 定义（动作名、状态取值、配置键），另两端的常量
由 `tools/sync_contracts.py` 生成。**改了来源却忘了重新生成**是这套做法的唯一失效
方式，这条测试就是堵它：生成物与重新生成的结果逐字节相同才算过。

在这个机制建立之前，两端各手写一份、靠事后对账——`test_action_names.py` 至今仍在
跑，它防的是"两端内容不一致"，而这条防的是"根本没人去改另一端"。两者不重复：
前者管正确性，后者管同步动作有没有发生。
"""

from pathlib import Path

import pytest
from tools import sync_contracts

REPO_ROOT = Path(__file__).resolve().parents[2]


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO_ROOT))


def test_generated_artifacts_exist() -> None:
    """生成物必须在仓库里。

    它们是入库的产物（不是构建期生成），所以"文件不见了"本身就是问题——
    设备端构建会直接失败，而失败信息不会指向真正的原因。
    """
    missing = [_rel(path) for path in sync_contracts.artifacts() if not path.is_file()]
    assert not missing, f"生成物缺失（跑 `python -m tools.sync_contracts --write`）：{missing}"


@pytest.mark.parametrize("path", sorted(sync_contracts.artifacts(), key=_rel))
def test_artifact_matches_generated_content(path: Path) -> None:
    """磁盘上的内容必须与重新生成的结果一致。"""
    if not path.is_file():
        pytest.skip("缺失由 test_generated_artifacts_exist 报出")

    expected = sync_contracts.artifacts()[path]
    actual = path.read_text(encoding="utf-8")
    assert actual == expected, (
        f"{_rel(path)} 与生成结果不一致——改了契约来源却没重新生成？\n"
        f"跑：cd server && python -m tools.sync_contracts --write"
    )


def test_artifacts_carry_the_generated_mark() -> None:
    """每个生成物都要声明自己是生成的。

    少了这一行，后来的人会以为那是手写文件，于是直接在上面改——下一轮生成把它
    抹掉，改动就无声地没了。
    """
    for path, content in sync_contracts.artifacts().items():
        if path.suffix == ".kt":
            assert sync_contracts.GENERATED_MARK in content, _rel(path)
        else:
            # JS 生成物里标记以「本文件<标记>」的形式出现
            assert sync_contracts.GENERATED_MARK in content, _rel(path)


def test_every_action_reaches_the_kotlin_artifact() -> None:
    """动作清单必须完整落到 Kotlin 里——漏一个就是设备端宣告不了那个能力。

    这条其实是生成逻辑的自检：`artifacts()` 若因为分组或遍历写错而漏项，
    上面那条"内容一致"仍会通过（磁盘与生成结果一样地漏），所以要单独盯。
    """
    from app.core.actions import ALL_ACTIONS

    content = sync_contracts.artifacts()[sync_contracts.KOTLIN_ACTIONS]
    for action in ALL_ACTIONS:
        assert f'"{action}"' in content, f"生成物里没有动作：{action}"
