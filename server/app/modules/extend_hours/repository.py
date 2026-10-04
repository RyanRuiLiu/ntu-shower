"""延长服务时间的状态表：一行一台设备，**有行即"设备钟还偏着"**。

## 为什么这张表必须存在

另外三个后台任务的记忆活在内存里（`sessions/scheduler` 的 `_attempted`），丢了只是
少续一次。这一个不能这样：它记的是"设备钟被我们拨走过"。服务端一重启就忘了的话，
那口钟会一直偏着——而偏着的钟会让之后所有数据都落在错一小时的位置上，事后只能靠人
对着 `clock_skew_s` 一条条辨。

## 四列，两个时刻回答两件事

`shifted_at` 是**拨钟成功那一刻**，不是"决定要拨那一刻"。写成后者的话，拨钟失败重试
到成功时保持时长可能早就过了，会立刻调回，等于没拨。

`restarted_at` 是**重启浴室 App 成功那一刻**，为空表示还没重启。它与 `shifted_at` 分开，
是因为中间夹着一段必须等的事：拨钟会把喷头清空，而清空之后要把名单里的人分配回去——
而分配要求 App 正在前台跑着，重启却会 `force-stop` 它。所以两件事之间隔着"等分配结束"，
这个列就是那个等待的进度。

`assignments` 是拨钟前"谁在哪个喷头"，JSON 数组。拨钟会让浴室 App 清空所有喷头，
事后再也查不到他原来在哪——拨之前那一刻是唯一能记下它的时机。

**没有 `restored_at`**：调回成功就删行。当前状态只有"有"与"没有"两种，加一个"已调回"
的时间戳会造出第三种状态，而每个读它的人都要自己记得去筛。
"""

import json
from sqlite3 import Row

from app.core.db import get_conn, read_one, write


def init_tables() -> None:
    get_conn().executescript(
        """
        -- 表名不带 device_ 前缀：那是 devices 域的（见 core/db 的命名约定），
        -- 而这张表归本域所有，讲的是"钟被拨过的那一次"。
        CREATE TABLE IF NOT EXISTS clock_shift (
            device_id    TEXT PRIMARY KEY,
            -- 拨钟命令成功那一刻（服务端轴）。保持时长从它起算。
            shifted_at   TEXT NOT NULL,
            -- 重启浴室 App 成功那一刻；空 = 还没重启（见模块注释里的"等待"）
            restarted_at TEXT,
            -- 拨钟前那一刻谁在哪个喷头：[{"door_no": 3, "cert_no": "..."}]。
            -- 只给重新分配用，不按它查询，所以存 JSON 而不是另开一张关联表。
            assignments  TEXT NOT NULL
        );
        """
    )


def record(device_id: str, shifted_at: str, assignments: list[dict[str, object]]) -> None:
    """记下一次拨钟。覆盖式写入：一台设备同时只可能偏着一次。

    `restarted_at` 一并归空——它只在这个阶段被调（那时本来就该是空的）。拨钟与重启
    是两个阶段，各自有各自的写入函数。
    """
    write(
        "INSERT OR REPLACE INTO clock_shift (device_id, shifted_at, restarted_at, assignments) VALUES (?,?,NULL,?)",
        (device_id, shifted_at, json.dumps(assignments, ensure_ascii=False)),
    )


def mark_restarted(device_id: str, restarted_at: str) -> None:
    """记下"计划已经重读过了"。"""
    write("UPDATE clock_shift SET restarted_at = ? WHERE device_id = ?", (restarted_at, device_id))


def get(device_id: str) -> Row | None:
    """这台设备是否还偏着；有行就是。"""
    return read_one("SELECT * FROM clock_shift WHERE device_id = ?", (device_id,))


def list_shifted() -> list[Row]:
    """全部还偏着的设备，供定时任务扫。

    按 `shifted_at` 升序：拨得最早的那台最该被调回，而它的到期时刻也最早。
    """
    rows = get_conn().execute("SELECT * FROM clock_shift ORDER BY shifted_at").fetchall()
    return list(rows)


def clear(device_id: str) -> None:
    """调回成功之后删掉这一行。"""
    write("DELETE FROM clock_shift WHERE device_id = ?", (device_id,))


def parse_assignments(row: Row) -> list[dict[str, object]]:
    """把 `assignments` 那一列读回列表。

    读的是我们自己写下的 JSON，格式由 [record] 保证；坏数据只可能来自人手工改库，
    那种情况按"没有可分配的人"处理——拨钟本身已经发生，不该因为这份记录读不出来
    就把它整件事当成没发生过。
    """
    try:
        value = json.loads(str(row["assignments"]))
    except (ValueError, TypeError):
        return []
    return value if isinstance(value, list) else []
