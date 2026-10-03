"""审计域 API：两类审计的只读出口（admin）。

api_call（HTTP 与网关生命周期）的写入在 core/audit；command_audit（命令级）
的写入在各下发方经 modules/audit.repository。这里只暴露查询，查询本身也照常
被审计。两表以 trace_id 关联，查两边即得全链路。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.audit import ApiCallRecord, list_calls
from app.modules.access.dependencies import require_admin
from app.modules.audit import repository as command_audit
from app.modules.audit.schemas import CommandAuditRecord

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
) -> list[CommandAuditRecord]:
    """命令级审计：参数、结果与设备回报（与 calls 以 trace_id 关联）。"""
    return command_audit.list_commands(device_id=device_id, limit=limit)
