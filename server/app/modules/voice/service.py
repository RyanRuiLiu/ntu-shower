"""语音播报的规则、模板渲染、事件投递与预热编排。

## 唯一判定链

`handle_events` 只做一次「时效 → 匹配 → 渲染 → 认领 → 生成/取缓存 → 延迟 → 下发」。
事件来自 `doors` 的帧派生；规则只按设备/学号/事件精确匹配；同一事件只认领一次。

## 模板与缓存

规则存模板，事件发生时用真实会话值渲染成最终文本；`voice_clip` 按最终文本缓存。
静态模板（无占位符）在保存时预热；带占位符的规则不预热，首次出现新文本时再合成，
之后命中缓存。缓存按最近使用淘汰，动态占位符不会无限增长。

## 延迟

`delay_ms` 从事件发生时刻算起，用来错开浴室 App 自己的结算语音。音频合成可以与
延迟重叠：先准备好音频，再补足剩余的等待。实际下发时刻写入 `voice_delivery`，
现场按该记录与设备日志调整。
"""

import asyncio
import threading
from sqlite3 import Row
from typing import NoReturn

from starlette.concurrency import run_in_threadpool

from app.core import actions
from app.core.clock import now_rfc3339, parse_rfc3339, seconds_between
from app.core.logging import log_event, trace_id_var
from app.core.problems import (
    DEVICE_NOT_FOUND,
    VOICE_RULE_NOT_FOUND,
    VOICE_SYNTHESIS_FAILED,
    ApiProblemError,
)
from app.modules.config import store as config_store
from app.modules.devices import service as devices_service
from app.modules.doors import service as doors_service
from app.modules.doors.events import DoorEvent, DoorEventKind
from app.modules.sessions import service as sessions_service
from app.modules.sessions.schemas import CommandOutcome

from . import render, repository, schemas, tts

VOICE_PREPARE_TIMEOUT_S = 60.0
PLAY_TIMEOUT_MIN_S = 30.0
PLAY_TIMEOUT_MAX_S = 120.0

#: 规则保存/试听的 TTS 串行锁。Piper 一次只跑一个，避免并发子进程抢 CPU。
_clip_lock = threading.Lock()

#: 后台任务强引用；asyncio 只保留弱引用，丢弃的 Task 可能在跑完之前被回收。
_tasks: set[asyncio.Task[None]] = set()


class InvalidVoiceRuleError(ValueError):
    """规则模板/学号等不合法；路由转 422。"""


def _reject(reason: str) -> NoReturn:
    raise InvalidVoiceRuleError(reason)


def _normalize_text(text: str) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        _reject("播报内容不能为空")
    if len(normalized) > render.MAX_TEMPLATE_CHARS:
        _reject(f"播报内容最多 {render.MAX_TEMPLATE_CHARS} 个字符")
    return normalized


def _validated_text(text: str, event: DoorEventKind) -> str:
    normalized = _normalize_text(text)
    label = schemas.event_spec(event).label
    try:
        render.validate_template(normalized)
        render.render_template(normalized, render.sample_values(label))
    except render.TemplateError as exc:
        _reject(str(exc))
    return normalized


def _sample_text(text: str, event: DoorEventKind) -> str:
    label = schemas.event_spec(event).label
    try:
        return render.render_template(text, render.sample_values(label))
    except render.TemplateError:
        return text


