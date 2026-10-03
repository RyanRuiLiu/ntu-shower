"""doors 五表 SQL 集中管理。

分两层：`door_snapshot` 是原始层——设备说了什么就记什么，不做任何解释；其余四张是
派生层——`door_live` 现态、`door_state_event` 状态转换流水、`usage_session` 洗浴会话、
`usage_segment` 每一次连续出水。

这条分层是整套设计的骨架：派生层的每条结论都依赖"当时的推导规则"，规则一旦发现缺陷
或要加新指标，没有原始帧就永久无法重算。旧系统只留了原始快照，那 100 天才能重放回来。
代价约 170MB/年。

会话与段分开成两张表，是因为两件事粒度不同：会话是"一次占用"（人 + 门 + 起止），段是
占用里的一段连续出水。合成一张就把"洗一次"与"开了几次水"压成一件事，而"这个人洗了
多久"和"他中间停了几次"要分别回答。

## 刻意不存的几样

每一条都能从现有列推导出来。**可推导的不存**——存了就是制造第二个真相：

- **结束原因**：数据库层面不存在。主动出门与四种超时释放写的是同一套清零 SQL，
  `bathing→free` 与 `paused→free` 完全同形，硬分就是编造；
- **出水次数**：数 `usage_segment` 的行数；
- **暂停次数**：数 `door_state_event` 的 `bathing→paused`。它与出水次数**不相等**——
  一次出水可能直接以 `bathing→free` 收场（洗完就走），那是出门不是暂停；
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
            -- 采集那一刻**设备墙钟**的读数，与 captured_at 取自同一瞬间。两者相减就是
            -- 设备钟偏差，open_time / in_time / close_time 靠那个偏差平移到服务端时间轴。
            --
            -- 它必须落在原始层：偏差是**随时间变**的量，事后要从历史重算任何一条换算，
            -- 都得先有"当时那口钟读数是多少"。它推不出来，只能在采集那一刻记下。
            device_clock TEXT,
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
            -- 三个时间字段都是**服务端轴**上的值（设备那口钟的读数只留在原始帧里）。
            -- 它们对应的会话与段由 usage_session / usage_segment 回答，这里只是为了
            -- 让"这个门现在什么状况"一次查询就能渲染出来。
            open_time   TEXT    NOT NULL DEFAULT '',
            in_time     TEXT    NOT NULL DEFAULT '',
            close_time  TEXT    NOT NULL DEFAULT '',
            bath_time_s INTEGER NOT NULL DEFAULT 0,
            status_raw  INTEGER NOT NULL DEFAULT 0,
            reported_at TEXT    NOT NULL,
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

        -- 段层：一次连续出水。两端时刻都在服务端轴上，设备轴的两份原文另存。
        CREATE TABLE IF NOT EXISTS usage_segment (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  INTEGER NOT NULL,
            -- 门号与设备号能从会话取到，这里冗余一份是为了按门筛段时免联表。与 door_live
            -- 保留协议字段名同类：刻意的、写明白的例外，不是疏漏。
            device_id   TEXT    NOT NULL,
            door_no     INTEGER NOT NULL,
            opened_at   TEXT    NOT NULL,
            -- 空 = 尚未关阀。它为空只有两种情形：数据到此为止，或设备卡住不再写库。
            closed_at   TEXT,
            flow_s      INTEGER NOT NULL DEFAULT 0,
            -- 设备写下的那两个读数原文（yyyyMMddHHmmss）。段长取"同一口钟两个读数之差"，
            -- 没有原文就无法复核，也无法在事后重算。
            opened_raw  TEXT    NOT NULL DEFAULT '',
            closed_raw  TEXT    NOT NULL DEFAULT '',
            -- measured：设备轴差与观测差一致（取前者）；observed：只信观测差；
            -- capped：观测差越过一次占用的物理上限、被截断；derived：从设备痕迹推回；
            -- unclosed：数据到此为止仍在出水，长度不可知。五种来源强度不同，统计必须
            -- 分得清——capped 与"它真的就洗了那么久"绝不能混成一件事。
            source      TEXT    NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_usage_segment_session ON usage_segment(session_id);
        CREATE INDEX IF NOT EXISTS idx_usage_segment_door
            ON usage_segment(device_id, door_no, opened_at);

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
            -- 观测连续性：`continuous` 我们一路看着；`gap` 中间有超过一次占用上限的空档。
            --
            -- 它只描述**我们看没看见**，不描述发生过什么。缺口前的那一场在我们最后看见
            -- 它的时刻收尾、缺口后另起一场，两场各自完整——而中间那段是空的，这里如实
            -- 标出来，不让统计把"我们没看见"当成"什么都没发生"。
            observed_span TEXT NOT NULL DEFAULT 'continuous',
            -- 设备累计的出水秒数（bath_time_s 的最大值）。它是**设备说的话**，用来与我们
            -- 的段之和对照：两者不等，就说明中间有一段没进段表（丢帧、离线、设备卡住）。
            device_flow_s INTEGER NOT NULL DEFAULT 0,
            -- 段之和与设备累计值是否一致。**有 derived 段时恒为假**——那段的长度就是从
            -- 设备那个值推出来的，等式必然成立，拿它当"完整"的证据是循环论证。
            segments_complete INTEGER NOT NULL DEFAULT 0,
            -- 离开占用态那一帧。**跨盲区时它不是真实结束时刻**，只是"下次见到它的时刻"。
            ended_at     TEXT,
            -- 最后一次**确认它还占着**的那一帧。它才是可信的结束下界：`ended_at` 与它的
            -- 差就是观测盲区，超过 SESSION_MAX_S 说明中间发生过一次我们没看见的释放。
            -- 统计因此分两层：占用/等待/暂停类指标走"盲区 ≤ SESSION_MAX_S"的那些会话，
            -- 出水类指标（flow_s）不受影响——它只累加有证据的段。
            last_seen_at TEXT    NOT NULL,
            -- 段之和。它能从 usage_segment 推出来，在这里存一份是为了让统计有个不联表的
            -- 入口——"这个人总共洗了多久"是最常问的一句，不该每次去求和。
            flow_s       INTEGER NOT NULL DEFAULT 0
        );
        -- 一个喷头的使用历史。按**会话起点**排而不是开阀时刻：后者已不在本表（它在段表
        -- 里），而且拿可空的列筛时间会漏行。
        CREATE INDEX IF NOT EXISTS idx_usage_session_door
            ON usage_session(device_id, door_no, assigned_at);
        -- "这个门现在开着会话吗"（get_open_session）：一帧里每个门问一次，实时路径上
        -- 帧密度是每秒数帧，所以它必须走索引而不是扫全表。把 ended_at 也放进键里，
        -- 三列全命中；只到 door_no 的话还要在一堆历史会话里筛 ended_at IS NULL。
        CREATE INDEX IF NOT EXISTS idx_usage_session_open
            ON usage_session(device_id, door_no, ended_at);
        -- 按学号统计（"这个人洗了多久"）走这条；cert_no 才是人，card_no 是卡，
        -- 无卡模式下 card_no 恒为 0，拿它当人查是错的。
        CREATE INDEX IF NOT EXISTS idx_usage_session_cert
            ON usage_session(cert_no, assigned_at);
        -- 按时间范围统计（"这个月洗了几次"）走这条。按**会话起点**而不是开阀时刻：
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
) -> None:
    """写下这个门的现态。

    三个时间字段存的是**服务端轴**的值（调用方负责换算）：设备那口钟的读数只在原始帧
    里保真，对外一律不出现——同一个字段名在两处放两条轴的值，读的人分不出来。
    """
    write(
        """
        INSERT INTO door_live (device_id, door_no, state, card_no, cert_no, acc_no,
                               open_time, in_time, close_time, bath_time_s, status_raw,
                               reported_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(device_id, door_no) DO UPDATE SET
            state=excluded.state, card_no=excluded.card_no, cert_no=excluded.cert_no,
            acc_no=excluded.acc_no, open_time=excluded.open_time, in_time=excluded.in_time,
            close_time=excluded.close_time, bath_time_s=excluded.bath_time_s,
            status_raw=excluded.status_raw, reported_at=excluded.reported_at
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
        ),
    )


