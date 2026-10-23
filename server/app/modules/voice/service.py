"""语音播报的规则、模板渲染、事件投递与预热编排。

## 唯一判定链

`handle_events` 只做一次「时效 → 匹配 → 渲染 → 认领 → 生成/取缓存 → 延迟 → 下发」。
事件来自 `doors` 的帧派生；规则按「设备/学号两维度各自具体或通配」匹配，具体压过
通配；同一事件只认领一次。

## 模板与缓存

规则存模板，事件发生时用真实会话值渲染成最终文本；`voice_clip` 按最终文本加语速
缓存。静态模板（无占位符）在保存/启用时预热；带占位符的规则不预热，首次出现新文本
时再合成，之后命中缓存。时长占位符每场取值都不同，含它们的模板每次都现场合成——
`voice_delivery.clip_ms` 记录的就是这段耗时。缓存按最近使用淘汰，不会无限增长。

## 延迟与预热

`delay_ms` 从事件发生时刻算起，用来错开浴室 App 自己的结算语音。延迟窗口用于两件
重叠的事：音频合成（服务端）与音频下载（设备端）——合成完成后若窗口还有剩余，先发
`voice.prepare` 让设备把音频拉下来，`voice.play` 到达即命中设备缓存，下载不再排到
延迟之后。实际下发时刻与各阶段耗时写入 `voice_delivery`，现场按该记录与设备日志调整。
"""

import asyncio
import json
import threading
import time
import uuid
from sqlite3 import Row
from typing import NoReturn

from starlette.concurrency import run_in_threadpool

from app.core import actions
from app.core.clock import now_rfc3339, parse_rfc3339
from app.core.logging import log_event, trace_id_var
from app.core.problems import (
    DEVICE_NOT_FOUND,
    INVALID_VOICE_EFFECT,
    INVALID_VOICE_RULE,
    VOICE_EFFECT_IN_USE,
    VOICE_EFFECT_NOT_FOUND,
    VOICE_RULE_NOT_FOUND,
    VOICE_SYNTHESIS_FAILED,
    ApiProblemError,
)
from app.modules.config import store as config_store
from app.modules.devices import service as devices_service
from app.modules.doors.events import DoorEvent, DoorEventKind
from app.modules.sessions import service as sessions_service
from app.modules.sessions.schemas import CommandOutcome

from . import render, repository, schemas, tts

VOICE_PREPARE_TIMEOUT_S = 60.0
#: 延迟窗口内预热的超时 = 剩余窗口 + 这段宽限：窗口本身很短时下载还能在宽限里完成，
#: voice.play 会在设备的串行 voice 通道上排在它后面，总时序不受影响。
PREPARE_GRACE_S = 30.0
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


def _fx_names(text: str) -> list[str]:
    """模板里引用的音效名（去重、保持出现顺序）。"""
    return list(dict.fromkeys(render.FX_NAME_PATTERN.findall(text)))


def _validated_text(text: str, event: DoorEventKind) -> str:
    normalized = _normalize_text(text)
    label = schemas.event_spec(event).label
    try:
        render.validate_template(normalized)
        segments = render.render_segments(normalized, render.sample_values(label))
        render.render_text(segments)
    except render.TemplateError as exc:
        _reject(str(exc))
    missing = [name for name in _fx_names(normalized) if repository.get_effect_by_name(name) is None]
    if missing:
        _reject(f"音效不在库里：{'、'.join(missing)}")
    return normalized



