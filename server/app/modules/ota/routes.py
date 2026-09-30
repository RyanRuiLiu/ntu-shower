"""OTA API：设备面（manifest/下载）与管理面（发布上传）。

版本号在 HTTP 边界即按 VERSION_PATTERN 校验：该参数参与文件路径构造，
边界校验是第一道防线，service.release_path 是第二道（纵深防御）。
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Path, UploadFile
from fastapi.responses import FileResponse

from app.core.config import settings
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

logger = logging.getLogger("ntu-shower.ota")

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
    return {
        "up_to_date": False,
        "version": rel["version"],
        "version_code": rel["version_code"],
        "url": f"/ota/download/{rel['version']}",
        "sha256": rel["sha256"],
        "mandatory": bool(rel["mandatory"]),
        "force": bool(rel["force"]),
    }


@router.get("/download/{version}")
def download(version: ReleaseVersion) -> FileResponse:
    path = service.release_path(version)
    if not path.is_file():
        raise ApiProblemError(RELEASE_NOT_FOUND, version)
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
    """下架发布（紧急止损，如终止 OTA 循环）。"""
    deleted = repository.delete_release(version)
    if deleted:
        logger.warning("release withdrawn: version=%s", version)
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
        logger.warning("release rejected, too large: version=%s size=%d", version, len(data))
        raise ApiProblemError(RELEASE_TOO_LARGE, f"limit {settings.max_ota_bytes} bytes")
    sha256 = service.store_release(version, data)
    repository.insert_release(version, version_code, sha256, mandatory, force, target)
    notified = await service.notify_target_devices(version, version_code, mandatory, target)
    logger.info(
        "release published: version=%s code=%d size=%d force=%s target=%s notified=%d",
        version,
        version_code,
        len(data),
        force,
        target,
        len(notified),
    )
    return {
        "version": version,
        "version_code": version_code,
        "sha256": sha256,
        "size": len(data),
        "notified_devices": notified,
    }
