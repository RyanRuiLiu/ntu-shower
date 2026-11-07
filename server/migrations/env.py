"""Alembic 运行环境。

连接串由应用配置推导，不写在 alembic.ini 里：迁移打的库和应用连的库必须是同一个来源，
否则会出现「迁移成功但应用读到的是另一个库」。

`render_as_batch=True`：SQLite 除 ADD COLUMN 以外的 ALTER 都要重建整张表。不开 batch 模式，
改列类型、加约束这类迁移会直接失败。
`compare_type=True`：类型变化也纳入比对，否则把 VARCHAR(32) 改成 VARCHAR(64) 不会被发现。
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context

from app.db.engine import create_db_engine
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    """迁移目标库。显式传入 DATABASE_URL 时优先，供测试与一次性的转换脚本使用。"""
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    # 延迟导入：环境搭建阶段不需要把整个应用加载一遍
    from app.core.config import settings

    return f"sqlite:///{settings.db_path}"


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_db_engine(database_url())
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
                compare_type=True,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
