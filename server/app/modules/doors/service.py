"""喷头状态派生与洗浴后台任务：`t_doorlist` 解析规则的单一来源。

## 时刻只有一个来源

落库的每一个时刻都表达在**服务端时间轴**上。取值按可信度分三种：

- **帧的采集时刻**（`captured_at`，设备锚在服务端时间上，见设备侧 core/clock/ServerClock）
  ——它在库变化那一刻就定了，不受积压与重发影响；
- **设备钟平移**：设备报的 `openTime`（整场会话不变，就是分配那一刻）与 `in_time`
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
1、2 两条排在占用之前，是因为 `ntu-bathapp-reverse/notes/2026-06-21-doorlist-schema.md`
写明报修会清空占用字段；两者本不该同时出现，排前面是为了在它们意外同时出现时站在安全的一侧。

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

实测 100 天发生 **13 次换人**，其中 10 次的形态是"前一个人在暂停态、后一个人直接分配
覆盖了他的占用字段"。**换人判据用证件号而不是卡号**：这台设备无卡，实测 235270 个门帧
里 `cardno` 非 0 的一个都没有，比它等于没比，那 13 次会全部漏检。

这不是"列举异常"，而是把合法空间穷举干净——异常不是空间外的东西，是空间里稀有的格子
（例如 `free→bathing`，跳过等待开阀直接淋浴，实测 3 次）。

## 首帧与两条帧结构规则

首次见到某个门时没有前态：占用态就开会话（`assigned_at` 由 [_assigned_at] 给出，
能平移就是真实分配时刻，否则是观测下界），空闲态不动。**首帧不记 `door_state_event`**
——没有"从哪来"，记一条来源不明的 `from_state` 会污染转换统计。语音的 `assigned`
是会话生命周期事实：首帧/全貌帧/盲区帧只要有可平移的 `open_time`，仍会补发。

- 同一帧内重复门号取**最后一条**（见 `_clean` 的调用点）；
- 门在后续帧里消失时**保留原状态**，不判成空闲——漏报一个门不等于那个门空了。

## 三条输入清洗（都在派生之前）

见 [_clean] 与 [_as_door_no]：门号必须严格整数且在 `1..DOOR_MAX`；`card_no` 取非负、
`bath_time_s` 夹进合法域；不可解析的时间字段置空。**收敛而不是丢整帧**——坏掉的通常
只是某几个字段，其余仍有意义。

## 设备字段的语义

`inTime` 是本次开阀时刻，暂停时清空、恢复时重新填入。它的**值**经偏差平移到服务端时间
轴后就是那一段的出阀起点（见 [_maintain_segment]），但**不能**当会话起点——跟着它走会把
起点拽到最后一次恢复那一刻。`bathTime` 只在暂停那一刻累加本次出水，出水期间不动，释放时
整体归零；它是**时长不是时刻**，与钟的读数无关，所以可靠。

一段出水算不算"一次使用"，看它有没有进 `usage_segment`——设备在离线期间完成的
"开阀→结算"我们两个帧都收不到，只见得到结算后的 free，那时 `in_time` 已清零；但那段水
仍会在 `bath_time_s` 里留下秒数，于是能从"关阀时刻减累计秒数"推回来（见
[_derive_segment]）。那是推导、不是观测，来源标 `derived`。

## 算不出来的三样

在注释里承认，不找办法硬凑：

- **会话因何结束**——主动出门与四种超时释放用的是同一套清零语句，数据库层面完全同形
  （见 [_close]）；
- **盲区内的出水秒数**——那段既没进 `bathTime`、也没被我们看见。它若在设备那边结算过，
  会体现在 `device_flow_s` 与段之和的差额上；而那个差额本身没有形状，还原不出它是几次、
  各多长；
- **第二次暂停之后的首次开阀时刻**——设备只在暂停那一刻留下"关阀时刻"与"累计出水秒数"，
  两者相减得到本段起点；多段之后那个和已经混了，无从分辨（见 [_derive_segment]）。

另外两件不归本模块管：**设备什么时候在线**由 `device_link_event` 回答（帧是事件驱动
的，空闲时不上报，"没消息"无法区分"没人用"与"离线"）；**用了多少水、花了多少钱**只有
设备端才有。
"""

import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NoReturn

from sqlalchemy.orm import Session

from app.contracts import ASSIGNABLE_STATES, OCCUPIED_STATES, DoorState, SegmentSource
from app.core import device_clock, events
from app.core.clock import now_rfc3339, parse_rfc3339, seconds_between, shift_rfc3339
from app.core.logging import exc_info_text, log_event
from app.core.protocol import JsonObject, as_int, text_of
from app.modules.doors import crud as repository
from app.modules.doors.events import DoorEvent, DoorEventKind, SnapshotResult
from app.modules.doors.schemas import DoorView

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

#: 一段**连续出水**的物理上限，秒。与一次占用同源——设备满 `maxbatchtime` 就强制释放，
#: 那同时也结束了正在出水的那一段。所以直接引用 [SESSION_MAX_S]：两者不是同一个判断，
#: 却由设备同一个参数决定，分开写两份数字迟早漂移。
MAX_SEGMENT_S = SESSION_MAX_S