def insert_snapshot(
    device_id: str,
    snapshot_id: str,
    seq: int | None,
    source: str,
    captured_at: str,
    device_clock: str,
    received_at: str,
    doors: list[JsonObject],
) -> None:
    """原始层落库：设备说了什么就记什么。

    `INSERT OR IGNORE` 承担重复投递的幂等——设备在收到 ack 前会重发同一条，而重发
    与"设备确实又报了一次"在协议上是同一件事（同一个 snapshot_id），落库时只留第一份。
    """
    write(
        "INSERT OR IGNORE INTO door_snapshot"
        " (device_id, snapshot_id, seq, source, captured_at, device_clock, received_at, doors)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (
            device_id,
            snapshot_id,
            seq,
            source,
            captured_at,
            device_clock,
            received_at,
            json.dumps(doors, ensure_ascii=False),
        ),
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


def touch_session(session_id: int, seen: bool, at: str, flow_s: int, device_flow_s: int, gap: bool) -> None:
    """推进会话的里程碑。几个字段都只增不减，重复调用无副作用。

    `seen` 表示"这一帧确认了它仍然被占着"。结束那一帧要传假——它恰恰证明门已经不占
    了，而 `last_seen_at` 记的是最后一次确认占着的那一刻，两者的差就是盲区大小。

    `flow_s` 只取更大的值：调用方给的是"截至此刻的出水秒数"，而设备的 `bathTime`
    只在暂停那一刻累加、释放时整体归零，本身并不单调。`device_flow_s` 同理。

    `gap` 表示这一帧发现有断层（见 service 的 `_has_gap`）。它是**单向**的：断开过
    一次之后，后面接上的连续观测补不回失去的那一段。
    """
    write(
        "UPDATE usage_session SET"
        " last_seen_at = CASE WHEN ? THEN ? ELSE last_seen_at END,"
        " flow_s = MAX(flow_s, ?),"
        " device_flow_s = MAX(device_flow_s, ?),"
        " observed_span = CASE WHEN ? THEN 'gap' ELSE observed_span END"
        " WHERE id = ?",
        (seen, at, flow_s, device_flow_s, gap, session_id),
    )


