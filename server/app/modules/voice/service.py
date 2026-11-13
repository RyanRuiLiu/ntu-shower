"""固定音频素材、分段规则、事件投递与设备预下载。

## 唯一判定链

`handle_events` 只做一次「时效 → 匹配 → 解析分段 → 认领 → 下发」。规则按
「设备/学号/时段」匹配：前两维具体压过通配，时段只在前面完全相同时参与，
且更短的时段更紧；同一事件只认领一次。

规则不再保存文本或音色：`segments` 是素材片段、喷头号/学号数字变量与停顿的序列。
服务端按事件值解析成设备可顺序播放的片段清单，设备只负责下载缓存、等浴室提示音
结束、按顺序播放。规则保存后向在线目标设备预下载素材；事件到来时通常直接命中缓存。
"""

import asyncio
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from sqlalchemy.orm import Session

from app.contracts import CommandOutcome
from app.core import actions
from app.core.clock import now_rfc3339, parse_rfc3339, to_local
from app.core.logging import log_event, trace_id_var
from app.core.problems import (
    DEVICE_NOT_FOUND,
    INVALID_VOICE_RULE,
    VOICE_ASSET_IN_USE,
    VOICE_ASSET_NOT_FOUND,
    VOICE_RULE_NOT_FOUND,
    ApiProblemError,
)
from app.db.base import utcnow
from app.modules.config import store as config_store
from app.modules.devices import actions as device_actions
from app.modules.devices import service as devices_service
from app.modules.doors.events import DoorEvent, DoorEventKind
from app.persistence import voice as repository

from . import audio, numbers, schemas
from . import time_windows as time_rules
from .priority import rule_priority

#: 预下载命令只让设备排入后台队列，不等待实际下载。预留 0.1 Mbps 链路下
#: 命令帧与回执的往返时间；它走独立通道，超时只影响后续预下载，不阻塞播放。
VOICE_PREPARE_TIMEOUT_S = 10.0
PLAY_TIMEOUT_MIN_S = 30.0
#: 播放预算之外的余量：浴室提示音等待最长 30 秒，再留音量设置/恢复与调度开销。
PLAY_TIMEOUT_MARGIN_S = 60.0
#: 设备在播放前最多花多长时间把缺失素材下载齐。0.1 Mbps 链路上，一条 30 秒的
#: 128kbps 素材约 38 秒；60 秒留出重试与协议开销，又不让一条命令无限占住语音队列。
PLAY_DOWNLOAD_BUDGET_MS = 60_000
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

MANUAL_EVENT_PREFIX = "manual:"

#: 「全部学号」规则试听/手动推送时用的示例学号：数字变量要能念出内容，
#: 不能用非数字字符串把 cert_no 段跳过。
SAMPLE_CERT_NO = "2026000003"

_tasks: set[asyncio.Task[None]] = set()


class InvalidVoiceRuleError(ValueError):
    """规则/分段不合法；路由转 422。"""


class InvalidVoiceAssetError(ValueError):
    """上传音频不合法；路由转 422。"""


@dataclass(frozen=True)
class ClipPlan:
    clip_id: str
    sha256: str
    size_bytes: int
    duration_ms: int


@dataclass(frozen=True)
class PausePlan:
    ms: int


PlanItem = ClipPlan | PausePlan


