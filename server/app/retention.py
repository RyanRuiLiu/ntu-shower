"""审计与观测数据的保留与库维护（横切运维，不住在任何业务域里）。

清单横跨各域的表（审计三表、设备链接事件、语音投递），所以住在 app 根
的装配侧：持久化层不得反向 import 各域模型，核心层不得向上依赖，业务模块彼此
零引用——唯一能同时看见全部这些表的地方就是组合根这一层。

保留期统一单一数值：全部表跟随 settings.audit_retention_days（默认 90 天，
NIST SP 800-92 的热保留主流口径），不按表分散——改一处即全库生效。业务数据
（喷头状态迁移、使用会话、段）是运营历史，永久保留、不在此列。

库维护（VACUUM）也在本模块：滚动删除每天释放数千行的空闲页，文件不缩只会在
备份与全量恢复里背着空页。设备按 MCU 布防每晚定时关机，关机后的静默期就是
天然的维护窗口。
"""

import sqlite3
import time
from typing import Any

from app.core.config import settings
from app.core.logging import log_event
from app.db.session import session_scope
from app.modules.devices.models import DeviceLinkEvent
from app.modules.voice.models import VoiceDelivery
from app.persistence import api_calls
from app.persistence.audit import ApiCall, CommandAudit, JobRun

PRUNE_INTERVAL_S = 6 * 3600.0
VACUUM_INTERVAL_S = 3600.0
#: 距上次 VACUUM 的最小间隔：每天至多一次，维护窗口在设备夜间关机后。
VACUUM_MIN_GAP_S = 24 * 3600.0

#: 保留表 → 时刻列。天数统一来自 settings.audit_retention_days（单一旋钮），
#: 这里只登记「哪些表、按哪列清」。由 tests/test_retention.py 钉住登记完整性。
RETENTIONS: tuple[tuple[Any, Any], ...] = (
    (ApiCall, ApiCall.requested_at),
    (CommandAudit, CommandAudit.created_at),
    (JobRun, JobRun.occurred_at),
    (DeviceLinkEvent, DeviceLinkEvent.occurred_at),
    (VoiceDelivery, VoiceDelivery.occurred_at),
)

#: 距上次 VACUUM 的时刻（循环单调钟）；进程重启后为 0，首轮静默窗口即执行。
_last_vacuum = 0.0


async def prune_once() -> None:
    """清理一轮：全部保留表按统一期限删除过期行。

    生命周期（启动、取消、间隔）由 core/background 统一管理。各保留表的时刻列
    均有索引，DELETE 只触及本轮 aged-out 的行（约等于一个间隔的增量）。
    """
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


async def maybe_vacuum() -> None:
    """库维护：全设备离线（夜间关机后的静默期）且距上次 ≥24 小时时执行 VACUUM，
    归还滚动删除留下的自由页。有设备在线或窗口未到时跳过。

    VACUUM 重写整个库文件，需要独占——设备在线时的并发写入会撞 SQLITE_BUSY，
    所以以「零设备在线」为闸而不是按时刻硬编码。执行耗时与文件大小成正比
    （600MB 级秒级完成）；litestream 随后自动跟一次全量快照，无需干预。
    """
    global _last_vacuum
    from app.core.connections import manager

    if manager.online_connections():
        return
    now = time.monotonic()
    if _last_vacuum and now - _last_vacuum < VACUUM_MIN_GAP_S:
        return
    _last_vacuum = now
    con = sqlite3.connect(settings.db_path, isolation_level=None)
    try:
        before = con.execute("PRAGMA freelist_count").fetchone()[0]
        con.execute("VACUUM")
        after = con.execute("PRAGMA freelist_count").fetchone()[0]
    finally:
        con.close()
    log_event("INFO", "maintenance.vacuum", "库维护 VACUUM 完成", freed_pages=before - after)


__all__ = [
    "PRUNE_INTERVAL_S",
    "RETENTIONS",
    "VACUUM_INTERVAL_S",
    "maybe_vacuum",
    "prune_once",
]
