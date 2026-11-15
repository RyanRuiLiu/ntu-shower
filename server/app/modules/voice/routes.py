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
from app.core.errors import problem_responses
from app.core.problems import INVALID_VOICE_ASSET, INVALID_VOICE_RULE, VOICE_ASSET_NOT_FOUND, ApiProblemError
from app.db.deps import get_db
from app.db.session import session_scope

from . import schemas, service

admin_router = APIRouter(prefix="/api/v1", tags=["voice"])
router = APIRouter(tags=["voice"])

#: 只读出口的公共错误面。
_READ_RESPONSES = problem_responses(401, 403)
#: 写出口的公共错误面（含请求体校验）。
_WRITE_RESPONSES = problem_responses(401, 403, 422)

_ASSET_ID = re.compile(r"[0-9a-f]{64}")


@admin_router.get(
    "/voice/spec",
    dependencies=[Security(caller, scopes=["voice:read"])],
    summary="语音登记表",
    responses=_READ_RESPONSES,
)
def get_spec(session: Session = Depends(get_db)) -> schemas.VoiceSpecView:
    return service.spec(session)


@admin_router.get(
    "/voice/status",
    dependencies=[Security(caller, scopes=["voice:read"])],
    summary="语音播报状态",
    responses=_READ_RESPONSES,
)
def get_status(session: Session = Depends(get_db)) -> schemas.VoiceStatusView:
    return service.status(session)


@admin_router.get(
    "/voice/assets",
    dependencies=[Security(caller, scopes=["voice:read"])],
    summary="语音素材清单",
    responses=_READ_RESPONSES,
)
def list_assets(session: Session = Depends(get_db)) -> list[schemas.VoiceAssetView]:
    return service.list_assets(session)


@admin_router.post(
    "/voice/assets",
    dependencies=[Security(caller, scopes=["voice:write"])],
    summary="上传语音素材",
    responses=_WRITE_RESPONSES,
)
async def create_asset(
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    digit: Annotated[str, Form()] = "",
    unit: Annotated[str, Form()] = "",
) -> schemas.VoiceAssetView:
    """上传固定音频；可选 0–9 标记为数字素材，或十/百/千/万标记为单位素材。

    会话在线程函数内部开、内部关（铁律见 gateway._call 的注释）：整个生命周期
    待在同一条线程里，与线程池的取消语义解耦。下面四个 async 端点同此。
    """
    if digit.strip():
        if not digit.strip().isdigit() or not (0 <= int(digit) <= 9):
            raise ApiProblemError(INVALID_VOICE_ASSET, "数字标记只能是 0–9")
        digit_value: int | None = int(digit)
    else:
        digit_value = None
    unit_value = unit.strip() or None
    data = await file.read()

    def _create() -> schemas.VoiceAssetView:
        with session_scope() as session:
            return service.create_asset(session, name, data, digit_value, unit_value)

    try:
        return await run_in_threadpool(_create)
    except service.InvalidVoiceAssetError as exc:
        raise ApiProblemError(INVALID_VOICE_ASSET, str(exc)) from exc


@admin_router.patch(
    "/voice/assets/{asset_id}",
    dependencies=[Security(caller, scopes=["voice:write"])],
    summary="重命名素材",
    responses=problem_responses(401, 403, 404, 422),
)
async def rename_asset(asset_id: str, req: schemas.VoiceAssetRename) -> schemas.VoiceAssetView:
    def _rename() -> schemas.VoiceAssetView:
        with session_scope() as session:
            return service.rename_asset(session, asset_id, req.name)

    try:
        return await run_in_threadpool(_rename)
    except service.InvalidVoiceAssetError as exc:
        raise ApiProblemError(INVALID_VOICE_ASSET, str(exc)) from exc


@admin_router.delete(
    "/voice/assets/{asset_id}",
    status_code=204,
    dependencies=[Security(caller, scopes=["voice:write"])],
    summary="删除素材",
    responses=problem_responses(401, 403, 404, 409, 422),
)
def delete_asset(asset_id: str, session: Session = Depends(get_db)) -> None:
    service.delete_asset(session, asset_id)


