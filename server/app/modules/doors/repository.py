"""doors 四表 SQL 集中管理。

分两层：`door_snapshot` 是原始层——设备说了什么就记什么，不做任何解释；其余三张是
派生层——`door_live` 现态、`door_state_event` 状态转换流水、`usage_session` 洗浴会话。

这条分层是整套设计的骨架：派生层的每条结论都依赖"当时的推导规则"，规则一旦发现缺陷
或要加新指标，没有原始帧就永久无法重算。旧系统只留了原始快照，那 100 天才能重放回来。
代价约 170MB/年。

## 刻意不存的几样

每一条都能从现有列推导出来。**可推导的不存**——存了就是制造第二个真相：

- **暂停次数**：数 `door_state_event` 的 `bathing→paused` 即可；
- **结束原因**：数据库层面不存在。主动出门与四种超时释放写的是同一套清零 SQL，
  `bathing→free` 与 `paused→free` 完全同形，硬分就是编造；
- **跨盲区标记**：`ended_at − last_seen_at > 1200 秒` 就是判据，一条 WHERE 的事；
- **占用时长、等待时长、暂停时长**：都是几个时刻的差。

`door_state_event` 同理不加 `acc_no`（实测与 `cert_no` 严格一对一，255 个映射无例外）
和 `status_raw`（从 `to_state` 可反推）。

**不建汇总表**：明细按年计也就十万行量级，直接查毫无压力，而汇总必然与明细漂移。
"""

import json
from sqlite3 import Row

from app.core.db import get_conn, read_one, write
from app.core.protocol import JsonObject
from app.modules.doors.schemas import DoorView


