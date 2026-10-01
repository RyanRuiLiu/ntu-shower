"""设备注册表：设备档案（身份/信任/版本/链路时刻）的持久化与查询。

**这里不回答"设备在不在线"。** 在线只有一个来源——`gateway.manager` 的内存连接表，
它才是事实；`service` 在返回记录时把那个答案合并进去（见 `service.get_device`）。

早先这张表另存了一个 `online` 列，于是同一个概念有两个存放处：内存与库。进程一重启，
内存连接表就空了，而库里的 1 还留着，前端照旧显示"在线"——一台根本没有连接的设备。
每次部署都会进入这个状态。
"""

import json
from sqlite3 import Row

from app.core.clock import now_rfc3339
from app.core.db import get_conn

from .schemas import DeviceRecord

#: 读模型的列清单。列名只在此处出现一次，避免两条查询各写一遍后逐渐分叉。
_DEVICE_COLUMNS = (
    "device_id, name, trusted, app_version, version_code, os_version, capabilities, "
    "last_seen_at, last_ip, booted_at, clock_skew_s"
)


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS device (
            device_id    TEXT PRIMARY KEY,
            name         TEXT NOT NULL DEFAULT '',
            trusted      INTEGER NOT NULL DEFAULT 0,
            app_version  TEXT NOT NULL DEFAULT '',
            version_code INTEGER NOT NULL DEFAULT 0,
            os_version   TEXT NOT NULL DEFAULT '',
            capabilities TEXT NOT NULL DEFAULT '[]',
            last_seen_at TEXT NOT NULL DEFAULT '',
            last_ip      TEXT NOT NULL DEFAULT '',
            booted_at    TEXT NOT NULL DEFAULT '',
            clock_skew_s REAL NOT NULL DEFAULT 0
        );
        """
    )


def set_clock_skew(device_id: str, skew_s: float) -> None:
    """记下设备时钟相对服务端的偏差（秒，正数表示设备快）。

    存库而不只留在进程内存里：那台设备的墙上时钟可能一直不准（实测有一台快了 59 分
    47 秒，且自动对时是关的），而校正全靠这个值。只在内存里的话，服务每次重启都会退回
    0——变成"不校正"，于是设备上报的时间戳被原样写库，与用服务端时钟写的那些字段
    混在一起，排序就乱了。
    """
    conn = get_conn()
    conn.execute("UPDATE device SET clock_skew_s = ? WHERE device_id = ?", (skew_s, device_id))
    conn.commit()


def get_clock_skew(device_id: str) -> float:
    """取上次记下的偏差；设备不在档案里时给 0。"""
    row = get_conn().execute("SELECT clock_skew_s FROM device WHERE device_id = ?", (device_id,)).fetchone()
    return float(row["clock_skew_s"]) if row is not None else 0.0


def get(device_id: str) -> DeviceRecord | None:
    row = get_conn().execute(f"SELECT {_DEVICE_COLUMNS} FROM device WHERE device_id = ?", (device_id,)).fetchone()
    return _as_device(row) if row is not None else None


def list_all() -> list[DeviceRecord]:
    rows = get_conn().execute(f"SELECT {_DEVICE_COLUMNS} FROM device ORDER BY last_seen_at DESC").fetchall()
    return [_as_device(row) for row in rows]


def register(
    device_id: str,
    app_version: str,
    version_code: int,
    os_version: str,
    capabilities: list[str],
    ip: str,
    booted_at: str,
) -> None:
    """上线登记：只覆盖版本/能力/时间/开机时刻，绝不触碰 name 与 trusted。"""
    conn = get_conn()
    conn.execute(
        """
        INSERT INTO device (device_id, app_version, version_code, os_version,
            capabilities, last_seen_at, last_ip, booted_at)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(device_id) DO UPDATE SET
            app_version  = excluded.app_version,
            version_code = excluded.version_code,
            os_version   = excluded.os_version,
            capabilities = excluded.capabilities,
            last_seen_at = excluded.last_seen_at,
            last_ip      = excluded.last_ip,
            booted_at    = excluded.booted_at
        """,
        (
            device_id,
            app_version,
            version_code,
            os_version,
            json.dumps(capabilities),
            now_rfc3339(),
            ip,
            booted_at,
        ),
    )
    conn.commit()


def touch_seen(device_id: str) -> None:
    """记下"这一帧是刚从它那儿收到的"。

    心跳（`link.ping` / `link.pong`）会调它，所以在线设备的 `last_seen_at` 保持新鲜。
    此前只有上线与下线两处写它，于是设备跑了几小时之后，那一列还停在连接建立那一刻——
    字段名说的是"最后见到"，值却是"最后连接变化"，前端那行"最后上报"因此长期是个假象。

    不在每一帧都写：喷头快照密集时每秒好几帧，而这条信息的心跳粒度足够回答
    "它还在吗"。"数据还在来吗"该看喷头自己的 `reported_at`。
    """
    conn = get_conn()
    conn.execute("UPDATE device SET last_seen_at = ? WHERE device_id = ?", (now_rfc3339(), device_id))
    conn.commit()


def set_offline(device_id: str) -> None:
    """记下断开时刻。

    这里不再改一个"在线位"——那个位已经删掉，在不在线由 `gateway.manager` 的连接表
    回答（见模块注释）。
    """
    conn = get_conn()
    conn.execute("UPDATE device SET last_seen_at = ? WHERE device_id = ?", (now_rfc3339(), device_id))
    conn.commit()


def set_name(device_id: str, name: str) -> bool:
    conn = get_conn()
    cur = conn.execute("UPDATE device SET name = ? WHERE device_id = ?", (name, device_id))
    conn.commit()
    return cur.rowcount > 0


def set_trusted(device_id: str, trusted: bool) -> bool:
    """设置信任位；设备不存在时返回 False（信任只能在已有档案上操作）。"""
    conn = get_conn()
    cur = conn.execute(
        "UPDATE device SET trusted = ? WHERE device_id = ?",
        (1 if trusted else 0, device_id),
    )
    conn.commit()
    return cur.rowcount > 0


def _as_device(row: Row) -> DeviceRecord:
    """把行转成 DeviceRecord。

    SQLite 以 0/1 存布尔、以 JSON 文本存数组，转换集中在此：`get` 与 `list_all`
    此前各写一遍，改一处就会漏另一处。
    """
    device: DeviceRecord = {
        "device_id": row["device_id"],
        "name": row["name"],
        "trusted": bool(row["trusted"]),
        "app_version": row["app_version"],
        "version_code": row["version_code"],
        "os_version": row["os_version"],
        "capabilities": _load_capabilities(row["capabilities"]),
        "online": False,  # 占位：由 service 用内存连接表覆盖，见模块注释
        "last_seen_at": row["last_seen_at"],
        "last_ip": row["last_ip"],
        "booted_at": row["booted_at"],
        "clock_skew_s": float(row["clock_skew_s"]),
    }
    return device


def _load_capabilities(raw: str) -> list[str]:
    """解析 capabilities 列；内容不是 JSON 数组时按空能力集处理。

    该列的内容源自设备上报，写库前虽经 json.dumps，但库可能被手工改动过；
    解析不出数组就按空能力集处理，能力校验随之按"未声明"走（见 sessions 的能力预检）。
    """
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]
