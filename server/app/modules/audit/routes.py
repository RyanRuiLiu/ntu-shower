"""审计查询：/ops/audit 只读出口。

审计的**写入**在 core/audit（中间件与网关生命周期），这里只暴露查询——
让"查审计"也要过 ops 令牌、也要被审计（写入侧跳过静态与 healthz，本路由不跳）。
"""

from typing import Annotated

from fastapi import APIRouter, Query

from ...core.audit import list_calls
from ...core.protocol import JsonObject

ops_router = APIRouter(prefix="/ops")


@ops_router.get("/audit")
def list_audit(
    device_id: Annotated[str | None, Query(description="按设备过滤")] = None,
    actor_kind: Annotated[str | None, Query(description="按调用方过滤：ops / identity / device / anonymous")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> JsonObject:
    """接口调用审计（不含命令级明细——那在 /ops/commands，两者以 trace_id 关联）。"""
    return {"calls": list_calls(device_id=device_id, actor_kind=actor_kind, limit=limit)}
