"""审计标准化：api_calls 去自创关联，job_runs 哨兵改 NULL，词表去残留。

三件事（ADR-0014 修订）：
- api_calls 删除 kind 列（网关生命周期行的唯一出处是 device_link_events，旧
  gateway 行与身份头时代的 identity 行一并删除——业主裁定不保留旧语义行）；
  删除 device_id 列（从路径/查询串猜设备是自创关联，删除后调用审计只记录
  请求本身：谁、何时、对什么、结果如何）。
- actor_kind 词表删除 identity（X-Identity 已于 ADR-0019 删除）。
- job_runs 的 door_no/cert_no 改可空：设备级行不再用 0/空串哨兵占位，
  旧哨兵值改写为 NULL（0 从来不是真实喷头号）。

SQLite 的列级变更没有可靠路径（batch 反射对索引改名不可靠），两表按标准流程
手工重建：删旧索引 → 改旧表名 → 按当前模型建新表（索引名与建表产物逐字节一致，
test_migrations_reproduce_the_models 钉住）→ 按共同列拷数据 → 删旧表。

Revision ID: b8f4c2a1d7e9
Revises: c7e2a1d94b50
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import MetaData

from app.db.base import NAMING_CONVENTION, UtcDateTime
from app.persistence.audit import ApiCall, JobRun

revision = "b8f4c2a1d7e9"
down_revision = "c7e2a1d94b50"
branch_labels = None
depends_on = None

_API_CALLS_COLUMNS = (
    "id, requested_at, request_id, trace_id, actor_kind, actor, client_ip, user_agent,"
    " method, path, query, request_body, status_code, duration_ms, problem_type, detail,"
    " response_body, created_at"
)
_JOB_RUNS_COLUMNS = "id, occurred_at, device_id, door_no, session_id, cert_no, job, action, outcome, reason, threshold_s, created_at"


def _model_table(model: type) -> sa.Table:
    """当前模型的目标形状（单一来源；索引名由命名约定在建表时生成）。

    列级 index/unique 旗标清掉：op.create_table 内部按旗标会再建一遍索引，与
    本函数的显式建索引重复。
    """
    target = model.__table__.to_metadata(MetaData(naming_convention=NAMING_CONVENTION))
    for column in target.columns:
        column.index = False
        column.unique = False
    return target


def _rebuild(model: type, legacy_name: str, columns: str) -> None:
    """把表按当前模型重建：旧表改名让位 → 建新表 → 拷共同列 → 删旧表。

    调用方须已删掉该表的全部旧索引（SQLite 索引名全库唯一，改名不改名都会与
    新表冲突）。
    """
    op.rename_table(op.f(model.__tablename__), legacy_name)
    target = _model_table(model)
    op.create_table(model.__tablename__, *target.columns, *target.constraints)
    for index in sorted(target.indexes, key=lambda item: str(item.name)):
        op.create_index(op.f(str(index.name)), model.__tablename__, [column.name for column in index.columns])
    op.execute(f"insert into {model.__tablename__} ({columns}) select {columns} from {legacy_name}")
    op.drop_table(legacy_name)


def upgrade() -> None:
    # 旧语义行与哨兵占位先处理：此刻旧列仍在（引用旧列的语句在重建前跑）
    op.execute("delete from api_calls where kind = 'gateway'")
    op.execute("delete from api_calls where actor_kind = 'identity'")
    op.execute("update job_runs set door_no = null where door_no = 0")
    op.execute("update job_runs set cert_no = null where cert_no = ''")
    op.drop_index("ix_api_calls_device_id_requested_at", table_name="api_calls")
    op.drop_index("ix_api_calls_requested_at", table_name="api_calls")
    op.drop_index("ix_api_calls_trace_id", table_name="api_calls")
    _rebuild(ApiCall, "api_calls_legacy", _API_CALLS_COLUMNS)
    op.drop_index("ix_job_runs_device_id_occurred_at", table_name="job_runs")
    op.drop_index("ix_job_runs_occurred_at", table_name="job_runs")
    _rebuild(JobRun, "job_runs_legacy", _JOB_RUNS_COLUMNS)


def downgrade() -> None:
    """回退到带 kind/device_id 与 NOT NULL 哨兵的旧形状（历史形状无模型，显式建）。"""
    meta = MetaData(naming_convention=NAMING_CONVENTION)

    def api_calls_legacy() -> sa.Table:
        return sa.Table(
            "api_calls",
            meta,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "kind",
                sa.Enum("http", "gateway", name="api_call_kind", native_enum=False, create_constraint=True, length=32),
                nullable=False,
            ),
            sa.Column("requested_at", UtcDateTime(), nullable=False),
            sa.Column("request_id", sa.String(length=64), nullable=False),
            sa.Column("trace_id", sa.String(length=64), nullable=False),
            sa.Column(
                "actor_kind",
                sa.Enum(
                    "device",
                    "anonymous",
                    "identity",
                    "token",
                    "session",
                    name="actor_kind",
                    native_enum=False,
                    create_constraint=True,
                    length=32,
                ),
                nullable=False,
            ),
            sa.Column("actor", sa.String(length=64), nullable=False),
            sa.Column("client_ip", sa.String(length=45), nullable=True),
            sa.Column("user_agent", sa.String(length=255), nullable=True),
            sa.Column("method", sa.String(length=8), nullable=False),
            sa.Column("path", sa.String(length=255), nullable=False),
            sa.Column("query", sa.String(length=512), nullable=True),
            sa.Column("request_body", sa.Text(), nullable=True),
            sa.Column("status_code", sa.Integer(), nullable=False),
            sa.Column("duration_ms", sa.Integer(), nullable=False),
            sa.Column("device_id", sa.String(length=64), nullable=True),
            sa.Column("problem_type", sa.String(length=64), nullable=True),
            sa.Column("detail", sa.Text(), nullable=True),
            sa.Column("response_body", sa.Text(), nullable=True),
            sa.Column("created_at", UtcDateTime(), nullable=False),
        )

    def job_runs_legacy() -> sa.Table:
        return sa.Table(
            "job_runs",
            meta,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("occurred_at", UtcDateTime(), nullable=False),
            sa.Column("device_id", sa.String(length=64), nullable=True),
            sa.Column("door_no", sa.Integer(), nullable=False),
            sa.Column("session_id", sa.Integer(), nullable=True),
            sa.Column("cert_no", sa.String(length=32), nullable=False),
            sa.Column("job", sa.String(length=32), nullable=False),
            sa.Column("action", sa.String(length=32), nullable=True),
            sa.Column(
                "outcome",
                sa.Enum(
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
                    "reassigned",
                    "reassign-failed",
                    "hold-applied",
                    "hold-failed",
                    "resumed",
                    "resume-failed",
                    name="job_outcome",
                    native_enum=False,
                    create_constraint=True,
                    length=32,
                ),
                nullable=False,
            ),
            sa.Column("reason", sa.String(length=128), nullable=True),
            sa.Column("threshold_s", sa.Integer(), nullable=True),
            sa.Column("created_at", UtcDateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["session_id"],
                ["door_sessions.id"],
                name=op.f("fk_job_runs_session_id_door_sessions"),
                ondelete="SET NULL",
            ),
        )

    op.drop_index("ix_api_calls_requested_at", table_name="api_calls")
    op.drop_index("ix_api_calls_trace_id", table_name="api_calls")
    legacy = api_calls_legacy()
    op.rename_table("api_calls", "api_calls_modern")
    op.create_table("api_calls", *legacy.columns, *legacy.constraints)
    op.create_index(
        "ix_api_calls_device_id_requested_at", "api_calls", ["device_id", "requested_at"], unique=False
    )
    op.create_index(op.f("ix_api_calls_requested_at"), "api_calls", ["requested_at"], unique=False)
    op.create_index(op.f("ix_api_calls_trace_id"), "api_calls", ["trace_id"], unique=False)
    op.execute(
        f"insert into api_calls ({_API_CALLS_COLUMNS}, kind, device_id)"
        f" select {_API_CALLS_COLUMNS}, 'http', null from api_calls_modern"
    )
    op.drop_table("api_calls_modern")

    op.drop_index("ix_job_runs_device_id_occurred_at", table_name="job_runs")
    op.drop_index("ix_job_runs_occurred_at", table_name="job_runs")
    legacy = job_runs_legacy()
    op.rename_table("job_runs", "job_runs_modern")
    op.create_table("job_runs", *legacy.columns, *legacy.constraints)
    op.create_index(
        "ix_job_runs_device_id_occurred_at", "job_runs", ["device_id", "occurred_at"], unique=False
    )
    op.create_index(op.f("ix_job_runs_occurred_at"), "job_runs", ["occurred_at"], unique=False)
    job_runs_select = _JOB_RUNS_COLUMNS.replace("door_no", "coalesce(door_no, 0)").replace(
        "cert_no", "coalesce(cert_no, '')"
    )
    op.execute(
        f"insert into job_runs ({_JOB_RUNS_COLUMNS})"
        f" select {job_runs_select}"
        f" from job_runs_modern"
    )
    op.drop_table("job_runs_modern")
