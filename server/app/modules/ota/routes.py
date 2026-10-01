"""OTA API：设备面（manifest/下载）与管理面（发布上传）。

版本号在 HTTP 边界即按 VERSION_PATTERN 校验，service.release_path 按同一规则
构造文件名——两处各自成立，不依赖对方先拦。
"""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.logging import log_event
from app.core.problems import RELEASE_NOT_FOUND, RELEASE_TOO_LARGE, ApiProblemError
from app.core.security import require_ops_token
from app.modules.ota import repository, service
from app.modules.ota.schemas import (
    OtaManifest,
    ReleasePublished,
    ReleaseRearmed,
    ReleaseRecord,
    ReleaseRemoved,
)
from app.modules.ota.service import VERSION_PATTERN

router = APIRouter(prefix="/ota", tags=["ota"])

ops_router = APIRouter(prefix="/ops", dependencies=[Depends(require_ops_token)], tags=["ota-ops"])

# 路径/表单中的发布版本号：格式不合规由 FastAPI 返回 422
ReleaseVersion = Annotated[str, Path(pattern=VERSION_PATTERN)]
ReleaseVersionForm = Annotated[str, Form(pattern=VERSION_PATTERN)]


@router.get("/releases")
def list_releases() -> list[ReleaseRecord]:
    return repository.list_releases()


@router.get("/manifest")
def manifest(device_id: str, version_code: int) -> OtaManifest:
    """设备定期轮询：返回是否有可用更新及其元数据。"""
    rel = repository.latest_for(device_id, version_code)
    if rel is None:
        return {"up_to_date": True}
    path = service.release_path(rel["version"])
    if not path.is_file():
        # 库里有记录、文件却不在（手工清理过目录之类）：当作没有更新，而不是给设备一个
        # 下不动的地址——它那边会在下载时失败，而失败的原因与服务端实情对不上。
        log_event("WARNING", "ota.release-file-missing", "发布文件缺失", version=str(rel["version"]))
        return {"up_to_date": True}
    return {
        "up_to_date": False,
        "version": rel["version"],
        "version_code": rel["version_code"],
        "url": f"/ota/download/{rel['version']}",
        "sha256": rel["sha256"],
        # 大小现取文件的，不在库里再存一列：那个数字是文件的属性，存一份就多一处可能对不上的
        # 副本——手工替换过 APK 之后，库里那份就是错的，而错的方向是"设备收下了别的包"。
        "size": path.stat().st_size,
        "mandatory": bool(rel["mandatory"]),
        "force": bool(rel["force"]),
    }


@router.get("/download/{version}")
def download(version: ReleaseVersion) -> FileResponse:
    path = service.release_path(version)
    if not path.is_file():
        raise ApiProblemError(RELEASE_NOT_FOUND, f"版本 {version}")
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=f"ntu-shower-{version}.apk",
    )


@ops_router.post("/releases/{version}/rearm/{device_id}")
def rearm_release(version: str, device_id: str) -> ReleaseRearmed:
    """清除安装确认：force 发布对该设备重新生效（调试重推）。"""
    return {"version": version, "device_id": device_id, "rearmed": repository.rearm(device_id, version)}


@ops_router.delete("/releases/{version}")
def delete_release(version: str) -> ReleaseRemoved:
    """下架发布（紧急止损，如终止 OTA 循环）。注册行与 APK 文件一起删。"""
    deleted = repository.delete_release(version)
    file_removed = service.remove_release_file(version)
    if deleted or file_removed:
        log_event("INFO", "ota.release-deleted", "发布已下架", version=version, file_removed=file_removed)
    return {"version": version, "deleted": deleted}


@ops_router.post("/releases")
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
