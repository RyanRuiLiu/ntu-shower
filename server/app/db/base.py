"""ORM 基类与列类型约定。

约束命名必须集中在 metadata 上：Alembic 自动生成迁移时按它给索引、外键、唯一约束起名。
不集中，同一个约束在不同机器上会得到不同名字，迁移脚本就无法复用。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, MetaData
from sqlalchemy import Enum as SaEnum
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

#: 写入 naive datetime 时的错误文案。抽成常量，避免在异常构造处写长字符串。
NAIVE_DATETIME_MESSAGE = "时间必须带时区：naive datetime 不允许写入"

#: 约束名模板。顺序固定，改动视为迁移（会让已有约束改名）。
NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    """带时区的当前时刻。

    用 Python 侧默认值而不是 SQL 的 CURRENT_TIMESTAMP：后者在 SQLite 上产出的是
    不带时区的字符串，取回来会被当成 naive datetime，跨时区比较时静默偏移。
    """
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class UtcDateTime(TypeDecorator[datetime]):
    """带时区语义的时间列。

    SQLite 没有带时区的时间类型：`DateTime(timezone=True)` 写进去是 aware，读回来却是
    naive，而且不报错。这里在绑定前统一换算成 UTC 并去掉 tzinfo，读取时补回 UTC——
    存取两端都保证「拿到的是 UTC」，比较和相减才有意义。

    写 naive datetime 直接报错：宁可当场失败，也不要让一个不知道时区的时间流进库里。
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, _dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(NAIVE_DATETIME_MESSAGE)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, _dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC)


def enum_column(py_enum: type[StrEnum], name: str) -> SaEnum:
    """把一个 StrEnum 映射成 VARCHAR + CHECK。

    `native_enum=False`：SQLite 没有原生枚举类型，用 CHECK 约束表达，行为一致且可迁移。
    `values_callable`：默认取成员名（大写），这里要的是取值（小写），与协议、API 对齐。
    """
    return SaEnum(
        py_enum,
        name=name,
        native_enum=False,
        # SQLAlchemy 1.4 起默认不再生成 CHECK，必须显式打开；
        # 不打开就只是 VARCHAR，枚举约束形同虚设。
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
        length=32,
    )


__all__: list[str] = ["NAMING_CONVENTION", "Base", "UtcDateTime", "enum_column", "utcnow"]
