"""rename background_task_runs.feature to task

Revision ID: d699ea5e3016
Revises: 005d56b1c684
Create Date: 2026-11-14

后台任务命名全链统一（task/feature 三词并存 → task）：列值历来存的就是
任务名（renew_wait 等），改名只动列头，不动任何行。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd699ea5e3016'
down_revision: Union[str, Sequence[str], None] = '005d56b1c684'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("background_task_runs") as batch_op:
        batch_op.alter_column("feature", new_column_name="task", existing_type=sa.String(length=32))


def downgrade() -> None:
    with op.batch_alter_table("background_task_runs") as batch_op:
        batch_op.alter_column("task", new_column_name="feature", existing_type=sa.String(length=32))