def init_tables() -> None:
    get_conn().executescript(
        """
        -- 原始层：设备说了什么就原样记什么，不做任何解释。
        --
        -- 它必须存在：派生层的每一条结论都依赖"当时的推导规则"，规则一旦发现缺陷、
        -- 或将来要加一个现在想不到的指标，没有原始帧就永久无法重算。旧系统只留了
        -- 原始快照，那 100 天今天才能重放；新系统不能让自己变成没有退路的一方。
        --
        -- 重复投递交给 snapshot_id 的唯一索引：设备在收到 ack 之前会重发同一条（连接
        -- 断开、服务端重启都会触发），落库时原地不动，派生也随之跳过。
        CREATE TABLE IF NOT EXISTS door_snapshot (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id   TEXT    NOT NULL,
            snapshot_id TEXT    NOT NULL,
            -- 发件箱序号：设备侧单调递增，只在最旧的被裁掉时才跳号，据此看得出丢帧。
            seq         INTEGER,
            source      TEXT    NOT NULL DEFAULT '',
            -- 采集时刻，由设备锚在服务端时间上（设备侧 core/clock/ServerClock）。它是
            -- 本帧的**权威时刻**：会话的起点、终点与出水时长全部以它为准。设备刚起来
            -- 还没握上手时为空，此时退回用 received_at。
            captured_at TEXT,
            received_at TEXT    NOT NULL,
            doors       TEXT    NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_door_snapshot_snapshot_id
            ON door_snapshot(snapshot_id);
        CREATE INDEX IF NOT EXISTS idx_door_snapshot_device
            ON door_snapshot(device_id, received_at);

        CREATE TABLE IF NOT EXISTS door_live (
            device_id   TEXT    NOT NULL,
            door_no     INTEGER NOT NULL,
            state       TEXT    NOT NULL,
            card_no     INTEGER NOT NULL DEFAULT 0,
            cert_no     TEXT    NOT NULL DEFAULT '',
            acc_no      TEXT    NOT NULL DEFAULT '',
            open_time   TEXT    NOT NULL DEFAULT '',
            in_time     TEXT    NOT NULL DEFAULT '',
            close_time  TEXT    NOT NULL DEFAULT '',
            bath_time_s INTEGER NOT NULL DEFAULT 0,
            status_raw  INTEGER NOT NULL DEFAULT 0,
            reported_at TEXT    NOT NULL,
            -- 当前这段连续出水的**观测**起点；不在出水时为空。
            --
            -- 它必须持久化，不能只放内存：出水时长的合成要分清"本段已经在设备那边结算
            -- 过"与"还没结算"，而这个区分只有靠"这一帧到段起点过了多久"才能做（见
            -- service 的 _flow_after）。进程重启后内存没了，而下一帧还要接着算。
            segment_started_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (device_id, door_no)
        );

        CREATE TABLE IF NOT EXISTS door_state_event (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT    NOT NULL,
            device_id   TEXT    NOT NULL,
            door_no     INTEGER NOT NULL,
            from_state  TEXT    NOT NULL,
            to_state    TEXT    NOT NULL,
            -- 那一刻的占用者。提成独立列而不是塞进 JSON：统计要按人筛，JSON 走不了索引。
            -- 无卡模式下 card_no 恒为 0（实测 235270 个门帧无一非零），"谁"只能看 cert_no。
            card_no     INTEGER NOT NULL DEFAULT 0,
            cert_no     TEXT    NOT NULL DEFAULT '',
            -- 那一刻设备报的出水进度，诊断用。
            bath_time_s INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_door_state_event_door
            ON door_state_event(device_id, door_no, occurred_at);

        CREATE TABLE IF NOT EXISTS usage_session (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            device_id    TEXT    NOT NULL,
            door_no      INTEGER NOT NULL,
            card_no      INTEGER NOT NULL DEFAULT 0,
            cert_no      TEXT    NOT NULL DEFAULT '',

            -- 会话起点：进入占用态那一刻。
            --
            -- 来源有两种，看下面的 assigned_source。它**不是**设备的 openTime 原值——
            -- 那是设备钟，实测跳变过。inferred 时它是把 openTime 平移过来的结果。
            assigned_at  TEXT    NOT NULL,
            -- 起点的来源。
            --
            -- `inferred`：由设备报的 open_time 加上钟偏差平移而来，那是真实的刷卡时刻；
            -- `observed`：设备还没回过 pong（或字段缺失），退回我们首次观测到它占着的
            -- 时刻——**真实起点更早**，这只是下界。
            --
            -- 统计时必须分得清：`inferred` 的跨度（ended_at − assigned_at）可以用，
            -- `observed` 的不能，它天然偏小。
            assigned_source TEXT NOT NULL DEFAULT 'observed',
            -- 首次观测到出水那一帧；没出过水则为空。
            opened_at    TEXT,
            -- 离开占用态那一帧。**跨盲区时它不是真实结束时刻**，只是"下次见到它的时刻"。
            ended_at     TEXT,
            -- 最后一次**确认它还占着**的那一帧。它才是可信的结束下界：`ended_at` 与它的
            -- 差就是观测盲区，超过 SESSION_MAX_S 说明中间发生过一次我们没看见的释放。
            -- 统计因此分两层：占用/等待/暂停类指标走"盲区 ≤ SESSION_MAX_S"的那些会话，
            -- 出水类指标（flow_s）不受影响——它只累加有证据的段。
            last_seen_at TEXT    NOT NULL,
            flow_s       INTEGER NOT NULL DEFAULT 0
        );
        -- 一个喷头的使用历史（含 latest_usage_by_door 那句"上次使用"）。
        CREATE INDEX IF NOT EXISTS idx_usage_session_door
            ON usage_session(device_id, door_no, opened_at);
        -- "这个门现在开着会话吗"（get_open_session）：一帧里每个门问一次，实时路径上
        -- 帧密度是每秒数帧，所以它必须走索引而不是扫全表。把 ended_at 也放进键里，
        -- 三列全命中；只到 door_no 的话还要在一堆历史会话里筛 ended_at IS NULL。
        CREATE INDEX IF NOT EXISTS idx_usage_session_open
            ON usage_session(device_id, door_no, ended_at);
        -- 按学号统计（"这个人洗了多久"）走这条；cert_no 才是人，card_no 是卡，
        -- 无卡模式下 card_no 恒为 0，拿它当人查是错的。
        CREATE INDEX IF NOT EXISTS idx_usage_session_cert
            ON usage_session(cert_no, opened_at);
        -- 按时间范围统计（"这个月洗了几次"）走这条。按**会话起点**而不是 opened_at：
        -- 后者可空（从没出过水的会话没有它），拿可空列筛时间会漏掉那些行。
        CREATE INDEX IF NOT EXISTS idx_usage_session_time
            ON usage_session(assigned_at);
        """
    )


