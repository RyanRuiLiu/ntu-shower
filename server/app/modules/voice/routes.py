"""语音播报的管理面与设备面 HTTP 端点。

管理面：素材库、规则分段、预览、推送、投递记录（admin）。
设备面：`/voice/assets/{asset_id}.mp3`，文件名是内容身份，无需身份、可长期缓存。

写路径把 ffmpeg/SQLite 放到线程池；规则保存后只调度在线设备的素材预下载，不阻塞
保存响应。
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.core.problems import INVALID_VOICE_ASSET, INVALID_VOICE_RULE, VOICE_ASSET_NOT_FOUND, ApiProblemError
from app.modules.access.dependencies import require_admin

from . import schemas, service

admin_router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_admin)], tags=["voice"])
router = APIRouter(tags=["voice"])

_ASSET_ID = re.compile(r"[0-9a-f]{64}")


@admin_router.get("/voice/spec")
def get_spec() -> schemas.VoiceSpecView:
    return service.spec()


@admin_router.get("/voice/status")
def get_status() -> schemas.VoiceStatusView:
    return service.status()


@admin_router.get("/voice/assets")
def list_assets() -> list[schemas.VoiceAssetView]:
    return service.list_assets()


@admin_router.post("/voice/assets")
async def create_asset(
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    digit: Annotated[str, Form()] = "",
    unit: Annotated[str, Form()] = "",
) -> schemas.VoiceAssetView:
    """上传固定音频；可选 0–9 标记为数字素材，或十/百/千/万标记为单位素材。"""
    if digit.strip():
        if not digit.strip().isdigit() or not (0 <= int(digit) <= 9):
            raise ApiProblemError(INVALID_VOICE_ASSET, "数字标记只能是 0–9")
        digit_value: int | None = int(digit)
    else:
        digit_value = None
    unit_value = unit.strip() or None
    data = await file.read()
    try:
        return await run_in_threadpool(service.create_asset, name, data, digit_value, unit_value)
    except service.InvalidVoiceAssetError as exc:
        raise ApiProblemError(INVALID_VOICE_ASSET, str(exc)) from exc


@admin_router.patch("/voice/assets/{asset_id}")
async def rename_asset(asset_id: str, req: schemas.VoiceAssetRename) -> schemas.VoiceAssetView:
    try:
        return await run_in_threadpool(service.rename_asset, asset_id, req.name)
    except service.InvalidVoiceAssetError as exc:
        raise ApiProblemError(INVALID_VOICE_ASSET, str(exc)) from exc


@admin_router.delete("/voice/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: str) -> None:
    service.delete_asset(asset_id)


@router.get("/voice/assets/{asset_id}.mp3")
def download_asset(asset_id: str) -> FileResponse:
    if _ASSET_ID.fullmatch(asset_id) is None:
        raise ApiProblemError(VOICE_ASSET_NOT_FOUND, asset_id)
    path = service.asset_file(asset_id)
    return FileResponse(
        path,
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@admin_router.get("/voice/rules")
def list_rules() -> list[schemas.VoiceRuleView]:
    return service.list_rules()


@admin_router.post("/voice/rules")
async def create_rule(req: schemas.VoiceRuleCreate) -> schemas.VoiceRuleView:
    try:
        view = await run_in_threadpool(service.create_rule, req)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc
    service.schedule_rule_prepare(view)
    return view


@admin_router.put("/voice/rules/{rule_id}")
async def update_rule(rule_id: int, req: schemas.VoiceRuleUpdate) -> schemas.VoiceRuleView:
    try:
        view = await run_in_threadpool(service.update_rule, rule_id, req)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc
    service.schedule_rule_prepare(view)
    return view


@admin_router.patch("/voice/rules/{rule_id}")
async def patch_rule(rule_id: int, req: schemas.VoiceRuleEnabledPatch) -> schemas.VoiceRuleView:
    view = await run_in_threadpool(service.set_enabled, rule_id, req.enabled)
    service.schedule_rule_prepare(view)
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


@admin_router.post("/voice/rules/{rule_id}/push")
async def push_rule(rule_id: int, req: schemas.VoicePushRequest) -> schemas.VoicePushView:
    return await service.push_to_device(rule_id, req.device_id)


@admin_router.get("/voice/deliveries")
def list_deliveries(limit: int = Query(default=50, ge=1, le=200)) -> list[schemas.VoiceDeliveryView]:
    return service.list_deliveries(limit)