def _plan_json(plan: list[PlanItem]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in plan:
        if isinstance(item, ClipPlan):
            result.append({"kind": "clip", **_clip_payload(item)})
        else:
            result.append({"kind": "pause", "ms": item.ms})
    return result


def _reject(reason: str) -> NoReturn:
    raise InvalidVoiceRuleError(reason)


def _reject_asset(reason: str) -> NoReturn:
    raise InvalidVoiceAssetError(reason)


# ── 素材 ──


def asset_url(asset_id: str) -> str:
    return f"/voice/assets/{asset_id}.mp3"


def _asset_view(session: Session, row: dict[str, Any]) -> schemas.VoiceAssetView:
    asset_id = str(row["asset_id"])
    unit_row = repository.get_unit_by_asset(session, asset_id)
    return schemas.VoiceAssetView(
        asset_id=asset_id,
        name=str(row["name"]),
        digit=int(row["digit"]) if row["digit"] is not None else None,
        unit=str(unit_row["unit"]) if unit_row is not None else None,
        duration_ms=int(row["duration_ms"]),
        size_bytes=int(row["size_bytes"]),
        sha256=asset_id,
        url=asset_url(asset_id),
        created_at=str(row["created_at"]),
    )


def list_assets(session: Session) -> list[schemas.VoiceAssetView]:
    return [_asset_view(session, row) for row in repository.list_assets(session)]


def _digit_rows(session: Session) -> dict[int, dict[str, Any]]:
    return {
        int(row["digit"]): row
        for row in repository.list_assets(session)
        if row["digit"] is not None and 0 <= int(row["digit"]) <= 9
    }


def _digit_set(session: Session) -> list[int]:
    return sorted(_digit_rows(session))


def _unit_rows(session: Session) -> dict[str, dict[str, Any]]:
    return repository.unit_rows(session)


def _unit_set(session: Session) -> list[str]:
    present = set(_unit_rows(session))
    return [unit for unit in numbers.unit_chars() if unit in present]


def status(session: Session) -> schemas.VoiceStatusView:
    return schemas.VoiceStatusView(
        asset_count=len(repository.list_assets(session)),
        digits=_digit_set(session),
        units=_unit_set(session),
        ffmpeg_available=shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None,
    )


def spec(session: Session) -> schemas.VoiceSpecView:
    return schemas.VoiceSpecView(
        events=[
            schemas.VoiceEventView(
                value=item.value,
                label=item.label,
                description=item.description,
                phase=item.phase,
            )
            for item in schemas.VOICE_EVENTS
        ],
        volume=schemas.VoiceBoundView(
            minimum=schemas.VOLUME_MIN, maximum=schemas.VOLUME_MAX, default=schemas.VOLUME_DEFAULT
        ),
        digits=_digit_set(session),
        asset_count=len(repository.list_assets(session)),
        max_segments=schemas.MAX_SEGMENTS_PER_RULE,
        max_time_windows=time_rules.MAX_TIME_WINDOWS_PER_RULE,
        max_pause_ms=schemas.MAX_PAUSE_MS,
        min_pause_ms=schemas.MIN_PAUSE_MS,
        max_digits_length=schemas.MAX_DIGITS_LENGTH,
        units=list(numbers.unit_chars()),
        sample_cert_no=SAMPLE_CERT_NO,
    )


def create_asset(
    session: Session, name: str, data: bytes, digit: int | None, unit: str | None
) -> schemas.VoiceAssetView:
    clean = name.strip()
    if not clean or len(clean) > schemas.MAX_NAME_LENGTH:
        _reject_asset(f"名称必须为 1–{schemas.MAX_NAME_LENGTH} 个字符")
    if digit is not None and not (0 <= digit <= 9):
        _reject_asset("数字标记只能是 0–9")
    if unit is not None and unit not in numbers.unit_chars():
        _reject_asset("单位只能是 十、百、千、万")
    if digit is not None and unit is not None:
        _reject_asset("一个素材不能同时是数字和单位")
    if len(data) == 0:
        _reject_asset("文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        _reject_asset(f"文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)}MB")
    if repository.get_asset_by_name(session, clean) is not None:
        _reject_asset(f"素材名称已存在：{clean}")
    if digit is not None and repository.get_asset_by_digit(session, digit) is not None:
        _reject_asset(f"数字 {digit} 已有素材")
    if unit is not None and unit in _unit_rows(session):
        _reject_asset(f"单位「{unit}」已有素材")

    source = audio.tmp_dir() / f"upload-{uuid.uuid4().hex}.src"
    target = audio.tmp_dir() / f"upload-{uuid.uuid4().hex}.mp3"
    source.parent.mkdir(parents=True, exist_ok=True)
    try:
        source.write_bytes(data)
        duration_ms = audio.normalize_to_mp3(source, target)
        asset_id = audio.sha256_file(target)
        existing = repository.get_asset(session, asset_id)
        if existing is not None:
            _reject_asset(f"相同音频已存在：{existing['name']}")
        final = audio.asset_path(asset_id)
        final.parent.mkdir(parents=True, exist_ok=True)
        created_final = not final.exists()
        if created_final:
            target.replace(final)
        try:
            repository.insert_asset(session, asset_id, clean, digit, duration_ms, final.stat().st_size)
            if unit is not None:
                repository.insert_unit(session, unit, asset_id)
        except Exception:
            repository.delete_unit(session, asset_id)
            repository.delete_asset(session, asset_id)
            if created_final:
                final.unlink(missing_ok=True)
            raise
        row = repository.get_asset(session, asset_id)
        if row is None:
            repository.delete_asset(session, asset_id)
            if created_final:
                final.unlink(missing_ok=True)
            _reject_asset("素材写入后读不到")
        return _asset_view(session, row)
    except audio.AudioError as exc:
        _reject_asset(str(exc))
    finally:
        source.unlink(missing_ok=True)
        target.unlink(missing_ok=True)


def rename_asset(session: Session, asset_id: str, name: str) -> schemas.VoiceAssetView:
    clean = name.strip()
    if not clean or len(clean) > schemas.MAX_NAME_LENGTH:
        _reject_asset(f"名称必须为 1–{schemas.MAX_NAME_LENGTH} 个字符")
    existing = repository.get_asset_by_name(session, clean)
    if existing is not None and str(existing["asset_id"]) != asset_id:
        _reject_asset(f"素材名称已存在：{clean}")
    if repository.rename_asset(session, asset_id, clean) is False:
        raise ApiProblemError(VOICE_ASSET_NOT_FOUND, asset_id)
    row = repository.get_asset(session, asset_id)
    if row is None:
        raise ApiProblemError(VOICE_ASSET_NOT_FOUND, asset_id)
    return _asset_view(session, row)


