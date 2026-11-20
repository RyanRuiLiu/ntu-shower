"""电源计划域：上报事实与 hold 状态入设备影子行；job_runs 词表重钉。

- devices：删旧拨钟两列（clock_shifted_at / clock_shift_assignments），加上报事实
  三列（auto_close_time / auto_open_time / zone_offset_minutes，列名镜像 V8 平台的
  autoCloseTime / autoOpenTime）与 hold 状态五列；
- job_runs：outcome 的 CHECK 词表按 contracts.JobOutcome 当前全集重建。此前词表
  冻结在 initial schema 的 11 个调度器值，power 域的终局值入库即被拒——收尾审计
  一失败，整个状态迁移被连带打断（2026-11-20 二楼事故的引信）；
- app_settings：配置键随域改名（extend_hours_* → hold_*），两键均为 SERVER 作用域，
  只存在于全局单行。

Revision ID: b3d81f60c2a4
Revises: 5e0f1d5dd2c0
"""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from alembic import op

from app.db.base import UtcDateTime

revision = "b3d81f60c2a4"
down_revision = "5e0f1d5dd2c0"
branch_labels = None
depends_on = None

#: job_runs.outcome 的词表快照。**这里必须与 contracts.JobOutcome 逐值一致**——
#: 漂移由 tests/test_power.py 的迁移词表守卫当场抓住，不再依赖人工同步。
OLD_OUTCOMES = (
    "settled",
    "assigned",
    "settle-failed",
    "assign-waiting",
    "assign-failed",
    "assign-expired",
    "skipped",
    "failed",
    "rejected",
    "no-password",
    "not-applicable",
)
NEW_OUTCOMES = (
    *OLD_OUTCOMES,
    "reassigned",
    "reassign-failed",
    "hold-applied",
    "hold-failed",
    "resumed",
    "resume-failed",
)


def _enum(values: tuple[str, ...]) -> sa.Enum:
    return sa.Enum(
        *values,
        name="job_outcome",
        native_enum=False,
        create_constraint=True,
        length=32,
    )


def upgrade() -> None:
    # 上一次失败的部署在库里留了 batch 重建的半成品表：devices 本体未动，先清残留。
    # （表无被引用者，外键开启下也可安全 DROP。）
    op.execute(sa.text("DROP TABLE IF EXISTS _alembic_tmp_devices"))

    # devices 影子行换列全部走原生 ALTER（SQLite ≥3.35 支持 ADD/DROP COLUMN）——
    # 不用 batch 重建：devices 被 door_states 等多张表外键引用，重建需要整库关外键。
    # 两列均无索引/无外键参与，原生 DROP COLUMN 成立。
    op.add_column("devices", sa.Column("auto_close_time", sa.String(length=5), nullable=True))
    op.add_column("devices", sa.Column("auto_open_time", sa.String(length=5), nullable=True))
    op.add_column("devices", sa.Column("zone_offset_minutes", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("facts_at", UtcDateTime(), nullable=True))
    op.add_column("devices", sa.Column("hold_started_at", UtcDateTime(), nullable=True))
    op.add_column(
        "devices",
        sa.Column(
            "hold_source",
            sa.Enum("manual", "automated", name="hold_source", native_enum=False, create_constraint=True, length=32),
            nullable=True,
        ),
    )
    op.add_column("devices", sa.Column("hold_offset_s", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("hold_assignments", sa.JSON(), nullable=True))
    op.add_column("devices", sa.Column("lease_deadline", UtcDateTime(), nullable=True))
    op.drop_column("devices", "clock_shifted_at")
    op.drop_column("devices", "clock_shift_assignments")

    with op.batch_alter_table("job_runs") as batch:
        batch.alter_column(
            "outcome",
            existing_type=_enum(OLD_OUTCOMES),
            type_=_enum(NEW_OUTCOMES),
            existing_nullable=False,
        )

    _rename_setting_keys()


def _rename_setting_keys() -> None:
    """全局配置单行里的键随域改名（SERVER 作用域键只在 app_settings 存在）。"""
    conn = op.get_bind()
    rows = conn.execute(sa.text("select id, config from app_settings")).fetchall()
    for row_id, config_value in rows:
        value: dict[str, Any] | None = config_value
        if isinstance(value, str):
            value = json.loads(value)
        if not isinstance(value, dict):
            continue
        if "extend_hours_enabled" not in value and "extend_hours_students" not in value:
            continue
        renamed = {
            (
                "hold_enabled"
                if key == "extend_hours_enabled"
                else "hold_students"
                if key == "extend_hours_students"
                else key
            ): item
            for key, item in value.items()
        }
        conn.execute(
            sa.text("update app_settings set config = :config where id = :id"),
            {"config": json.dumps(renamed, ensure_ascii=False), "id": row_id},
        )


def downgrade() -> None:
    raise NotImplementedError("电源计划域重写是单向迁移：不提供回到拨钟态的降级路径")
