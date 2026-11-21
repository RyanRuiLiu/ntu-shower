"""hold 布防截止列更名：lease_deadline → arming_deadline。

hold 语义随整夜覆盖布防修正（ADR-0025 修订）：列里存的不再是租约到期，而是
整夜覆盖布防的截止（次日 auto_close_time）——"lease" 一词描述的语义已不存在，
列名随语义更名。纯更名，无数据变化。

Revision ID: c7e2a1d94b50
Revises: b3d81f60c2a4
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.db.base import UtcDateTime

revision = "c7e2a1d94b50"
down_revision = "b3d81f60c2a4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # devices 被多表外键引用，但该列无索引/无外键参与，SQLite ≥3.25 的原生
    # RENAME COLUMN 成立。
    op.alter_column(
        "devices",
        "lease_deadline",
        new_column_name="arming_deadline",
        existing_type=UtcDateTime(),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "devices",
        "arming_deadline",
        new_column_name="lease_deadline",
        existing_type=UtcDateTime(),
        existing_nullable=True,
    )