def _rule_view(row: Row) -> schemas.VoiceRuleView:
    event = DoorEventKind(str(row["event"]))
    return schemas.VoiceRuleView(
        id=int(row["id"]),
        device_id=str(row["device_id"]),
        cert_no=str(row["cert_no"]),
        event=event,
        text=str(row["text"]),
        delay_ms=int(row["delay_ms"]),
        volume=int(row["volume"]),
        enabled=bool(row["enabled"]),
        priority=int(row["priority"]),
        sample_text=_sample_text(str(row["text"]), event),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def spec() -> schemas.VoiceSpecView:
    return schemas.VoiceSpecView(
        events=[
            schemas.VoiceEventView(value=item.value, label=item.label, description=item.description)
            for item in schemas.VOICE_EVENTS
        ],
        placeholders=[
            schemas.VoicePlaceholderView(key=item.key, label=item.label, description=item.description)
            for item in schemas.PLACEHOLDERS
        ],
        delay=schemas.VoiceDelayView(
            minimum_ms=schemas.DELAY_MIN_MS,
            maximum_ms=schemas.DELAY_MAX_MS,
            default_ms=schemas.DELAY_DEFAULT_MS,
        ),
        volume=schemas.VoiceBoundView(
            minimum=schemas.VOLUME_MIN,
            maximum=schemas.VOLUME_MAX,
            default=schemas.VOLUME_DEFAULT,
        ),
        priority=schemas.VoiceBoundView(
            minimum=schemas.PRIORITY_MIN,
            maximum=schemas.PRIORITY_MAX,
            default=schemas.PRIORITY_DEFAULT,
        ),
    )


def status() -> schemas.VoiceStatusView:
    probe = tts.probe()
    return schemas.VoiceStatusView(
        ready=probe.model_present and probe.piper_available and probe.espeak_available and probe.ffmpeg_available,
        model_present=probe.model_present,
        piper_available=probe.piper_available,
        espeak_available=probe.espeak_available,
        ffmpeg_available=probe.ffmpeg_available,
        clip_count=repository.count_clips(),
    )


def list_rules(device_id: str | None) -> list[schemas.VoiceRuleView]:
    return [_rule_view(row) for row in repository.list_rules(device_id)]


def create_rule(req: schemas.VoiceRuleCreate) -> schemas.VoiceRuleView:
    if devices_service.get_device(req.device_id) is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {req.device_id} 不在档案里")
    cert_no = req.cert_no.strip()
    if not cert_no:
        _reject("学号不能为空")
    text = _validated_text(req.text, req.event)
    now = now_rfc3339()
    rule_id = repository.insert_rule(
        req.device_id,
        cert_no,
        req.event.value,
        text,
        req.delay_ms,
        req.volume,
        req.enabled,
        req.priority,
        now,
    )
    row = repository.get_rule(rule_id)
    if row is None:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    return _rule_view(row)


def update_rule(rule_id: int, req: schemas.VoiceRuleUpdate) -> schemas.VoiceRuleView:
    existing = repository.get_rule(rule_id)
    if existing is None:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    cert_no = req.cert_no.strip()
    if not cert_no:
        _reject("学号不能为空")
    text = _validated_text(req.text, req.event)
    changed = repository.update_rule(
        rule_id,
        cert_no,
        req.event.value,
        text,
        req.delay_ms,
        req.volume,
        req.enabled,
        req.priority,
        now_rfc3339(),
    )
    if changed == 0:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    row = repository.get_rule(rule_id)
    if row is None:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    return _rule_view(row)


def delete_rule(rule_id: int) -> None:
    if repository.delete_rule(rule_id) == 0:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))


def preview(req: schemas.VoicePreviewRequest) -> schemas.VoicePreviewView:
    text = _validated_text(req.text, req.event)
    label = schemas.event_spec(req.event).label
    values = render.sample_values(
        label,
        door_no=req.door_no,
        cert_no=req.cert_no,
        assigned_seconds=req.assigned_seconds,
        shower_seconds=req.shower_seconds,
    )
    try:
        rendered = render.render_template(text, values)
    except render.TemplateError as exc:
        _reject(str(exc))
    clip = _ensure_clip(rendered)
    return schemas.VoicePreviewView(
        rendered_text=rendered,
        clip_id=clip.clip_id,
        clip_url=f"/voice/clips/{clip.clip_id}.mp3",
        size_bytes=clip.size_bytes,
        duration_ms=clip.duration_ms,
    )


def _ensure_clip(text: str) -> tts.ClipFile:
    """取最终文本对应的音频；命中缓存不跑 TTS，缺失/损坏则重新合成。"""
    clip_id = tts.clip_id_for_text(text)
    with _clip_lock:
        row = repository.get_clip(clip_id)
        if row is not None:
            path = tts.clip_path(clip_id)
            if (
                path.is_file()
                and path.stat().st_size == int(row["size_bytes"])
                and tts.sha256_of(path) == str(row["sha256"])
            ):
                now = now_rfc3339()
                repository.touch_clip(clip_id, now)
                return tts.ClipFile(
                    clip_id=clip_id,
                    sha256=str(row["sha256"]),
                    size_bytes=int(row["size_bytes"]),
                    duration_ms=int(row["duration_ms"]),
                    path=path,
                )
        now = now_rfc3339()
        try:
            clip = tts.synthesize(text, clip_id)
        except tts.VoiceTextTooLongError as exc:
            _reject(str(exc))
        except tts.VoiceSynthesisError as exc:
            raise ApiProblemError(VOICE_SYNTHESIS_FAILED, str(exc)) from exc
        repository.upsert_clip(clip.clip_id, clip.sha256, clip.size_bytes, clip.duration_ms, now)
        for removed_id in repository.prune_clips(tts.MAX_CLIP_ROWS, tts.MAX_CLIP_TOTAL_BYTES):
            tts.clip_path(removed_id).unlink(missing_ok=True)
        return clip