def close_session(session_id: int, ended_at: str, segments_complete: bool) -> None:
    """标记会话结束，并把完整性定稿。

    `segments_complete` 在这里定稿而不是随手更新：它要回答的是"这一场我们记全了吗"，
    而只有结束时段表才不再增长。

    只写这两样——`flow_s` 与 `last_seen_at` 的推进是 `touch_session` 的职责，结束这
    一帧同样先经过它，不在这里重复。

    **不记结束原因**：浴室 App 在"用户主动出门"和三种超时释放下写的是同一套清零语句，
    数据库层面完全同形，服务端没有依据区分。硬分就是编造。真要看"怎么结束的"，
    `door_state_event` 里每次转换都在（`bathing→free` 与 `paused→free` 含义不同）。
    """
    write(
        "UPDATE usage_session SET ended_at = ?, segments_complete = ? WHERE id = ?",
        (ended_at, int(segments_complete), session_id),
    )


def get_open_segment(device_id: str, door_no: int) -> Row | None:
    """这个门当前未关阀的那一段。"""
    return read_one(
        "SELECT * FROM usage_segment"
        " WHERE device_id = ? AND door_no = ? AND closed_at IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (device_id, door_no),
    )


def open_segment(session_id: int, device_id: str, door_no: int, opened_at: str, opened_raw: str) -> int:
    """开一段出水，返回段 id。

    `opened_raw` 是设备报的 `in_time` 原文：段长取"同一口钟两个读数之差"，没有它就
    无法在事后复核这一次换算。
    """
    cursor = write(
        "INSERT INTO usage_segment (session_id, device_id, door_no, opened_at, opened_raw) VALUES (?,?,?,?,?)",
        (session_id, device_id, door_no, opened_at, opened_raw),
    )
    return int(cursor.lastrowid or 0)


def close_segment(segment_id: int, closed_at: str, flow_s: int, source: str, closed_raw: str) -> None:
    """关掉一段出水。`source` 说明这个长度是怎么得来的（见建表处的五种来源）。"""
    write(
        "UPDATE usage_segment SET closed_at = ?, flow_s = ?, source = ?, closed_raw = ? WHERE id = ?",
        (closed_at, flow_s, source, closed_raw, segment_id),
    )