def get_all_live(device_id: str) -> dict[int, Row]:
    rows = get_conn().execute("SELECT * FROM door_live WHERE device_id = ?", (device_id,)).fetchall()
    return {r["door_no"]: r for r in rows}


def get_door_live(device_id: str, door_no: int) -> Row | None:
    return read_one("SELECT * FROM door_live WHERE device_id = ? AND door_no = ?", (device_id, door_no))


def get_occupied_by_cert(device_id: str, cert_no: str) -> Row | None:
    """按证件号查占用中的门（无卡模式下占用者标识记录在 cert_no）。"""
    return read_one(
        "SELECT * FROM door_live WHERE device_id = ? AND cert_no = ?"
        " AND state IN ('waiting','bathing','paused') LIMIT 1",
        (device_id, cert_no),
    )


def upsert_door_live(
    device_id: str,
    door_no: int,
    state: str,
    door: JsonObject,
    reported_at: str,
    segment_started_at: str = "",
) -> None:
    """写下这个门的现态。`segment_started_at` 是当前出水段的观测起点（不在出水时为空）。"""
    write(
        """
        INSERT INTO door_live (device_id, door_no, state, card_no, cert_no, acc_no,
                               open_time, in_time, close_time, bath_time_s, status_raw,
                               reported_at, segment_started_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(device_id, door_no) DO UPDATE SET
            state=excluded.state, card_no=excluded.card_no, cert_no=excluded.cert_no,
            acc_no=excluded.acc_no, open_time=excluded.open_time, in_time=excluded.in_time,
            close_time=excluded.close_time, bath_time_s=excluded.bath_time_s,
            status_raw=excluded.status_raw, reported_at=excluded.reported_at,
            segment_started_at=excluded.segment_started_at
        """,
        (
            device_id,
            door_no,
            state,
            int(door.get("card_no") or 0),
            str(door.get("cert_no") or ""),
            str(door.get("acc_no") or ""),
            str(door.get("open_time") or ""),
            str(door.get("in_time") or ""),
            str(door.get("close_time") or ""),
            int(door.get("bath_time_s") or 0),
            int(door.get("status_raw") or 0),
            reported_at,
            segment_started_at,
        ),
    )


def insert_snapshot(
    device_id: str,
    snapshot_id: str,
    seq: int | None,
    source: str,
    captured_at: str,
    received_at: str,
    doors: list[JsonObject],
) -> None:
    """原始层落库：设备说了什么就记什么。

    `INSERT OR IGNORE` 承担重复投递的幂等——设备在收到 ack 前会重发同一条，而重发
    与"设备确实又报了一次"在协议上是同一件事（同一个 snapshot_id），落库时只留第一份。
    """
    write(
        "INSERT OR IGNORE INTO door_snapshot"
        " (device_id, snapshot_id, seq, source, captured_at, received_at, doors)"
        " VALUES (?,?,?,?,?,?,?)",
        (device_id, snapshot_id, seq, source, captured_at, received_at, json.dumps(doors, ensure_ascii=False)),
    )