def _rules_using_asset(session: Session, asset_id: str, digit: int | None, unit: str | None) -> bool:
    for row in repository.list_rules(session):
        for item in row["segments"]:
            if item.get("kind") == "asset" and item.get("asset_id") == asset_id:
                return True
            if item.get("kind") != "digits":
                continue
            if digit is not None:
                return True
            if unit is not None and item.get("source") == "door_no":
                return True
    return False


def delete_asset(session: Session, asset_id: str) -> None:
    row = repository.get_asset(session, asset_id)
    if row is None:
        raise ApiProblemError(VOICE_ASSET_NOT_FOUND, asset_id)
    digit = int(row["digit"]) if row["digit"] is not None else None
    unit_row = repository.get_unit_by_asset(session, asset_id)
    unit = str(unit_row["unit"]) if unit_row is not None else None
    if _rules_using_asset(session, asset_id, digit, unit):
        raise ApiProblemError(VOICE_ASSET_IN_USE, f"「{row['name']}」正在被规则使用")
    repository.delete_unit(session, asset_id)
    repository.delete_asset(session, asset_id)
    audio.asset_path(asset_id).unlink(missing_ok=True)


def asset_file(session: Session, asset_id: str) -> Path:
    row = repository.get_asset(session, asset_id)
    path = audio.asset_path(asset_id)
    if row is None or not path.is_file():
        raise ApiProblemError(VOICE_ASSET_NOT_FOUND, asset_id)
    return path


# ── 分段 ──


def _segment_from_dict(value: object) -> schemas.Segment:
    if not isinstance(value, dict):
        _reject("分段格式不合法")
    kind = value.get("kind")
    if kind == "asset":
        return schemas.AssetSegment(**value)
    if kind == "digits":
        return schemas.DigitsSegment(**value)
    if kind == "pause":
        return schemas.PauseSegment(**value)
    _reject(f"未知的分段类型：{kind}")


def _validate_segments(session: Session, segments: list[schemas.Segment]) -> None:
    if not segments:
        _reject("至少需要一个分段")
    asset_ids = [segment.asset_id for segment in segments if isinstance(segment, schemas.AssetSegment)]
    missing = [asset_id for asset_id in set(asset_ids) if repository.get_asset(session, asset_id) is None]
    if missing:
        _reject(f"素材不存在：{missing[0]}")
    for segment in segments:
        if isinstance(segment, schemas.DigitsSegment) and segment.source == "door_no" and segment.length is not None:
            _reject("喷头号不支持截取位数")
    if any(isinstance(segment, schemas.DigitsSegment) for segment in segments):
        missing_digits = [digit for digit in range(10) if digit not in _digit_rows(session)]
        if missing_digits:
            _reject("数字素材不全，缺：" + "、".join(str(digit) for digit in missing_digits))
    if any(isinstance(segment, schemas.DigitsSegment) and segment.source == "door_no" for segment in segments):
        missing_units = [unit for unit in numbers.unit_chars() if unit not in _unit_rows(session)]
        if missing_units:
            _reject("单位素材不全，缺：" + "、".join(missing_units))


def _segments_dicts(segments: list[schemas.Segment]) -> list[dict[str, object]]:
    return [segment.model_dump() for segment in segments]


def _segment_summary(segments: list[schemas.Segment], assets: dict[str, dict[str, Any]]) -> str:
    parts: list[str] = []
    for segment in segments:
        if isinstance(segment, schemas.AssetSegment):
            row = assets.get(segment.asset_id)
            parts.append(str(row["name"]) if row is not None else "缺失素材")
        elif isinstance(segment, schemas.DigitsSegment):
            label = "喷头号" if segment.source == "door_no" else "学号"
            if segment.length:
                label += f"后 {segment.length} 位"
            parts.append(label)
        else:
            parts.append(f"停顿 {segment.ms / 1000:g} 秒")
    return " · ".join(parts)