def add_derived_segment(
    session_id: int,
    device_id: str,
    door_no: int,
    opened_at: str,
    closed_at: str,
    flow_s: int,
    closed_raw: str,
) -> int:
    """记一段**推出来的**出水。

    冷启动接手用：我们上线时人已经洗到一半、正停在暂停态，`in_time` 早被清空，只有
    "关阀时刻"与"累计出水秒数"两道痕迹——从前者往前数那么多秒，就是那一段的开阀时刻。

    它与观测得的段同住一张表，靠 `source` 区分：长度取自设备的累计值，不是我们看出来
    的。同一会话只推一次，理由见 service 的 `_derive_segment`。
    """
    cursor = write(
        "INSERT INTO usage_segment (session_id, device_id, door_no, opened_at, closed_at, flow_s, closed_raw, source)"
        " VALUES (?,?,?,?,?,?,?,'derived')",
        (session_id, device_id, door_no, opened_at, closed_at, flow_s, closed_raw),
    )
    return int(cursor.lastrowid or 0)


def mark_segment_unclosed(segment_id: int) -> None:
    """把仍在出水的段标成"长度不可知"。

    数据到此为止（重放到历史截止）时它还没关阀——那一段水有多长，没有任何一帧能证明，
    所以长度留 0、来源标 `unclosed`，不让"到现在为止"冒充它。
    """
    write("UPDATE usage_segment SET source = 'unclosed' WHERE id = ?", (segment_id,))


def segments_of_session(session_id: int) -> list[Row]:
    """一个会话的全部段，按开阀先后。"""
    return (
        get_conn()
        .execute("SELECT * FROM usage_segment WHERE session_id = ? ORDER BY opened_at, id", (session_id,))
        .fetchall()
    )


def latest_usage_by_door() -> dict[tuple[str, int], Row]:
    """全部设备每个喷头**最后一次真正使用**的那一段，键是 `(device_id, door_no)`。

    判据从"会话有没有开阀"换成"有没有段"：段表里每一行就是一次出水，"分配后直接结算"
    那种从没出过水的会话一行都不会有；而"开了阀立刻关"（`flow_s` 为 0）会被保留，
    因为确实出过水。

    **只取已关阀的段**：在途段的 `flow_s` 还是 0（它只在关阀那一刻才写），把它当"上次
    使用"会让淋浴中的喷头显示"上次用了 0 秒"——而那一格问的是"上一次**完整**使用"。
    正在进行的这一次由 `in_time` + `bath_time_s` 回答，不需要它兼职。

    段表不存 `cert_no`（一次出水里不可能换人），所以取"谁用的"要联会话表——这一句的
    文件头注释解释了那个冗余是刻意的、为什么只冗余了门号与设备号。

    一次查全部而不是按设备查：行数按年计也就几万，有 `(device_id, door_no, opened_at)`
    索引在，全查比让调用方循环更省。
    """
    rows = (
        get_conn()
        .execute(
            "SELECT g.device_id, g.door_no, g.opened_at, g.flow_s, s.cert_no"
            " FROM usage_segment g JOIN usage_session s ON s.id = g.session_id"
            " WHERE g.closed_at IS NOT NULL"
            "   AND g.id = (SELECT id FROM usage_segment"
            "               WHERE device_id = g.device_id AND door_no = g.door_no"
            "                 AND closed_at IS NOT NULL"
            "               ORDER BY opened_at DESC, id DESC LIMIT 1)"
        )
        .fetchall()
    )
    return {(row["device_id"], row["door_no"]): row for row in rows}


_DOOR_LIST_SQL = """
    SELECT d.device_id, d.door_no, d.state, d.card_no, d.cert_no, d.acc_no,
           d.open_time, d.in_time, d.close_time, d.bath_time_s, d.status_raw, d.reported_at
    FROM door_live d
"""


def list_doors(device_id: str | None = None) -> list[DoorView]:
    """喷头现态列表，带上次使用；不指定设备时返回所有设备。

    任何返回喷头数据的接口都走这一个查询：权限管的是能不能调接口，不是返回
    什么内容（见 `schemas` 的模块注释）。不联 device 表：设备域的事实
    （名称、在线）由设备资源回答，门行不携带（见 `schemas` 的 DoorView）。
    """
    if device_id:
        rows = get_conn().execute(_DOOR_LIST_SQL + " WHERE d.device_id = ? ORDER BY d.door_no", (device_id,)).fetchall()
    else:
        rows = get_conn().execute(_DOOR_LIST_SQL + " ORDER BY d.device_id, d.door_no").fetchall()

    usage = latest_usage_by_door()
    return [_as_door_view(row, usage.get((row["device_id"], row["door_no"]))) for row in rows]


def _as_door_view(row: Row, last_usage: Row | None) -> DoorView:
    """把行转成 DoorView 形状。"""
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
