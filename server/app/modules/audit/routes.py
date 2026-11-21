"""审计域 API：三类审计的只读出口（`audit:read`）。

api_call（HTTP 请求）的写入在 core 层的审计中间件；command_audit（命令级）与
job_run（后台任务的自动动作）的写入在各自的动作方。这里只暴露查询，查询本身
也照常被审计。前两表以 trace_id 关联，命令与维护记录以 command_id 关联，
查两边即得全链路。设备网关的连接与断开在 devices 的 link events，不在本表。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Security
from sqlalchemy.orm import Session

from app.auth.dependencies import caller
from app.contracts import ActorKind
from app.core.errors import problem_responses
from app.db.deps import get_db
from app.modules.audit.schemas import CommandAuditRecord, JobRun
from app.persistence import audit as command_audit
from app.persistence.api_calls import ApiCallRecord, list_calls

admin_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Security(caller, scopes=["audit:read"])],
    tags=["audit"],
    responses=problem_responses(401, 403, 422),
)


@admin_router.get("/audit/calls", summary="接口调用审计")
def list_calls_endpoint(
    actor_kind: Annotated[ActorKind | None, Query(description="按调用方类型过滤")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ApiCallRecord]:
    """接口调用审计。"""
    return list_calls(actor_kind=actor_kind, limit=limit)


@admin_router.get("/audit/commands", summary="命令审计")
def list_commands_endpoint(
    device_id: Annotated[str | None, Query(description="按设备过滤")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    session: Session = Depends(get_db),
) -> list[CommandAuditRecord]:
    """命令级审计：参数、结果与设备回报（与 calls 以 trace_id 关联）。"""
    return command_audit.list_commands(session, device_id=device_id, limit=limit)


@admin_router.get("/audit/job-runs", summary="后台任务审计")
def list_job_runs_endpoint(
    device_id: Annotated[str | None, Query(description="按设备过滤")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    session: Session = Depends(get_db),
) -> list[JobRun]:
    """后台任务审计：自动化任务以谁的名义执行了何种操作及其依据（与 commands 按时间对照）。"""
    return command_audit.list_job_runs(session, device_id=device_id, limit=limit)