def prepare_static(view: schemas.VoiceRuleView) -> tts.ClipFile | None:
    """静态模板保存时预热；带占位符的规则不生成固定音频。"""
    if render.has_placeholders(view.text):
        return None
    try:
        return _ensure_clip(view.text)
    except Exception as exc:  # noqa: BLE001 —— 预热失败不能把已保存的规则判成失败
        log_event(
            "WARNING",
            "voice.prepare-skipped",
            "静态模板预热失败，首次播报时会再试",
            rule_id=view.id,
            device_id=view.device_id,
            error=str(exc),
        )
        return None


def schedule_prepare(view: schemas.VoiceRuleView, clip: tts.ClipFile | None) -> None:
    if clip is None:
        return
    params = {
        "clip_id": clip.clip_id,
        "sha256": clip.sha256,
        "size_bytes": clip.size_bytes,
        "duration_ms": clip.duration_ms,
    }
    try:
        task = asyncio.get_running_loop().create_task(_prepare(view.device_id, params))
    except Exception as exc:  # noqa: BLE001 —— 调度失败只记日志，不影响保存响应
        log_event("ERROR", "voice.schedule-failed", "语音预热调度失败", error=str(exc))
        return
    _tasks.add(task)
    task.add_done_callback(_task_done)


async def _prepare(device_id: str, params: dict[str, object]) -> None:
    try:
        outcome = await sessions_service.send_command_and_wait(
            device_id,
            actions.VOICE_PREPARE,
            params,
            timeout_s=VOICE_PREPARE_TIMEOUT_S,
        )
    except Exception as exc:  # noqa: BLE001 —— 预热是后台优化，失败只留痕
        log_event("WARNING", "voice.prepare-failed", "语音预热失败", device_id=device_id, error=str(exc))
        return
    log_event(
        "INFO",
        "voice.prepared",
        "语音预热命令已收尾",
        device_id=device_id,
        status=str(outcome["status"]),
        reason_code=str(outcome["reason_code"] or "-"),
    )


def schedule_events(events: tuple[DoorEvent, ...]) -> None:
    """帧处理后调用：同步建任务，绝不等待命令。"""
    if not events:
        return
    try:
        task = asyncio.get_running_loop().create_task(handle_events(events))
    except Exception as exc:  # noqa: BLE001 —— 不能因为语音调度拖垮帧回执
        log_event("ERROR", "voice.schedule-failed", "语音事件调度失败", error=str(exc))
        return
    _tasks.add(task)
    task.add_done_callback(_task_done)


async def handle_events(events: tuple[DoorEvent, ...]) -> None:
    value = config_store.global_effective()["voice_event_max_age_s"]
    max_age = value if isinstance(value, int) else 0
    for event in events:
        try:
            await _dispatch_event(event, max_age)
        except Exception as exc:  # noqa: BLE001 —— 单事件失败不拖垮同一帧的其它事件
            log_event(
                "ERROR",
                "voice.delivery-error",
                "语音事件处理失败",
                device_id=event.device_id,
                door_no=event.door_no,
                event=event.kind.value,
                error=str(exc),
            )


