"""测试直读门域的短会话包装：喂一帧、读一行。

门域服务已改为显式 session 注入（与设备域同批定稿的会话三入口）；测试侧的
喂帧与直读统一经这里，避免每个测试文件各写一遍 `session_scope` 包裹。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.db.session import session_scope
from app.persistence import doors as repository


def feed(device_id: str, payload: dict[str, Any]) -> object:
    """喂一帧给 process_snapshot：短会话包住，返回其 SnapshotResult。

    设备不在册时先登记：线上帧只来自已握手在册的设备，直喂的测试补齐同一前提。
    已在册的不动——信任位等档案字段归测试自己管（有「未信任设备」的场景）。
    """
    from app.models.identity import Device
    from app.modules.doors import service

    from . import device_link

    with session_scope() as session:
        registered = session.scalar(select(Device.id).where(Device.device_id == device_id)) is not None
    if not registered:
        device_link.register_trusted_device(device_id)
    with session_scope() as session:
        return service.process_snapshot(session, device_id, payload)


def list_sessions(device_id: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        return repository.list_sessions(session, device_id)


def list_events(device_id: str, door_no: int | None = None) -> list[dict[str, Any]]:
    with session_scope() as session:
        return repository.list_events(session, device_id, door_no)


def list_segments_of(session_id: int) -> list[dict[str, Any]]:
    with session_scope() as session:
        return repository.segments_of_session(session, session_id)


def list_snapshots(device_id: str) -> list[dict[str, Any]]:
    with session_scope() as session:
        return repository.list_snapshots(session, device_id)


def clear_live(device_id: str) -> None:
    """清掉这台设备的现态行：模拟「会话还在、门的现态却没了」这类残缺现场。"""
    from sqlalchemy import delete

    from app.models.doors import DoorState as DoorStateRow
    from app.models.identity import Device

    with session_scope() as session:
        device_pk = session.scalar(select(Device.id).where(Device.device_id == device_id))
        if device_pk is not None:
            session.execute(delete(DoorStateRow).where(DoorStateRow.device_id == device_pk))
            session.commit()


def get_all_live(device_id: str) -> dict[int, dict[str, Any]]:
    with session_scope() as session:
        return repository.get_all_live(session, device_id)


def get_door_live(device_id: str, door_no: int) -> dict[str, Any] | None:
    with session_scope() as session:
        return repository.get_door_live(session, device_id, door_no)


def get_open_session(device_id: str, door_no: int) -> dict[str, Any] | None:
    with session_scope() as session:
        return repository.get_open_session(session, device_id, door_no)


def latest_usage_by_door() -> dict[tuple[str, int], dict[str, Any]]:
    with session_scope() as session:
        return repository.latest_usage_by_door(session)


def list_doors(device_id: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as session:
        return repository.list_doors(session, device_id)
