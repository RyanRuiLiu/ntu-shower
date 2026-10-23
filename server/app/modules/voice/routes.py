"""语音播报的管理面与设备面 HTTP 端点。

管理面：规则/模板/试听/状态/最近播报（admin）。
设备面：`/voice/clips/{clip_id}.mp3`，与 `/ota/manifest` 同类，无需身份；文件名是
内容身份，响应可长期缓存。
"""

import re

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.core.problems import (
    INVALID_VOICE_RULE,
    VOICE_CLIP_NOT_FOUND,
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
def list_rules(device_id: str | None = Query(default=None)) -> list[schemas.VoiceRuleView]:
    return service.list_rules(device_id)


@admin_router.post("/voice/rules")
async def create_rule(req: schemas.VoiceRuleCreate) -> schemas.VoiceRuleView:
    try:
        view = await run_in_threadpool(service.create_rule, req)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc
    clip = await run_in_threadpool(service.prepare_static, view)
    service.schedule_prepare(view, clip)
    return view


@admin_router.put("/voice/rules/{rule_id}")
async def update_rule(rule_id: int, req: schemas.VoiceRuleUpdate) -> schemas.VoiceRuleView:
    try:
        view = await run_in_threadpool(service.update_rule, rule_id, req)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc
    clip = await run_in_threadpool(service.prepare_static, view)
    service.schedule_prepare(view, clip)
    return view


@admin_router.delete("/voice/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int) -> None:
    service.delete_rule(rule_id)


@admin_router.post("/voice/preview")
async def preview(req: schemas.VoicePreviewRequest) -> schemas.VoicePreviewView:
    try:
        return await run_in_threadpool(service.preview, req)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc


@admin_router.get("/voice/status")
def get_status() -> schemas.VoiceStatusView:
    return service.status()


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
