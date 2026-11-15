"""发布域 API：清单、下载与发布管理，统一在 /api/v1/ota 下。

权限分层：清单与下载公开（设备按 manifest 的 url 下载，服务器控制路径）；
发布、下架、重推要求 admin；manifest 是设备的硬编码契约，保留无版本前缀的
冻结路径。

版本号在 HTTP 边界即按 VERSION_PATTERN 校验，service.release_path 按同一规则
构造文件名——两处各自成立，不依赖对方先拦。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, Security, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.auth.dependencies import caller, device_scope_guard
from app.contracts import Caller
from app.core.config import settings
from app.core.errors import problem_responses
from app.core.logging import log_event
from app.core.problems import (
    OTA_APK_MISMATCH,
    RELEASE_NOT_FOUND,
    RELEASE_TOO_LARGE,
    VALIDATION_FAILED,
    ApiProblemError,
)
from app.db.deps import get_db
from app.modules.ota import apk_manifest, service
from app.modules.ota import crud as repository
from app.modules.ota.schemas import (
    OtaManifest,
    ReleasePublished,
    ReleaseRearmed,
    ReleaseRearmRequest,
    ReleaseRecord,
    ReleaseRemoved,
)
from app.modules.ota.service import VERSION_PATTERN

router = APIRouter(prefix="/api/v1/ota", tags=["ota"])

# 设备硬编码契约：manifest 的路径不随版本前缀走（冻结，见 docs/architecture.md）
device_router = APIRouter(tags=["ota"])

# 路径/表单中的发布版本号：格式不合规由 FastAPI 返回 422
ReleaseVersion = Annotated[str, Path(pattern=VERSION_PATTERN)]
ReleaseVersionForm = Annotated[str, Form(pattern=VERSION_PATTERN)]


def _parse_targets(raw: str) -> list[str]:
    """发布表单的 target 文本 → 目标设备列表：'all' → ['*']，逗号分隔 → 数组。

    存储与通知共用这一种形状（ota_releases.target），通配词汇 `*` 与 voice_rules
    的设备维度一致。
    """
    value = raw.strip()
    if value == "all":
        return ["*"]
    targets = [item.strip() for item in value.split(",") if item.strip()]
    if not targets:
        raise ApiProblemError(VALIDATION_FAILED, "target 不能解析出任何目标设备")
    return targets


@router.get("/releases", summary="发布清单")
def list_releases(session: Session = Depends(get_db)) -> list[ReleaseRecord]:
    return repository.list_releases(session)


@device_router.get("/ota/manifest", summary="设备升级清单（硬编码契约）", responses=problem_responses(422))
def manifest(device_id: str, version_code: int, session: Session = Depends(get_db)) -> OtaManifest:
    """设备定期轮询：返回是否有可用更新及其元数据。路径为设备硬编码契约，冻结。"""
    rel = repository.latest_for(session, device_id, version_code)
    if rel is None:
        return {"up_to_date": True}
    path = service.release_path(rel["version"])
    if not path.is_file():
        # 库里有记录、文件却不在（手工清理过目录之类）：当作没有更新，而不是给
        # 设备一个下不动的地址
        log_event("WARNING", "ota.release-file-missing", "发布文件缺失", version=str(rel["version"]))
        return {"up_to_date": True}
    return {
        "up_to_date": False,
        "version": rel["version"],
        "version_code": rel["version_code"],
        "url": f"/api/v1/ota/releases/{rel['version']}/download",
        # sha256 是设备端发起下载前就要读的校验基准，缺了它升级在下载前即中止——
        # manifest 的字段集是设备硬编码契约，tests/test_ota.py 逐字段断言锁死
        "sha256": rel["sha256"],
        # 大小现取文件的：它是文件的属性，存一份副本会在手工替换后对不上
        "size": path.stat().st_size,
        "force": bool(rel["force"]),
    }


@router.get("/releases/{version}/download", summary="下载发布 APK", responses=problem_responses(404, 422))
def download(version: ReleaseVersion) -> FileResponse:
    path = service.release_path(version)
    if not path.is_file():
        raise ApiProblemError(RELEASE_NOT_FOUND, f"版本 {version}")
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=f"ntu-shower-{version}.apk",
    )


@router.post(
    "/releases/{version}/rearm",
    dependencies=[Security(caller, scopes=["ota:write"])],
    summary="清除安装确认（重新生效）",
    responses=problem_responses(401, 403, 422),
)
def rearm_release(
    version: str, req: ReleaseRearmRequest, caller: Caller = Depends(caller), session: Session = Depends(get_db)
) -> ReleaseRearmed:
    """清除安装确认：force 发布对该设备重新生效（调试重推）。目标设备在请求体里。"""
    device_id = req.device_id
    device_scope_guard(device_id, caller)
    return {"version": version, "device_id": device_id, "rearmed": repository.rearm(session, device_id, version)}


@router.delete(
    "/releases/{version}",
    dependencies=[Security(caller, scopes=["ota:write"])],
    summary="下架发布",
    responses=problem_responses(401, 403, 422),
)
def delete_release(version: str, session: Session = Depends(get_db)) -> ReleaseRemoved:
    """下架发布（紧急止损，如终止 OTA 循环）。注册行与 APK 文件一并删除。"""
    deleted = repository.delete_release(session, version)
    file_removed = service.remove_release_file(version)
    if deleted or file_removed:
        log_event("INFO", "ota.release-deleted", "发布已下架", version=version, file_removed=file_removed)
    return {"version": version, "deleted": deleted}


@router.post(
    "/releases",
    dependencies=[Security(caller, scopes=["ota:write"])],
    summary="发布新版本",
    responses=problem_responses(401, 403, 413, 422),
)
async def upload_release(
    version: ReleaseVersionForm,
    version_code: Annotated[int, Form()],
    file: Annotated[UploadFile, File()],
    force: Annotated[bool, Form()] = False,
    target: Annotated[str, Form()] = "all",
    session: Session = Depends(get_db),
) -> ReleasePublished:
    """上传新版本 APK 并注册发布（target='all' 表示全部设备，或逗号分隔的 device_id 列表）。

    发布成功后向在线的目标设备即时推送 ota.upgrade_available。

    **版本必须在 HTTP 边界与 APK 内容比对**（根因闸门，源自 2026-10-24 事故）：
    曾有表单声明 0.4.40、APK 实际为 0.4.39 的发布被直接受理，六台设备安装成功
    而版本不变，陷入无限重装循环。APK 的真实版本从二进制 manifest 解析
    （apk_manifest），不一致即拒——这是发布内容的唯一可信来源；发布端以
    `-SkipBuild` 复用陈旧构建产物时在此被拒绝。
    """
    targets = _parse_targets(target)
    data = await file.read()
    if len(data) > settings.max_ota_bytes:
        log_event("WARNING", "ota.release-too-large", "发布被拒：超出单文件上限", version=version, size=len(data))
        raise ApiProblemError(RELEASE_TOO_LARGE, f"上限 {settings.max_ota_bytes} 字节")
    try:
        declared = apk_manifest.extract_versions(data)
    except apk_manifest.ApkManifestError as exc:
        log_event("WARNING", "ota.release-invalid-apk", "发布被拒：APK 版本读取失败", version=version, error=str(exc))
        raise ApiProblemError(OTA_APK_MISMATCH, str(exc)) from exc
    if declared.version_code != version_code or declared.version_name != version:
        detail = (
            f"APK 内容声明 {declared.version_name}({declared.version_code})，与发布的 {version}({version_code}) 不一致"
        )
        log_event(
            "WARNING",
            "ota.release-version-mismatch",
            "发布被拒：APK 内容版本与声明不一致",
            version=version,
            version_code=version_code,
            apk_version=declared.version_name,
            apk_version_code=declared.version_code,
        )
        raise ApiProblemError(OTA_APK_MISMATCH, detail)
    sha256 = service.store_release(version, data)
    repository.insert_release(session, version, version_code, sha256, force, targets)
    notified = await service.notify_target_devices(version, version_code, targets)
    pruned = service.prune_releases(session, settings.ota_release_keep)
    if pruned:
        log_event(
            "INFO",
            "ota.release-pruned",
            "旧发布已按保留个数裁剪",
            pruned=",".join(pruned),
            keep=settings.ota_release_keep,
        )
    log_event(
        "INFO",
        "ota.release-published",
        "发布已登记并推送目标设备",
        version=version,
        version_code=version_code,
        size=len(data),
        force=force,
        target=target,
        notified=len(notified),
    )
    return {
        "version": version,
        "version_code": version_code,
        "sha256": sha256,
        "size": len(data),
        "notified_devices": notified,
    }
