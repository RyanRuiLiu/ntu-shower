"""事件 → 规则匹配：一次设备事件命中哪条启用规则。

从 persistence.matching_rules 迁入（2026-11 审计）：时段解析、优先级排序与歧义
拒绝是业务规则——「哪条规则更紧」不是存储的事。持久化层只负责把启用规则行交出
来（repository.list_enabled_rules），匹配是纯读判定，供 service._dispatch_event
调用。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import log_event
from app.modules.voice import crud as repository

from . import time_windows as time_rules
from .priority import rule_priority


def matching_rules(
    session: Session,
    device_id: str,
    cert_no: str,
    event: str,
    local: datetime,
) -> list[dict[str, Any]]:
    """一次事件命中的启用规则，按优先级排序；完全同优先级时拒绝替用户挑一条。"""
    hits: list[tuple[tuple[int, int, int, int, int, int], dict[str, Any]]] = []
    for view in repository.list_enabled_rules(session):
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
            windows = time_rules.parse_json(view["time_windows"])
        except (TypeError, ValueError) as exc:
            # TypeError 与 ValueError 都来自解析：前者是列内容不是数组（库被外部改坏），
            # 后者是时段值非法。两者同属"这条规则的时段不可用"，一并跳过并留痕。
            log_event(
                "ERROR",
                "voice.rule-time-invalid",
                "规则时段格式非法，跳过",
                rule_id=int(view["id"]),
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
                rule_id=int(view["id"]),
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
