"""迁移与模型的一致性：改了 models 忘了生成迁移，这里当场红。

迁移是表结构的唯一事实来源（生产由 compose 的 migrate 服务 `alembic upgrade head`
应用），但"模型改了、迁移忘了生成"没有任何其他门禁能发现——migrate 只负责把
已有迁移跑完，不会提示模型又变了。Django 用 `makemigrations --check` 拦同类漂移；
这里用 Alembic 的 `check` 命令做同一件事。
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _config(database_url: str) -> Config:
    cfg = Config(str(_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    os.environ["DATABASE_URL"] = database_url
    return cfg


def test_migrations_reproduce_the_models(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """从零跑完迁移后，库的形状必须与模型定义一致（无待生成的差异）。"""
    url = f"sqlite:///{(tmp_path / 'mig.db').as_posix()}"
    cfg = _config(url)
    command.upgrade(cfg, "head")
    command.check(cfg)


def test_check_detects_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """对照组：库与模型不一致时 check 必须报错——上一条的红不是摆设。"""
    from alembic.util.exc import CommandError

    url = f"sqlite:///{(tmp_path / 'empty.db').as_posix()}"
    cfg = _config(url)
    with pytest.raises(CommandError):
        command.check(cfg)