def _rule_view(session: Session, row: dict[str, Any]) -> schemas.VoiceRuleView:
    segments = [_segment_from_dict(value) for value in row["segments"]]
    asset_ids = [segment.asset_id for segment in segments if isinstance(segment, schemas.AssetSegment)]
    assets = repository.asset_rows_by_ids(session, asset_ids)
    try:
        windows = time_rules.parse_json(row["time_windows"])
    except ValueError as exc:
        log_event(
            "ERROR",
            "voice.rule-time-invalid",
            "规则时段格式非法",
            rule_id=int(row["id"]),
            error=str(exc),
        )
        windows = []
    return schemas.VoiceRuleView(
        id=int(row["id"]),
        devices=list(row["devices"]),
        certs=list(row["certs"]),
        exempt=list(row["exempt"]),
        events=[DoorEventKind(value) for value in row["events"]],
        time_windows=[
            schemas.TimeWindow(days=list(window.days), start=window.start, end=window.end) for window in windows
        ],
        segments=segments,
        summary=_segment_summary(segments, assets),
        time_summary=time_rules.summary(windows),
        volume=int(row["volume"]),
        enabled=bool(row["enabled"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _normalized_targets(values: list[str], label: str) -> list[str]:
    targets: list[str] = []
    for raw in values:
        item = raw.strip()
        if not item:
            _reject(f"{label}里不能有空值")
        if len(item) > 64:
            _reject(f"{label}最多 64 个字符")
        if item not in targets:
            targets.append(item)
    if schemas.WILDCARD in targets and len(targets) > 1:
        _reject(f"{label}里「全部」不能与具体值混填")
    return targets


def _normalized_exemptions(values: list[str]) -> list[str]:
    targets = _normalized_targets(values, "排除名单")
    if schemas.WILDCARD in targets:
        _reject("排除名单里不能有「全部」")
    return targets


def _exempt_list(cert_nos: list[str], exemptions: list[str]) -> list[str]:
    if exemptions and schemas.WILDCARD not in cert_nos:
        _reject("排除名单只用于「全部学号」的规则")
    return exemptions


def _require_known_devices(session: Session, device_ids: list[str]) -> None:
    for device_id in device_ids:
        if device_id == schemas.WILDCARD:
            continue
        if devices_service.get_device(session, device_id) is None:
            raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id} 不在档案里")


def _duplicate_rule(
    session: Session,
    devices: list[str],
    certs: list[str],
    events: list[DoorEventKind],
    time_windows: list[schemas.TimeWindow],
    exclude_id: int | None,
) -> None:
    """同优先级规则命中同一目标时没有确定胜者，必须在保存时拒绝。

    范围更小的规则优先级更高，所以目标重叠但优先级不同可以共存；只有优先级完全相同
    且时段有交集才无法判定该播哪一条。规则 id 不是优先级，不参与决胜。
    """
    target_devices = set(devices)
    target_certs = set(certs)
    target_events = {event.value for event in events}
    target_windows = [window.to_window() for window in time_windows]
    priority = rule_priority(devices, certs, target_windows)
    for row in repository.list_rules(session):
        if exclude_id is not None and int(row["id"]) == exclude_id:
            continue
        row_devices = set(row["devices"])
        row_certs = set(row["certs"])
        if not target_events & set(row["events"]):
            continue
        device_overlap = "*" in target_devices or "*" in row_devices or bool(target_devices & row_devices)
        cert_overlap = "*" in target_certs or "*" in row_certs or bool(target_certs & row_certs)
        if not (device_overlap and cert_overlap):
            continue
        try:
            row_windows = time_rules.parse_json(row["time_windows"])
        except ValueError as exc:
            log_event(
                "ERROR",
                "voice.rule-time-invalid",
                "已有规则时段格式非法，跳过重名检查",
                rule_id=int(row["id"]),
                error=str(exc),
            )
            continue
        if rule_priority(sorted(row_devices), sorted(row_certs), row_windows) != priority:
            continue
        if not target_windows and not row_windows:
            _reject("同优先级的规则目标重叠，请调整设备或学号范围")
        if target_windows and row_windows and time_rules.overlap(target_windows, row_windows):
            _reject("同优先级的规则目标与生效时段重叠，请调整设备、学号或时段")


def _normalize_rule(
    session: Session,
    devices: list[str],
    certs: list[str],
    events: list[DoorEventKind],
    time_windows: list[schemas.TimeWindow],
    segments: list[schemas.Segment],
    exempt_cert_nos: list[str],
    exclude_id: int | None,
) -> tuple[list[str], list[str], list[DoorEventKind], list[dict[str, object]], list[schemas.Segment], list[str]]:
    normalized_devices = _normalized_targets(devices, "设备")
    normalized_certs = _normalized_targets(certs, "学号")
    _require_known_devices(session, normalized_devices)
    normalized_events = list(dict.fromkeys(events))
    if not normalized_events:
        _reject("至少选择一个事件")
    _validate_segments(session, segments)
    exemptions = _normalized_exemptions(exempt_cert_nos)
    exempt = _exempt_list(normalized_certs, exemptions)
    _duplicate_rule(session, normalized_devices, normalized_certs, normalized_events, time_windows, exclude_id)
    windows: list[dict[str, Any]] = [
        {"days": list(window.to_window().days), "start": window.to_window().start, "end": window.to_window().end}
        for window in time_windows
    ]
    return normalized_devices, normalized_certs, normalized_events, windows, segments, exempt


def create_rule(session: Session, req: schemas.VoiceRuleBody) -> schemas.VoiceRuleView:
    devices, certs, events, windows, segments, exempt = _normalize_rule(
        session, req.devices, req.certs, req.events, req.time_windows, req.segments, req.exempt_cert_nos, None
    )
    rule_id = repository.insert_rule(
        session,
        devices,
        certs,
        [event.value for event in events],
        windows,
        exempt,
        _segments_dicts(segments),
        req.volume,
        req.enabled,
        utcnow(),
    )
    return _rule_view(session, _rule_row_or_404(session, rule_id))


def update_rule(session: Session, rule_id: int, req: schemas.VoiceRuleBody) -> schemas.VoiceRuleView:
    _rule_row_or_404(session, rule_id)
    devices, certs, events, windows, segments, exempt = _normalize_rule(
        session, req.devices, req.certs, req.events, req.time_windows, req.segments, req.exempt_cert_nos, rule_id
    )
    if (
        repository.update_rule(
            session,
            rule_id,
            devices,
            certs,
            [event.value for event in events],
            windows,
            exempt,
            _segments_dicts(segments),
            req.volume,
            req.enabled,
            utcnow(),
        )
        is False
    ):
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    return _rule_view(session, _rule_row_or_404(session, rule_id))


def set_enabled(session: Session, rule_id: int, enabled: bool) -> schemas.VoiceRuleView:
    if repository.set_enabled(session, rule_id, enabled, utcnow()) is False:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    return _rule_view(session, _rule_row_or_404(session, rule_id))


def delete_rule(session: Session, rule_id: int) -> None:
    if repository.delete_rule(session, rule_id) is False:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))


