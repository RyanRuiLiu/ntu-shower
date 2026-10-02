"""喷头状态派生与洗浴会话维护：`t_doorlist` 解析规则的单一来源。

## 时刻只有一个来源

落库的每一个时刻都表达在**服务端时间轴**上。取值按可信度分三种：

- **帧的采集时刻**（`captured_at`，设备锚在服务端时间上，见设备侧 core/clock/ServerClock）
  ——它在库变化那一刻就定了，不受积压与重发影响；
- **设备钟平移**：设备报的 `openTime`（整场会话不变，就是刷卡那一刻）与 `in_time`
  （本次开阀时刻），各自加上当时的钟偏差（core/device_clock）；
- 拿不到偏差时退回帧时刻，并如实标明那只是观测下界。

设备那口钟的**绝对值**不可信：自然漂移 8.5 秒/天，还会被人为回拨一小时（代价是把自动
释放推后），100 天里 16.6% 的时间锚点落在被改过的时段。但**偏差在一次会话的 20 分钟
窗口里是常量**（20 分钟里的漂移只 0.03 秒），所以平移成立；跳变只有 23:5x 那一处，
而设备 23:53 自动释放所有喷头，任何会话都不会跨过它。

## 七态

判据自上而下，先命中先返回：repair > disabled > bathing > paused > waiting >
selected > free。"有人" = `cardno ≠ 0` **或** `accno` 既非空串也非 `"0"`。
**占用者标识**取 `certno`，为空时退回 `accno`（见 [_occupant]）。

分两组：占用组 `{waiting, bathing, paused}` 必然有人，空闲组
`{free, selected, repair, disabled}` 必然无人。

**这是对任意输入的满射**：设备的 `status` 会报出 0/1/2/3 之外的值，规则让 1 与 3 之外
的任何值都落到"非报修非停用"那条路上，最终仍落在七态之内——不存在算不出状态的门读数。
1、2 两条排在占用之前，是因为 dbGUIDE 写明报修会清空占用字段；两者本不该同时出现，
排前面是为了在它们意外同时出现时站在安全的一侧。

## 五条规则（相邻两帧的划分，互斥且穷尽）

一个门在相邻两帧之间能被观测到的只有状态与占用者两样，拼起来恰好 58 种转换：

| 规则 | 命中条件 | 数量 | 处理 |
|---|---|---|---|
| R1 不动会话 | 两端都在空闲组 | 16 | 状态变了才记事件 |
| R2 开会话 | 空闲 → 占用 | 12 | 记事件 + 开会话 |
| R3 结束会话 | 占用 → 空闲 | 12 | 记事件 + 结束会话 |
| R4 推进 | 都在占用组、**同一人** | 9 | 推进 `last_seen_at` 与 `flow_s` |
| R5 换人 | 都在占用组、**不同人** | 9 | 结旧账 + 开新账 |

**只有"有人→有人"那 9 对会分裂成两种**——只有那种情形下"是不是同一个人"才有意义。
`waiting→waiting` 在状态矩阵里看不出任何异常，但若是两个人，那是两笔账。

实测 100 天发生 **13 次换人**，其中 10 次的形态是"前一个人在暂停态、后一个人直接刷卡
覆盖了他的占用字段"。**换人判据用证件号而不是卡号**：这台设备无卡，实测 235270 个门帧
里 `cardno` 非 0 的一个都没有，比它等于没比，那 13 次会全部漏检。

这不是"列举异常"，而是把合法空间穷举干净——异常不是空间外的东西，是空间里稀有的格子
（例如 `free→bathing`，跳过入门直接出水，实测 3 次）。

## 首帧与两条帧结构规则

首次见到某个门时没有前态：占用态就开会话（`assigned_at` 由 [_assigned_at] 给出，
能平移就是真实刷卡时刻，否则是观测下界），空闲态不动。**首帧不记事件**——没有"从哪来"，
记一条来源不明的 `from_state` 会污染转换统计。

- 同一帧内重复门号取**最后一条**（见 `_clean` 的调用点）；
- 门在后续帧里消失时**保留原状态**，不判成空闲——漏报一个门不等于那个门空了。

## 三条输入清洗（都在派生之前）

见 [_clean] 与 [_as_door_no]：门号必须严格整数且在 `1..DOOR_MAX`；`card_no` 取非负、
`bath_time_s` 夹进合法域；不可解析的时间字段置空。**收敛而不是丢整帧**——坏掉的通常
只是某几个字段，其余仍有意义。

## 设备字段的语义

`inTime` 是本次开阀时刻，暂停时清空、恢复时重新填入。它的**值**经偏差平移到服务端时间
轴后可用于推导开阀时刻（见 [_opened_at]），但**不能**当会话起点——跟着它走会把起点拽到
最后一次恢复那一刻。`bathTime` 只在暂停那一刻累加本次出水，出水期间不动，释放时整体
归零；它是**时长不是时刻**，与钟的读数无关，所以可靠。

`flow_s > 0` 才是"算不算一次使用"的判据，不是 `opened_at IS NOT NULL`：设备在离线期间
完成的"开阀→结算"我们两个帧都收不到，只见得到结算后的 free，那时 `in_time` 已清零、
`opened_at` 补不回来，但设备累加的 `bath_time_s` 会留下秒数。

## 算不出来的三样

在注释里承认，不找办法硬凑：

- **会话因何结束**——主动出门与四种超时释放用的是同一套清零语句，数据库层面完全同形
  （见 [_close]）；
- **盲区内的出水秒数**——那段既没进 `bathTime`、也没被我们看见（见 [_flow_after]）；
- **第二次暂停之后的首次开阀时刻**——设备只在暂停那一刻留下"关阀时刻"与"累计出水秒数"，
  两者相减得到本段起点；多段之后那个和已经混了，无从分辨（见 [_opened_at]）。

另外两件不归本模块管：**设备什么时候在线**由 `device_link_event` 回答（帧是事件驱动
的，空闲时不上报，"没消息"无法区分"没人用"与"离线"）；**用了多少水、花了多少钱**只有
设备端才有。
"""

