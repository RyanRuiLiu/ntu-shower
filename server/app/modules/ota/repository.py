"""releases 表：APK 版本注册表。"""

from app.core.db import get_conn


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS release_install (
            release_version TEXT NOT NULL,
            device_id       TEXT NOT NULL,
            applied_at      TEXT NOT NULL,
            PRIMARY KEY (release_version, device_id)
        );
        CREATE TABLE IF NOT EXISTS releases (
            version      TEXT    PRIMARY KEY,
            version_code INTEGER NOT NULL,
            sha256       TEXT    NOT NULL,
            mandatory    INTEGER NOT NULL DEFAULT 0,
            force        INTEGER NOT NULL DEFAULT 0,
            target       TEXT    NOT NULL DEFAULT 'all',
            created_at   TEXT    NOT NULL
        );
        """
    )


def insert(version: str, version_code: int, sha256: str, mandatory: bool, force: bool, target: str) -> None:
    get_conn().execute(
        "INSERT OR REPLACE INTO releases (version, version_code, sha256, mandatory, force, target, created_at)"
        " VALUES (?,?,?,?,?,?, datetime('now'))",
        (version, version_code, sha256, 1 if mandatory else 0, 1 if force else 0, target),
    )
    get_conn().commit()


def latest_for(device_id: str, current_version_code: int) -> dict | None:
    """取该设备适用的最新发布。

    - target='all' 或逗号分隔列表包含该 device_id；
    - 常规发布：version_code 大于设备当前版本才适用；
    - force 调试发布：无视版本号推送同一/任意版本，但**已安装确认过即不再推**
      （防死循环，行业标准 roll-forward + 安装状态跟踪）；
      需要重推时经 /admin/releases/{version}/rearm/{device_id} 重新武装。
    """
    rows = (
        get_conn()
        .execute(
            """
        SELECT version, version_code, sha256, mandatory, force FROM releases
        WHERE (target = 'all' OR (',' || target || ',') LIKE '%,' || ? || ',%')
        ORDER BY version_code DESC, created_at DESC
        """,
            (device_id,),
        )
        .fetchall()
    )
    for r in rows:
        if r["version_code"] > current_version_code or (r["force"] and not is_installed(device_id, r["version"])):
            d = dict(r)
            d["mandatory"] = bool(d["mandatory"])
            d["force"] = bool(d["force"])
            return d
    return None


def mark_installed_upto(device_id: str, version_code: int) -> None:
    """设备上报了某 version_code：把所有 ≤ 该版本的未确认发布标记为已安装。

    在设备 hello（身份登记）时调用——安装确认即设备以新版本出现在网关。
    """
    from app.core.clock import now_rfc3339

    rows = get_conn().execute("SELECT version FROM releases WHERE version_code <= ?", (version_code,)).fetchall()
    for r in rows:
        get_conn().execute(
            "INSERT OR IGNORE INTO release_install (release_version, device_id, applied_at) VALUES (?,?,?)",
            (r["version"], device_id, now_rfc3339()),
        )
    get_conn().commit()


def is_installed(device_id: str, version: str) -> bool:
    return (
        get_conn()
        .execute(
            "SELECT 1 FROM release_install WHERE release_version = ? AND device_id = ?",
            (version, device_id),
        )
        .fetchone()
        is not None
    )


def rearm(device_id: str, version: str) -> bool:
    """清除安装确认，使 force 发布对该设备重新生效（调试期重推同一版本）。"""
    cur = get_conn().execute(
        "DELETE FROM release_install WHERE release_version = ? AND device_id = ?",
        (version, device_id),
    )
    get_conn().commit()
    return cur.rowcount > 0


def delete_release(version: str) -> bool:
    """下架发布（紧急止损用，如终止 OTA 循环）。"""
    cur = get_conn().execute("DELETE FROM releases WHERE version = ?", (version,))
    get_conn().commit()
    return cur.rowcount > 0


def list_releases() -> list[dict]:
    rows = (
        get_conn()
        .execute(
            "SELECT version, version_code, sha256, mandatory, force, target, created_at"
            " FROM releases ORDER BY created_at DESC"
        )
        .fetchall()
    )
    result = []
    for r in rows:
        d = dict(r)
        d["mandatory"] = bool(d["mandatory"])
        d["force"] = bool(d["force"])
        result.append(d)
    return result