def _rule_order(session: Session, view: schemas.VoiceRuleView) -> tuple[bool, str, bool, int, int]:
    cert_label = "~" if "*" in view.certs else (view.certs[0] if view.certs else "~")
    device_order = {
        str(record["device_id"]): index for index, record in enumerate(devices_service.list_devices(session))
    }
    return (
        "*" in view.certs,
        cert_label,
        "*" in view.devices,
        device_order.get(view.devices[0], len(device_order)) if view.devices else len(device_order),
        view.id,
    )


def list_rules(session: Session) -> list[schemas.VoiceRuleView]:
    return sorted(
        (_rule_view(session, row) for row in repository.list_rules(session)),
        key=lambda view: _rule_order(session, view),
    )


# ── 解析与播放清单 ──


def _resolve(session: Session, segments: list[schemas.Segment], door_no: int, cert_no: str) -> list[PlanItem]:
    """把规则分段解析成设备播放清单；数字变量只取数字字符。"""
    plan: list[PlanItem] = []
    asset_ids = {segment.asset_id for segment in segments if isinstance(segment, schemas.AssetSegment)}
    # 数字/单位素材也要档案：door_no 段按"百""十"展开到对应素材
    asset_ids.update(unit["asset_id"] for unit in _unit_rows(session).values())
    asset_ids.update(digit["asset_id"] for digit in _digit_rows(session).values())
    assets = repository.asset_rows_by_ids(session, sorted(asset_ids))
    digits = _digit_rows(session)
    units = _unit_rows(session)

    for segment in segments:
        if isinstance(segment, schemas.AssetSegment):
            row = assets.get(segment.asset_id)
            if row is None:
                _reject(f"素材不存在：{segment.asset_id}")
            plan.append(_clip_from_row(row))
        elif isinstance(segment, schemas.DigitsSegment):
            if segment.source == "door_no":
                try:
                    symbols = numbers.cardinal_chars(door_no)
                except ValueError as exc:
                    _reject(str(exc))
                for symbol in symbols:
                    if symbol in numbers.DIGIT_SYMBOLS:
                        row = digits.get(numbers.digit_value(symbol))
                    else:
                        # 单位表只给 asset_id：取到后再查素材档案，clip 需要完整形状
                        unit_row = units.get(symbol)
                        row = assets.get(unit_row["asset_id"]) if unit_row else None
                    if row is None:
                        _reject(f"数字/单位素材缺失：{symbol}")
                    plan.append(_clip_from_row(row))
            else:
                chars = numbers.digit_chars(str(cert_no))
                if segment.length is not None:
                    chars = chars[-segment.length :]
                for char in chars:
                    row = digits.get(int(char))
                    if row is None:
                        _reject(f"数字素材缺失：{char}")
                    plan.append(_clip_from_row(row))
        else:
            plan.append(PausePlan(ms=segment.ms))
    return plan


def _plan_duration_ms(plan: list[PlanItem]) -> int:
    return sum(item.duration_ms if isinstance(item, ClipPlan) else item.ms for item in plan)


def _play_params(plan: list[PlanItem], volume: int) -> dict[str, object]:
    return {
        "segments": _plan_json(plan),
        "volume": volume,
        "download_budget_ms": PLAY_DOWNLOAD_BUDGET_MS,
    }