import threading
from sqlite3 import Row

from app.core import device_clock, events
from app.core.clock import now_rfc3339, parse_rfc3339, seconds_between, shift_rfc3339
from app.core.protocol import JsonObject, as_int, text_of
from app.modules.devices import service as devices_service
from app.modules.doors import repository
from app.modules.doors.schemas import OCCUPIED_STATES, DoorState, DoorView

# t_doorlist.status 的原始值（设备上报的 int，仅作辅助判据）
RAW_STATUS_REPAIR = 1
RAW_STATUS_SELECTED = 2
RAW_STATUS_DISABLED = 3

#: 一次会话（一个门从被占到被释放）的物理上限，秒 = 设备配置 maxbatchtime（20 分钟）。
#:
#: 设备满这个时长会强制关阀释放，所以"一次占用超过它"本身不成立。它在这里的作用是
#: 判"两帧之间的出水断没断"：间隔超过它，中间必然发生过一次我们没看见的释放，那段
#: 出水没有任何证据，不并入时长。
#:
#: **这个值变过**：2026-05 之前是 1800 秒，换系统时改成 1200。四处互相独立的数据都
#: 指到同一处断崖——设备流水的记录时长（老口径堆在 1806 秒后断崖、新口径堆在 1209
#: 秒后断崖）、设备活跃库最大 1201 秒、本项目采集的 bathTime 最大 1154 秒（1200~1800
#: 之间一条都没有）。要回溯 2026-05 之前的历史得按当时的配置另取一个值。
SESSION_MAX_S = 1200

#: 喷头数上限 = 设备的 `DoorCount`（共享配置实测 48；本机 waterList 只装了 7 个，
#: 从 1 开始编号）。
DOOR_MAX = 48

#: `bath_time_s` 的上界。取一天而不是设备上限：那个上限由设备侧服务器下发、随时可能
#: 变大，拿它当闸门会误伤真实数据；而一天以上的单次出水不存在。
MAX_BATH_S = 86400

