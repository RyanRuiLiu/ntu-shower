"""门域：现态、状态流水、原始帧、会话与出水段。

读写原语。状态派生、会话判定、盲区推断属于业务逻辑，留在 `modules/doors/service`。
返回形状与旧实现一致：时间序列化成 RFC 3339，设备标识用业务字符串。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..contracts import DoorView
from ..core.clock import format_rfc3339, parse_rfc3339, seconds_between
from ..models.device import Device
from ..models.doors import DoorSnapshot, DoorState, DoorStateEvent
from ..models.enums import OCCUPIED_STATES, ObservedSpan, SegmentSource
from ..models.enums import DoorState as DoorStateEnum
from ..models.usage import UsageSegment, UsageSession

_RUN_WALK_LIMIT = 50


def _ts(value: datetime | None) -> str:
    return format_rfc3339(value) if value is not None else ""


def _live_view(row: DoorState, device_id: str) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "door_no": int(row.door_no),
        "state": str(row.state),
        "card_no": int(row.card_no),
        "cert_no": row.cert_no,
        "acc_no": row.acc_no,
        "open_time": _ts(row.open_time),
        "in_time": _ts(row.in_time),
        "close_time": _ts(row.close_time),
        "bath_time_s": int(row.bath_time_s),
        "status_raw": int(row.status_raw),
        "reported_at": _ts(row.reported_at),
    }


def _session_view(row: UsageSession, device_id: str) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "device_id": device_id,
        "door_no": int(row.door_no),
        "card_no": int(row.card_no),
        "cert_no": row.cert_no,
        "assigned_at": _ts(row.assigned_at),
        "assigned_source": str(row.assigned_source),
        "observed_span": str(row.observed_span),
        "device_flow_s": int(row.device_flow_s),
        "segments_complete": bool(row.segments_complete),
        "ended_at": _ts(row.ended_at) if row.ended_at else "",
        "last_seen_at": _ts(row.last_seen_at),
        "flow_s": int(row.flow_s),
    }


def _segment_view(row: UsageSegment, device_id: str) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "session_id": int(row.session_id),
        "device_id": device_id,
        "door_no": int(row.door_no),
        "opened_at": _ts(row.opened_at),
        "closed_at": _ts(row.closed_at) if row.closed_at else "",
        "flow_s": int(row.flow_s),
        "opened_raw": row.opened_raw,
        "closed_raw": row.closed_raw,
        "source": str(row.source) if row.source is not None else "",
    }


# ── 现态 ──


def get_all_live(session: Session, device_id: str) -> dict[int, dict[str, Any]]:
    rows = session.scalars(select(DoorState).where(DoorState.device_id == device_id)).all()
    return {int(row.door_no): _live_view(row, device_id) for row in rows}


def get_door_live(session: Session, device_id: str, door_no: int) -> dict[str, Any] | None:
    row = session.scalars(
        select(DoorState).where(DoorState.device_id == device_id, DoorState.door_no == door_no)
    ).one_or_none()
    return _live_view(row, device_id) if row is not None else None


def get_occupied_by_cert(session: Session, device_id: str, cert_no: str) -> dict[str, Any] | None:
    occupied = [DoorStateEnum(name) for name in OCCUPIED_STATES]
    row = session.scalars(
        select(DoorState)
        .where(
            DoorState.device_id == device_id,
            DoorState.cert_no == cert_no,
            DoorState.state.in_(occupied),
        )
        .limit(1)
    ).first()
    return _live_view(row, device_id) if row is not None else None


def upsert_door_live(
    session: Session,
    device_id: str,
    door_no: int,
    state: DoorStateEnum,
    *,
    card_no: int = 0,
    cert_no: str = "",
    acc_no: str = "",
    open_time: datetime | None = None,
    in_time: datetime | None = None,
    close_time: datetime | None = None,
    bath_time_s: int = 0,
    status_raw: int = 0,
    reported_at: datetime,
) -> None:
    """写下这个门的现态。时间字段一律是服务端轴的换算结果。"""
    row = session.scalars(
        select(DoorState).where(DoorState.device_id == device_id, DoorState.door_no == door_no)
    ).one_or_none()
    if row is None:
        row = DoorState(device_id=device_id, door_no=door_no)
        session.add(row)
    row.state = state
    row.card_no = card_no
    row.cert_no = cert_no
    row.acc_no = acc_no
    row.open_time = open_time
    row.in_time = in_time
    row.close_time = close_time
    row.bath_time_s = bath_time_s
    row.status_raw = status_raw
    row.reported_at = reported_at
    session.commit()


# ── 原始帧与状态流水 ──


def insert_snapshot(
    session: Session,
    device_id: str,
    snapshot_id: str,
    seq: int | None,
    source: str,
    captured_at: datetime | None,
    device_clock: str | None,
    received_at: datetime,
    doors: list[dict[str, Any]],
) -> bool:
    """落一帧原始数据。返回「此刻是否已在库中」。

    同一 `snapshot_id` 重复投递只留第一份，但重复**算已落库**（True）：设备在收到 ack
    前会重发同一条，重发与首投对回执是同一件事，照常让设备清发件箱。False 只留给
    「帧没有落下去」一种情形——设备不在册（外键落不了行），调用方据此扣下回执。
    """
    exists = session.scalars(
        select(DoorSnapshot.id).where(DoorSnapshot.device_id == device_id, DoorSnapshot.snapshot_id == snapshot_id)
    ).one_or_none()
    if exists is not None:
        return True
    session.add(
        DoorSnapshot(
            device_id=device_id,
            snapshot_id=snapshot_id,
            seq=seq,
            source=source,
            captured_at=captured_at,
            device_clock=device_clock,
            received_at=received_at,
            doors=doors,
        )
    )
    session.commit()
    return True


def insert_event(
    session: Session,
    occurred_at: datetime,
    device_id: str,
    door_no: int,
    from_state: DoorStateEnum,
    to_state: DoorStateEnum,
    card_no: int,
    cert_no: str,
    bath_time_s: int,
) -> int:
    """记一次状态迁移，返回行 id（播报事件的幂等身份）。"""
    row = DoorStateEvent(
        occurred_at=occurred_at,
        device_id=device_id,
        door_no=door_no,
        from_state=from_state,
        to_state=to_state,
        card_no=card_no,
        cert_no=cert_no,
        bath_time_s=bath_time_s,
    )
    session.add(row)
    session.commit()
    return int(row.id)


# ── 会话 ──


def get_open_session(session: Session, device_id: str, door_no: int) -> dict[str, Any] | None:
    row = session.scalars(
        select(UsageSession)
        .where(
            UsageSession.device_id == device_id,
            UsageSession.door_no == door_no,
            UsageSession.ended_at.is_(None),
        )
        .order_by(UsageSession.id.desc())
        .limit(1)
    ).first()
    return _session_view(row, device_id) if row is not None else None


def get_session(session: Session, session_id: int) -> dict[str, Any] | None:
    row = session.get(UsageSession, session_id)
    if row is None:
        return None
    business = session.scalar(select(Device.id).where(Device.id == row.device_id))
    return _session_view(row, business or "")


def open_session(
    session: Session,
    device_id: str,
    door_no: int,
    card_no: int,
    cert_no: str,
    assigned_at: datetime,
    assigned_source: str,
) -> int:
    row = UsageSession(
        device_id=device_id,
        door_no=door_no,
        card_no=card_no,
        cert_no=cert_no,
        assigned_at=assigned_at,
        assigned_source=assigned_source,
        last_seen_at=assigned_at,
    )
    session.add(row)
    session.commit()
    return int(row.id)


def touch_session(
    session: Session,
    session_id: int,
    *,
    seen: bool,
    at: datetime,
    flow_s: int,
    device_flow_s: int,
    gap: bool,
) -> None:
    """推进里程碑。字段只增不减，重复调用无副作用。"""
    row = session.get(UsageSession, session_id)
    if row is None:
        return
    if seen:
        row.last_seen_at = at
    row.flow_s = max(int(row.flow_s), flow_s)
    row.device_flow_s = max(int(row.device_flow_s), device_flow_s)
    if gap:
        row.observed_span = ObservedSpan.GAP
    session.commit()


def close_session(session: Session, session_id: int, ended_at: datetime, segments_complete: bool) -> None:
    row = session.get(UsageSession, session_id)
    if row is None:
        return
    row.ended_at = ended_at
    row.segments_complete = segments_complete
    session.commit()


def get_active_by_cert(session: Session, device_id: str, cert_no: str) -> dict[str, Any] | None:
    row = session.scalars(
        select(UsageSession)
        .where(
            UsageSession.device_id == device_id,
            UsageSession.cert_no == cert_no,
            UsageSession.ended_at.is_(None),
        )
        .order_by(UsageSession.id.desc())
        .limit(1)
    ).first()
    return _session_view(row, device_id) if row is not None else None


def list_open_sessions_of(session: Session, device_id: str) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(UsageSession)
        .where(UsageSession.device_id == device_id, UsageSession.ended_at.is_(None))
        .order_by(UsageSession.door_no)
    ).all()
    return [_session_view(row, device_id) for row in rows]


def run_started_at(session: Session, device_id: str, door_no: int, cert_no: str, gap_s: int) -> str:
    """这一串连续占用里最早那一场的分配时刻；没有则空串。"""
    rows = session.scalars(
        select(UsageSession)
        .where(
            UsageSession.device_id == device_id,
            UsageSession.door_no == door_no,
            UsageSession.cert_no == cert_no,
        )
        .order_by(UsageSession.assigned_at.desc())
        .limit(_RUN_WALK_LIMIT)
    ).all()
    started = ""
    newer_assigned = ""
    for index, row in enumerate(rows):
        if index == 0:
            started = newer_assigned = _ts(row.assigned_at)
            continue
        previous_ended = _ts(row.ended_at) if row.ended_at else ""
        if not previous_ended:
            break
        if seconds_between(previous_ended, newer_assigned) > gap_s:
            break
        started = newer_assigned = _ts(row.assigned_at)
    return started


# ── 出水段 ──


def get_open_segment(session: Session, device_id: str, door_no: int) -> dict[str, Any] | None:
    row = session.scalars(
        select(UsageSegment)
        .where(
            UsageSegment.device_id == device_id,
            UsageSegment.door_no == door_no,
            UsageSegment.closed_at.is_(None),
        )
        .order_by(UsageSegment.id.desc())
        .limit(1)
    ).first()
    return _segment_view(row, device_id) if row is not None else None


def latest_segment_opened_at(session: Session, device_id: str, door_no: int) -> str:
    """当前未结束会话里最近一段的开阀时刻；没有则空串。"""
    value = session.scalar(
        select(UsageSegment.opened_at)
        .join(UsageSession, UsageSession.id == UsageSegment.session_id)
        .where(
            UsageSession.device_id == device_id,
            UsageSegment.door_no == door_no,
            UsageSession.ended_at.is_(None),
        )
        .order_by(UsageSegment.opened_at.desc(), UsageSegment.id.desc())
        .limit(1)
    )
    return _ts(value) if value is not None else ""


def open_segment(
    session: Session,
    session_id: int,
    device_id: str,
    door_no: int,
    opened_at: datetime,
    opened_raw: str,
) -> int:
    row = UsageSegment(
        session_id=session_id,
        device_id=device_id,
        door_no=door_no,
        opened_at=opened_at,
        opened_raw=opened_raw,
    )
    session.add(row)
    session.commit()
    return int(row.id)


def close_segment(
    session: Session,
    segment_id: int,
    closed_at: datetime,
    flow_s: int,
    source: SegmentSource,
    closed_raw: str,
) -> None:
    row = session.get(UsageSegment, segment_id)
    if row is None:
        return
    row.closed_at = closed_at
    row.flow_s = flow_s
    row.source = source
    row.closed_raw = closed_raw
    session.commit()


def add_derived_segment(
    session: Session,
    session_id: int,
    device_id: str,
    door_no: int,
    opened_at: datetime,
    closed_at: datetime,
    flow_s: int,
    closed_raw: str,
) -> int:
    """记一段推出来的出水（冷启动接手用），`source` 固定为 `derived`。"""
    row = UsageSegment(
        session_id=session_id,
        device_id=device_id,
        door_no=door_no,
        opened_at=opened_at,
        closed_at=closed_at,
        flow_s=flow_s,
        closed_raw=closed_raw,
        source=SegmentSource.DERIVED,
    )
    session.add(row)
    session.commit()
    return int(row.id)


def segments_of_session(session: Session, session_id: int) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(UsageSegment)
        .where(UsageSegment.session_id == session_id)
        .order_by(UsageSegment.opened_at, UsageSegment.id)
    ).all()
    result: list[dict[str, Any]] = []
    for row in rows:
        result.append(_segment_view(row, str(row.device_id)))
    return result


def latest_usage_by_door(session: Session) -> dict[tuple[str, int], dict[str, Any]]:
    """每台设备每个喷头最后一次**完整**使用（只取已关阀的段）。"""
    latest_ids = (
        select(func.max(UsageSegment.id))
        .where(UsageSegment.closed_at.is_not(None))
        .group_by(UsageSegment.device_id, UsageSegment.door_no)
        .scalar_subquery()
    )
    rows = session.execute(
        select(UsageSegment, UsageSession.cert_no, Device.id)
        .join(UsageSession, UsageSession.id == UsageSegment.session_id)
        .join(Device, Device.id == UsageSegment.device_id)
        .where(UsageSegment.id.in_(latest_ids))
    ).all()
    return {
        (device_id, int(segment.door_no)): {
            "device_id": device_id,
            "door_no": int(segment.door_no),
            "opened_at": _ts(segment.opened_at),
            "flow_s": int(segment.flow_s),
            "cert_no": cert_no,
        }
        for segment, cert_no, device_id in rows
    }


def list_sessions(session: Session, device_id: str) -> list[dict[str, Any]]:
    """这台设备的全部占用会话，按 id 升序（读模型与统计/核对共用）。"""
    rows = session.scalars(
        select(UsageSession).where(UsageSession.device_id == device_id).order_by(UsageSession.id)
    ).all()
    return [_session_view(row, device_id) for row in rows]


def list_events(session: Session, device_id: str, door_no: int | None = None) -> list[dict[str, Any]]:
    """这台设备（可再按门过滤）的状态迁移流水，按 id 升序。"""
    statement = select(DoorStateEvent).where(DoorStateEvent.device_id == device_id)
    if door_no is not None:
        statement = statement.where(DoorStateEvent.door_no == door_no)
    rows = session.scalars(statement.order_by(DoorStateEvent.id)).all()
    return [
        {
            "door_no": int(row.door_no),
            "from_state": str(row.from_state),
            "to_state": str(row.to_state),
            "card_no": int(row.card_no),
            "cert_no": str(row.cert_no),
            "bath_time_s": int(row.bath_time_s),
            "occurred_at": _ts(row.occurred_at),
        }
        for row in rows
    ]


def list_snapshots(session: Session, device_id: str) -> list[dict[str, Any]]:
    """这台设备的原始帧：设备说的原话按帧 id 留底，doors 是落库时的原样载荷。"""
    rows = session.scalars(
        select(DoorSnapshot).where(DoorSnapshot.device_id == device_id).order_by(DoorSnapshot.id)
    ).all()
    return [
        {
            "snapshot_id": str(row.snapshot_id),
            "seq": int(row.seq) if row.seq is not None else None,
            "source": str(row.source),
            "captured_at": _ts(row.captured_at),
            "received_at": _ts(row.received_at),
            "device_clock": str(row.device_clock or ""),
            "doors": row.doors,
        }
        for row in rows
    ]


# ── 窗口读 ──
# 窗口条件在这里换算成 datetime，出去的行仍经统一视图（时刻是 RFC 3339 文本），
# 调用方不碰存储形状。


def door_count(session: Session, device_id: str) -> int:
    """这台设备上报过现态的喷头数。"""
    return int(session.scalar(select(func.count()).select_from(DoorState).where(DoorState.device_id == device_id)) or 0)


def door_count_in_state(session: Session, device_id: str, state: str) -> int:
    """这台设备处于指定状态的喷头数。"""
    return int(
        session.scalar(
            select(func.count())
            .select_from(DoorState)
            .where(DoorState.device_id == device_id, DoorState.state == state)
        )
        or 0
    )


def last_closed_by_door(session: Session, device_id: str, before: str) -> list[dict[str, Any]]:
    """每扇门在 `before` 之前的最后一次关阀（段联会话取占用者）。"""
    high = _parse_moment(before)
    ranked = (
        select(
            UsageSegment.door_no,
            UsageSegment.closed_at,
            UsageSession.cert_no,
            func.row_number()
            .over(partition_by=UsageSegment.door_no, order_by=UsageSegment.closed_at.desc())
            .label("rn"),
        )
        .join(UsageSession, UsageSession.id == UsageSegment.session_id)
        .where(UsageSegment.device_id == device_id, UsageSegment.closed_at.is_not(None), UsageSegment.closed_at < high)
        .subquery()
    )
    rows = session.execute(
        select(ranked.c.door_no, ranked.c.closed_at, ranked.c.cert_no)
        .where(ranked.c.rn == 1)
        .order_by(ranked.c.door_no)
    ).all()
    return [
        {"door_no": int(door_no), "closed_at": _ts(closed_at), "cert_no": str(cert_no)}
        for door_no, closed_at, cert_no in rows
    ]


def _parse_moment(value: str) -> datetime:
    """窗口端点的转换：统计层的窗口端点必然是合法 RFC 3339，坏值在调用方就挡了。"""
    moment = parse_rfc3339(value)
    if moment is None:
        message = f"窗口端点不是合法时刻：{value!r}"
        raise ValueError(message)
    return moment


def list_doors(session: Session, device_id: str | None = None) -> list[DoorView]:
    """喷头现态列表，带上次完整使用的时刻、时长与凭证。"""
    statement = select(DoorState, Device.id).join(Device, Device.id == DoorState.device_id)
    if device_id:
        statement = statement.where(Device.id == device_id)
    statement = statement.order_by(Device.id, DoorState.door_no)
    usage = latest_usage_by_door(session)
    views: list[DoorView] = []
    for row, device_id in session.execute(statement).all():
        last = usage.get((device_id, int(row.door_no)))
        live = _live_view(row, device_id)
        view: DoorView = {
            "device_id": live["device_id"],
            "door_no": live["door_no"],
            "state": live["state"],
            "card_no": live["card_no"],
            "cert_no": live["cert_no"],
            "acc_no": live["acc_no"],
            "open_time": live["open_time"],
            "in_time": live["in_time"],
            "close_time": live["close_time"],
            "bath_time_s": live["bath_time_s"],
            "status_raw": live["status_raw"],
            "reported_at": live["reported_at"],
            "last_used_at": "" if last is None else str(last["opened_at"]),
            "last_used_s": 0 if last is None else int(last["flow_s"]),
            "last_used_cert": "" if last is None else str(last["cert_no"]),
        }
        views.append(view)
    return views


__all__: list[str] = [
    "add_derived_segment",
    "close_segment",
    "close_session",
    "get_active_by_cert",
    "get_all_live",
    "get_door_live",
    "get_occupied_by_cert",
    "get_open_segment",
    "get_open_session",
    "get_session",
    "insert_event",
    "insert_snapshot",
    "latest_segment_opened_at",
    "latest_usage_by_door",
    "list_doors",
    "list_events",
    "list_open_sessions_of",
    "list_sessions",
    "list_snapshots",
    "open_segment",
    "open_session",
    "run_started_at",
    "segments_of_session",
    "touch_session",
    "upsert_door_live",
]