def _plan_summary(session: Session, plan: list[PlanItem]) -> str:
    assets = repository.asset_rows_by_ids(session, [item.clip_id for item in plan if isinstance(item, ClipPlan)])
    parts: list[str] = []
    for item in plan:
        if isinstance(item, ClipPlan):
            row = assets.get(item.clip_id)
            parts.append(str(row["name"]) if row is not None else "音频")
        else:
            parts.append(f"停顿 {item.ms / 1000:g} 秒")
    return " · ".join(parts)


def _preview(session: Session, plan: list[PlanItem]) -> schemas.VoicePreviewView:
    assets = repository.asset_rows_by_ids(session, [item.clip_id for item in plan if isinstance(item, ClipPlan)])
    segments: list[schemas.ResolvedSegment] = []
    for item in plan:
        if isinstance(item, ClipPlan):
            row = assets.get(item.clip_id)
            if row is None:
                continue
            segments.append(
                schemas.ResolvedSegment(
                    kind="clip",
                    clip=schemas.ResolvedClip(
                        asset_id=item.clip_id,
                        name=str(row["name"]),
                        url=asset_url(item.clip_id),
                        sha256=item.sha256,
                        size_bytes=item.size_bytes,
                        duration_ms=item.duration_ms,
                    ),
                )
            )
        else:
            segments.append(schemas.ResolvedSegment(kind="pause", ms=item.ms))
    return schemas.VoicePreviewView(
        segments=segments,
        total_ms=_plan_duration_ms(plan),
        summary=_plan_summary(session, plan),
    )


def preview(session: Session, req: schemas.VoicePreviewRequest) -> schemas.VoicePreviewView:
    _validate_segments(session, req.segments)
    return _preview(session, _resolve(session, req.segments, req.door_no, req.cert_no))


def _sample_cert(row: dict[str, Any]) -> str:
    certs = list(row["certs"])
    return SAMPLE_CERT_NO if "*" in certs or not certs else str(certs[0])


def _sample_event(row: dict[str, Any]) -> DoorEventKind:
    return DoorEventKind(next(iter(row["events"])))


def _rule_row_or_404(session: Session, rule_id: int) -> dict[str, Any]:
    """取规则行；不存在即领域异常（HTTP 层转 404）。"""
    row = repository.get_rule(session, rule_id)
    if row is None:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    return row


def _clip_from_row(row: dict[str, Any]) -> ClipPlan:
    """素材行 → 片段计划。sha256 即 asset_id：素材内容寻址，下载后按它校验。"""
    asset_id = str(row["asset_id"])
    return ClipPlan(
        clip_id=asset_id,
        sha256=asset_id,
        size_bytes=int(row["size_bytes"]),
        duration_ms=int(row["duration_ms"]),
    )


def _clip_payload(clip: ClipPlan) -> dict[str, object]:
    """协议面（session.command 参数）与预下载面（VOICE_PREPARE 参数）共用的片段四字段。"""
    return {
        "clip_id": clip.clip_id,
        "sha256": clip.sha256,
        "size_bytes": clip.size_bytes,
        "duration_ms": clip.duration_ms,
    }


def _claim_delivery(session: Session, event: DoorEvent, rule_id: int, plan: list[PlanItem], duration_ms: int) -> bool:
    """幂等认领投递权：event_key 是唯一键，重复事件只有第一次拿到。"""
    return repository.claim_delivery(
        session,
        event.event_key,
        rule_id,
        event.device_id,
        event.door_no,
        event.cert_no,
        event.kind.value,
        _plan_summary(session, plan),
        len(plan),
        duration_ms,
        parse_rfc3339(event.occurred_at) or utcnow(),
        trace_id_var.get(),
        utcnow(),
    )


async def _send_play(session: Session, device_id: str, plan: list[PlanItem], volume: int) -> CommandOutcome:
    """下发 VOICE_PLAY 并等待设备回执。"""
    return await device_actions.send_command_and_wait(
        session,
        device_id,
        actions.VOICE_PLAY,
        _play_params(plan, volume),
        timeout_s=_play_timeout_s(_plan_duration_ms(plan)),
    )


def _finish_delivery(session: Session, event_key: str, outcome: CommandOutcome, detail: str) -> None:
    """落回执：状态、说明与下载标记写入投递记录。"""
    repository.finish_delivery(
        session,
        event_key,
        utcnow(),
        str(outcome["status"]),
        detail,
        utcnow(),
        _downloaded(outcome),
    )


