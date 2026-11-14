"""审计域 API：三类审计的只读出口（admin）。

api_call（HTTP 与网关生命周期）的写入在 core/audit；command_audit（命令级）与
job_run（后台任务的自动动作）的写入在各自的动作方经
modules/audit.repository。这里只暴露查询，查询本身也照常被审计。
前两表以 trace_id 关联，命令与维护记录以 command_id 关联，查两边即得全链路。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.modules.access.dependencies import require_admin
from app.modules.audit.schemas import CommandAuditRecord, JobRun
from app.persistence import audit as command_audit
from app.persistence.api_calls import ApiCallRecord, list_calls

admin_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)], tags=["audit"])


@admin_router.get("/audit/calls")
def list_calls_endpoint(
    device_id: Annotated[str | None, Query(description="按设备过滤")] = None,
    actor_kind: Annotated[str | None, Query(description="按调用方过滤：identity / device / anonymous")] = None,
    kind: Annotated[str | None, Query(description="按类别过滤：http / gateway")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ApiCallRecord]:
    """接口调用与网关生命周期审计。"""
    return list_calls(device_id=device_id, actor_kind=actor_kind, kind=kind, limit=limit)


@admin_router.get("/audit/commands")
def list_commands_endpoint(
    device_id: Annotated[str | None, Query(description="按设备过滤")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    session: Session = Depends(get_db),
) -> list[CommandAuditRecord]:
    """命令级审计：参数、结果与设备回报（与 calls 以 trace_id 关联）。"""
    return command_audit.list_commands(session, device_id=device_id, limit=limit)


@admin_router.get("/audit/job-runs")
def list_job_runs_endpoint(
    device_id: Annotated[str | None, Query(description="按设备过滤")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    session: Session = Depends(get_db),
) -> list[JobRun]:
    """后台任务审计：配置好的任务自动替谁做了什么、为什么（与 commands 按时间对照）。"""
    return command_audit.list_job_runs(session, device_id=device_id, limit=limit)