#: 浴室 App"连续无水就释放"的门槛，秒：未开阀是 `maxinspacetime`、关阀后是
#: `maxbathspacetime`，两者都是 300。它与 [SESSION_MAX_S] 同源（浴室 App 同一个参数家族），
#: 所以住在一起而不各写一份。后台任务用它划断"同一串会话"：喷头空着超过这么久，说明那个
#: 人的占用真的结束了（浴室端早释放了他、之后没人接手），预约续期的次数从头数。
NO_WATER_RELEASE_S = 300

#: 交叉校验的容差，秒。段长的两种来源相差不超过它时采信设备轴差。
#:
#: 它换的是"钟漂不被误判成拨钟"：两个来源天然差着一个"我们看见关阀"的延迟（帧间隔
#: 量级，1 秒上下），而 20 分钟窗口里的自然漂移只有 0.03 秒。代价是**拨钟量小于这个
#: 容差时系统看不出来**——那几秒会被当成真实时长。这是容差的固有边界，不是能调掉的
#: 东西：调小会把正常段的延迟误判成拨钟，调大则放过更多。
AB_TOLERANCE_S = 3

#: 喷头数上限 = 设备的 `DoorCount`（共享配置实测 48；本机 waterList 只装了 7 个，
#: 从 1 开始编号）。
DOOR_MAX = 48

#: `bath_time_s` 的上界。取一天而不是设备上限：那个上限由设备侧服务器下发、随时可能
#: 变大，拿它当闸门会误伤真实数据；而一天以上的单次出水不存在。
MAX_BATH_S = 86400

#: 帧来源：浴室 App 写库触发，状态真的变了。
SOURCE_DB_WATCH = "db_watch"

#: 帧来源：重连或开机后推的一遍全貌，与状态是否变化无关。
#:
#: 它与另外一种的区别只在**能不能用来记事件**：它回答的是"当前是什么样"，不是"刚刚
#: 发生了什么"，拿它写 `door_state_event` 会记出一串从未在那一刻发生的迁移——离线期间
#: 的变化会被记到重连这一刻。**会话照常派生**：那一帧带着设备当时的完整读数，占用中的
#: 门能从 `open_time` 平移出真实起点（见 [_assigned_at]），空闲的门能把开着的那场结掉。
#: 曾经连会话一起跳过，代价是"重连时正有人洗着"的那一场整场消失——2026-10-02 门 4
#: 就是这么丢的。
SOURCE_STARTUP = "startup"

#: 会话起点的来源：由设备报的 `open_time` 加上钟偏差平移而来——那是真实的分配时刻。
SESSION_ASSIGNED_INFERRED = "inferred"

#: 会话起点的来源：我们首次观测到它占着的时刻。**真实起点更早**，这只是下界。
#: 设备还没回过 pong（偏差无从谈起）、或 `open_time` 缺失时落到这里。
SESSION_ASSIGNED_OBSERVED = "observed"


def list_doors(session: Session, device_id: str | None = None) -> list[DoorView]:
    """喷头现态列表。两个列表端点与 `door.update` 推送都走这里。

    行只含门域事实；设备域的事实（在线、名称）由设备资源回答（GET /devices 与
    device.update 推送），客户端不在门数据里找它们——同一事实只出现一处，两份
    数据源打架的问题在契约层就不成立。
    """
    return repository.list_doors(session, device_id)


# ── 供会话域取数 ──────────────────────────────────────────────────────────
# 别的领域不读本域的表：门与喷头的形状、"哪些态算占用"、"哪些态可分配"、串的划断
# 规则都在本文件里，出去的是答案而不是行对象——持久化层的行形状是存储细节。


@dataclass(frozen=True)
class OpenSession:
    """这台设备上一个未结束的会话，判据要的那几个字段。"""

    session_id: int
    door_no: int
    cert_no: str
    assigned_at: str


@dataclass(frozen=True)
class LiveDoor:
    """一个喷头此刻的现态：状态取值与本次关阀时刻。"""

    state: str
    close_time: str


@dataclass(frozen=True)
class SessionDurations:
    """一场会话的两个时长来源：分配起点/结束与已累计的淋浴秒数。

    `ended_at` 为空表示会话仍在进行；`flow_s` 是段之和，已包含在途那一段。
    """

    assigned_at: str
    ended_at: str
    flow_s: int


def live_doors(session: Session, device_id: str) -> dict[int, LiveDoor]:
    """这台设备每个喷头此刻的现态（门号 → 现态）。

    `state` 是 [derive_state] 落在 `door_live` 上的结果。**调用方读它，不要自己再推
    一遍**——"这个喷头现在什么状态"只有一处判据。
    """
    return {
        door_no: LiveDoor(state=str(row["state"]), close_time=str(row["close_time"] or ""))
        for door_no, row in repository.get_all_live(session, device_id).items()
    }


def open_sessions(session: Session, device_id: str) -> list[OpenSession]:
    """这台设备上全部未结束的会话。"""
    return [
        OpenSession(
            session_id=as_int(row["id"]),
            door_no=as_int(row["door_no"]),
            cert_no=str(row["cert_no"] or ""),
            assigned_at=str(row["assigned_at"] or ""),
        )
        for row in repository.list_open_sessions_of(session, device_id)
    ]