async def push_to_device(session: Session, rule_id: int, device_id: str) -> schemas.VoicePushView:
    row = _rule_row_or_404(session, rule_id)
    if devices_service.get_device(session, device_id) is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id} 不在档案里")
    segments = [_segment_from_dict(value) for value in row["segments"]]
    try:
        plan = _resolve(session, segments, 1, _sample_cert(row))
    except InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc
    duration_ms = _plan_duration_ms(plan)
    now = now_rfc3339()
    event = DoorEvent(
        kind=_sample_event(row),
        device_id=device_id,
        door_no=1,
        cert_no=_sample_cert(row),
        session_id=0,
        occurred_at=now,
        event_key=f"{MANUAL_EVENT_PREFIX}{rule_id}:{uuid.uuid4().hex[:12]}",
    )
    # 手动推送的 event_key 内嵌随机段，认领永不撞车，认领结果无需判读
    _claim_delivery(session, event, rule_id, plan, duration_ms)
    outcome = await _send_play(session, device_id, plan, int(row["volume"]))
    detail = _detail(outcome)
    _finish_delivery(session, event.event_key, outcome, "手动推送：" + detail)
    return schemas.VoicePushView(
        device_id=device_id,
        status=str(outcome["status"]),
        reason_code=str(outcome["reason_code"] or ""),
        detail=detail,
        segment_count=len(plan),
        duration_ms=duration_ms,
    )


# ── 预下载 ──


def _clips_for_rule(session: Session, row: dict[str, Any]) -> list[dict[str, object]]:
    segments = [_segment_from_dict(value) for value in row["segments"]]
    asset_ids = {segment.asset_id for segment in segments if isinstance(segment, schemas.AssetSegment)}
    digit_segments = [segment for segment in segments if isinstance(segment, schemas.DigitsSegment)]
    if digit_segments:
        asset_ids.update(str(digit_row["asset_id"]) for digit_row in _digit_rows(session).values())
    if any(segment.source == "door_no" for segment in digit_segments):
        asset_ids.update(str(unit_row["asset_id"]) for unit_row in _unit_rows(session).values())
    rows = repository.asset_rows_by_ids(session, sorted(asset_ids))
    return [
        _clip_payload(_clip_from_row(asset)) for asset in sorted(rows.values(), key=lambda item: str(item["asset_id"]))
    ]


def _spawn_prepare(device_id: str, clips: list[dict[str, object]]) -> None:
    if not clips:
        return
    try:
        task = asyncio.get_running_loop().create_task(_prepare_task(device_id, {"clips": clips}))
    except Exception as exc:  # noqa: BLE001 — 调度失败只记日志
        log_event("ERROR", "voice.schedule-failed", "语音预下载调度失败", device_id=device_id, error=str(exc))
        return
    _tasks.add(task)
    task.add_done_callback(_task_done)


async def _prepare_task(device_id: str, params: dict[str, object]) -> None:
    """asyncio 任务入口：自己开短会话。"""
    from app.db.session import session_scope

    with session_scope() as session:
        await _prepare(session, device_id, params, VOICE_PREPARE_TIMEOUT_S)


