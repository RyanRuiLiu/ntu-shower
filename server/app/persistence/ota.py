"""OTA 版本注册表与逐设备安装确认。

契约与旧实现一致：
- `latest_for` 先按 `version_code` 降序，再看是否大于设备当前版本
- `force` 发布无视版本号，但已确认安装过就不再推（防重装循环）
- `target` 是 `all` 或逗号分隔的设备白名单
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.protocol import JsonObject

from ..contracts import ReleaseRecord
from ..core.clock import format_rfc3339
from ..db.base import utcnow
from ..models.device import Device
from ..models.ota import OtaRelease, OtaReleaseInstall


def _device(session: Session, device_id: str) -> Device | None:
    return session.scalars(select(Device).where(Device.id == device_id)).one_or_none()


def _targets(release: OtaRelease, device_id: str) -> bool:
    if release.target == "all":
        return True
    return device_id in {item.strip() for item in release.target.split(",") if item.strip()}


def insert_release(
    session: Session,
    version: str,
    version_code: int,
    sha256: str,
    mandatory: bool,
    force: bool,
    target: str,
) -> None:
    row = session.scalars(select(OtaRelease).where(OtaRelease.version == version)).one_or_none()
    if row is None:
        session.add(
            OtaRelease(
                version=version,
                version_code=version_code,
                sha256=sha256,
                mandatory=mandatory,
                force=force,
                target=target,
            )
        )
    else:
        row.version_code = version_code
        row.sha256 = sha256
        row.mandatory = mandatory
        row.force = force
        row.target = target
    session.commit()


def latest_for(session: Session, device_id: str, current_version_code: int) -> JsonObject | None:
    """该设备适用的最新发布；没有则 None。"""
    releases = session.scalars(
        select(OtaRelease).order_by(OtaRelease.version_code.desc(), OtaRelease.created_at.desc())
    ).all()
    installed = set(
        session.scalars(
            select(OtaRelease.version)
            .join(OtaReleaseInstall, OtaReleaseInstall.release_id == OtaRelease.id)
            .join(Device, OtaReleaseInstall.device_id == Device.id)
            .where(Device.id == device_id)
        ).all()
    )
    for release in releases:
        if not _targets(release, device_id):
            continue
        if release.version_code > current_version_code or (release.force and release.version not in installed):
            return {
                "version": release.version,
                "version_code": int(release.version_code),
                "sha256": release.sha256,
                "mandatory": bool(release.mandatory),
                "force": bool(release.force),
            }
    return None


def mark_installed_upto(session: Session, device_id: str, version_code: int) -> None:
    """设备上报了某版本：把所有 ≤ 该版本的未确认发布标记为已安装。"""
    device = _device(session, device_id)
    if device is None:
        return
    releases = session.scalars(select(OtaRelease).where(OtaRelease.version_code <= version_code)).all()
    existing = {
        install.release_id
        for install in session.scalars(select(OtaReleaseInstall).where(OtaReleaseInstall.device_id == device.id)).all()
    }
    applied_at = utcnow()
    for release in releases:
        if release.id not in existing:
            session.add(OtaReleaseInstall(release_id=release.id, device_id=device.id, applied_at=applied_at))
    session.commit()


def is_installed(session: Session, device_id: str, version: str) -> bool:
    row = session.execute(
        select(OtaReleaseInstall.release_id)
        .join(OtaRelease, OtaReleaseInstall.release_id == OtaRelease.id)
        .join(Device, OtaReleaseInstall.device_id == Device.id)
        .where(OtaRelease.version == version, Device.id == device_id)
    ).first()
    return row is not None


def rearm(session: Session, device_id: str, version: str) -> bool:
    """清除安装确认，使 force 发布对该设备重新生效。"""
    device = _device(session, device_id)
    release = session.scalars(select(OtaRelease).where(OtaRelease.version == version)).one_or_none()
    if device is None or release is None:
        return False
    install = session.scalars(
        select(OtaReleaseInstall).where(
            OtaReleaseInstall.release_id == release.id,
            OtaReleaseInstall.device_id == device.id,
        )
    ).one_or_none()
    if install is None:
        return False
    session.delete(install)
    session.commit()
    return True


def delete_release(session: Session, version: str) -> bool:
    row = session.scalars(select(OtaRelease).where(OtaRelease.version == version)).one_or_none()
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def list_releases(session: Session) -> list[ReleaseRecord]:
    rows = session.scalars(
        select(OtaRelease).order_by(OtaRelease.version_code.desc(), OtaRelease.created_at.desc())
    ).all()
    return [as_record(row) for row in rows]


def as_record(row: OtaRelease) -> ReleaseRecord:
    record: ReleaseRecord = {
        "version": row.version,
        "version_code": int(row.version_code),
        "sha256": row.sha256,
        "mandatory": bool(row.mandatory),
        "force": bool(row.force),
        "target": row.target,
        "created_at": format_rfc3339(row.created_at),
    }
    return record


__all__: list[str] = [
    "as_record",
    "delete_release",
    "insert_release",
    "is_installed",
    "latest_for",
    "list_releases",
    "mark_installed_upto",
    "rearm",
]
