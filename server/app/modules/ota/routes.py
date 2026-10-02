"""发布域 API：清单、下载与发布管理，统一在 /api/v1/ota 下。

权限分层：清单与下载公开（设备按 manifest 的 url 下载，服务器控制路径）；
发布、下架、重推要求 admin；manifest 是设备的硬编码契约，保留无版本前缀的
冻结路径。

版本号在 HTTP 边界即按 VERSION_PATTERN 校验，service.release_path 按同一规则
构造文件名——两处各自成立，不依赖对方先拦。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.logging import log_event
from app.core.problems import RELEASE_NOT_FOUND, RELEASE_TOO_LARGE, ApiProblemError
from app.modules.access.dependencies import require_admin
from app.modules.ota import repository, service
from app.modules.ota.schemas import (
    OtaManifest,
    ReleasePublished,
    ReleaseRearmed,
    ReleaseRecord,
    ReleaseRemoved,
)
from app.modules.ota.service import VERSION_PATTERN

router = APIRouter(prefix="/api/v1/ota", tags=["ota"])

# 设备硬编码契约：manifest 的路径不随版本前缀走（冻结，见 docs/ARCHITECTURE.md）
device_router = APIRouter(tags=["ota"])

# 路径/表单中的发布版本号：格式不合规由 FastAPI 返回 422
ReleaseVersion = Annotated[str, Path(pattern=VERSION_PATTERN)]
ReleaseVersionForm = Annotated[str, Form(pattern=VERSION_PATTERN)]


@router.get("/releases")
def list_releases() -> list[ReleaseRecord]:
    return repository.list_releases()


@device_router.get("/ota/manifest")
def manifest(device_id: str, version_code: int) -> OtaManifest:
    """设备定期轮询：返回是否有可用更新及其元数据。路径为设备硬编码契约，冻结。"""
    rel = repository.latest_for(device_id, version_code)
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
        "mandatory": bool(rel["mandatory"]),
        "force": bool(rel["force"]),
    }


@router.get("/releases/{version}/download")
def download(version: ReleaseVersion) -> FileResponse:
    path = service.release_path(version)
    if not path.is_file():
        raise ApiProblemError(RELEASE_NOT_FOUND, f"版本 {version}")
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=f"ntu-shower-{version}.apk",
    )


@router.post("/releases/{version}/rearm/{device_id}", dependencies=[Depends(require_admin)])
def rearm_release(version: str, device_id: str) -> ReleaseRearmed:
    """清除安装确认：force 发布对该设备重新生效（调试重推）。"""
    return {"version": version, "device_id": device_id, "rearmed": repository.rearm(device_id, version)}


@router.delete("/releases/{version}", dependencies=[Depends(require_admin)])
def delete_release(version: str) -> ReleaseRemoved:
    """下架发布（紧急止损，如终止 OTA 循环）。注册行与 APK 文件一起删。"""
    deleted = repository.delete_release(version)
    file_removed = service.remove_release_file(version)
    if deleted or file_removed:
        log_event("INFO", "ota.release-deleted", "发布已下架", version=version, file_removed=file_removed)
    return {"version": version, "deleted": deleted}


@router.post("/releases", dependencies=[Depends(require_admin)])
async def upload_release(
    version: ReleaseVersionForm,
    version_code: Annotated[int, Form()],
    file: Annotated[UploadFile, File()],
    mandatory: Annotated[bool, Form()] = False,
    force: Annotated[bool, Form()] = False,
    target: Annotated[str, Form()] = "all",
) -> ReleasePublished:
    """上传新版本 APK 并注册发布（target='all' 或逗号分隔的 device_id 列表）。

    发布成功后向在线的目标设备即时推送 ota.upgrade_available。
    """
    data = await file.read()
    if len(data) > settings.max_ota_bytes:
        log_event("WARNING", "ota.release-too-large", "发布被拒：超出单文件上限", version=version, size=len(data))
        raise ApiProblemError(RELEASE_TOO_LARGE, f"上限 {settings.max_ota_bytes} 字节")
    sha256 = service.store_release(version, data)
    repository.insert_release(version, version_code, sha256, mandatory, force, target)
    notified = await service.notify_target_devices(version, version_code, mandatory, target)
    pruned = service.prune_releases(settings.ota_release_keep)
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
