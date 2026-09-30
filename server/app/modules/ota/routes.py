"""OTA API：设备面（manifest/下载）与管理面（发布上传）。"""

import os

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.core.config import settings
from app.core.security import require_admin
from app.modules.ota import repository, service

router = APIRouter(prefix="/ota", tags=["ota"])

admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)], tags=["ota-admin"])


@router.get("/releases")
def list_releases() -> list[dict]:
    return repository.list_releases()


@router.get("/manifest")
def manifest(device_id: str, version_code: int) -> dict:
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
def download(version: str) -> FileResponse:
    path = os.path.join(settings.ota_dir, f"{version}.apk")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="release not found")
    return FileResponse(
        path, media_type="application/vnd.android.package-archive", filename=f"ntu-shower-{version}.apk"
    )


@admin_router.post("/releases/{version}/rearm/{device_id}")
def rearm_release(version: str, device_id: str) -> dict:
    """清除安装确认：force 发布对该设备重新生效（调试重推）。"""
    return {"version": version, "device_id": device_id, "rearmed": repository.rearm(device_id, version)}


@admin_router.delete("/releases/{version}")
def delete_release(version: str) -> dict:
    """下架发布（紧急止损，如终止 OTA 循环）。"""
    return {"version": version, "deleted": repository.delete_release(version)}


@admin_router.post("/releases")
async def upload_release(
    version: str = Form(...),
    version_code: int = Form(...),
    mandatory: bool = Form(False),
    force: bool = Form(False),
    target: str = Form("all"),
    file: UploadFile = File(...),
) -> dict:
    """上传新版本 APK 并注册发布（target='all' 或逗号分隔的 device_id 列表）。

    发布成功后向在线的目标设备即时推送 ota.upgrade_available。
    """
    data = await file.read()
    if len(data) > settings.max_ota_bytes:
        raise HTTPException(status_code=413, detail=f"release file exceeds {settings.max_ota_bytes} bytes")
    sha256 = service.store_release(version, data)
    repository.insert(version, version_code, sha256, mandatory, force, target)
    notified = await service.notify_target_devices(version, version_code, mandatory, target)
    return {
        "version": version,
        "version_code": version_code,
        "sha256": sha256,
        "size": len(data),
        "notified_devices": notified,
    }