async def _dispatch_event(event: DoorEvent, max_age: int) -> None:
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

    rule = repository.match_rule(event.device_id, event.cert_no, event.kind.value)
    if rule is None:
        log_event(
            "DEBUG",
            "voice.no-rule",
            "没有匹配的语音规则",
            device_id=event.device_id,
            event=event.kind.value,
        )
        return

    rendered = _event_text(event, str(rule["text"]))
    if rendered is None:
        return
    clip_id = tts.clip_id_for_text(rendered)
    if not repository.claim_delivery(
        event,
        rule,
        rendered,
        clip_id,
        event.occurred_at,
        trace_id_var.get(),
        now_rfc3339(),
    ):
        return

    try:
        # 合成最长 60+30 秒且持 _clip_lock：必须离开事件循环线程去跑，否则网关的
        # 心跳与全部 HTTP 一起冻结。路由层的 preview/prepare 同理已用线程池。
        clip = await run_in_threadpool(_ensure_clip, rendered)
    except Exception as exc:  # noqa: BLE001 —— 认领后失败要留下结果，但不重试
        repository.finish_delivery(event.event_key, "", "failed", str(exc)[:500])
        log_event(
            "ERROR",
            "voice.delivery-failed",
            "语音音频准备失败",
            device_id=event.device_id,
            event=event.kind.value,
            error=str(exc),
        )
        return

    await _wait_for_delay(event, int(rule["delay_ms"]))
    if max_age > 0 and _elapsed_s(event.occurred_at) > max_age:
        repository.finish_delivery(event.event_key, "", "skipped", "事件已超过时效")
        log_event(
            "INFO",
            "voice.event-stale",
            "语音事件在延迟期间超过时效，跳过",
            device_id=event.device_id,
            event=event.kind.value,
        )
        return

    dispatched_at = now_rfc3339()
    params = {
        "clip_id": clip.clip_id,
        "sha256": clip.sha256,
        "size_bytes": clip.size_bytes,
        "duration_ms": clip.duration_ms,
        "volume": int(rule["volume"]),
    }
    outcome = await sessions_service.send_command_and_wait(
        event.device_id,
        actions.VOICE_PLAY,
        params,
        timeout_s=_play_timeout_s(clip.duration_ms),
    )
    repository.finish_delivery(
        event.event_key,
        dispatched_at,
        str(outcome["status"]),
        _detail(outcome),
    )
    log_event(
        "INFO",
        "voice.delivery-finished",
        "语音播放命令已收尾",
        device_id=event.device_id,
        event=event.kind.value,
        status=str(outcome["status"]),
        reason_code=str(outcome["reason_code"] or "-"),
        delay_ms=int(rule["delay_ms"]),
    )


def _event_text(event: DoorEvent, template: str) -> str | None:
    durations = doors_service.session_durations(event.session_id)
    if durations is None:
        log_event(
            "WARNING",
            "voice.session-missing",
            "语音事件找不到会话，跳过",
            device_id=event.device_id,
            session_id=event.session_id,
        )
        return None
    end = durations.ended_at or event.occurred_at
    assigned_seconds = max(0, seconds_between(durations.assigned_at, end))
    values = render.sample_values(
        schemas.event_spec(event.kind).label,
        door_no=event.door_no,
        cert_no=event.cert_no,
        assigned_seconds=assigned_seconds,
        shower_seconds=durations.flow_s,
    )
    try:
        return render.render_template(template, values)
    except render.TemplateError as exc:
        log_event(
            "WARNING",
            "voice.render-failed",
            "语音模板渲染失败，跳过",
            device_id=event.device_id,
            event=event.kind.value,
            error=str(exc),
        )
        return None


async def _wait_for_delay(event: DoorEvent, delay_ms: int) -> None:
    remaining = delay_ms / 1000 - _elapsed_s(event.occurred_at)
    if remaining > 0:
        await asyncio.sleep(remaining)


def _elapsed_s(occurred_at: str) -> float:
    started = parse_rfc3339(occurred_at)
    now = parse_rfc3339(now_rfc3339())
    if started is None or now is None:
        return 0.0
    return max(0.0, (now - started).total_seconds())


def _play_timeout_s(duration_ms: int) -> float:
    return min(PLAY_TIMEOUT_MAX_S, max(PLAY_TIMEOUT_MIN_S, duration_ms / 1000 + 30.0))


def _detail(outcome: CommandOutcome) -> str:
    reason = outcome["reason_code"]
    message = outcome["terminal_message"]
    return f"{reason} {message}".strip()[:500]


def list_deliveries(limit: int) -> list[schemas.VoiceDeliveryView]:
    return [
        schemas.VoiceDeliveryView(
            id=int(row["id"]),
            rule_id=int(row["rule_id"]),
            device_id=str(row["device_id"]),
            cert_no=str(row["cert_no"]),
            event=DoorEventKind(str(row["event"])),
            door_no=int(row["door_no"]),
            text=str(row["text"]),
            delay_ms=int(row["delay_ms"]),
            occurred_at=str(row["occurred_at"]),
            dispatched_at=str(row["dispatched_at"]),
            status=str(row["status"]),
            detail=str(row["detail"]),
        )
        for row in repository.list_deliveries(limit)
    ]


def _task_done(task: asyncio.Task[None]) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        log_event("ERROR", "voice.task-failed", "语音后台任务失败", error=str(error))