def insert_event(
    occurred_at: str,
    device_id: str,
    door_no: int,
    from_state: str,
    to_state: str,
    card_no: int,
    cert_no: str,
    bath_time_s: int,
) -> None:
    """记录一次状态转换。

    它回答 `usage_session` 回答不了的问题：暂停次数（数 `bathing→paused`）、报修次数与
    停用时长（数 `→repair` 与 `repair→`）。会话表只覆盖"进入占用态到离开"那一条路，
    区间内的状态变化它看不见。
    """
    write(
        "INSERT INTO door_state_event"
        " (occurred_at, device_id, door_no, from_state, to_state, card_no, cert_no, bath_time_s)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (occurred_at, device_id, door_no, from_state, to_state, card_no, cert_no, bath_time_s),
    )


def get_open_session(device_id: str, door_no: int) -> Row | None:
    return read_one(
        "SELECT * FROM usage_session WHERE device_id = ? AND door_no = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
        (device_id, door_no),
    )


def open_session(
    device_id: str,
    door_no: int,
    card_no: int,
    cert_no: str,
    assigned_at: str,
    assigned_source: str,
) -> int:
    """开一次新会话，返回会话 id。

    `assigned_at` 与 `assigned_source` 是一对：前者是时刻，后者说明它是怎么来的
    （设备钟平移 / 观测下界，见 service 的两个常量）。**两者一起传**，免得调用方
    只更新其中一个而让列的语义分叉。

    `last_seen_at` 先落到 `assigned_at`；开会话这一帧本身就确认了"它被占着"，而这一帧
    的准确时刻随后由 touch_session 推进。
    """
    cursor = write(
        "INSERT INTO usage_session"
        " (device_id, door_no, card_no, cert_no, assigned_at, assigned_source, last_seen_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (device_id, door_no, card_no, cert_no, assigned_at, assigned_source, assigned_at),
    )
    return int(cursor.lastrowid or 0)


def touch_session(session_id: int, opened: bool, seen: bool, at: str, flow_s: int) -> None:
    """推进会话的里程碑。几个字段都只增不减，重复调用无副作用。

    `opened` 由调用方按"这一帧在不在出水"给出，写入的时刻是 `at`（帧的采集时刻）。
    **不用设备的 `inTime`**：它是设备的墙上时钟，实测偏差可达 24 小时；而且它会在
    暂停时清空、恢复时重写，跟着它走会让会话起点漂到最后一次恢复那一刻。

    `seen` 表示"这一帧确认了它仍然被占着"。结束那一帧要传假——它恰恰证明门已经不占
    了，而 `last_seen_at` 记的是最后一次确认占着的那一刻，两者的差就是盲区大小。

    `flow_s` 只取更大的值：调用方给的是"截至此刻的出水秒数"，而设备的 `bathTime`
    只在暂停那一刻累加、释放时整体归零，本身并不单调。
    """
    write(
        "UPDATE usage_session SET"
        " opened_at = CASE WHEN opened_at IS NULL AND ? THEN ? ELSE opened_at END,"
        " last_seen_at = CASE WHEN ? THEN ? ELSE last_seen_at END,"
        " flow_s = MAX(flow_s, ?)"
        " WHERE id = ?",
        (opened, at, seen, at, flow_s, session_id),
    )


def close_session(session_id: int, ended_at: str) -> None:
    """标记会话结束。

    只写 `ended_at`——`flow_s` 与 `opened_at` 的推进是 `touch_session` 的职责，结束这
    一帧同样先经过它，不在这里重复。

    **不记结束原因**：浴室 App 在"用户主动出门"和三种超时释放下写的是同一套清零语句，
    数据库层面完全同形，服务端没有依据区分。硬分就是编造。真要看"怎么结束的"，
    `door_state_event` 里每次转换都在（`bathing→free` 与 `paused→free` 含义不同）。
    """
    write("UPDATE usage_session SET ended_at = ? WHERE id = ?", (ended_at, session_id))