async def _prepare(session: Session, device_id: str, params: dict[str, object], timeout_s: float) -> None:
    try:
        outcome = await device_actions.send_command_and_wait(
            session,
            device_id,
            actions.VOICE_PREPARE,
            params,
            timeout_s=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 — 预下载是后台优化
        log_event("WARNING", "voice.prepare-failed", "语音预下载失败", device_id=device_id, error=str(exc))
        return
    clips = params.get("clips")
    data = outcome["data"]
    queued = data.get("queued") if isinstance(data, dict) else None
    dropped = data.get("dropped") if isinstance(data, dict) else None
    log_event(
        "INFO",
        "voice.prepared",
        "语音预下载命令已收尾（设备端后台执行）",
        device_id=device_id,
        clip_count=len(clips) if isinstance(clips, list) else 0,
        queued=queued,
        dropped=dropped,
        status=str(outcome["status"]),
        reason_code=str(outcome["reason_code"] or "-"),
    )


def schedule_rule_prepare(session: Session, view: schemas.VoiceRuleView) -> None:
    if not view.enabled:
        return
    row = repository.get_rule(session, view.id)
    if row is None:
        return
    clips = _clips_for_rule(session, row)
    devices = view.devices
    if schemas.WILDCARD in devices:
        ids = [str(record["device_id"]) for record in devices_service.list_devices(session)]
    else:
        ids = list(devices)
    for device_id in ids:
        if devices_service.is_online(device_id):
            _spawn_prepare(device_id, clips)


def schedule_events(events: tuple[DoorEvent, ...]) -> None:
    if not events:
        return
    try:
        task = asyncio.get_running_loop().create_task(handle_events(events))
    except Exception as exc:  # noqa: BLE001 — 不能因为语音调度拖垮帧回执
        log_event("ERROR", "voice.schedule-failed", "语音事件调度失败", error=str(exc))
        return
    _tasks.add(task)
    task.add_done_callback(_task_done)


def _task_done(task: asyncio.Task[None]) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log_event("ERROR", "voice.task-failed", "语音后台任务失败", error=str(error))


# ── 事件投递 ──


async def handle_events(events: tuple[DoorEvent, ...]) -> None:
    from app.db.session import session_scope

    with session_scope() as session:
        value = config_store.global_effective(session)["voice_event_max_age_s"]
        max_age = value if isinstance(value, int) else 0
        for event in events:
            try:
                await _dispatch_event(session, event, max_age)
            except Exception as exc:  # noqa: BLE001 — 单事件失败不拖垮同一帧其它事件
                log_event(
                    "ERROR",
                    "voice.delivery-error",
                    "语音事件处理失败",
                    device_id=event.device_id,
                    door_no=event.door_no,
                    event=event.kind.value,
                    error=str(exc),
                )


async def _dispatch_event(session: Session, event: DoorEvent, max_age: int) -> None:
    if max_age > 0 and _elapsed_s(event.occurred_at) > max_age:
        log_event(
            "INFO",
            "voice.event-stale",
            "语音事件超过时效，跳过",
            device_id=event.device_id,
            event=event.kind.value,
            age_s=round(_elapsed_s(event.occurred_at), 1),
        )
        return
    if not event.cert_no:
        return
    zone_offset = config_store.global_effective(session)["extend_hours_zone_offset_minutes"]
    if not isinstance(zone_offset, int):
        log_event(
            "ERROR",
            "voice.zone-offset-invalid",
            "时区偏移配置不是整数，跳过语音匹配",
            value=str(zone_offset),
        )
        return
    local = to_local(now_rfc3339(), zone_offset)
    if local is None:
        log_event("ERROR", "voice.local-time-invalid", "无法换算服务端当地时间，跳过语音匹配")
        return
    matched = repository.matching_rules(session, event.device_id, event.cert_no, event.kind.value, local)
    rule = matched[0] if matched else None
    if rule is None:
        log_event(
            "DEBUG",
            "voice.no-rule",
            "没有匹配的语音规则",
            device_id=event.device_id,
            event=event.kind.value,
            local_time=local.isoformat(timespec="minutes"),
        )
        return
    segments = [_segment_from_dict(value) for value in rule["segments"]]
    try:
        plan = _resolve(session, segments, event.door_no, event.cert_no)
    except InvalidVoiceRuleError as exc:
        log_event(
            "ERROR",
            "voice.rule-invalid",
            "规则分段解析失败，跳过",
            device_id=event.device_id,
            event=event.kind.value,
            error=str(exc),
        )
        return
    duration_ms = _plan_duration_ms(plan)
    if not _claim_delivery(session, event, int(rule["id"]), plan, duration_ms):
        return
    outcome = await _send_play(session, event.device_id, plan, int(rule["volume"]))
    _finish_delivery(session, event.event_key, outcome, _detail(outcome))
    log_event(
        "INFO",
        "voice.delivery-finished",
        "语音播放命令已收尾",
        device_id=event.device_id,
        event=event.kind.value,
        status=str(outcome["status"]),
        reason_code=str(outcome["reason_code"] or "-"),
        segment_count=len(plan),
        duration_ms=duration_ms,
        downloaded=_downloaded(outcome),
    )


def list_deliveries(session: Session, limit: int) -> list[schemas.VoiceDeliveryView]:
    deliveries: list[schemas.VoiceDeliveryView] = []
    for row in repository.list_deliveries(session, limit):
        event_key = str(row["event_key"])
        deliveries.append(
            schemas.VoiceDeliveryView(
                id=int(row["id"]),
                source="manual" if event_key.startswith(MANUAL_EVENT_PREFIX) else "auto",
                rule_id=int(row["rule_id"]),
                device_id=str(row["device_id"]),
                cert_no=str(row["cert_no"]),
                event=DoorEventKind(str(row["event"])),
                door_no=int(row["door_no"]),
                segments=str(row["segments"]),
                segment_count=int(row["segment_count"]),
                duration_ms=int(row["duration_ms"]),
                occurred_at=str(row["occurred_at"]),
                dispatched_at=str(row["dispatched_at"]),
                done_at=str(row["done_at"]),
                status=str(row["status"]),
                detail=str(row["detail"]),
                downloaded=bool(row["downloaded"]),
            )
        )
    return deliveries


# ── 小工具 ──


def _elapsed_s(occurred_at: str) -> float:
    started = parse_rfc3339(occurred_at)
    now = parse_rfc3339(now_rfc3339())
    if started is None or now is None:
        return 0.0
    return max(0.0, (now - started).total_seconds())


def _play_timeout_s(duration_ms: int) -> float:
    # 规则可以是一串片段，时长由分段累加；设备下载缺失素材的时间也要算进预算。
    # 设备端按 download_budget_ms 主动截止，服务端只比它多留出播放与浴室提示音的余量。
    return max(
        PLAY_TIMEOUT_MIN_S,
        duration_ms / 1000 + PLAY_DOWNLOAD_BUDGET_MS / 1000 + PLAY_TIMEOUT_MARGIN_S,
    )


def _detail(outcome: CommandOutcome) -> str:
    reason = outcome["reason_code"]
    message = outcome["terminal_message"]
    return f"{reason} {message}".strip()[:500]


def _downloaded(outcome: CommandOutcome) -> bool:
    data = outcome["data"]
    return bool(data.get("downloaded")) if isinstance(data, dict) else False