#: 快照来源：浴室 App 写库触发，状态真的变了。
SOURCE_DB_WATCH = "db_watch"

#: 快照来源：重连或开机后推的一遍全貌，与状态是否变化无关。
#:
#: 这一类**只刷新现态**。它回答的是"当前是什么样"，不是"刚刚发生了什么"：拿它派生，
#: 每次断线重连都会记出一串从未发生的迁移，并把会话起点拖到重连那一刻——而真实起点
#: 在此之前。
SOURCE_STARTUP = "startup"

#: 快照来源：从旧系统（`ntu-remote-shower`）的历史记录重放进来的。
#:
#: 与另外两种的区别在**时刻**：实时帧的采集时刻由设备侧锚点给出（或退回服务端接收
#: 时刻），而重放帧的采集时刻就是它当年的接收时刻——不这样的话 100 天的活动会全部堆
#: 到重放这一刻。重放器见 tools/replay_history.py。
SOURCE_IMPORT = "import"

#: 会话起点的来源：由设备报的 `open_time` 加上钟偏差平移而来——那是真实的刷卡时刻。
SESSION_ASSIGNED_INFERRED = "inferred"

#: 会话起点的来源：我们首次观测到它占着的时刻。**真实起点更早**，这只是下界。
#: 设备还没回过 pong（偏差无从谈起）、或 `open_time` 缺失时落到这里。
SESSION_ASSIGNED_OBSERVED = "observed"


def list_doors(device_id: str | None = None) -> list[DoorView]:
    """喷头现态列表。两个列表端点与 shadow 都走这里；device_online 由
    core/connections 的连接表填入。"""
    doors = repository.list_doors(device_id)
    for door in doors:
        door["device_online"] = devices_service.is_online(door["device_id"])
    return doors


def derive_state(door: JsonObject) -> DoorState:
    """由一帧设备快照派生喷头状态（判据见模块注释）。"""
    status = as_int(door.get("status_raw"))
    card_no = as_int(door.get("card_no"))
    acc_no = str(door.get("acc_no") or "")
    in_time = door.get("in_time")
    close_time = door.get("close_time")
    bath_time = as_int(door.get("bath_time_s"))

    if status == RAW_STATUS_REPAIR:
        return DoorState.REPAIR
    if status == RAW_STATUS_DISABLED:
        return DoorState.DISABLED

    occupied = card_no != 0 or acc_no not in ("", "0")
    if occupied:
        if in_time:
            return DoorState.BATHING
        if bath_time > 0 and close_time:
            return DoorState.PAUSED
        return DoorState.WAITING
    if status == RAW_STATUS_SELECTED:
        return DoorState.SELECTED
    return DoorState.FREE


