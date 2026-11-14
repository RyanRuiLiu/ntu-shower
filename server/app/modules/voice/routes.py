"""语音播报的管理面与设备面 HTTP 端点。

管理面：素材库、规则分段、预览、推送、投递记录（admin）。
设备面：`/voice/assets/{asset_id}.mp3`，文件名是内容身份，无需身份、可长期缓存。

写路径把 ffmpeg/SQLite 放到线程池；规则保存后只调度在线设备的素材预下载，不阻塞
保存响应。
"""

import re
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, Security, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import caller
from app.core.problems import INVALID_VOICE_ASSET, INVALID_VOICE_RULE, VOICE_ASSET_NOT_FOUND, ApiProblemError
from app.db.deps import get_db

from . import schemas, service

admin_router = APIRouter(prefix="/api/v1", tags=["voice"])
router = APIRouter(tags=["voice"])

_ASSET_ID = re.compile(r"[0-9a-f]{64}")


@admin_router.get("/voice/spec", dependencies=[Security(caller, scopes=["voice:read"])])
def get_spec(session: Session = Depends(get_db)) -> schemas.VoiceSpecView:
    return service.spec(session)


@admin_router.get("/voice/status", dependencies=[Security(caller, scopes=["voice:read"])])
def get_status(session: Session = Depends(get_db)) -> schemas.VoiceStatusView:
    return service.status(session)


@admin_router.get("/voice/assets", dependencies=[Security(caller, scopes=["voice:read"])])
def list_assets(session: Session = Depends(get_db)) -> list[schemas.VoiceAssetView]:
    return service.list_assets(session)


@admin_router.post("/voice/assets", dependencies=[Security(caller, scopes=["voice:write"])])
async def create_asset(
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    digit: Annotated[str, Form()] = "",
    unit: Annotated[str, Form()] = "",
    session: Session = Depends(get_db),
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
        return await run_in_threadpool(service.create_asset, session, name, data, digit_value, unit_value)
    except service.InvalidVoiceAssetError as exc:
        raise ApiProblemError(INVALID_VOICE_ASSET, str(exc)) from exc


@admin_router.patch("/voice/assets/{asset_id}", dependencies=[Security(caller, scopes=["voice:write"])])
async def rename_asset(
    asset_id: str, req: schemas.VoiceAssetRename, session: Session = Depends(get_db)
) -> schemas.VoiceAssetView:
    try:
        return await run_in_threadpool(service.rename_asset, session, asset_id, req.name)
    except service.InvalidVoiceAssetError as exc:
        raise ApiProblemError(INVALID_VOICE_ASSET, str(exc)) from exc


@admin_router.delete(
    "/voice/assets/{asset_id}", status_code=204, dependencies=[Security(caller, scopes=["voice:write"])]
)
def delete_asset(asset_id: str, session: Session = Depends(get_db)) -> None:
    service.delete_asset(session, asset_id)


@router.get("/voice/assets/{asset_id}.mp3")
def download_asset(asset_id: str, session: Session = Depends(get_db)) -> FileResponse:
    if _ASSET_ID.fullmatch(asset_id) is None:
        raise ApiProblemError(VOICE_ASSET_NOT_FOUND, asset_id)
    path = service.asset_file(session, asset_id)
    return FileResponse(
        path,
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@admin_router.get("/voice/rules", dependencies=[Security(caller, scopes=["voice:read"])])
def list_rules(session: Session = Depends(get_db)) -> list[schemas.VoiceRuleView]:
    return service.list_rules(session)


@admin_router.post("/voice/rules", dependencies=[Security(caller, scopes=["voice:write"])])
async def create_rule(req: schemas.VoiceRuleBody, session: Session = Depends(get_db)) -> schemas.VoiceRuleView:
    try:
        view = await run_in_threadpool(service.create_rule, session, req)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc
    service.schedule_rule_prepare(session, view)
    return view


@admin_router.put("/voice/rules/{rule_id}", dependencies=[Security(caller, scopes=["voice:write"])])
async def update_rule(
    rule_id: int, req: schemas.VoiceRuleBody, session: Session = Depends(get_db)
) -> schemas.VoiceRuleView:
    try:
        view = await run_in_threadpool(service.update_rule, session, rule_id, req)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc
    service.schedule_rule_prepare(session, view)
    return view


@admin_router.patch("/voice/rules/{rule_id}", dependencies=[Security(caller, scopes=["voice:write"])])
async def patch_rule(
    rule_id: int, req: schemas.VoiceRuleEnabledPatch, session: Session = Depends(get_db)
) -> schemas.VoiceRuleView:
    view = await run_in_threadpool(service.set_enabled, session, rule_id, req.enabled)
    service.schedule_rule_prepare(session, view)
    return view


@admin_router.delete("/voice/rules/{rule_id}", status_code=204, dependencies=[Security(caller, scopes=["voice:write"])])
def delete_rule(rule_id: int, session: Session = Depends(get_db)) -> None:
    service.delete_rule(session, rule_id)


@admin_router.post("/voice/preview", dependencies=[Security(caller, scopes=["voice:write"])])
async def preview(req: schemas.VoicePreviewRequest, session: Session = Depends(get_db)) -> schemas.VoicePreviewView:
    try:
        return await run_in_threadpool(service.preview, session, req)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc


@admin_router.post("/voice/rules/{rule_id}/push", dependencies=[Security(caller, scopes=["voice:write"])])
async def push_rule(
    rule_id: int, req: schemas.VoicePushRequest, session: Session = Depends(get_db)
) -> schemas.VoicePushView:
    return await service.push_to_device(session, rule_id, req.device_id)


@admin_router.get("/voice/deliveries", dependencies=[Security(caller, scopes=["voice:read"])])
def list_deliveries(
    limit: int = Query(default=50, ge=1, le=200), session: Session = Depends(get_db)
) -> list[schemas.VoiceDeliveryView]:
    return service.list_deliveries(session, limit)
