"""设备档案、链路事件与设备日志的持久化。

与旧实现的契约一致：
- `register` 只覆盖版本、能力、时刻与开机时间，不动 name / trusted / sort_order
- 展示顺序以 `sort_order` 为准，同序回落到名字的自然排序
- 设备日志每个（设备，种类）只留最近 `LOG_KEEP` 份

区别在参数类型：新层接收带时区的 `datetime`，返回的记录仍把时间序列化成
RFC 3339 字符串，与路由层的既有形状保持一致。
"""

from __future__ import annotations

import re
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..core.clock import format_rfc3339
from ..models.enums import DeviceLogKind, LinkEvent
from ..models.identity import Device
from ..models.ops import DeviceLinkEvent, DeviceLog
from ..modules.devices.schemas import DeviceRecord

LOG_KEEP = 5

_NATURAL_SPLIT = re.compile(r"(\d+)")


def _natural_name_key(name: str) -> list[tuple[int, object]]:
    """名字的自然排序键：数字段按数值比（1F < 2F < 10F），其余按字面。"""
    return [(0, int(part)) if part.isdigit() else (1, part) for part in _NATURAL_SPLIT.split(name)]


def _find(session: Session, device_id: str) -> Device | None:
    return session.scalars(select(Device).where(Device.device_id == device_id)).one_or_none()


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
            device_id=device_id,
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
        "device_id": row.device_id,
        "name": row.name,
        "trusted": bool(row.trusted),
        "app_version": row.app_version,
        "version_code": int(row.version_code),
        "os_version": row.os_version,
        "capabilities": list(caps),
        "online": False,
        "last_seen_at": format_rfc3339(row.last_seen_at) if row.last_seen_at else "",
        "last_ip": row.last_ip or "",
        "booted_at": format_rfc3339(row.booted_at) if row.booted_at else "",
        "clock_skew_s": float(row.clock_skew_s),
        "sort_order": int(row.sort_order),
    }
    return record


__all__: list[str] = [
    "LOG_KEEP",
    "append_device_log",
    "as_record",
    "get",
    "get_clock_skew",
    "insert_link_event",
    "latest_device_log",
    "link_events",
    "list_all",
    "register",
    "set_clock_skew",
    "set_name",
    "set_order",
    "set_trusted",
    "touch_seen",
]