def session_durations(session: Session, session_id: int) -> SessionDurations | None:
    """按会话 id 取播报需要的两个时长来源；会话不存在时给 None。

    别的领域不直接读本域的表：这里只交出答案，不把 `sqlite3.Row` 漏出去。
    """
    row = repository.get_session(session, session_id)
    if row is None:
        return None
    return SessionDurations(
        assigned_at=str(row["assigned_at"] or ""),
        ended_at=str(row["ended_at"] or ""),
        flow_s=as_int(row["flow_s"]),
    )


def run_started_at(session: Session, device_id: str, door_no: int, cert_no: str) -> str:
    """这一串的第一场是什么时候分配的；一场都没有时给空串。

    串由浴室 App 那条无水释放门槛划断（[NO_WATER_RELEASE_S]）——那是本域的规则，
    所以阈值不由调用方给。
    """
    return repository.run_started_at(session, device_id, door_no, cert_no, NO_WATER_RELEASE_S)


def can_assign(session: Session, device_id: str, door_no: int) -> tuple[bool, str] | None:
    """这个喷头此刻能不能被分配；从未上报过该喷头时给 None。

    返回（可以与否，它此刻的状态取值）——后者用于把拒绝的原因如实说出去。可分配的
    判据只有 [ASSIGNABLE_STATES] 一处。
    """
    row = repository.get_door_live(session, device_id, door_no)
    if row is None:
        return None
    raw = str(row["state"])
    try:
        state: DoorState | None = DoorState(raw)
    except ValueError:
        # 设备报了本端不认识的状态：不分配，也不去猜它是什么意思
        state = None
    return state in ASSIGNABLE_STATES, raw


def open_session_door(session: Session, device_id: str, cert_no: str) -> int | None:
    """这个学号在这台设备上未结束的会话占着几号喷头；没有则 None。"""
    row = repository.get_active_by_cert(session, device_id, cert_no)
    return None if row is None else as_int(row["door_no"])


def is_occupied(session: Session, device_id: str, cert_no: str) -> bool:
    """这个学号在这台设备上有没有占用中的喷头（[OCCUPIED_STATES]）。"""
    return repository.get_occupied_by_cert(session, device_id, cert_no) is not None


def derive_state(door: JsonObject) -> DoorState:
    """由一帧派生喷头状态（判据见模块注释）。"""
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


def _assigned_at(door: JsonObject, at: str, device_id: str, previous: dict[str, Any] | None) -> tuple[str, str]:
    """会话起点与它的来源，一起返回。

    **优先**：把设备报的 `open_time` 加上钟偏差平移过来。那是**分配那一刻**设备写下的，
    而它在整场会话里不变——暂停与恢复都不改它，只有释放才整体清零。所以无论中间暂停过
    几次，它都还是同一个值，平移回来就是真实的会话起点。

    平移所需的钟偏差来自设备回的 pong（见 core/device_clock）。它之所以成立，是因为
    一次会话压在 20 分钟以内、而 20 分钟里的钟漂移只有 0.03 秒；跳变只有 23:5x 那一处，
    而设备 23:53 自动释放所有喷头，任何会话都不会跨过它。

    **退路**：设备还没回过 pong、或这一帧没带 `open_time`、或格式解析不了——退回本帧
    时刻，并如实标成观测下界（真实起点更早）。两种来源的强度不同，统计时必须分得清：
    `inferred` 的跨度可以用，`observed` 的天然偏小。

    **不要改退到同帧的 `in_time`**：两个读数出自同一口钟，钟被改过时它们一起偏，用哪个
    都一样偏；而"本帧时刻"来自服务端钟，是这里唯一不依赖设备钟的锚。退回它是**有意的**，
    不是将就——`tests/test_derivation_edges.py` 的 `test_start_far_from_in_time_is_not_adopted`
    锁着这条。代价是"看见"那一场的时刻可能远晚于真实开阀（2026-07-02 实测晚一小时零五秒，
    跨过午夜落进"凌晨"），而 `observed` 这个标记就是在说这件事。

    ## 平移值要先过自洽检验

    偏差是**一个数**，而 `open_time` 是设备在**写下它的那一刻**用**当时那口钟**写的——
    两者分属不同时刻。只要中间设备钟被改过而我们没跟上，算出来的起点就会偏掉整个偏差。
    2026-10-02 实测：断连期间有人分配，重连前设备钟被拨快一小时，算出来的起点 `11:01`
    比开阀还早一小时。

    三条判据，任一不成立就退回观测下界（真实起点只会更晚，标成下界是诚实的）：

    1. 起点不晚于本帧——分配不可能发生在我们看见它之后；
    2. 起点不早于**上次看见这个门**的时刻——更早的会话我们已经见过了；
    3. 与同帧 `in_time` 的平移值相差不超过 [SESSION_MAX_S]——两个读数出自同一口钟，而
       一次占用压在 20 分钟内。

    第 3 条是**唯一能识破"拨钟时我们离线"**的：那时第 2 条没有参照（前一帧可能远在
    几小时前、或根本不存在），只剩它。
    """
    shifted = device_clock.to_server(door.get("open_time"), device_id)
    if shifted is None or not _start_readings_agree(shifted, door, at, device_id, previous):
        return at, SESSION_ASSIGNED_OBSERVED
    return shifted, SESSION_ASSIGNED_INFERRED


