"""ota_release 表：APK 版本注册表与逐设备的安装确认。

时间字段一律写入 RFC 3339（core.clock 是时间的唯一来源）——不使用 SQLite 的
datetime('now')，它的格式（"YYYY-MM-DD HH:MM:SS"）与全库其余时间列不一致。
"""

from sqlite3 import Row

from app.core.clock import now_rfc3339
from app.core.db import get_conn
from app.core.protocol import JsonObject
from app.modules.ota.schemas import ReleaseRecord


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS ota_release (
            version      TEXT    PRIMARY KEY,
            version_code INTEGER NOT NULL,
            sha256       TEXT    NOT NULL,
            mandatory    INTEGER NOT NULL DEFAULT 0,
            force        INTEGER NOT NULL DEFAULT 0,
            target       TEXT    NOT NULL DEFAULT 'all',
            created_at   TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ota_release_install (
            version    TEXT NOT NULL,
            device_id  TEXT NOT NULL,
            applied_at TEXT NOT NULL,
            PRIMARY KEY (version, device_id)
        );
        """
    )


def insert_release(version: str, version_code: int, sha256: str, mandatory: bool, force: bool, target: str) -> None:
    get_conn().execute(
        "INSERT OR REPLACE INTO ota_release"
        " (version, version_code, sha256, mandatory, force, target, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (version, version_code, sha256, 1 if mandatory else 0, 1 if force else 0, target, now_rfc3339()),
    )
    get_conn().commit()


def latest_for(device_id: str, current_version_code: int) -> JsonObject | None:
    """取该设备适用的最新发布。

    - target='all' 或逗号分隔列表包含该 device_id；
    - 常规发布：version_code 大于设备当前版本才适用；
    - force 调试发布：无视版本号推送同一/任意版本，但**已安装确认过即不再推**
      （防死循环，行业标准 roll-forward + 安装状态跟踪）；
      需要重推时经 /ops/releases/{version}/rearm/{device_id} 重新武装。
    """
    rows = (
        get_conn()
        .execute(
            """
        SELECT version, version_code, sha256, mandatory, force FROM ota_release
        WHERE (target = 'all' OR (',' || target || ',') LIKE '%,' || ? || ',%')
        ORDER BY version_code DESC, created_at DESC
        """,
            (device_id,),
        )
        .fetchall()
    )
    for row in rows:
        if row["version_code"] > current_version_code or (row["force"] and not is_installed(device_id, row["version"])):
            release = dict(row)
            release["mandatory"] = bool(release["mandatory"])
            release["force"] = bool(release["force"])
            return release
    return None


def mark_installed_upto(device_id: str, version_code: int) -> None:
    """设备上报了某 version_code：把所有 ≤ 该版本的未确认发布标记为已安装。

    在设备 hello（身份登记）时调用——安装确认即设备以新版本出现在网关。
    漏掉这个调用会让 force 发布形成无限重装循环（2026-09-30 真机实测复现过）。
    """
    applied_at = now_rfc3339()
    rows = get_conn().execute("SELECT version FROM ota_release WHERE version_code <= ?", (version_code,)).fetchall()
    if not rows:
        return
    for row in rows:
        get_conn().execute(
            "INSERT OR IGNORE INTO ota_release_install (version, device_id, applied_at) VALUES (?,?,?)",
            (row["version"], device_id, applied_at),
        )
    get_conn().commit()


def is_installed(device_id: str, version: str) -> bool:
    row = (
        get_conn()
        .execute(
            "SELECT 1 FROM ota_release_install WHERE version = ? AND device_id = ?",
            (version, device_id),
        )
        .fetchone()
    )
    return row is not None


def rearm(device_id: str, version: str) -> bool:
    """清除安装确认，使 force 发布对该设备重新生效（调试期重推同一版本）。"""
    cursor = get_conn().execute(
        "DELETE FROM ota_release_install WHERE version = ? AND device_id = ?",
        (version, device_id),
    )
    get_conn().commit()
    return cursor.rowcount > 0


def delete_release(version: str) -> bool:
    """下架发布（紧急止损用，如终止 OTA 循环）。"""
    cursor = get_conn().execute("DELETE FROM ota_release WHERE version = ?", (version,))
    get_conn().commit()
    return cursor.rowcount > 0


def list_releases() -> list[ReleaseRecord]:
    rows = (
        get_conn()
        .execute(
            "SELECT version, version_code, sha256, mandatory, force, target, created_at"
            " FROM ota_release ORDER BY version_code DESC, created_at DESC"
        )
        .fetchall()
    )
    return [_as_release(row) for row in rows]


def _as_release(row: Row) -> ReleaseRecord:
    """把行转成 ReleaseRecord；SQLite 以 0/1 存布尔，此处转换。"""
    release: ReleaseRecord = {
        "version": row["version"],
        "version_code": row["version_code"],
        "sha256": row["sha256"],
        "mandatory": bool(row["mandatory"]),
        "force": bool(row["force"]),
        "target": row["target"],
        "created_at": row["created_at"],
    }
    return release
