"""审计与观测数据的保留期：登记与执行（横切运维，不住在任何业务域里）。

清单横跨各域的表（审计三表、设备链接事件、门快照、语音投递），所以住在 app 根
的装配侧：持久化层不得反向 import 各域模型，核心层不得向上依赖，业务模块彼此
零引用——唯一能同时看见全部这些表的地方就是组合根这一层。执行由 core/background
的统一间隔任务驱动；api_call 与 command_audit 的可配期限（audit_retention_days）
在 persistence.api_calls.prune 里生效，其余表按登记值执行。
"""

from typing import Any

from app.core.config import settings
from app.core.logging import log_event
from app.db.session import session_scope
from app.modules.devices.models import DeviceLinkEvent
from app.modules.doors.models import DoorSnapshot
from app.modules.voice.models import VoiceDelivery
from app.persistence import api_calls
from app.persistence.audit import ApiCall, CommandAudit, JobRun

PRUNE_INTERVAL_S = 6 * 3600.0

#: 各保留期表 → (时刻列, 保留天数)。ApiCall 与 CommandAudit 实际按
#: settings.audit_retention_days 执行（登记值是配置缺省的对照，由
#: tests/test_retention.py 钉住登记完整性）；其余表按登记天数执行，天数与各表
#: 模型的 docstring 声明一致。业务数据（喷头状态迁移、使用会话）是运营历史，
#: 永久保留、不在此列。
RETENTIONS: tuple[tuple[Any, Any, int], ...] = (
    (ApiCall, ApiCall.requested_at, 90),
    (CommandAudit, CommandAudit.created_at, 90),
    (JobRun, JobRun.occurred_at, 180),
    (DeviceLinkEvent, DeviceLinkEvent.occurred_at, 90),
    (DoorSnapshot, DoorSnapshot.received_at, 30),
    (VoiceDelivery, VoiceDelivery.occurred_at, 90),
)


async def prune_once() -> None:
    """清理一轮；生命周期（启动、取消、间隔）由 core/background 统一管理。"""
    with session_scope() as session:
        removed = api_calls.prune(session, settings.audit_retention_days, RETENTIONS)
    if removed:
        log_event(
            "INFO",
            "audit.pruned",
            "审计过期行已清理",
            rows=removed,
            retention_days=settings.audit_retention_days,
        )


__all__ = ["PRUNE_INTERVAL_S", "RETENTIONS", "prune_once"]