def _start_readings_agree(
    start: str, door: JsonObject, at: str, device_id: str, previous: dict[str, Any] | None
) -> bool:
    """`open_time` 平移出的起点，与同一帧里其他读数是否自洽（判据见 [_assigned_at]）。

    与 `in_time` 那一条用 [seconds_between]，它把"结束早于开始"归零——换人时新人的
    `open_time` 晚于仍在延续的前人那一段，那是对的，不该被这条判成坏值。
    """
    if start > at:
        return False
    if previous is not None and start < str(previous["reported_at"]):
        return False
    opened = device_clock.to_server(door.get("in_time"), device_id)
    return opened is None or seconds_between(start, opened) <= SESSION_MAX_S


def _no_persist(device_id: str) -> NoReturn:
    """原始帧没有落下去（设备已不在册）：抛错让网关扣下回执，设备保留那一行重发。

    ack 与「原始帧已落库」严格等价——库满、库锁超时经 SQLAlchemy 自然抛错，这里补上
    外键设计下唯一剩下的「落不了行」路径。
    """
    message = f"设备 {device_id} 不在册，原始帧未落库"
    raise RuntimeError(message)


def _dt(value: object) -> datetime | None:
    """RFC 3339 文本 → UTC datetime；空串或解析不了给 None（可空列的空值语义）。

    服务层全程持有 RFC 3339 文本（判据、比对、展示都吃文本），datetime 只在
    持久化边界出现——转换只在这一处，不散在调用点。
    """
    if not isinstance(value, str) or not value:
        return None
    return parse_rfc3339(value)


def _moment(value: object) -> datetime:
    """必填时刻列的转换：空缺是调用方的 bug，宁可炸在边界也不写半个空值进库。"""
    moment = _dt(value)
    assert moment is not None, f"必填时刻缺失：{value!r}"
    return moment


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


def process_snapshot(session: Session, device_id: str, payload: JsonObject) -> SnapshotResult:
    """处理一帧；同一设备的帧串行（理由见 [_device_lock]）。

    锁覆盖整段：派生读的是刚写下的现态，把读放到锁外会读到别的帧的写入。
    """
    with _device_lock(device_id):
        return _apply_snapshot(session, device_id, payload)