def _as_door_no(value: object) -> int | None:
    """门号：接受整数、能精确表示的浮点、以及纯数字字符串，其余一律 None。

    **不做截断。** `3.7` 截成 `3` 会把一条坏数据伪装成 3 号喷头的真实读数，脏账记到
    别人头上——别的数值字段可以宽容，门号不行，它决定"这是哪个喷头"。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        return None
    return number if 1 <= number <= DOOR_MAX else None


def _occupant(door: JsonObject) -> tuple[int, str]:
    """占用者标识：证件号，为空时退回账户号。

    实测两者始终同时有值，退回这一步只覆盖极端输入——但少了它，一条只有 `acc_no`
    的帧会被当成"无人认领"，按人统计时多出一个空身份。
    """
    cert_no = str(door.get("cert_no") or "")
    return as_int(door.get("card_no")), cert_no or str(door.get("acc_no") or "")


def _assigned_at(door: JsonObject, at: str, device_id: str) -> tuple[str, str]:
    """会话起点与它的来源，一起返回。

    **优先**：把设备报的 `open_time` 加上钟偏差平移过来。那是**刷卡那一刻**设备写下的，
    而它在整场会话里不变——暂停与恢复都不改它，只有释放才整体清零。所以无论中间暂停过
    几次，它都还是同一个值，平移回来就是真实的会话起点。

    平移所需的钟偏差来自设备回的 pong（见 core/device_clock）。它之所以成立，是因为
    一次会话压在 20 分钟以内、而 20 分钟里的钟漂移只有 0.03 秒；跳变只有 23:5x 那一处，
    而设备 23:53 自动释放所有喷头，任何会话都不会跨过它。

    **退路**：设备还没回过 pong、或这一帧没带 `open_time`、或格式解析不了——退回本帧
    时刻，并如实标成观测下界（真实起点更早）。两种来源的强度不同，统计时必须分得清：
    `inferred` 的跨度可以用，`observed` 的天然偏小。
    """
    shifted = device_clock.to_server(door.get("open_time"), device_id)
    if shifted is None:
        return at, SESSION_ASSIGNED_OBSERVED
    return shifted, SESSION_ASSIGNED_INFERRED


def _clean(raw_door: JsonObject) -> JsonObject | None:
    """把一条门读数收敛到合法域；收敛不了的整条丢弃。

    **收敛而不是丢整帧**：坏掉的通常只是某几个字段，其余仍有意义。

    - 门号必须严格整数且在 `1..DOOR_MAX`（理由见 [_as_door_no]）。
    - `card_no` 取非负：负卡号不是合法卡号，而占用判据是"卡号非 0"，放行会记出一个
      并不存在的占用者。
    - `bath_time_s` 夹到 `[0, MAX_BATH_S]`：负值与天文数字都只可能是脏数据。
    - 三个时间字段不可解析即置空：原样留着会一路显示到网页上，看起来像"设备在某个
      不存在的时刻开了阀"。它们的**值**不参与推导，但"有没有"参与——坏值不清掉，
      一条乱码就会被读成"正在出水"。
    """
    door_no = _as_door_no(raw_door.get("door_no"))
    if door_no is None:
        return None

    cleaned = dict(raw_door)
    cleaned["door_no"] = door_no
    cleaned["card_no"] = max(0, as_int(raw_door.get("card_no")))
    cleaned["bath_time_s"] = min(MAX_BATH_S, max(0, as_int(raw_door.get("bath_time_s"))))
    for key in ("open_time", "in_time", "close_time"):
        value = raw_door.get(key)
        if value and parse_rfc3339(value) is None:
            cleaned[key] = ""
    return cleaned


#: 每台设备一把锁：同一设备的帧必须串行处理。两帧并发进入会让双方读到同一份旧现态，
#: 于是同一次迁移被记两遍、或同一段时间被开出两条会话。跨设备互不影响，所以按设备
#: 分锁而不是共用一把。
_device_locks: dict[str, threading.Lock] = {}
_device_locks_guard = threading.Lock()


def _device_lock(device_id: str) -> threading.Lock:
    with _device_locks_guard:
        lock = _device_locks.get(device_id)
        if lock is None:
            lock = threading.Lock()
            _device_locks[device_id] = lock
        return lock


def process_snapshot(device_id: str, payload: JsonObject) -> JsonObject:
    """处理一帧快照；同一设备的帧串行（理由见 [_device_lock]）。

    锁覆盖整段：派生读的是刚写下的现态，把读放到锁外会读到别的帧的写入。
    """
    with _device_lock(device_id):
        return _apply_snapshot(device_id, payload)


def _apply_snapshot(device_id: str, payload: JsonObject) -> JsonObject:
    """一帧的完整处理：先落原始帧，再派生状态、diff 记事件、更新现态、维护会话。

    帧时刻取设备锚定的采集时刻（`captured_at`）——它在库变化那一刻就定了，不受积压
    与重发影响；设备尚未建立锚点时退回本机接收时刻。**这是本系统唯一的时间来源**：
    设备的三个原始时间字段逐字保留供溯源，不参与任何时刻。

    空的一帧不是错误：设备读库失败时会照送一条空的（见 DoorSnapshotSender），那本身
    就是一条事实。落库留痕、派生跳过即可——回错误会让设备端不删发件箱那一行，把整个
    队列堵死。
    """
    received_at = now_rfc3339()
    captured_at = text_of(payload, "captured_at")
    # 采集时刻不规范就退回接收时刻：宁可差一个网络往返，也不能把坏值放进时间轴
    occurred_at = captured_at if parse_rfc3339(captured_at) is not None else received_at

    doors = payload.get("doors")
    raw_doors = [d for d in doors if isinstance(d, dict)] if isinstance(doors, list) else []
    repository.insert_snapshot(
        device_id,
        text_of(payload, "snapshot_id"),
        as_int(payload.get("seq")) or None,
        text_of(payload, "source"),
        occurred_at,
        received_at,
        raw_doors,
    )

    prev_live = repository.get_all_live(device_id)
    # 全貌帧只刷新现态，不派生（判据与理由见 SOURCE_STARTUP）。
    snapshot_only = text_of(payload, "source") == SOURCE_STARTUP

    # 先收敛、再按门号去重：同一帧里重复的门号取最后一条——设备按门号整行读取，重复
    # 只可能来自拼装错误，取后者与"后来者覆盖"的直觉一致。收敛发生在派生之前，所以
    # 后面几处用的都是干净值；而落进原始层的是**未清洗的原话**（上面那一步已经落完）。
    by_door: dict[int, JsonObject] = {}
    for raw_door in raw_doors:
        cleaned = _clean(raw_door)
        if cleaned is not None:
            by_door[as_int(cleaned["door_no"])] = cleaned

    processed = 0
    for door_no, door in by_door.items():
        state = derive_state(door)
        old = prev_live.get(door_no)
        old_state: str = str(old["state"]) if old is not None else ""
        card_no, cert_no = _occupant(door)

        if old is not None and old_state != state and not snapshot_only:
            repository.insert_event(
                occurred_at,
                device_id,
                door_no,
                old_state,
                state.value,
                card_no,
                cert_no,
                as_int(door.get("bath_time_s")),
            )

        # 先派生、再落现态：会话维护要读**上一帧**的段起点（`old` 里那份），而
        # `upsert_door_live` 会把它覆盖掉。全貌帧不派生，沿用旧值。
        segment = ""
        if not snapshot_only:
            segment = _maintain_session(device_id, door_no, state, door, occurred_at, old)
        elif old is not None:
            segment = str(old["segment_started_at"])
        repository.upsert_door_live(device_id, door_no, state.value, door, occurred_at, segment)
        processed += 1

    # 载荷是该设备的完整现态行，与 GET /doors 同一 service 路径产出（构造上同源）。
    # WS 单连接有序投递，数据承载推送是行业标准（Supabase/Firebase 同型）：客户端
    # 收到后直接应用，请求量与事件量解耦；无变化的上报也照发，充当状态再同步。
    events.publish(events.DOOR_UPDATE, {"device_id": device_id, "at": occurred_at, "doors": list_doors(device_id)})
    return {"status": "ok", "processed": processed}


def _gap(previous: Row | None, at: str) -> int:
    """上一帧到本帧的秒数。没有上一帧时给 -1——它与任何"不超过上限"的判断都不成立，
    调用方本来就该靠"段起点为空"另行判断首帧。"""
    if previous is None:
        return -1
    return seconds_between(str(previous["reported_at"]), at)


def _flow_after(
    door: JsonObject,
    previous: Row | None,
    prior_flow: int,
    at: str,
    occupied: bool,
    segment_start: str,
) -> tuple[int, str]:
    """推进一次出水时长，返回 `(新的 flow_s, 新的段起点)`。

    ## 为什么要分两种情形

    设备在**暂停**那一刻才把本段累加进 `bath_time_s`，而**释放喷头**时把它整体清零。
    所以"本帧在不在出水"决定了两件事：本段有没有被设备结算过、以及要不要由我们补上。

    - **本帧在出水**：设备还没结算这一段，`bath_time_s + 本段已观测` 正好；
    - **本帧不在出水**：这一段结束了。走的是暂停（仍占着）→ `bath_time_s` 已含本段，
      直接用；走的是释放（不再占着）→ `bath_time_s` 被清零、本段没结算，补上"上一帧
      到本帧"那一小段。

    两种情形**不能合并成一个公式**。早先写成 `max(bathTime, 上一帧 + 间隔)` 一把抓，
    结果暂停那一帧把间隔重复算了一遍——100 天里有 1,422 条会话因此偏大，合计 2,011 秒。

    ## 段起点

    这段连续出水的观测起点。它在两种情况下重置为"本帧"：之前不在出水，或者中间出现了
    观测盲区（间隔超过 [SESSION_MAX_S]）——一次占用最长就 1200 秒，"这段水从上一帧一直
    延续到本帧"只在间隔不超过它时才可能。放得更宽会把断开的水接上。

    `prior_flow` 与观测值取大：设备累计的秒数可能比我们看到的更多（漏帧时），那是它的
    权威值，只要跨度容得下就该采信。
    """
    accumulated = as_int(door.get("bath_time_s"))

    if door.get("in_time"):
        if not segment_start or _gap(previous, at) > SESSION_MAX_S:
            segment_start = at
        observed = seconds_between(segment_start, at)
        return max(prior_flow, accumulated + max(0, observed)), segment_start

    flow = max(prior_flow, accumulated)
    if not occupied and segment_start:
        gap = _gap(previous, at)
        if 0 < gap <= SESSION_MAX_S:
            flow = max(flow, prior_flow + gap)
    return flow, ""


def _opened_at(door: JsonObject, at: str, device_id: str, assigned_at: str, segment: str) -> str:
    """这一帧能给出的**开阀时刻**；没有证据时给空串。

    **正在出水**（`in_time` 非空）：取本段的观测起点 `segment`——它优先由设备报的
    `in_time` 平移而来，退回本帧时刻。与出水时长同源，两者不会分叉。

    **已经暂停**（设备把 `in_time` 清空了）：它只留下"这次关阀的时刻"与"累计出水秒数"。
    后者是**时长**，与钟的读数无关，所以"关阀时刻减累计秒数"就是本段起点。这条推导只在
    "这是全场第一次暂停"时成立——第二段之后 `bath_time_s` 是多段之和，算出来会比真实
    开阀更早。用 `[assigned_at, 本帧]` 区间筛掉那些：开阀不可能早于会话起点，也不可能
    晚于此刻。

    冷启动接手时（人已经洗到一半我们才上线），这是唯一还能拿回开阀时刻的途径。
    """
    if door.get("in_time"):
        return segment or at
    closed = device_clock.to_server(door.get("close_time"), device_id)
    bath_s = as_int(door.get("bath_time_s"))
    if closed is None or bath_s <= 0:
        return ""
    opened = shift_rfc3339(closed, -bath_s)
    if opened is None or not (assigned_at <= opened <= at):
        return ""
    return opened


def _maintain_session(
    device_id: str,
    door_no: int,
    state: DoorState,
    door: JsonObject,
    at: str,
    previous: Row | None,
) -> str:
    """维护洗浴会话，返回这个门**新的出水段起点**。

    四类情形：进入占用态开会话；占用态内部推进里程碑（`opened_at` 与 `flow_s` 只增
    不减）；离开占用态结束会话；与占用无关的迁移不涉及会话。

    `assigned_at` 与它的来源由 [_assigned_at] 一起给出：优先把设备报的 `openTime` 加上
    钟偏差平移回真实刷卡时刻，拿不到偏差时退回本帧时刻并如实标明那只是观测下界。

    返回值由调用方写回 `door_live`。段起点是**跨帧**的状态：下一帧要知道"这段水从哪
    开始的"，而进程重启后内存里什么都没有，所以它必须落库。
    """
    # 段起点：上一帧记下的那个；没有（接手时那边已经在出水）就从设备报的 `in_time` 平移
    # 过来——正在出水时它就是本段的起点，平移回来让出水时长自然算成
    # "bathTime + (本帧 − 本段起点)"，不必知道中间暂停过几次。
    segment = str(previous["segment_started_at"]) if previous is not None else ""
    if not segment:
        segment = device_clock.to_server(door.get("in_time"), device_id) or ""

    if state not in OCCUPIED_STATES:
        # 空闲态：只有上一帧还占着，才可能有会话要结。这一判据让绝大多数帧省掉一次查询
        # ——一帧里有几个门就要问几次，而空闲帧占大头。等价性是靠"每帧都处理"保证的：
        # 会话不可能在两次观测之间被开出来又没人结（开它的那一帧就会把会话写下）。
        #
        # 跨盲区也覆盖得到：那时上一帧仍是占用态，照样会问。
        if previous is not None and str(previous["state"]) in OCCUPIED_STATES:
            open_session = repository.get_open_session(device_id, door_no)
            if open_session is not None:
                _close(open_session, door, at, previous, segment)
        return ""

    open_session = repository.get_open_session(device_id, door_no)
    card_no, cert_no = _occupant(door)
    if open_session is None:
        assigned, source = _assigned_at(door, at, device_id)
        session_id = repository.open_session(device_id, door_no, card_no, cert_no, assigned, source)
        prior_flow = 0
    elif str(open_session["cert_no"]) != cert_no:
        # 换人：先结旧账再开新账。新账的起点同样能平移（新人刚刷卡，`open_time` 是他写
        # 下的），时长则从零起算——设备报的 `bath_time_s` 那一刻还是前一个人的累计
        # （实测 13 次换人里 10 次如此）。但**段起点要延续**：这一帧如果还在出水，那段水
        # 从上一帧就开始了，只是用的人换了。不延续的话新账会把它从头再算一遍。
        #
        # 换人判据用证件号而不是卡号——这台设备无卡，实测 235270 个门帧里 cardno 非 0
        # 的一个都没有，拿卡号比等于没比（那 13 次换人会全漏）。
        _close(open_session, door, at, previous, segment)
        assigned, source = _assigned_at(door, at, device_id)
        session_id = repository.open_session(device_id, door_no, card_no, cert_no, assigned, source)
        prior_flow = 0
        # 这一帧的开阀证据属于**前人**那一段（`segment` 是它的起点），不能记到新人名下：
        # 新人此刻才进门，他名下的开阀时刻最早只能是这一刻。段起点本身仍要延续给
        # `flow_s`（那段水从上一帧就开始了），两者在这一处必须分开。
        repository.touch_session(session_id, at if door.get("in_time") else "", True, at, 0)
        if not door.get("in_time"):
            return ""
        return segment if segment and _gap(previous, at) <= SESSION_MAX_S else at
    else:
        session_id = int(open_session["id"])
        prior_flow = int(open_session["flow_s"])
        assigned = str(open_session["assigned_at"])

    flow, segment = _flow_after(door, previous, prior_flow, at, True, segment)
    repository.touch_session(session_id, _opened_at(door, at, device_id, assigned, segment), True, at, flow)
    return segment


def _close(open_session: Row, door: JsonObject, at: str, previous: Row | None, segment_start: str) -> None:
    """结束一次会话：先按这一帧推进里程碑，再收尾。

    结束这一帧本身可能还带着出水的证据——"开着阀直接出门"时设备的 `inTime` 已经清零，
    释放又把 `bathTime` 一并归零，那一次出水留下的唯一痕迹是**上一帧仍在出水**，而那
    要靠 `segment_start` 非空才认得出来。不补这一下，那一段就永久丢了（见 [_flow_after]
    的第二种情形）。

    `last_seen_at` 在这里**不动**：它记的是最后一次确认还占着的时刻，而这一帧恰恰证明
    它已经不是占用了。两者的差值就是这次观测盲区的大小。

    不记结束原因：浴室 App 在主动出门与超时释放下写的是同一套清零语句，服务端没有依据
    区分，硬分就是编造。
    """
    session_id = int(open_session["id"])
    flow, _ = _flow_after(door, previous, int(open_session["flow_s"]), at, False, segment_start)
    repository.touch_session(
        session_id,
        _opened_at(door, at, str(open_session["device_id"]), str(open_session["assigned_at"]), segment_start),
        False,
        at,
        flow,
    )
    repository.close_session(session_id, at)
