"""设备档案、链路事件与设备日志的持久化。

- `register` 只覆盖版本、能力、时刻与开机时间，不动 name / trusted / sort_order
- 展示顺序以 `sort_order` 为准，同序回落到名字的自然排序
- 设备日志每个（设备，种类）只留最近 `LOG_KEEP` 份

时间一律接收带时区的 `datetime`，返回的记录把时刻序列化成 RFC 3339 字符串。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.contracts import DeviceLogKind, DeviceRecord, HoldSource, LinkEvent
from app.core.clock import format_rfc3339
from app.core.tokens import generate_token, hash_token
from app.db.base import utcnow
from app.modules.devices.models import Device, DeviceCredential, DeviceLinkEvent, DeviceLog

LOG_KEEP = 5

_NATURAL_SPLIT = re.compile(r"(\d+)")


def _natural_name_key(name: str) -> list[tuple[int, object]]:
    """名字的自然排序键：数字段按数值比（1F < 2F < 10F），其余按字面。"""
    return [(0, int(part)) if part.isdigit() else (1, part) for part in _NATURAL_SPLIT.split(name)]


def _find(session: Session, device_id: str) -> Device | None:
    return session.scalars(select(Device).where(Device.id == device_id)).one_or_none()


def get(session: Session, device_id: str) -> DeviceRecord | None:
    row = _find(session, device_id)
    return as_record(row) if row is not None else None


def list_all(session: Session) -> list[DeviceRecord]:
    """全部设备，按手动顺序，同序按名字自然排序。

    自然序 SQLite 表达不了，所以在 Python 侧补——数据库只负责 `sort_order`。
    """
    rows = session.scalars(select(Device).order_by(Device.sort_order)).all()
    records = [as_record(row) for row in rows]
    records.sort(key=lambda record: (int(record["sort_order"]), _natural_name_key(record["name"])))
    return records


def register(
    session: Session,
    device_id: str,
    app_version: str,
    version_code: int,
    os_version: str,
    capabilities: list[str],
    ip: str,
    booted_at: datetime,
    seen_at: datetime,
) -> None:
    """上线登记。新设备排到末尾，已登记的设备不动位置。"""
    row = _find(session, device_id)
    if row is None:
        highest = session.scalar(select(func.max(Device.sort_order)))
        row = Device(
            id=device_id,
            app_version=app_version,
            version_code=version_code,
            os_version=os_version,
            capabilities=list(capabilities),
            last_seen_at=seen_at,
            last_ip=ip,
            booted_at=booted_at,
            sort_order=int(highest) + 1 if highest is not None else 0,
        )
        session.add(row)
    else:
        row.app_version = app_version
        row.version_code = version_code
        row.os_version = os_version
        row.capabilities = list(capabilities)
        row.last_seen_at = seen_at
        row.last_ip = ip
        row.booted_at = booted_at
    session.commit()


def touch_seen(session: Session, device_id: str, seen_at: datetime) -> None:
    row = _find(session, device_id)
    if row is not None:
        row.last_seen_at = seen_at
        session.commit()


def set_name(session: Session, device_id: str, name: str) -> bool:
    row = _find(session, device_id)
    if row is None:
        return False
    row.name = name
    session.commit()
    return True


def set_trusted(session: Session, device_id: str, trusted: bool) -> bool:
    row = _find(session, device_id)
    if row is None:
        return False
    row.trusted = trusted
    session.commit()
    return True


def set_retired(session: Session, device_id: str, retired: bool) -> bool:
    """停用/恢复。停用即拒绝握手与自注册，历史数据全部保留。"""
    row = _find(session, device_id)
    if row is None:
        return False
    row.retired_at = utcnow() if retired else None
    session.commit()
    return True


def set_power_facts(
    session: Session,
    device_id: str,
    auto_close_time: str,
    auto_open_time: str,
    zone_offset_minutes: int,
) -> bool:
    """落一份 device.status 上报事实（影子行的 reported 部分）。设备不在册返回 False。"""
    row = _find(session, device_id)
    if row is None:
        return False
    row.auto_close_time = auto_close_time
    row.auto_open_time = auto_open_time
    row.zone_offset_minutes = zone_offset_minutes
    row.facts_at = utcnow()
    session.commit()
    return True


def power_facts(session: Session, device_id: str) -> tuple[str, str, int] | None:
    """上报事实（计划关闭、计划开启、时区偏移）；从未上报过时 None。

    唯一消费入口：hold 对账（触发与恢复都按它换算）、voice 生效时段的当地时刻、
    设备页展示。缺哪一项都按整体缺失处理——半个计划没法对账。
    """
    row = _find(session, device_id)
    if row is None or not row.auto_close_time or not row.auto_open_time:
        return None
    if row.zone_offset_minutes is None:
        return None
    return (str(row.auto_close_time), str(row.auto_open_time), int(row.zone_offset_minutes))


def get_hold(session: Session, device_id: str) -> dict[str, Any] | None:
    """hold 状态：意图建立即非 None（`hold_offset_s` NULL 表示尚未施加）。无行给 None。"""
    row = _find(session, device_id)
    if row is None or row.hold_started_at is None:
        return None
    return {
        "started_at": row.hold_started_at,
        "source": HoldSource(row.hold_source) if row.hold_source is not None else None,
        "offset_s": row.hold_offset_s,
        "assignments": list(row.hold_assignments or []),
        "lease_deadline": row.lease_deadline,
    }


def start_hold(session: Session, device_id: str, source: HoldSource) -> None:
    """建立 hold 意图（状态先行）：对账器随后施加偏置。已在 hold 中时是空操作。"""
    row = _find(session, device_id)
    if row is None or row.hold_started_at is not None:
        return
    row.hold_started_at = utcnow()
    row.hold_source = source
    row.hold_offset_s = None
    row.hold_assignments = None
    session.commit()


def mark_hold_applied(
    session: Session,
    device_id: str,
    offset_s: int,
    assignments: list[dict[str, Any]],
    lease_deadline: datetime,
) -> None:
    """记录偏置已施加：墙钟 = 真实 + 偏移，租约到点入库（崩溃后对账可见）。"""
    row = _find(session, device_id)
    if row is None:
        return
    row.hold_offset_s = offset_s
    row.hold_assignments = assignments
    row.lease_deadline = lease_deadline
    session.commit()


def set_lease(session: Session, device_id: str, deadline: datetime) -> None:
    row = _find(session, device_id)
    if row is None:
        return
    row.lease_deadline = deadline
    session.commit()


def clear_hold(session: Session, device_id: str) -> None:
    """回到 following_schedule：hold 全列清空（幂等）。"""
    row = _find(session, device_id)
    if row is None or row.hold_started_at is None:
        return
    row.hold_started_at = None
    row.hold_source = None
    row.hold_offset_s = None
    row.hold_assignments = None
    row.lease_deadline = None
    session.commit()


def mark_assignment(session: Session, device_id: str, index: int, *, done: bool) -> None:
    """放回的一次性标记：写进 hold_assignments 的对应项，进程重启不丢。"""
    row = _find(session, device_id)
    if row is None or row.hold_assignments is None:
        return
    assignments = list(row.hold_assignments)
    if 0 <= index < len(assignments):
        assignments[index] = {**assignments[index], "done": done, "failed": not done}
        row.hold_assignments = assignments
        session.commit()


def set_order(session: Session, device_ids: list[str]) -> None:
    for position, device_id in enumerate(device_ids):
        row = _find(session, device_id)
        if row is not None:
            row.sort_order = position
    session.commit()


def get_clock_skew(session: Session, device_id: str) -> float:
    row = _find(session, device_id)
    return float(row.clock_skew_s) if row is not None else 0.0


def set_clock_skew(session: Session, device_id: str, skew_s: float) -> None:
    row = _find(session, device_id)
    if row is not None:
        row.clock_skew_s = skew_s
        session.commit()


def insert_link_event(
    session: Session,
    device_id: str,
    event: LinkEvent,
    occurred_at: datetime,
    reason: str = "",
) -> None:
    row = _find(session, device_id)
    if row is None:
        return
    session.add(DeviceLinkEvent(device_id=row.id, occurred_at=occurred_at, event=event, reason=reason))
    session.commit()


def link_events(session: Session, device_id: str, before: datetime) -> list[dict[str, str]]:
    """`before` 之前的全部上下线事件，按时间升序。

    必须给到窗口之前的事件：设备在窗口开始前已掉线、窗口内才上线的那段同样是离线。
    """
    row = _find(session, device_id)
    if row is None:
        return []
    events = session.scalars(
        select(DeviceLinkEvent)
        .where(DeviceLinkEvent.device_id == row.id, DeviceLinkEvent.occurred_at < before)
        .order_by(DeviceLinkEvent.occurred_at)
    ).all()
    return [{"occurred_at": format_rfc3339(item.occurred_at), "event": str(item.event)} for item in events]


def append_device_log(
    session: Session,
    device_id: str,
    kind: DeviceLogKind,
    content: str,
    uploaded_at: datetime,
) -> None:
    """落一份日志，并把该（设备，种类）超量的旧份裁掉。"""
    row = _find(session, device_id)
    if row is None:
        return
    session.add(DeviceLog(device_id=row.id, kind=kind, content=content, uploaded_at=uploaded_at))
    session.flush()
    keep_ids = session.scalars(
        select(DeviceLog.id)
        .where(DeviceLog.device_id == row.id, DeviceLog.kind == kind)
        .order_by(DeviceLog.id.desc())
        .limit(LOG_KEEP)
    ).all()
    session.execute(
        delete(DeviceLog).where(
            DeviceLog.device_id == row.id,
            DeviceLog.kind == kind,
            DeviceLog.id.not_in(keep_ids),
        )
    )
    session.commit()


def latest_device_log(session: Session, device_id: str, kind: DeviceLogKind) -> dict[str, str] | None:
    row = _find(session, device_id)
    if row is None:
        return None
    log = session.scalars(
        select(DeviceLog)
        .where(DeviceLog.device_id == row.id, DeviceLog.kind == kind)
        .order_by(DeviceLog.id.desc())
        .limit(1)
    ).one_or_none()
    if log is None:
        return None
    return {"content": log.content, "uploaded_at": format_rfc3339(log.uploaded_at)}


def as_record(row: Device) -> DeviceRecord:
    # capabilities 若被外部写成非数组（手改库、旧数据形状），按「未声明」处理而不是让
    # 一条查询崩掉——档案读取在守卫链上，读不出来等于设备完全不可用。
    caps = row.capabilities if isinstance(row.capabilities, list) else []
    record: DeviceRecord = {
        "device_id": row.id,
        "name": row.name,
        "trusted": bool(row.trusted),
        "retired": row.retired_at is not None,
        "app_version": row.app_version,
        "version_code": int(row.version_code),
        "os_version": row.os_version,
        "capabilities": list(caps),
        "online": False,
        "last_seen_at": format_rfc3339(row.last_seen_at) if row.last_seen_at else "",
        "last_ip": row.last_ip or "",
        "booted_at": format_rfc3339(row.booted_at) if row.booted_at else "",
        "clock_skew_s": float(row.clock_skew_s),
        "auto_close_time": str(row.auto_close_time or ""),
        "auto_open_time": str(row.auto_open_time or ""),
        "zone_offset_minutes": int(row.zone_offset_minutes) if row.zone_offset_minutes is not None else None,
        "facts_at": format_rfc3339(row.facts_at) if row.facts_at else "",
        "sort_order": int(row.sort_order),
    }
    return record


# ── 设备凭据 ──


def issue(
    session: Session,
    device_id: str,
    label: str = "",
) -> str | None:
    """签发一枚令牌，返回明文（只返回这一次）。设备不存在时返回 None。"""
    if session.get(Device, device_id) is None:
        return None
    token = generate_token()
    session.add(
        DeviceCredential(
            device_id=device_id,
            token_hash=hash_token(token),
            label=label,
        )
    )
    session.commit()
    return token


def list_for_device(session: Session, device_id: str) -> list[dict[str, object]]:
    """列出某设备的凭据元数据；明文不存库，所以这里没有 token 字段。

    生产路径暂无消费者：凭据事实的读者是派生测试——它是本域的读原语。
    """
    rows = session.scalars(
        select(DeviceCredential)
        .where(DeviceCredential.device_id == device_id, DeviceCredential.revoked_at.is_(None))
        .order_by(DeviceCredential.id)
    ).all()
    return [
        {
            "id": int(row.id),
            "label": row.label,
            "created_at": row.created_at,
        }
        for row in rows
    ]


def revoke_all(session: Session, device_id: str, now: datetime | None = None) -> int:
    """吊销该设备全部有效凭据，返回吊销条数。轮换前先调用它。"""
    stamp = now or utcnow()
    active_ids = session.scalars(
        select(DeviceCredential.id).where(
            DeviceCredential.device_id == device_id,
            DeviceCredential.revoked_at.is_(None),
        )
    ).all()
    if active_ids:
        session.execute(update(DeviceCredential).where(DeviceCredential.id.in_(active_ids)).values(revoked_at=stamp))
        session.commit()
    return len(active_ids)


def revoke(session: Session, token: str, now: datetime | None = None) -> bool:
    """按令牌吊销（运维手里只有明文，没有行 id）。"""
    row = session.scalars(
        select(DeviceCredential).where(DeviceCredential.token_hash == hash_token(token))
    ).one_or_none()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = now or utcnow()
    session.commit()
    return True


def authenticate_device(session: Session, token: str) -> str | None:
    """令牌有效则返回设备业务标识，否则 None。

    校验两件事：摘要存在、未吊销。凭据没有过期语义——生命周期就是
    签发→吊销（enroll 轮换即吊销重发），不存在"到点失效"的第三态。
    """
    row = session.execute(
        select(DeviceCredential, Device.id)
        .join(Device, DeviceCredential.device_id == Device.id)
        .where(DeviceCredential.token_hash == hash_token(token))
    ).first()
    if row is None:
        return None
    credential, business_id = row
    if credential.revoked_at is not None:
        return None
    return str(business_id)


__all__: list[str] = [
    "LOG_KEEP",
    "append_device_log",
    "as_record",
    "authenticate_device",
    "clear_hold",
    "get",
    "get_clock_skew",
    "get_hold",
    "insert_link_event",
    "issue",
    "latest_device_log",
    "link_events",
    "list_all",
    "list_for_device",
    "mark_assignment",
    "mark_hold_applied",
    "power_facts",
    "register",
    "revoke",
    "revoke_all",
    "set_clock_skew",
    "set_lease",
    "set_name",
    "set_order",
    "set_power_facts",
    "set_retired",
    "set_trusted",
    "start_hold",
    "touch_seen",
]