def _apply_snapshot(session: Session, device_id: str, payload: JsonObject) -> SnapshotResult:
    """一帧的完整处理：先落原始帧，再派生状态、diff 记事件、更新现态、维护会话。

    帧时刻取设备锚定的采集时刻（`captured_at`）——它在库变化那一刻就定了，不受积压
    与重发影响；设备尚未建立锚点时退回本机接收时刻。**这是本系统唯一的时间来源**：
    设备的三个原始时间字段逐字保留供溯源；它们的**值**只在推导时刻时经偏差平移使用。

    ## 回执语义：收到 ack ⇔ 原始帧已落库

    本函数只有「原始帧没有落下去」（库满、库锁超时这一类）才会抛错——网关据此**扣下
    回执**，设备保留发件箱那一行稍后重发，原始数据不丢。派生的任何失败在此止住并照常
    返回：帧已经收下，原始帧留底可查；回执让设备删掉发件箱那一行才是对的。

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
        session,
        device_id,
        text_of(payload, "snapshot_id"),
        as_int(payload.get("seq")) or None,
        text_of(payload, "source"),
        _moment(occurred_at),
        text_of(payload, "device_clock"),
        _moment(received_at),
        raw_doors,
    ) or _no_persist(device_id)
    try:
        return _derive_snapshot(session, device_id, text_of(payload, "source"), raw_doors, occurred_at)
    except Exception:  # noqa: BLE001 —— 派生可以以任何方式失败，而这一帧已经收下了
        log_event(
            "ERROR",
            "doors.snapshot-derive-failed",
            "派生失败，原始帧已落库",
            device_id=device_id,
            error=exc_info_text(),
        )
        return SnapshotResult(0, ())


def _derive_snapshot(
    session: Session, device_id: str, source: str, raw_doors: list[JsonObject], occurred_at: str
) -> SnapshotResult:
    """派生半场：diff 记事件、维护会话、刷新现态、发布推送。原始帧已在此前落库。"""
    prev_live = repository.get_all_live(session, device_id)
    # 全貌帧只刷新现态，不派生（判据与理由见 SOURCE_STARTUP）。
    snapshot_only = source == SOURCE_STARTUP
    # 只有实时 db_watch 帧才产出播报事件；全貌帧与历史重放不补播。
    voice_eligible = source == SOURCE_DB_WATCH
    collected: list[DoorEvent] = []

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

        event_id: int | None = None
        if old is not None and old_state != state and not snapshot_only:
            event_id = repository.insert_event(
                session,
                _moment(occurred_at),
                device_id,
                door_no,
                DoorState(old_state),
                state,
                card_no,
                cert_no,
                as_int(door.get("bath_time_s")),
            )

        # 先派生、再落现态：后台任务要读**上一帧**的状态与时刻，而 `upsert_door_live`
        # 会把它们覆盖掉。全貌帧同样要派生——它挡住的只是事件流水（见 SOURCE_STARTUP），
        # 会话跟着一起跳过就会丢掉重连时正在进行的那一场。
        door_events = _maintain_session(
            session,
            device_id,
            door_no,
            state,
            door,
            occurred_at,
            old,
            event_id,
            startup=source == SOURCE_STARTUP,
        )
        if voice_eligible:
            collected.extend(door_events)
        elif source == SOURCE_STARTUP:
            # 全貌帧不记状态迁移，但会话生命周期（分配/释放）不依赖某一帧的迁移：
            # open_time 可平移的 assigned、以及上一帧仍占用而本帧已空的释放，都补发。
            # 盲区里的收尾已在 _maintain_session 里标 gap 并跳过，不会在这里漏成假事件。
            lifecycle = {
                DoorEventKind.ASSIGNED,
                DoorEventKind.RELEASED_UNUSED,
                DoorEventKind.RELEASED_AFTER_SHOWER,
                DoorEventKind.RELEASED_WHILE_PAUSED,
            }
            collected.extend(event for event in door_events if event.kind in lifecycle)

        # 现态对外的三个时间字段是**服务端轴**的：设备那口钟的读数只保留在原始帧里
        # （为什么对外不能出现设备轴，见 docs/architecture.md 的「两条时间轴」）。
        view = dict(door)
        for key in ("open_time", "in_time", "close_time"):
            view[key] = _to_server_or_empty(door.get(key), device_id)
        # 已暂停：设备在关阀那一刻把 `inTime` 清空了（`bathTime` 的累加与清空 `inTime`
        # 是同一条 SQL），帧里于是没有开阀时刻，上面那一轮平移只能得到空串。但暂停会一直
        # 持续到结算，关阀之后每一帧都还要答得出这一段是什么时候开的——`in_time` 的含义
        # 就是"当前这一段的开阀时刻"，与阀此刻开着还是关着无关（"此刻在不在出水"由
        # `state` 回答）。那一段我们记过，段表里就有它：上面那几行先跑的就是
        # `_maintain_session`，本帧的段到这会儿已经维护好了。
        if state == DoorState.PAUSED:
            view["in_time"] = repository.latest_segment_opened_at(session, device_id, door_no)
        repository.upsert_door_live(
            session,
            device_id,
            door_no,
            state,
            card_no=as_int(view.get("card_no")),
            cert_no=str(view.get("cert_no") or ""),
            acc_no=str(view.get("acc_no") or ""),
            open_time=_dt(view.get("open_time")),
            in_time=_dt(view.get("in_time")),
            close_time=_dt(view.get("close_time")),
            bath_time_s=as_int(view.get("bath_time_s")),
            status_raw=as_int(view.get("status_raw")),
            reported_at=_moment(occurred_at),
        )
        processed += 1

    # 载荷是该设备的完整现态行，与 GET /doors 同一 service 路径产出（构造上同源）。
    # WS 单连接有序投递，数据承载推送是行业标准（Supabase/Firebase 同型）：客户端
    # 收到后直接应用，请求量与事件量解耦；无变化的上报也照发，充当状态再同步。
    events.publish(
        events.DOOR_UPDATE, {"device_id": device_id, "at": occurred_at, "doors": list_doors(session, device_id)}
    )
    return SnapshotResult(processed, tuple(collected))


def _to_server_or_empty(value: object, device_id: str) -> str:
    """设备轴时刻 → 服务端轴；没有值或换算不出来时给空串。

    换算不出来的情形只有一种：这台设备还没有任何偏差记录（刚上线，帧比首次 pong 先到）。
    那时给空串而**不是原值**——原值在设备轴上，对外不能出现设备轴。
    """
    return device_clock.to_server(value, device_id) or ""


def _gap(previous: dict[str, Any] | None, at: str) -> int:
    """上一帧到本帧的秒数。没有上一帧时给 -1——它与任何"不超过上限"的判断都不成立，
    调用方本来就该靠"有没有上一帧"另行判断首帧。"""
    if previous is None:
        return -1
    return seconds_between(str(previous["reported_at"]), at)


def _close_segment(session: Session, segment: dict[str, Any], door: JsonObject, device_id: str, at: str) -> None:
    """关掉一段出水，定下它的长度与来源。

    **A**（设备轴）：`close_time − in_time`。两个读数取自设备的同一口钟，偏差在里面
    相减抵消——这是唯一**不经过换算**的段长来源，因此比观测差更可信。

    **B**（服务端轴）：本帧到段起点，纯观测，含"我们看见关阀"的那点延迟。

    两者相差不超过 [AB_TOLERANCE_S] 就采信 A：说明这一段从头到尾都在同一段偏差里，
    设备自己结算的那个值就是真相。**不得超过它**——段内被拨钟时 A 会带上整个拨钟量，
    而 B 不受影响，交叉校验正是为此。

    **但两者必须同起点才可比**：B 的起点是段的服务端轴起点，只有当那个起点**就是设备
    报的 `in_time` 平移来的**时，两个差才在同一条起跑线上。平移失败（这台设备还没有
    偏差记录）时段起点退回了帧时刻，B 会偏小；那时拿 B 去否决 A 就错了——A 是同一口钟
    上两个读数之差，它根本不依赖平移。

    B 越过一次占用的物理上限时截断并标 `capped`：截断过的段与"它真的洗了那么久"
    必须分得开，否则统计会把"我们不知道有多长"当成"就那么长"。

    `close_time` 缺失（释放帧把整行清零）时 A 无从谈起，只能走 B。
    """
    in_raw = str(segment["opened_raw"])
    close_value = door.get("close_time")
    # 只在它**真的是字符串**时才当读数：载荷里"键不存在"是常态，而 `str(None)` 得到的是
    # 字符串 "None"——非空，于是"没有关阀时刻"被当成有值，两个读数之差算出来是 0，
    # 还标成 measured。这是本文件里最贵的一类错误：不报错，只是数字变成 0。
    close_raw = close_value if isinstance(close_value, str) else ""
    a = seconds_between(in_raw, close_raw) if in_raw and close_raw else None
    # `seconds_between` 把负值归零，所以这里的 0 有两种来历：两个读数真的是同一秒，或者
    # 关阀**早于**开阀（段内设备钟被往回拨过）。后者正是上面注释警告的那类错误——不报错，
    # 数字变成 0，而它会被标成最强的一档 `measured`，统计随后把它当真相。两个**不同**的
    # 读数不可能差 0 秒，据此把归零值当"没有 A"退回 B。
    if a == 0 and in_raw != close_raw:
        a = None
    shifted_in = device_clock.to_server(in_raw, device_id) if in_raw else None
    if a is not None and (shifted_in is None or shifted_in != str(segment["opened_at"])):
        repository.close_segment(session, int(segment["id"]), _moment(at), a, SegmentSource.MEASURED, close_raw)
        return
    b = seconds_between(str(segment["opened_at"]), at)
    if a is not None and abs(a - b) <= AB_TOLERANCE_S:
        flow, source = a, "measured"
    elif b > MAX_SEGMENT_S:
        flow, source = MAX_SEGMENT_S, "capped"
    else:
        flow, source = b, "observed"
    repository.close_segment(session, int(segment["id"]), _moment(at), flow, SegmentSource(source), close_raw)


def _derive_segment(
    session: Session, device_id: str, door_no: int, session_id: int, assigned_at: str, door: JsonObject, at: str
) -> None:
    """从设备留下的痕迹推回一段已经结束的出水（冷启动接手用）。

    我们上线时门已经在暂停态：`in_time` 被清空，但设备留下了"关阀时刻"与"累计出水
    秒数"——从前者往前数那么多秒，就是那一段的开阀时刻。

    **减法在服务端轴上做**：`bath_time_s` 是秒数、与钟的读数无关，所以"时刻 − 秒数"
    换算到哪条轴上都成立；但若在设备轴上减完、把结果直接当服务端轴用，推出来的时刻会
    落到未来、被下面的区间检查丢掉，结果是这一段整个消失**且不报错**。

    **只在这是全场第一次暂停时才成立**：第二段之后 `bath_time_s` 是多段之和，退出来
    会落在会话起点之前或本帧之后。那个区间检查同时也挡住了"`bath_time_s` 被拨钟放大"
    ——拨大之后退回来的时刻会跑到未来。
    """
    if repository.segments_of_session(session, session_id):
        return
    bath_s = as_int(door.get("bath_time_s"))
    close_value = door.get("close_time")
    # 只在它**真的是非空字符串**时才当读数：载荷里"键缺失"是常态，而 `str(None)` 会得到
    # 字符串 "None"，非空，于是"没有关阀时刻"被当成有值，算出来是个 0。
    if not isinstance(close_value, str) or not close_value or bath_s <= 0:
        return
    closed = device_clock.to_server(close_value, device_id)
    if closed is None:
        return
    opened = shift_rfc3339(closed, -bath_s)
    if opened is None or not (assigned_at <= opened <= at):
        return
    repository.add_derived_segment(
        session, session_id, device_id, door_no, _moment(opened), _moment(closed), bath_s, close_value
    )


def _segments_complete(segments: list[dict[str, Any]], device_flow_s: int) -> bool:
    """我们的段之和与设备累计值是否一致。

    **有 derived 段时恒为假**：那一段的长度就是从设备那个值推出来的，等式必然成立，
    拿它当"完整"的证据是循环论证。而设备那个值本身可能被拨钟放大——100 天里那 8 次
    冷启动接手，设备的说法与真实可能差出十倍。
    """
    if any(str(s["source"]) == "derived" for s in segments):
        return False
    return sum(int(s["flow_s"]) for s in segments) == device_flow_s


def _door_event(
    kind: DoorEventKind,
    device_id: str,
    door_no: int,
    cert_no: str,
    session_id: int,
    at: str,
    event_key: str,
) -> DoorEvent:
    return DoorEvent(
        kind=kind,
        device_id=device_id,
        door_no=door_no,
        cert_no=cert_no,
        session_id=session_id,
        occurred_at=at,
        event_key=event_key,
    )


def _transition_kind(old_state: str, state: DoorState) -> DoorEventKind | None:
    """同一会话内的状态迁移 → 播报事件；三对之外一律不播。"""
    if old_state == DoorState.WAITING.value and state is DoorState.BATHING:
        return DoorEventKind.SHOWER_STARTED
    if old_state == DoorState.PAUSED.value and state is DoorState.BATHING:
        return DoorEventKind.RESUMED
    if old_state == DoorState.BATHING.value and state is DoorState.PAUSED:
        return DoorEventKind.PAUSED
    return None


def _release_kind(last_occupied_state: str) -> DoorEventKind:
    """释放前最后一帧的占用状态 → 释放事件；不认识的按未开阀处理（观测下界）。"""
    if last_occupied_state == DoorState.BATHING.value:
        return DoorEventKind.RELEASED_AFTER_SHOWER
    if last_occupied_state == DoorState.PAUSED.value:
        return DoorEventKind.RELEASED_WHILE_PAUSED
    return DoorEventKind.RELEASED_UNUSED


def _maintain_session(
    session: Session,
    device_id: str,
    door_no: int,
    state: DoorState,
    door: JsonObject,
    at: str,
    previous: dict[str, Any] | None,
    event_id: int | None,
    startup: bool,
) -> list[DoorEvent]:
    """维护会话与段：开、推进、收尾，并产出本帧的可播报事件。

    `assigned_at` 与它的来源由 [_assigned_at] 一起给出：优先把设备报的 `openTime` 加上
    钟偏差平移回真实分配时刻，拿不到偏差时退回本帧时刻并如实标明那只是观测下界。

    **先看盲区**：相邻两帧隔得超过一次占用的物理上限（[SESSION_MAX_S]）时，盲区前后
    的状态不能当成连续。前一场在我们**最后看见它的时刻**收尾，本帧若仍占着就另起一场
    ——否则库里的陈旧占用（浴室 App 停了、人早走了）会被当成"它还被占着"，那场会话永不
    结束、起点还落在几小时前。盲区里的状态迁移不产出播报事件：中间发生过什么没有证据；
    但新会话的 `assigned` 是生命周期事实，`open_time` 可平移时仍补发。

    事件的产出边界（与 voice 的事件目录一一对应）：
    - 新会话 / 换人 → `assigned`；换人不为旧学号产出 `released`；
    - 同一会话的 `waiting→bathing` / `paused→bathing` / `bathing→paused`
      → `shower_started` / `resumed` / `paused`；
    - 本帧离开占用态且上一帧仍占着 → 按**上一帧的占用状态**产出
      `released_unused` / `released_after_shower` / `released_while_paused`，
      学号取**被结束的会话行**。
    """
    out: list[DoorEvent] = []
    gap = _gap(previous, at)
    blind = previous is None or gap > SESSION_MAX_S

    if gap > SESSION_MAX_S and previous is not None:
        stale = repository.get_open_session(session, device_id, door_no)
        if stale is not None:
            _close(session, stale, door, str(previous["reported_at"]), True)

    if state not in OCCUPIED_STATES:
        # 空闲态：只有上一帧还占着，才可能有会话要结。这一判据让绝大多数帧省掉一次查询
        # ——一帧里有几个门就要问几次，而空闲帧占大头。等价性是靠"每帧都处理"保证的：
        # 会话不可能在两次观测之间被开出来又没人结（开它的那一帧就会把会话写下）。
        #
        # 跨盲区也覆盖得到：那时上一帧仍是占用态，照样会问。
        if previous is not None and str(previous["state"]) in OCCUPIED_STATES:
            open_session = repository.get_open_session(session, device_id, door_no)
            if open_session is not None:
                session_id = int(open_session["id"])
                session_cert = str(open_session["cert_no"])
                _close(session, open_session, door, at, False)
                if not blind and session_cert:
                    out.append(
                        _door_event(
                            _release_kind(str(previous["state"])),
                            device_id,
                            door_no,
                            session_cert,
                            session_id,
                            at,
                            f"session:{session_id}:{_release_kind(str(previous['state'])).value}",
                        )
                    )
        return out

    open_session = repository.get_open_session(session, device_id, door_no)
    card_no, cert_no = _occupant(door)
    continued = open_session is not None and str(open_session["cert_no"]) == cert_no
    assigned: str
    if open_session is None:
        assigned, source = _assigned_at(door, at, device_id, previous)
        session_id = repository.open_session(session, device_id, door_no, card_no, cert_no, _moment(assigned), source)
        # assigned 是会话生命周期事实，不是一次观测到的状态迁移：新会话一建立就产出。
        # 盲区/全貌帧里如果 open_time 能平移（source=inferred），用真实分配时刻补发；
        # 没有 open_time 的观测下界不补——不知道是什么时候分配的，补了就是编时刻。
        if cert_no and (source == SESSION_ASSIGNED_INFERRED or (not blind and not startup)):
            out.append(
                _door_event(
                    DoorEventKind.ASSIGNED,
                    device_id,
                    door_no,
                    cert_no,
                    session_id,
                    assigned,
                    f"session:{session_id}:assigned",
                )
            )
    elif not continued:
        # 换人：先结旧账再开新账。新账的起点同样能平移（新人刚分配，`open_time` 是他
        # 写下的）。换人判据用证件号而不是卡号——这台设备无卡，实测 235270 个门帧里
        # cardno 非 0 的一个都没有，拿卡号比等于没比（那 13 次换人会全漏）。
        #
        # 换人不为旧学号产出 `released`：门仍被占着，播「会话结束」与屏幕事实相反。
        _close(session, open_session, door, at, False)
        assigned, source = _assigned_at(door, at, device_id, previous)
        session_id = repository.open_session(session, device_id, door_no, card_no, cert_no, _moment(assigned), source)
        if cert_no and (source == SESSION_ASSIGNED_INFERRED or (not blind and not startup)):
            out.append(
                _door_event(
                    DoorEventKind.ASSIGNED,
                    device_id,
                    door_no,
                    cert_no,
                    session_id,
                    assigned,
                    f"session:{session_id}:assigned",
                )
            )
    else:
        session_id = int(open_session["id"])
        assigned = str(open_session["assigned_at"])

    if continued and not blind and event_id and cert_no:
        old_state = str(previous["state"]) if previous is not None else ""
        kind = _transition_kind(old_state, state)
        if kind is not None:
            out.append(
                _door_event(
                    kind,
                    device_id,
                    door_no,
                    cert_no,
                    session_id,
                    at,
                    f"door_event:{event_id}:{kind.value}",
                )
            )

    _maintain_segment(session, device_id, door_no, session_id, assigned, door, at)
    _advance_session(session, device_id, door_no, session_id, door, at, gap)
    return out


def _maintain_segment(
    session: Session,
    device_id: str,
    door_no: int,
    session_id: int,
    assigned_at: str,
    door: JsonObject,
    at: str,
) -> None:
    """维护当前出水段：开一段、关一段、或从设备痕迹推回一条。

    - **在出水**（`in_time` 非空）且没有在途段：开一段。起点优先由设备报的 `in_time`
      平移而来——正在出水时它就是本段起点，平移回来让时长自然算成"两个读数之差"，
      不必知道中间暂停过几次。它越界就退回本帧：开阀不可能早于分配，也不可能晚于
      我们看见它的这一刻。
    - **不在出水**（`in_time` 被清空）而还有在途段：设备把这一段结算完了，可以定长
      （见 [_close_segment]）。
    - **不在出水**且本会话还没有任何段：从设备的痕迹推回（见 [_derive_segment]）。
    """
    segment = repository.get_open_segment(session, device_id, door_no)
    if door.get("in_time"):
        if segment is None:
            opened = device_clock.to_server(door.get("in_time"), device_id) or at
            if not (assigned_at <= opened <= at):
                opened = at
            repository.open_segment(session, session_id, device_id, door_no, _moment(opened), str(door.get("in_time")))
        return
    if segment is not None:
        _close_segment(session, segment, door, device_id, at)
        return
    _derive_segment(session, device_id, door_no, session_id, assigned_at, door, at)


def _advance_session(
    session: Session, device_id: str, door_no: int, session_id: int, door: JsonObject, at: str, gap: int
) -> None:
    """把会话推进到本帧。

    `flow_s` 是**已结束段之和，加上在途的那一截**：会话总时长由段累加而来，不再另造一条
    合成通路——段是唯一记过出水的地方。在途的那一段还没结束、没有最终长度，但"从开阀到
    现在"这段是**观测到的事实**；不计的话正在淋浴的人时长为 0。上界取一次出水的物理上限，
    越过的部分没有证据。

    `device_flow_s` 取设备累计值（它在暂停那一刻累加、释放时归零，所以由 MAX 收敛）。

    `observed_span` 单向：`gap` 超过一次占用的上限就永久标上——断开一次之后，后面接上
    的连续观测补不回失去的那一段。
    """
    segments = repository.segments_of_session(session, session_id)
    open_segment = repository.get_open_segment(session, device_id, door_no)
    in_flight = (
        min(seconds_between(str(open_segment["opened_at"]), at), MAX_SEGMENT_S) if open_segment is not None else 0
    )
    repository.touch_session(
        session,
        session_id,
        seen=True,
        at=_moment(at),
        flow_s=sum(int(s["flow_s"]) for s in segments) + in_flight,
        device_flow_s=as_int(door.get("bath_time_s")),
        gap=gap > SESSION_MAX_S,
    )


def _close(session: Session, open_session: dict[str, Any], door: JsonObject, at: str, gap: bool) -> None:
    """结束一次会话：先把这一帧的段收干净，再收尾。

    `gap` 表示这次收尾是因为发现了一个盲区（而不是看见喷头空了）。它要落进
    `observed_span`：那颗标记只增不减，事后谁看这条记录都该知道中间有一段是空的。

    结束这一帧本身可能还带着出水证据——"开着阀直接出门"时设备的 `inTime` 已经清零、
    释放又把 `bathTime` 一并归零，那一次出水留下的痕迹是**我们上一帧还看见它在出水**，
    而那要靠"有没有在途段"才认得出来（见 [_maintain_segment] 的第二种情形）。

    `last_seen_at` 在这里**不动**：它记的是最后一次确认还占着的时刻，而这一帧恰恰证明
    它已经不是占用了。两者的差值就是这次观测盲区的大小。

    不记结束原因：浴室 App 在主动出门与超时释放下写的是同一套清零语句，服务端没有依据
    区分，硬分就是编造。
    """
    session_id = int(open_session["id"])
    device_id = str(open_session["device_id"])
    door_no = int(open_session["door_no"])

    segment = repository.get_open_segment(session, device_id, door_no)
    if segment is not None:
        _close_segment(session, segment, door, device_id, at)

    segments = repository.segments_of_session(session, session_id)
    flow = sum(int(s["flow_s"]) for s in segments)
    device_flow_s = max(int(open_session["device_flow_s"]), as_int(door.get("bath_time_s")))
    repository.touch_session(
        session, session_id, seen=False, at=_moment(at), flow_s=flow, device_flow_s=device_flow_s, gap=gap
    )
    repository.close_session(session, session_id, _moment(at), _segments_complete(segments, device_flow_s))
