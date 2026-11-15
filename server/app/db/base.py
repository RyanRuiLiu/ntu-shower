"""ORM 基类、全库模型约定与时间戳 Mixin——数据模型的唯一规定出处。

## 全库模型约定（models 必须遵守，tests/test_model_conventions.py 钉住）

- **主键**：默认代理 `id` 主键 + `<实体>_id` 外键，同名同型同义；例外三处各有
  注释声明理由——`devices` 业务键主键（ADR-0008）、`voice_units` 自然主键
  （两列映射无独立生命周期）、连接表复合主键（身份即两列组合）。
- **外键**：一律指向 `<表>.id`，ondelete 只允许两种——历史/业务数据 RESTRICT
  （除档保留历史）、随主键消亡的从属数据 CASCADE（凭据）；审计/日志/事实表
  无外键（事实先于档案，设备不在册也要落行）。
- **时间列**：一律 UtcDateTime（UTC 强制，naive 拒写）；created_at 全库事实表
  必备（TimestampMixin / CreatedAtMixin）；updated_at 仅业务现态表（UPSERT 行）
  必备（TimestampMixin）——只追加的流水/事件/凭据表行不可变，没有 updated_at。
- **豁免清单**（无 created_at，各有一条注释声明）：`voice_units`（纯映射）、
  `ota_release_installs`（applied_at 即创建时刻）、`app_settings`/`device_configs`
  （单行 UPSERT，updated_at 即最新写入）。
- **枚举列**：一律经 enum_column（VARCHAR+CHECK，取值小写）。
- **时长列**：一律 `*_s` 整数秒。
- **约束命名**：必须走 NAMING_CONVENTION——Alembic 按它给索引、外键、唯一约束
  起名，不集中则同名约束在不同机器上不同名，迁移脚本无法复用。
- **保留期**：声明在模型 docstring、登记在 audit_middleware.RETENTIONS、
  钉在 tests/test_retention.py（声明=实现）。
- **多词命名**：Python 侧 snake_case；布尔用单词形容词；JSON 只放设备原话或
  键值差异（见各模型注释）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, MetaData
from sqlalchemy import Enum as SaEnum
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
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


class TimestampMixin:
    """业务现态表（UPSERT 行）的时间戳约定列：created_at + updated_at。

    用法：`class Device(TimestampMixin, Base):`——Mixin 列在 DDL 里排在自有列
    之前（SQLAlchemy 的声明顺序），对行为无影响。
    """

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, onupdate=utcnow)


class CreatedAtMixin:
    """只追加的流水/事件/凭据表的时间戳约定列：仅 created_at（行不可变）。"""

    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


__all__: list[str] = [
    "NAMING_CONVENTION",
    "Base",
    "CreatedAtMixin",
    "TimestampMixin",
    "UtcDateTime",
    "enum_column",
    "utcnow",
]