@router.get("/voice/assets/{asset_id}.mp3", summary="下载素材（设备硬编码契约）", responses=problem_responses(404, 422))
def download_asset(asset_id: str, session: Session = Depends(get_db)) -> FileResponse:
    if _ASSET_ID.fullmatch(asset_id) is None:
        raise ApiProblemError(VOICE_ASSET_NOT_FOUND, asset_id)
    path = service.asset_file(session, asset_id)
    return FileResponse(
        path,
        media_type="audio/mpeg",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@admin_router.get(
    "/voice/rules",
    dependencies=[Security(caller, scopes=["voice:read"])],
    summary="播报规则清单",
    responses=_READ_RESPONSES,
)
def list_rules(session: Session = Depends(get_db)) -> list[schemas.VoiceRuleView]:
    return service.list_rules(session)


@admin_router.post(
    "/voice/rules",
    dependencies=[Security(caller, scopes=["voice:write"])],
    summary="新建播报规则",
    responses=_WRITE_RESPONSES,
)
async def create_rule(req: schemas.VoiceRuleBody) -> schemas.VoiceRuleView:
    def _create() -> schemas.VoiceRuleView:
        with session_scope() as session:
            return service.create_rule(session, req)

    try:
        view = await run_in_threadpool(_create)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc
    # 预下载调度要 spawn asyncio 任务，必须在事件循环线程上执行——重开一条
    # 本线程的会话读现态，与线程函数的会话互不相干。
    with session_scope() as session:
        service.schedule_rule_prepare(session, view)
    return view


@admin_router.put(
    "/voice/rules/{rule_id}",
    dependencies=[Security(caller, scopes=["voice:write"])],
    summary="整份更新规则",
    responses=problem_responses(401, 403, 404, 422),
)
async def update_rule(rule_id: int, req: schemas.VoiceRuleBody) -> schemas.VoiceRuleView:
    def _update() -> schemas.VoiceRuleView:
        with session_scope() as session:
            return service.update_rule(session, rule_id, req)

    try:
        view = await run_in_threadpool(_update)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc
    with session_scope() as session:
        service.schedule_rule_prepare(session, view)
    return view


@admin_router.patch(
    "/voice/rules/{rule_id}",
    dependencies=[Security(caller, scopes=["voice:write"])],
    summary="启用/停用规则",
    responses=problem_responses(401, 403, 404, 422),
)
async def patch_rule(rule_id: int, req: schemas.VoiceRuleEnabledPatch) -> schemas.VoiceRuleView:
    def _patch() -> schemas.VoiceRuleView:
        with session_scope() as session:
            return service.set_enabled(session, rule_id, req.enabled)

    view = await run_in_threadpool(_patch)
    with session_scope() as session:
        service.schedule_rule_prepare(session, view)
    return view


@admin_router.delete(
    "/voice/rules/{rule_id}",
    status_code=204,
    dependencies=[Security(caller, scopes=["voice:write"])],
    summary="删除规则",
    responses=problem_responses(401, 403, 404, 422),
)
def delete_rule(rule_id: int, session: Session = Depends(get_db)) -> None:
    service.delete_rule(session, rule_id)


@admin_router.post(
    "/voice/preview",
    dependencies=[Security(caller, scopes=["voice:write"])],
    summary="规则试听",
    responses=_WRITE_RESPONSES,
)
async def preview(req: schemas.VoicePreviewRequest) -> schemas.VoicePreviewView:
    def _preview() -> schemas.VoicePreviewView:
        with session_scope() as session:
            return service.preview(session, req)

    try:
        return await run_in_threadpool(_preview)
    except service.InvalidVoiceRuleError as exc:
        raise ApiProblemError(INVALID_VOICE_RULE, str(exc)) from exc


@admin_router.post(
    "/voice/rules/{rule_id}/push",
    dependencies=[Security(caller, scopes=["voice:write"])],
    summary="推送规则到设备",
    responses=problem_responses(401, 403, 404, 422),
)
async def push_rule(
    rule_id: int, req: schemas.VoicePushRequest, session: Session = Depends(get_db)
) -> schemas.VoicePushView:
    return await service.push_to_device(session, rule_id, req.device_id)


@admin_router.get(
    "/voice/deliveries",
    dependencies=[Security(caller, scopes=["voice:read"])],
    summary="播报投递记录",
    responses=problem_responses(401, 403, 422),
)
def list_deliveries(
    limit: int = Query(default=50, ge=1, le=200), session: Session = Depends(get_db)
) -> list[schemas.VoiceDeliveryView]:
    return service.list_deliveries(session, limit)
