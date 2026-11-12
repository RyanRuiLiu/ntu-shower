"""语音域：素材、单元映射、规则、投递。

只做读写。规则匹配的优先级与时段判断属于业务逻辑，留在 `modules/voice`：
持久化层不该知道「哪条规则更紧」。
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.clock import format_rfc3339
from ..core.logging import log_event
from ..models.identity import Device
from ..models.voice import VoiceAsset, VoiceDelivery, VoiceRule, VoiceUnit


def _device_pk(session: Session, device_id: str) -> int | None:
    return session.scalars(select(Device.id).where(Device.device_id == device_id)).one_or_none()


def _dumps(value: list[Any] | None) -> str | None:
    """JSON 列 → 字符串（API 契约里 segments 是 JSON 文本）。"""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _asset_view(row: VoiceAsset) -> dict[str, Any]:
    return {
        "asset_id": row.asset_id,
        "name": row.name,
        "digit": row.digit,
        "duration_ms": int(row.duration_ms),
        "size_bytes": int(row.size_bytes),
        "created_at": format_rfc3339(row.created_at),
    }


def _rule_view(row: VoiceRule) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "devices": list(row.devices),
        "certs": list(row.certs),
        "events": list(row.events),
        "time_windows": list(row.time_windows),
        "exempt": list(row.exempt),
        "segments": list(row.segments),
        "volume": int(row.volume),
        "enabled": bool(row.enabled),
        "created_at": format_rfc3339(row.created_at),
        "updated_at": format_rfc3339(row.updated_at),
    }


def _delivery_view(row: VoiceDelivery) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "event_key": row.event_key,
        "rule_id": int(row.rule_id),
        "device_id": row.device_key,
        "cert_no": row.cert_no,
        "event": row.event,
        "door_no": int(row.door_no),
        "segments": row.segments,
        "segment_count": int(row.segment_count),
        "duration_ms": int(row.duration_ms),
        "occurred_at": format_rfc3339(row.occurred_at),
        "dispatched_at": format_rfc3339(row.dispatched_at) if row.dispatched_at else "",
        "done_at": format_rfc3339(row.done_at) if row.done_at else "",
        "status": row.status,
        "detail": row.detail,
        "downloaded": bool(row.downloaded),
    }


# ── 素材 ──


def list_assets(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(select(VoiceAsset).order_by(VoiceAsset.asset_id)).all()
    return [_asset_view(row) for row in rows]


def get_asset(session: Session, asset_id: str) -> dict[str, Any] | None:
    row = session.scalars(select(VoiceAsset).where(VoiceAsset.asset_id == asset_id)).one_or_none()
    return _asset_view(row) if row is not None else None


def get_asset_by_name(session: Session, name: str) -> dict[str, Any] | None:
    row = session.scalars(select(VoiceAsset).where(VoiceAsset.name == name)).one_or_none()
    return _asset_view(row) if row is not None else None


def get_asset_by_digit(session: Session, digit: int) -> dict[str, Any] | None:
    row = session.scalars(select(VoiceAsset).where(VoiceAsset.digit == digit)).one_or_none()
    return _asset_view(row) if row is not None else None


def insert_asset(
    session: Session,
    asset_id: str,
    name: str,
    digit: int | None,
    duration_ms: int,
    size_bytes: int,
) -> None:
    session.add(
        VoiceAsset(
            asset_id=asset_id,
            name=name,
            digit=digit,
            duration_ms=duration_ms,
            size_bytes=size_bytes,
        )
    )
    session.commit()


def rename_asset(session: Session, asset_id: str, name: str) -> bool:
    row = session.scalars(select(VoiceAsset).where(VoiceAsset.asset_id == asset_id)).one_or_none()
    if row is None:
        return False
    row.name = name
    session.commit()
    return True


def delete_asset(session: Session, asset_id: str) -> bool:
    row = session.scalars(select(VoiceAsset).where(VoiceAsset.asset_id == asset_id)).one_or_none()
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def asset_rows_by_ids(session: Session, asset_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not asset_ids:
        return {}
    rows = session.scalars(select(VoiceAsset).where(VoiceAsset.asset_id.in_(asset_ids))).all()
    return {row.asset_id: _asset_view(row) for row in rows}


# ── 单元映射 ──


def unit_rows(session: Session) -> dict[str, dict[str, Any]]:
    """单位 → 素材完整档案（联表；调用方按素材形状消费，与数字素材同构）。"""
    rows = session.execute(
        select(VoiceUnit, VoiceAsset).join(VoiceAsset, VoiceUnit.asset_id == VoiceAsset.asset_id)
    ).all()
    return {unit.unit: _asset_view(asset) for unit, asset in rows}


def get_unit_by_asset(session: Session, asset_id: str) -> dict[str, str] | None:
    row = session.scalars(select(VoiceUnit).where(VoiceUnit.asset_id == asset_id)).one_or_none()
    return {"unit": row.unit, "asset_id": row.asset_id} if row is not None else None


def insert_unit(session: Session, unit: str, asset_id: str) -> None:
    session.merge(VoiceUnit(unit=unit, asset_id=asset_id))
    session.commit()


def delete_unit(session: Session, asset_id: str) -> None:
    row = session.scalars(select(VoiceUnit).where(VoiceUnit.asset_id == asset_id)).one_or_none()
    if row is not None:
        session.delete(row)
        session.commit()


# ── 规则 ──


def insert_rule(
    session: Session,
    devices: list[str],
    certs: list[str],
    events: list[str],
    time_windows: list[dict[str, Any]],
    exempt: list[str],
    segments: list[dict[str, Any]],
    volume: int,
    enabled: bool,
    created_at: datetime,
) -> int:
    row = VoiceRule(
        devices=devices,
        certs=certs,
        events=events,
        time_windows=time_windows,
        exempt=exempt,
        segments=segments,
        volume=volume,
        enabled=enabled,
        created_at=created_at,
        updated_at=created_at,
    )
    session.add(row)
    session.commit()
    return int(row.id)


def update_rule(
    session: Session,
    rule_id: int,
    devices: list[str],
    certs: list[str],
    events: list[str],
    time_windows: list[dict[str, Any]],
    exempt: list[str],
    segments: list[dict[str, Any]],
    volume: int,
    enabled: bool,
    updated_at: datetime,
) -> bool:
    row = session.get(VoiceRule, rule_id)
    if row is None:
        return False
    row.devices = devices
    row.certs = certs
    row.events = events
    row.time_windows = time_windows
    row.exempt = exempt
    row.segments = segments
    row.volume = volume
    row.enabled = enabled
    row.updated_at = updated_at
    session.commit()
    return True


def set_enabled(session: Session, rule_id: int, enabled: bool, updated_at: datetime) -> bool:
    row = session.get(VoiceRule, rule_id)
    if row is None:
        return False
    row.enabled = enabled
    row.updated_at = updated_at
    session.commit()
    return True


def get_rule(session: Session, rule_id: int) -> dict[str, Any] | None:
    row = session.get(VoiceRule, rule_id)
    return _rule_view(row) if row is not None else None


def list_rules(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(select(VoiceRule).order_by(VoiceRule.id)).all()
    return [_rule_view(row) for row in rows]


def list_enabled_rules(session: Session) -> list[dict[str, Any]]:
    rows = session.scalars(select(VoiceRule).where(VoiceRule.enabled.is_(True)).order_by(VoiceRule.id)).all()
    return [_rule_view(row) for row in rows]


def delete_rule(session: Session, rule_id: int) -> bool:
    row = session.get(VoiceRule, rule_id)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


# ── 投递 ──


def claim_delivery(
    session: Session,
    event_key: str,
    rule_id: int,
    device_id: str,
    door_no: int,
    cert_no: str,
    event: str,
    segments: str,
    segment_count: int,
    duration_ms: int,
    occurred_at: datetime,
    trace_id: str,
    created_at: datetime,
) -> bool:
    """认领一次投递。`event_key` 已存在时返回 False——幂等靠唯一约束。"""
    existing = session.scalars(select(VoiceDelivery.id).where(VoiceDelivery.event_key == event_key)).one_or_none()
    if existing is not None:
        return False
    session.add(
        VoiceDelivery(
            event_key=event_key,
            rule_id=rule_id,
            device_id=_device_pk(session, device_id),
            device_key=device_id,
            door_no=door_no,
            cert_no=cert_no,
            event=event,
            segments=segments,
            segment_count=segment_count,
            duration_ms=duration_ms,
            occurred_at=occurred_at,
            status="pending",
            trace_id=trace_id,
            created_at=created_at,
        )
    )
    session.commit()
    return True


def finish_delivery(
    session: Session,
    event_key: str,
    dispatched_at: datetime,
    status: str,
    detail: str,
    done_at: datetime,
    downloaded: bool,
) -> None:
    row = session.scalars(select(VoiceDelivery).where(VoiceDelivery.event_key == event_key)).one_or_none()
    if row is None:
        return
    row.dispatched_at = dispatched_at
    row.status = status
    row.detail = detail
    row.done_at = done_at
    row.downloaded = downloaded
    session.commit()


def list_deliveries(session: Session, limit: int) -> list[dict[str, Any]]:
    rows = session.scalars(select(VoiceDelivery).order_by(VoiceDelivery.id.desc()).limit(limit)).all()
    return [_delivery_view(row) for row in rows]


__all__: list[str] = [
    "asset_rows_by_ids",
    "claim_delivery",
    "delete_asset",
    "delete_rule",
    "delete_unit",
    "finish_delivery",
    "get_asset",
    "get_asset_by_digit",
    "get_asset_by_name",
    "get_rule",
    "get_unit_by_asset",
    "insert_asset",
    "insert_rule",
    "insert_unit",
    "list_assets",
    "list_deliveries",
    "list_enabled_rules",
    "list_rules",
    "rename_asset",
    "set_enabled",
    "unit_rows",
    "update_rule",
]


def matching_rules(
    session: Session,
    device_id: str,
    cert_no: str,
    event: str,
    local: datetime,
) -> list[dict[str, Any]]:
    """一次事件命中的启用规则，按优先级排序；完全同优先级时拒绝替用户挑一条。

    优先级比较与坏行处置见 modules.voice.service.rule_priority 与 time_rules——
    本函数只负责查询、命中判定与排序。
    """
    from ..modules.voice import time_windows as time_rules
    from ..modules.voice.priority import rule_priority

    hits: list[tuple[tuple[int, int, int, int, int, int], dict[str, Any]]] = []
    for row in session.scalars(select(VoiceRule).where(VoiceRule.enabled.is_(True))).all():
        view = _rule_view(row)
        if event not in view["events"]:
            continue
        devices = view["devices"]
        certs = view["certs"]
        if not ("*" in devices or device_id in devices):
            continue
        if "*" in certs:
            if cert_no in view["exempt"]:
                continue
        elif cert_no not in certs:
            continue
        try:
            windows = time_rules.parse_json(row.time_windows)
        except (TypeError, ValueError) as exc:
            # TypeError 与 ValueError 都来自解析：前者是列内容不是数组（库被外部改坏），
            # 后者是时段值非法。两者同属"这条规则的时段不可用"，一并跳过并留痕。
            log_event(
                "ERROR",
                "voice.rule-time-invalid",
                "规则时段格式非法，跳过",
                rule_id=int(row.id),
                error=str(exc),
            )
            continue
        if not time_rules.match(windows, local):
            log_event(
                "DEBUG",
                "voice.rule-time-filtered",
                "规则不在生效时段内",
                device_id=device_id,
                event=event,
                rule_id=int(row.id),
                local_time=local.isoformat(timespec="minutes"),
            )
            continue
        hits.append((rule_priority(devices, certs, windows), view))
    hits.sort(key=lambda item: item[0], reverse=True)
    if len(hits) >= 2 and hits[0][0] == hits[1][0]:
        log_event(
            "ERROR",
            "voice.rule-ambiguous",
            "同优先级规则同时命中，拒绝随机选择",
            device_id=device_id,
            event=event,
            local_time=local.isoformat(timespec="minutes"),
            rule_ids=[int(hits[0][1]["id"]), int(hits[1][1]["id"])],
        )
        return []
    return [row for _priority, row in hits]