def latest_usage_by_door() -> dict[tuple[str, int], Row]:
    """全部设备每个喷头最后一次真正使用的会话，键是 `(device_id, door_no)`。

    判据是 `opened_at IS NOT NULL`——"分配后直接结算"那种从没出过水的会话因此
    自动排除；而"开了阀立刻关"（`flow_s` 为 0）会被保留，因为确实出过水。

    按 `opened_at` 排序而非 `assigned_at`：排在前面的是"真正开始洗"更晚的那次。
    同一个喷头若两条记录 `opened_at` 相同，取 id 更大的那条，避免结果不定。

    一次查全部而不是按设备查：行数按年计也就几万，有 `(device_id, door_no,
    opened_at)` 索引在，全查比让调用方循环更省。
    """
    rows = (
        get_conn()
        .execute(
            "SELECT device_id, door_no, opened_at, cert_no, flow_s FROM usage_session u"
            " WHERE opened_at IS NOT NULL"
            "   AND u.id = (SELECT id FROM usage_session"
            "               WHERE device_id = u.device_id AND door_no = u.door_no"
            "                 AND opened_at IS NOT NULL"
            "               ORDER BY opened_at DESC, id DESC LIMIT 1)"
        )
        .fetchall()
    )
    return {(row["device_id"], row["door_no"]): row for row in rows}


_DOOR_LIST_SQL = """
    SELECT d.device_id, d.door_no, d.state, d.card_no, d.cert_no, d.acc_no,
           d.open_time, d.in_time, d.close_time, d.bath_time_s, d.status_raw, d.reported_at,
           dev.name AS device_name
    FROM door_live d LEFT JOIN device dev ON dev.device_id = d.device_id
"""


def list_doors(device_id: str | None = None) -> list[DoorView]:
    """喷头现态列表，联所属设备与上次使用；不指定设备时返回所有设备。

    任何返回喷头数据的接口都走这一个查询：权限管的是能不能调接口，不是返回
    什么内容（见 `schemas` 的模块注释）。
    """
    if device_id:
        rows = get_conn().execute(_DOOR_LIST_SQL + " WHERE d.device_id = ? ORDER BY d.door_no", (device_id,)).fetchall()
    else:
        rows = get_conn().execute(_DOOR_LIST_SQL + " ORDER BY d.device_id, d.door_no").fetchall()

    usage = latest_usage_by_door()
    return [_as_door_view(row, usage.get((row["device_id"], row["door_no"]))) for row in rows]


def _as_door_view(row: Row, last_usage: Row | None) -> DoorView:
    """把行转成 DoorView 形状。

    SQLite 用 0/1 存布尔，直接透出会让字段的实际类型与声明不符。LEFT JOIN 无匹配时
    为 NULL，保持 NULL 而不是折成 False——"设备记录不存在"与"设备离线"不是一回事。
    """
    view: DoorView = {
        "device_id": row["device_id"],
        "door_no": row["door_no"],
        "state": row["state"],
        "card_no": row["card_no"],
        "cert_no": row["cert_no"],
        "acc_no": row["acc_no"],
        "open_time": row["open_time"],
        "in_time": row["in_time"],
        "close_time": row["close_time"],
        "bath_time_s": row["bath_time_s"],
        "status_raw": row["status_raw"],
        "reported_at": row["reported_at"],
        "device_online": False,  # 占位：由 service 用内存连接表覆盖（唯一来源，见 devices/service）
        "device_name": row["device_name"],
        "last_used_at": "" if last_usage is None else str(last_usage["opened_at"]),
        "last_used_s": 0 if last_usage is None else int(last_usage["flow_s"]),
        "last_used_cert": "" if last_usage is None else str(last_usage["cert_no"]),
    }
    return view


def get_active_by_cert(device_id: str, cert_no: str) -> Row | None:
    """同设备上该凭证的活跃会话（重复分配拦截依据，对齐旧系统 duplicate_session）。"""
    return read_one(
        "SELECT * FROM usage_session WHERE device_id = ? AND cert_no = ? AND ended_at IS NULL ORDER BY id DESC LIMIT 1",
        (device_id, cert_no),
    )


def list_open_sessions() -> list[Row]:
    """全部设备的活跃会话（自动结算扫描用）。"""
    return get_conn().execute("SELECT * FROM usage_session WHERE ended_at IS NULL").fetchall()
