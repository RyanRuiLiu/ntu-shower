"""语音播报的管理面与设备面 HTTP 端点。

管理面：规则/模板/试听/状态/最近推送（admin）。写路径的编排固定为
「线程池落库 → 线程池预热静态音频 → 事件循环上派发设备预下载」：TTS 与 SQLite
都不上事件循环，create_task 必须在循环线程上调用。
设备面：`/voice/clips/{clip_id}.mp3`，与 `/ota/manifest` 同类，无需身份；文件名是
内容身份，响应可长期缓存。
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.core.problems import (
    INVALID_VOICE_RULE,
    VOICE_CLIP_NOT_FOUND,
    VOICE_EFFECT_NOT_FOUND,
    ApiProblemError,
)
from app.modules.access.dependencies import require_admin

from . import schemas, service, tts

admin_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)], tags=["voice"])
router = APIRouter(tags=["voice"])

_CLIP_ID = re.compile(r"[0-9a-f]{64}")


@admin_router.get("/voice/spec")
def get_spec() -> schemas.VoiceSpecView:
    """事件、占位符与边界；网页据此渲染下拉、帮助和输入范围。"""
    return service.spec()


@admin_router.get("/voice/rules")
def list_rules() -> list[schemas.VoiceRuleView]:
    return service.list_rules()


@admin_router.post("/voice/rules")
async def create_rule(req: schemas.VoiceRuleCreate) -> schemas.VoiceRuleView:
    try:
        view = await run_in_threadpool(service.create_rule, req)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc
    clip = await run_in_threadpool(service.prepare_static, view) if view.enabled else None
    service.schedule_prepare(service.rule_device_targets([view]), clip)
    return view


@admin_router.put("/voice/rules/{rule_id}")
async def update_rule(rule_id: int, req: schemas.VoiceRuleUpdate) -> schemas.VoiceRuleView:
    try:
        view = await run_in_threadpool(service.update_rule, rule_id, req)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc
    clip = await run_in_threadpool(service.prepare_static, view)
    service.schedule_prepare(service.rule_device_targets([view]), clip)
    return view


@admin_router.patch("/voice/rules/{rule_id}")
async def patch_rule(rule_id: int, req: schemas.VoiceRuleEnabledPatch) -> schemas.VoiceRuleView:
    """只改启用位。重新启用静态模板规则时按创建路径预热一遍。"""
    view = await run_in_threadpool(service.set_enabled, rule_id, req.enabled)
    clip = await run_in_threadpool(service.prepare_static, view) if req.enabled else None
    service.schedule_prepare(service.rule_device_targets([view]), clip)
    return view


@admin_router.post("/voice/rules/{rule_id}/push")
async def push_rule(rule_id: int, req: schemas.VoicePushRequest) -> schemas.VoicePushView:
    """手动把一条规则的播报推到指定设备真播一遍；参数与真实事件触发一致。"""
    return await service.push_to_device(rule_id, req.device_id)


@admin_router.delete("/voice/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int) -> None:
    service.delete_rule(rule_id)


@admin_router.post("/voice/preview")
async def preview(req: schemas.VoicePreviewRequest) -> schemas.VoicePreviewView:
    try:
        return await run_in_threadpool(
            service.preview, req.model_copy(update={"voice": req.voice or tts.DEFAULT_VOICE})
        )
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc


@admin_router.get("/voice/status")
def get_status() -> schemas.VoiceStatusView:
    return service.status()


@admin_router.get("/voice/effects")
def list_effects() -> list[schemas.VoiceEffectView]:
    return service.list_effects()


@admin_router.post("/voice/effects")
async def create_effect(
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> schemas.VoiceEffectView:
    """上传一段提示音：任意常见格式，服务端归一化为标准 WAV 并测时长。名称必填。"""
    data = await file.read()
    return await run_in_threadpool(service.create_effect, name, data)


@admin_router.put("/voice/effects/{effect_id}")
async def rename_effect(effect_id: str, req: schemas.VoiceEffectRename) -> schemas.VoiceEffectView:
    """音效改名。被规则模板引用的旧名会级联改写（{fx:旧} → {fx:新}），规则不失效。"""
    return await run_in_threadpool(service.rename_effect, effect_id, req.name)


@admin_router.delete("/voice/effects/{effect_id}", status_code=204)
def delete_effect(effect_id: str) -> None:
    service.delete_effect(effect_id)


@router.get("/voice/effects/{effect_id}.wav")
def download_effect(effect_id: str) -> FileResponse:
    """音效试听下载：文件名是内容身份，与 /voice/clips 同类，无需身份。"""
    if not re.fullmatch(r"[0-9a-f]{64}", effect_id):
        raise ApiProblemError(VOICE_EFFECT_NOT_FOUND, effect_id)
    path = tts.effect_path(effect_id)
    if not path.is_file():
        raise ApiProblemError(VOICE_EFFECT_NOT_FOUND, effect_id)
    return FileResponse(
        path,
        media_type="audio/wav",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@admin_router.get("/voice/deliveries")
def list_deliveries(limit: int = Query(default=50, ge=1, le=200)) -> list[schemas.VoiceDeliveryView]:
    return service.list_deliveries(limit)


@router.get("/voice/clips/{clip_id}.mp3")
def download_clip(clip_id: str) -> FileResponse:
    """设备面音频下载：文件名就是内容身份，内容不会原地变化。"""
    if _CLIP_ID.fullmatch(clip_id) is None:
        raise ApiProblemError(VOICE_CLIP_NOT_FOUND, clip_id)
    path = tts.clip_path(clip_id)
    if not path.is_file():
        raise ApiProblemError(VOICE_CLIP_NOT_FOUND, clip_id)
    return FileResponse(
        path,
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