def _rule_view(row: Row) -> schemas.VoiceRuleView:
    event_values = json.loads(str(row["events"]))
    return schemas.VoiceRuleView(
        id=int(row["id"]),
        devices=json.loads(str(row["devices"])),
        certs=json.loads(str(row["certs"])),
        exempt=json.loads(str(row["exempt"])),
        events=[DoorEventKind(value) for value in event_values],
        text=str(row["text"]),
        delay_ms=int(row["delay_ms"]),
        volume=int(row["volume"]),
        enabled=bool(row["enabled"]),
        voice=str(row["voice"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _cert_label(view: schemas.VoiceRuleView) -> str:
    """展示排序用的学号维度标识：具体学号的首个值，通配排最后。"""
    if "*" in view.certs:
        return "~"
    return view.certs[0] if view.certs else "~"


def list_rules() -> list[schemas.VoiceRuleView]:
    """列表顺序即页面呈现顺序：具体学号在前（按学号分组自然聚拢）、通配学号
    最后；同学号维度里具体设备在前、通配在后。"""
    views = [_rule_view(row) for row in repository.list_rules()]
    order = {str(record["device_id"]): index for index, record in enumerate(devices_service.list_devices())}
    views.sort(
        key=lambda view: (
            "*" in view.certs,
            _cert_label(view),
            "*" in view.devices,
            order.get(view.devices[0], len(order)) if view.devices else len(order),
            view.id,
        )
    )
    return views


def spec() -> schemas.VoiceSpecView:
    return schemas.VoiceSpecView(
        events=[
            schemas.VoiceEventView(value=item.value, label=item.label, description=item.description, phase=item.phase)
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
        voices=[
            schemas.VoiceInfoView(key=item.key, engine=item.engine, note=item.note, available=item.available)
            for item in tts.voice_statuses()
        ],
    )


def status() -> schemas.VoiceStatusView:
    import shutil

    return schemas.VoiceStatusView(
        voices=[
            schemas.VoiceInfoView(key=item.key, engine=item.engine, note=item.note, available=item.available)
            for item in tts.voice_statuses()
        ],
        ffmpeg_available=shutil.which("ffmpeg") is not None,
        clip_count=repository.count_clips(),
    )


def _normalized_targets(values: list[str], label: str) -> list[str]:
    """去空格、去重并保持顺序；「全部」与具体值混填直接拒绝。"""
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
    """排除名单与目标名单同一套归一化；「全部」没有排除的意义，出现即拒绝。"""
    targets = _normalized_targets(values, "排除名单")
    if schemas.WILDCARD in targets:
        _reject("排除名单里不能有「全部」")
    return targets


def _exempt_json(cert_nos: list[str], exemptions: list[str]) -> str:
    """排除名单的落库形状；只对「全部学号」的规则有意义，其余组合拒绝。"""
    if exemptions and schemas.WILDCARD not in cert_nos:
        _reject("排除名单只用于「全部学号」的规则")
    return json.dumps(exemptions, ensure_ascii=False)


def _require_known_voice(voice: str) -> None:
    if voice not in tts.VOICES:
        allowed = "、".join(tts.VOICES)
        raise ApiProblemError(INVALID_VOICE_RULE, f"音色只允许 {allowed}（收到 {voice}）")


def _require_known_devices(device_ids: list[str]) -> None:
    for device_id in device_ids:
        if device_id == schemas.WILDCARD:
            continue
        if devices_service.get_device(device_id) is None:
            raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id} 不在档案里")


def create_rule(req: schemas.VoiceRuleCreate) -> schemas.VoiceRuleView:
    """创建一条逻辑规则：目标（设备/学号/排除）与事件按提交的作用域整体存储。"""
    devices = _normalized_targets(req.devices, "设备")
    certs = _normalized_targets(req.certs, "学号")
    _require_known_devices(devices)
    events = list(dict.fromkeys(req.events))
    if not events:
        _reject("至少选择一个事件")
    exempt = _exempt_json(certs, _normalized_exemptions(req.exempt_cert_nos))
    _require_known_voice(req.voice)
    for event in events:
        _validated_text(req.text, event)

    now = now_rfc3339()
    rule_id = repository.insert_rule(
        json.dumps(devices, ensure_ascii=False),
        json.dumps(certs, ensure_ascii=False),
        json.dumps(events, ensure_ascii=False),
        exempt,
        req.text,
        req.delay_ms,
        req.volume,
        req.enabled,
        req.voice,
        now,
    )
    row = repository.get_rule(rule_id)
    if row is None:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    return _rule_view(row)


def update_rule(rule_id: int, req: schemas.VoiceRuleUpdate) -> schemas.VoiceRuleView:
    """整体编辑一条逻辑规则：目标与事件集合是规则的一部分，一并提交。"""
    if repository.get_rule(rule_id) is None:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    devices = _normalized_targets(req.devices, "设备")
    certs = _normalized_targets(req.certs, "学号")
    _require_known_devices(devices)
    events = list(dict.fromkeys(req.events))
    if not events:
        _reject("至少选择一个事件")
    exempt = _exempt_json(certs, _normalized_exemptions(req.exempt_cert_nos))
    _require_known_voice(req.voice)
    for event in events:
        _validated_text(req.text, event)
    if (
        repository.update_rule(
            rule_id,
            json.dumps(devices, ensure_ascii=False),
            json.dumps(certs, ensure_ascii=False),
            json.dumps(events, ensure_ascii=False),
            exempt,
            req.text,
            req.delay_ms,
            req.volume,
            req.enabled,
            req.voice,
            now_rfc3339(),
        )
        == 0
    ):
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    row = repository.get_rule(rule_id)
    if row is None:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    return _rule_view(row)


def set_enabled(rule_id: int, enabled: bool) -> schemas.VoiceRuleView:
    row = repository.get_rule(rule_id)
    if row is None:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    if repository.set_enabled(rule_id, enabled, now_rfc3339()) == 0:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    updated = repository.get_rule(rule_id)
    if updated is None:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    return _rule_view(updated)


def delete_rule(rule_id: int) -> None:
    if repository.delete_rule(rule_id) == 0:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))


def preview(req: schemas.VoicePreviewRequest) -> schemas.VoicePreviewView:
    text = _validated_text(req.text, req.event)
    label = schemas.event_spec(req.event).label
    values = render.sample_values(label, door_no=req.door_no, cert_no=req.cert_no)
    try:
        segments = render.render_segments(text, values)
    except render.TemplateError as exc:
        _reject(str(exc))
    _require_known_voice(req.voice)
    clip, _clip_ms = _ensure_clip(_plan_of(segments), req.voice)
    return schemas.VoicePreviewView(
        rendered_text=render.render_text(segments),
        clip_id=clip.clip_id,
        clip_url=f"/voice/clips/{clip.clip_id}.mp3",
        size_bytes=clip.size_bytes,
        duration_ms=clip.duration_ms,
    )


def _plan_of(segments: list[render.Segment]) -> tts.Plan:
    """渲染分段 → 合成计划：音效名解析成库里的文件，名字不在库即拒绝。"""
    plan: tts.Plan = []
    for segment in segments:
        if segment.kind == render.SAY:
            plan.append(("say", segment.text))
        elif segment.kind == render.FX:
            row = repository.get_effect_by_name(segment.effect)
            if row is None:
                raise ApiProblemError(VOICE_EFFECT_NOT_FOUND, f"音效「{segment.effect}」不在库里")
            path = tts.effect_path(str(row["effect_id"]))
            if not path.is_file():
                raise ApiProblemError(VOICE_EFFECT_NOT_FOUND, f"音效「{segment.effect}」的文件缺失")
            plan.append(("fx", path))
        else:
            plan.append(("pause", segment.pause_ms))
    return plan


def _ensure_clip(plan: tts.Plan, voice: str) -> tuple[tts.ClipFile, int]:
    """取拼装计划（按该规则的音色）对应的音频与全程耗时（含等锁与缓存检查）；
    命中缓存不跑合成，缺失/损坏则重新合成。耗时给 `voice_delivery.clip_ms`，
    现场据此判断排队程度。
    """
    started = time.monotonic()
    clip_id = tts.clip_id_for(plan, voice)
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
                return (
                    tts.ClipFile(
                        clip_id=clip_id,
                        sha256=str(row["sha256"]),
                        size_bytes=int(row["size_bytes"]),
                        duration_ms=int(row["duration_ms"]),
                        path=path,
                    ),
                    int((time.monotonic() - started) * 1000),
                )
        now = now_rfc3339()
        try:
            clip = tts.synthesize(plan, clip_id, voice)
        except tts.VoiceTextTooLongError as exc:
            _reject(str(exc))
        except tts.VoiceSynthesisError as exc:
            raise ApiProblemError(VOICE_SYNTHESIS_FAILED, str(exc)) from exc
        repository.upsert_clip(clip.clip_id, clip.sha256, clip.size_bytes, clip.duration_ms, now)
        for removed_id in repository.prune_clips(tts.MAX_CLIP_ROWS, tts.MAX_CLIP_TOTAL_BYTES):
            tts.clip_path(removed_id).unlink(missing_ok=True)
        return clip, int((time.monotonic() - started) * 1000)


def prepare_static(view: schemas.VoiceRuleView) -> tts.ClipFile | None:
    """静态模板保存/启用时预热；含任一种记法（占位符/音效/停顿）的模板不预热——
    它们的最终音频要等事件发生才知道。"""
    if render.has_placeholders(view.text):
        return None
    try:
        clip, _clip_ms = _ensure_clip([("say", view.text)], view.voice)
    except Exception as exc:  # noqa: BLE001 —— 预热失败不能把已保存的规则判成失败
        log_event(
            "WARNING",
            "voice.prepare-skipped",
            "静态模板预热失败，首次播报时会再试",
            rule_id=view.id,
            devices=",".join(view.devices),
            error=str(exc),
        )
        return None
    return clip


def rule_device_targets(views: list[schemas.VoiceRuleView]) -> list[str]:
    """这批规则要预热到哪些设备：作用域里的通配展开为全部在档设备，具体设备去重。"""
    targets: list[str] = []
    for view in views:
        if "*" in view.devices:
            return [str(record["device_id"]) for record in devices_service.list_devices()]
        targets.extend(view.devices)
    return list(dict.fromkeys(targets))


def _clip_params(clip: tts.ClipFile, volume: int | None = None) -> dict[str, object]:
    params: dict[str, object] = {
        "clip_id": clip.clip_id,
        "sha256": clip.sha256,
        "size_bytes": clip.size_bytes,
        "duration_ms": clip.duration_ms,
    }
    if volume is not None:
        params["volume"] = volume
    return params


def _spawn_prepare(device_id: str, clip: tts.ClipFile, timeout_s: float) -> None:
    """后台让设备预下载一段音频；失败只留痕，不影响之后的 voice.play。"""
    try:
        task = asyncio.get_running_loop().create_task(_prepare(device_id, _clip_params(clip), timeout_s))
    except Exception as exc:  # noqa: BLE001 —— 调度失败只记日志，不影响保存响应
        log_event("ERROR", "voice.schedule-failed", "语音预热调度失败", device_id=device_id, error=str(exc))
        return
    _tasks.add(task)
    task.add_done_callback(_task_done)


def schedule_prepare(device_ids: list[str], clip: tts.ClipFile | None) -> None:
    """规则保存/启用后，把静态音频推到目标设备预下载。"""
    if clip is None:
        return
    for device_id in device_ids:
        _spawn_prepare(device_id, clip, VOICE_PREPARE_TIMEOUT_S)


async def _prepare(device_id: str, params: dict[str, object], timeout_s: float) -> None:
    try:
        outcome = await sessions_service.send_command_and_wait(
            device_id,
            actions.VOICE_PREPARE,
            params,
            timeout_s=timeout_s,
        )
    except Exception as exc:  # noqa: BLE001 —— 预热是后台优化，失败只留痕
        log_event("WARNING", "voice.prepare-failed", "语音预热失败", device_id=device_id, error=str(exc))
        return
    log_event(
        "INFO",
        "voice.prepared",
        "语音预热命令已收尾",
        device_id=device_id,
        clip_id=str(params.get("clip_id", "")),
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

    matched = repository.matching_rules(event.device_id, event.cert_no, event.kind.value)
    rule = matched[0] if matched else None
    if rule is None:
        log_event(
            "DEBUG",
            "voice.no-rule",
            "没有匹配的语音规则",
            device_id=event.device_id,
            event=event.kind.value,
        )
        return

    prepared = _event_segments(event, str(rule["text"]))
    if prepared is None:
        return
    plan, display_text = prepared
    clip_id = tts.clip_id_for(plan, str(rule["voice"]))
    if not repository.claim_delivery(
        event,
        rule,
        display_text,
        clip_id,
        event.occurred_at,
        trace_id_var.get(),
        now_rfc3339(),
    ):
        return

    try:
        # 合成最长 60+30 秒且持 _clip_lock：必须离开事件循环线程去跑，否则网关的
        # 心跳与全部 HTTP 一起冻结。路由层的 preview/prepare 同理已用线程池。
        clip, clip_ms = await run_in_threadpool(_ensure_clip, plan, str(rule["voice"]))
    except Exception as exc:  # noqa: BLE001 —— 认领后失败要留下结果，但不重试
        repository.finish_delivery(event.event_key, "", "failed", str(exc)[:500], now_rfc3339(), False)
        log_event(
            "ERROR",
            "voice.delivery-failed",
            "语音音频准备失败",
            device_id=event.device_id,
            event=event.kind.value,
            error=str(exc),
        )
        return
    repository.mark_clip_ready(event.event_key, now_rfc3339(), clip_ms)

    delay_ms = int(rule["delay_ms"])
    remaining_s = delay_ms / 1000 - _elapsed_s(event.occurred_at)
    if remaining_s > 0:
        # 延迟窗口还有剩余：先让设备把音频拉下来，voice.play 到达即命中设备缓存，
        # 下载不再排到延迟之后。不等它完成——设备串行 voice 通道自然排队，晚到的
        # play 排在它后面，先后顺序不变。
        _spawn_prepare(event.device_id, clip, remaining_s + PREPARE_GRACE_S)
        await asyncio.sleep(remaining_s)
    if max_age > 0 and _elapsed_s(event.occurred_at) > max_age:
        repository.finish_delivery(event.event_key, "", "skipped", "事件已超过时效", now_rfc3339(), False)
        log_event(
            "INFO",
            "voice.event-stale",
            "语音事件在延迟期间超过时效，跳过",
            device_id=event.device_id,
            event=event.kind.value,
        )
        return

    dispatched_at = now_rfc3339()
    outcome = await sessions_service.send_command_and_wait(
        event.device_id,
        actions.VOICE_PLAY,
        _clip_params(clip, int(rule["volume"])),
        timeout_s=_play_timeout_s(clip.duration_ms),
    )
    downloaded = _downloaded(outcome)
    repository.finish_delivery(
        event.event_key,
        dispatched_at,
        str(outcome["status"]),
        _detail(outcome),
        now_rfc3339(),
        downloaded,
    )
    log_event(
        "INFO",
        "voice.delivery-finished",
        "语音播放命令已收尾",
        device_id=event.device_id,
        event=event.kind.value,
        status=str(outcome["status"]),
        reason_code=str(outcome["reason_code"] or "-"),
        delay_ms=delay_ms,
        clip_ms=clip_ms,
        downloaded=downloaded,
    )


async def push_to_device(rule_id: int, device_id: str) -> schemas.VoicePushView:
    """把一条规则的播报立即推到指定设备真播一遍（试听的真机版）。

    渲染、合成、下发参数与真实事件走同一条路径：同模板、同样例值、同音色、同音量、
    同播放超时。与真实事件只差两处：不等 delay_ms（那是给浴室 App 语音让路的，
    手动验证不需要）；投递记录以 manual:* 事件键落一条，现场在「最近播报」对照。
    """
    row = repository.get_rule(rule_id)
    if row is None:
        raise ApiProblemError(VOICE_RULE_NOT_FOUND, str(rule_id))
    if devices_service.get_device(device_id) is None:
        raise ApiProblemError(DEVICE_NOT_FOUND, f"设备 {device_id} 不在档案里")
    events = json.loads(str(row["events"]))
    event_kind = DoorEventKind(events[0])
    certs = json.loads(str(row["certs"]))
    cert_no = certs[0] if "*" not in certs and certs else "示例学号"
    values = render.sample_values(schemas.event_spec(event_kind).label, door_no=1, cert_no=cert_no)
    try:
        segments = render.render_segments(str(row["text"]), values)
    except render.TemplateError as exc:
        _reject(str(exc))
    _require_known_voice(str(row["voice"]))
    plan, rendered_text = _plan_of(segments), render.render_text(segments)
    clip, _clip_ms = await run_in_threadpool(_ensure_clip, plan, str(row["voice"]))

    now = now_rfc3339()
    event = DoorEvent(
        kind=event_kind,
        device_id=device_id,
        door_no=1,
        cert_no=cert_no,
        session_id=0,
        occurred_at=now,
        event_key=f"manual:{rule_id}:{uuid.uuid4().hex[:12]}",
    )
    repository.claim_delivery(event, row, rendered_text, clip.clip_id, now, trace_id_var.get(), now)
    outcome = await sessions_service.send_command_and_wait(
        device_id,
        actions.VOICE_PLAY,
        _clip_params(clip, int(row["volume"])),
        timeout_s=_play_timeout_s(clip.duration_ms),
    )
    downloaded = _downloaded(outcome)
    repository.finish_delivery(
        event.event_key,
        now_rfc3339(),
        str(outcome["status"]),
        "手动推送：" + _detail(outcome),
        now_rfc3339(),
        downloaded,
    )
    log_event(
        "INFO",
        "voice.push-finished",
        "手动推送播放已收尾",
        device_id=device_id,
        rule_id=rule_id,
        status=str(outcome["status"]),
        reason_code=str(outcome["reason_code"] or "-"),
        downloaded=downloaded,
    )
    return schemas.VoicePushView(
        device_id=device_id,
        rendered_text=rendered_text,
        status=str(outcome["status"]),
        reason_code=str(outcome["reason_code"] or ""),
        detail=_detail(outcome),
        duration_ms=clip.duration_ms,
    )


def _event_segments(event: DoorEvent, template: str) -> tuple[tts.Plan, str] | None:
    """真实事件值渲染模板 → 合成计划与展示文本；渲染失败返回 None。

    占位符只有事件自带的事实（喷头号/学号/目标状态），播报不依赖会话表——
    会话找不到也能照常播（曾为时长占位符查会话，已随时长占位符一并移除）。
    """
    values = render.sample_values(
        schemas.event_spec(event.kind).label,
        door_no=event.door_no,
        cert_no=event.cert_no,
    )
    try:
        segments = render.render_segments(template, values)
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
    return _plan_of(segments), render.render_text(segments)


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


def _downloaded(outcome: CommandOutcome) -> bool:
    """设备结果里带的「是否现场下载」；结果没有 data 时按未下载记。"""
    data = outcome["data"]
    return bool(data.get("downloaded")) if isinstance(data, dict) else False


def _offset_ms(start: str, end: str) -> int | None:
    """两个时刻的毫秒差；任一端缺失或无法解析即为 None（跳过的投递没有下发时刻）。"""
    if not start or not end:
        return None
    begin = parse_rfc3339(start)
    finish = parse_rfc3339(end)
    if begin is None or finish is None:
        return None
    return max(0, int((finish - begin).total_seconds() * 1000))


def _effect_view(row: Row) -> schemas.VoiceEffectView:
    return schemas.VoiceEffectView(
        effect_id=str(row["effect_id"]),
        name=str(row["name"]),
        size_bytes=int(row["size_bytes"]),
        duration_ms=int(row["duration_ms"]),
        created_at=str(row["created_at"]),
    )


def list_effects() -> list[schemas.VoiceEffectView]:
    return [_effect_view(row) for row in repository.list_effects()]


def create_effect(name: str, data: bytes) -> schemas.VoiceEffectView:
    """收一段音频：归一化成标准 WAV、算内容身份、登记入库。

    内容身份按归一化后的字节算：同一段声音换个容器再传不会变成两个音效，
    只会更新名字。名称由前端选中文件时自动填入（用户可见可改），必填。
    """
    name = " ".join(name.split())
    if not name:
        raise ApiProblemError(INVALID_VOICE_EFFECT, "音效名称不能为空")
    if len(name) > 40:
        raise ApiProblemError(INVALID_VOICE_EFFECT, "音效名称最多 40 个字符")
    if not data:
        raise ApiProblemError(INVALID_VOICE_EFFECT, "音效文件为空")
    if len(data) > tts.MAX_EFFECT_UPLOAD_BYTES:
        raise ApiProblemError(
            INVALID_VOICE_EFFECT, f"音效文件超过 {tts.MAX_EFFECT_UPLOAD_BYTES // (1024 * 1024)}MB 上限"
        )

    tts.effects_dir().mkdir(parents=True, exist_ok=True)
    tts.tmp_dir().mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    source = tts.tmp_dir() / f"effect-upload.{token}.src"
    normalized = tts.tmp_dir() / f"effect-upload.{token}.wav"
    try:
        source.write_bytes(data)
        try:
            duration_ms = tts.normalize_to_wav(source, normalized)
        except tts.VoiceSynthesisError as exc:
            raise ApiProblemError(INVALID_VOICE_EFFECT, f"音效解不开：{exc}") from exc
        if duration_ms <= 0:
            raise ApiProblemError(INVALID_VOICE_EFFECT, "音效时长为零")
        if duration_ms > tts.MAX_EFFECT_DURATION_MS:
            raise ApiProblemError(
                INVALID_VOICE_EFFECT,
                f"音效超过 {tts.MAX_EFFECT_DURATION_MS // 1000} 秒上限（收到 {duration_ms / 1000:.1f} 秒）",
            )
        effect_id = tts.sha256_of(normalized)
        # 名字唯一是 {fx:名} 能唯一解析的前提：与库内其他音效重名直接拒绝。
        # 同内容同名重传在这里天然放行（effect_id 相同，走的是改名/补登记）。
        squatter = repository.get_effect_by_name(name)
        if squatter is not None and str(squatter["effect_id"]) != effect_id:
            raise ApiProblemError(INVALID_VOICE_EFFECT, f"名称「{name}」已被其他音效占用")
        target = tts.effect_path(effect_id)
        normalized.replace(target)
    finally:
        source.unlink(missing_ok=True)
        normalized.unlink(missing_ok=True)
    existing = repository.get_effect(effect_id)
    if existing is not None and str(existing["name"]) != name:
        # 同内容换新名 = 改名：旧名被规则模板引用时级联改写（{fx:旧} → {fx:新}），
        # 与改名端点同一语义——规则不因改名而失效
        rewritten = repository.rewrite_rule_fx_references(str(existing["name"]), name, now_rfc3339())
        if rewritten:
            log_event(
                "INFO",
                "voice.effect-renamed",
                "音效已改名，引用它的规则模板一并改写",
                effect_id=effect_id,
                old=str(existing["name"]),
                new=name,
                rules_rewritten=rewritten,
            )
    repository.upsert_effect(effect_id, name, target.stat().st_size, duration_ms, now_rfc3339())
    row = repository.get_effect(effect_id)
    if row is None:
        raise ApiProblemError(VOICE_EFFECT_NOT_FOUND, effect_id)
    return _effect_view(row)


def rename_effect(effect_id: str, name: str) -> schemas.VoiceEffectView:
    """音效改名。旧名被规则模板引用时级联改写（{fx:旧} → {fx:新}），规则不失效；
    新名与库内其他音效重名则拒绝——模板按名字解析音效，重名会指不出是哪一个。"""
    row = repository.get_effect(effect_id)
    if row is None:
        raise ApiProblemError(VOICE_EFFECT_NOT_FOUND, effect_id)
    old_name = str(row["name"])
    new_name = " ".join(name.split())
    if not new_name:
        raise ApiProblemError(INVALID_VOICE_EFFECT, "音效名称不能为空")
    if len(new_name) > 40:
        raise ApiProblemError(INVALID_VOICE_EFFECT, "音效名称最多 40 个字符")
    if new_name == old_name:
        return _effect_view(row)
    other = repository.get_effect_by_name(new_name)
    if other is not None and str(other["effect_id"]) != effect_id:
        raise ApiProblemError(INVALID_VOICE_EFFECT, f"名称「{new_name}」已被其他音效占用")
    rewritten = repository.rewrite_rule_fx_references(old_name, new_name, now_rfc3339())
    repository.rename_effect(effect_id, new_name)
    log_event(
        "INFO",
        "voice.effect-renamed",
        "音效已改名，引用它的规则模板一并改写",
        effect_id=effect_id,
        old=old_name,
        new=new_name,
        rules_rewritten=rewritten,
    )
    updated = repository.get_effect(effect_id)
    if updated is None:
        raise ApiProblemError(VOICE_EFFECT_NOT_FOUND, effect_id)
    return _effect_view(updated)


def delete_effect(effect_id: str) -> None:
    row = repository.get_effect(effect_id)
    if row is None:
        raise ApiProblemError(VOICE_EFFECT_NOT_FOUND, effect_id)
    used = repository.count_rules_referencing_fx(str(row["name"]))
    if used > 0:
        raise ApiProblemError(VOICE_EFFECT_IN_USE, f"有 {used} 条规则的模板写着 {{fx:{row['name']}}}，先改规则再删")
    repository.delete_effect(effect_id)
    tts.effect_path(effect_id).unlink(missing_ok=True)


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
            done_at=str(row["done_at"]),
            status=str(row["status"]),
            detail=str(row["detail"]),
            clip_ms=int(row["clip_ms"]),
            dispatch_offset_ms=_offset_ms(str(row["occurred_at"]), str(row["dispatched_at"])),
            finish_offset_ms=_offset_ms(str(row["dispatched_at"]), str(row["done_at"])),
            downloaded=bool(row["downloaded"]),
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
