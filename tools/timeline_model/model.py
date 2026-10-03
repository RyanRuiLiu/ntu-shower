"""时间轴与口径的参考模型——独立于生产代码。

把三方行为各自实现成一个可执行的部件：

    BathApp    浴室 App 的写库（每个方法对应一条真实 SQL，见 dbGUIDE）
    Monitor    设备端采集（1 秒防抖，窗口内的写库合并成一次读取）
    derive     服务端派生（会话与段、锚点换算、三项校验）

然后用场景驱动它们，看派生结果是否等于预期。

本模型不是生产代码的复制品，而是算法规格的可执行表达。两者不一致时，以本模型
揭示的行为为准去修生产代码。

时间约定
    真实时刻 t          秒，模型的主时间轴，等价于服务端轴
    设备钟刻度          t + drift，格式化后就是 t_doorlist 里的 yyyyMMddHHmmss
    skew                服务端时刻 − 设备时刻 = −drift
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 10, 3, 0, 0, 0, tzinfo=timezone.utc)

#: 段时长 A/B 交叉校验的容许差。帧间隔上限（防抖窗口 + 采集耗时）加余量。
AB_TOLERANCE_S = 3

#: 单段出水的物理上界，等于设备的 maxbathtime。设备从进门起算到 1200 秒必然强制
#: 释放，所以任何一段出水都不可能超过它。跨越采集盲区的观测值会被这一条截断——
#: 不截断就等于把两次独立的使用合成了一段。
MAX_SEGMENT_S = 1200


def fmt(t: float) -> str:
    """设备轴字符串（yyyyMMddHHmmss）。"""
    return (BASE + timedelta(seconds=t)).strftime("%Y%m%d%H%M%S")


def parse(text: str) -> float | None:
    """设备轴字符串 → 秒。空值或格式坏返回 None。"""
    if not text:
        return None
    try:
        moment = datetime.strptime(text.strip(), "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None
    return (moment - BASE).total_seconds()


def server_fmt(t: float) -> str:
    """服务端轴字符串（RFC 3339 UTC 毫秒）。

    **毫秒要留住**：设备轴只有秒精度，平移之后必然带零头；把它截断丢掉，不只是精度
    损失——`23:59:59.5` 会被截成前一天的 `23:59:59`，凭空跨了一天。
    """
    moment = BASE + timedelta(seconds=t)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def parse_server(text: str | None) -> float | None:
    """服务端轴字符串 → 秒。两轴格式不同，解析函数不可混用。"""
    if not text:
        return None
    try:
        moment = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None
    return (moment - BASE).total_seconds()


def device_from_rfc(text: object) -> str:
    """帧载荷里的时刻 → 设备轴字符串。

    设备端把库里的 `yyyyMMddHHmmss` 按**设备时区**解析后，用固定 UTC 格式化成 RFC 3339，
    所以这一步是它的逆运算：先解成绝对时刻，再按 UTC 折回日期数字。

    **接受任意偏移写法**（`Z` 或 `+08:00`）并统一到 UTC——只认 `Z` 会让格式一变就静默
    丢值，而"丢值"的表现是时间字段变成空、状态派生跟着变。
    """
    if not isinstance(text, str) or not text:
        return ""
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return moment.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S")


#: 喷头数上限 = 设备的 `DoorCount`（共享配置实测 48；本机只装了 7 个，从 1 编号）。
DOOR_MAX = 48

#: `bath_time_s` 的上界，取一天。设备那个上限由服务端下发、随时可能变大，拿它当闸门
#: 会误伤真实数据；而一天以上的单次出水不存在。
MAX_BATH_S = 86400


def as_int(value: object) -> int:
    """宽松取整：取不到给 0（与生产 core.protocol 的同名函数同源）。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def as_door_no(value: object) -> int | None:
    """门号：接受整数、能精确表示的浮点、纯数字字符串，其余 None。

    **不做截断**（与生产 `_as_door_no` 一致）：`3.7` 截成 `3` 会把一条坏数据伪装成
    3 号喷头的真实读数，脏账记到别人头上——别的数值字段可以宽容，决定"这是哪个
    喷头"的这个不行。
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


def clean(obj: dict) -> dict | None:
    """一行载荷收敛到合法域；收敛不了的整条丢弃（与生产 service._clean 同源）。

    坏掉的通常只是某几个字段，其余仍有意义，所以收敛而不是丢整帧；只有**门号**
    不合法才丢整行——它决定这一行属于哪个喷头。

    时间字段不可解析就置空：它们的**值**不参与推导，但"有没有"参与，留着乱码会被
    读成"正在出水"。
    """
    door_no = as_door_no(obj.get("door_no"))
    if door_no is None:
        return None
    cleaned = dict(obj)
    cleaned["door_no"] = door_no
    cleaned["card_no"] = max(0, as_int(obj.get("card_no")))
    cleaned["bath_time_s"] = min(MAX_BATH_S, max(0, as_int(obj.get("bath_time_s"))))
    for key in ("open_time", "in_time", "close_time"):
        value = obj.get(key)
        if value and device_from_rfc(value) == "":
            cleaned[key] = ""
    return cleaned


def row_from_payload(obj: dict) -> Row | None:
    """door.snapshot 的 doors[] 元素 → 内部行；先过一遍清洗。

    返回 None 表示这一行不可用（门号非法），调用方丢弃它。
    """
    cleaned = clean(obj)
    if cleaned is None:
        return None
    return Row(
        door_no=cleaned["door_no"],
        cardno=as_int(cleaned.get("card_no")),
        certno=str(cleaned.get("cert_no") or ""),
        accno=str(cleaned.get("acc_no") or ""),
        open_time=device_from_rfc(cleaned.get("open_time")),
        in_time=device_from_rfc(cleaned.get("in_time")),
        close_time=device_from_rfc(cleaned.get("close_time")),
        bath_time=as_int(cleaned.get("bath_time_s")),
        status=as_int(cleaned.get("status_raw")),
    )


# ── 世界与时钟 ────────────────────────────────────────────────────────────


class World:
    """真实时间轴，以及挂在上面的设备钟偏移。"""

    def __init__(self, drift: float = 0.0) -> None:
        self.t = 0.0
        self.drift = drift

    def jump(self, delta: float) -> None:
        """拨钟：设备钟相对真实时刻偏移 delta 秒。"""
        self.drift += delta

    def device_now(self) -> str:
        return fmt(self.t + self.drift)

    def skew(self) -> float:
        return -self.drift


# ── 浴室 App 的写库 ───────────────────────────────────────────────────────


@dataclass
class Row:
    """t_doorlist 的一行。三个时间是设备轴字符串。"""

    door_no: int = 1
    cardno: int = 0
    certno: str = ""
    accno: str = ""
    open_time: str = ""
    in_time: str = ""
    close_time: str = ""
    bath_time: int = 0
    status: int = 0

    def copy(self) -> "Row":
        return Row(**self.__dict__)


@dataclass
class BathParams:
    """浴室 App 的自动释放阈值（秒）与关机时刻。

    `auto_close_time` 是配置下发的 "HH:mm"，代码把它拼成「当日日期 + HH:mm:00」
    再与此刻比（见 DoorStatusConfirmThread 的条件二）。空串表示不启用。
    """

    maxbathtime: int = 1200
    maxinspacetime: int = 300
    maxbathspacetime: int = 300
    auto_close_time: str = ""


class BathApp:
    """浴室 App 对 t_doorlist 的写库。每个方法对应一条真实 SQL。"""

    def __init__(self, world: World, door_count: int = 2, params: BathParams | None = None) -> None:
        self.world = world
        self.params = params or BathParams()
        self.rows = {n: Row(door_no=n) for n in range(1, door_count + 1)}
        self.hidden: set[int] = set()

    def row(self, door_no: int) -> Row:
        return self.rows[door_no]

    def select(self, door_no: int) -> None:
        """点选喷头：无人占用，但 open_time 被写上。"""
        r = self.row(door_no)
        r.status = 2
        r.certno = ""
        r.open_time = self.world.device_now()
        r.in_time = ""
        r.close_time = ""
        r.bath_time = 0

    def check_in(self, door_no: int, cardno: int, certno: str, accno: str = "") -> None:
        """入门（刷卡或无卡）。"""
        r = self.row(door_no)
        r.cardno = cardno
        r.certno = certno
        r.accno = accno
        r.open_time = self.world.device_now()
        r.in_time = ""
        r.close_time = ""
        r.bath_time = 0
        r.status = 0

    def valve_open(self, door_no: int) -> None:
        """开阀（手动或水控板回调）。"""
        r = self.row(door_no)
        r.in_time = self.world.device_now()
        r.close_time = ""

    def valve_close(self, door_no: int) -> None:
        """关阀（暂停）：bathTime += |inTime − now|，与清空 inTime 同一条 SQL。"""
        r = self.row(door_no)
        started = parse(r.in_time)
        now = parse(self.world.device_now())
        if started is not None and now is not None:
            r.bath_time += int(abs(now - started))
        r.in_time = ""
        r.close_time = self.world.device_now()

    def release(self, door_no: int) -> None:
        """释放喷头（四种超时与主动出门同形，全部清空）。"""
        r = self.row(door_no)
        r.cardno = 0
        r.certno = ""
        r.accno = ""
        r.open_time = ""
        r.in_time = ""
        r.close_time = ""
        r.bath_time = 0
        r.status = 0

    def repair(self, door_no: int) -> None:
        """快速报修：清空全部占用字段并置 status=1。"""
        r = self.row(door_no)
        r.cardno = 0
        r.certno = ""
        r.accno = ""
        r.open_time = ""
        r.in_time = ""
        r.close_time = ""
        r.bath_time = 0
        r.status = 1

    def clear_repair(self, door_no: int) -> None:
        """报修恢复：只把状态置回 0，不动别的（写的是 `set status='0'` 一条）。"""
        r = self.row(door_no)
        r.status = 0

    def set_managed(self, door_no: int) -> None:
        """疫情管控停用：只写 `status=3`，占用字段一个字不动。"""
        r = self.row(door_no)
        r.status = 3

    def poll(self) -> list[int]:
        """DoorStatusConfirmThread 的一轮扫描：四个条件命中任一即释放喷头。

        真实顺序是「进门空闲 → 到达关机时间 → 洗浴超时 → 关阀空闲」——四个分支的
        日志文本与判定代码见 BathMainActivity$DoorStatusConfirmThread，命中一个即
        closeDoor 并写那条清零 SQL，然后跳出该门的处理。四种原因写的是同一条清零
        SQL，在库里同形——所以结束原因永远不由我们编造。

        只扫 `open_time` 非空的行，与 queryAllUsingDoorInfors 的筛选一致。

        **自动释放不写 `close_time`**：命中后只有 closeDoor 与清零，没有"结算
        bathTime"那一条 SQL。所以被自动释放的段在设备侧没有留下边界，只能按观测
        取。只有用户手动关阀才写 `bathTime += |inTime−now|, closeTime=now`。
        """
        released: list[int] = []
        now = parse(self.world.device_now())
        if now is None:
            return released
        for door_no, r in self.rows.items():
            if not r.open_time:
                continue
            opened = parse(r.open_time)
            usetime = int(abs(now - opened)) if opened is not None else 0
            closed = parse(r.close_time) if r.close_time else None

            # 条件一：进门后既没开阀也没累计过出水，且已经等够 maxinspacetime
            if not r.in_time and r.bath_time == 0 and usetime >= self.params.maxinspacetime:
                self.release(door_no)
                released.append(door_no)
                continue
            # 条件二：距当日的自动关机时刻不超过 300 秒
            if self._near_auto_close(now):
                self.release(door_no)
                released.append(door_no)
                continue
            # 条件三：进门累计时长越过 maxbathtime（拨钟会让它提前命中）
            if usetime >= self.params.maxbathtime:
                self.release(door_no)
                released.append(door_no)
                continue
            # 条件四：关阀后闲置越过 maxbathspacetime，阈值须为正才启用
            if (
                self.params.maxbathspacetime > 0
                and closed is not None
                and int(abs(now - closed)) >= self.params.maxbathspacetime
            ):
                self.release(door_no)
                released.append(door_no)
                continue
        return released

    def _near_auto_close(self, now: float) -> bool:
        """条件二：`|当日的 autoCloseTime − now| <= 300`（秒）。

        关机时刻由「当日日期 + 配置的 HH:mm + ':00'」拼成，与代码一致；模型的
        时间基准是 UTC 零点，设备时区也是 UTC，「当日」于是同一个日界。
        """
        if not self.params.auto_close_time:
            return False
        hour, minute = (int(x) for x in self.params.auto_close_time.split(":"))
        target = int(now // 86400) * 86400 + hour * 3600 + minute * 60
        return abs(target - now) <= 300

    def valve_sync(self, door_no: int, hardware_open: bool) -> None:
        """水控板与库不一致时 DoorStatusConfirmThread 的自愈写库。

        库侧的"阀门开着"是由 `open_time` 是否为空推出来的（见 queryAllUsingDoorInfors：
        `opendoorTime.equals("") → bit5=0`），而水控板那一侧是真实阀门状态。两者不同
        时以硬件为准回写。

        硬件开着、库以为关：写 `in_time`——**注意此时 `open_time` 可能是空的**，
        于是产生一行"有 in_time 但无 open_time、无人占用"的记录，它不在自动释放的
        扫描范围内（筛选条件是 `openTime<>''`）。

        硬件关着、库以为开：按 `|inTime − now|` 结算。真实代码在这里读 `da.getStarttime()`，
        若 `in_time` 为空会抛 NPE 被外层吞掉——等价于什么都不做，这里如实照搬。
        """
        r = self.row(door_no)
        if bool(r.open_time) == hardware_open:
            return
        if hardware_open:
            r.in_time = self.world.device_now()
            r.close_time = ""
            return
        started = parse(r.in_time)
        now = parse(self.world.device_now())
        if started is None or now is None:
            return
        r.bath_time += int(abs(now - started))
        r.in_time = ""
        r.close_time = self.world.device_now()

    def snapshot(self) -> list[Row]:
        """当前整表读数。被 [hide] 标掉的门不在其中——模拟那一行没被读到。"""
        return [self.rows[n].copy() for n in sorted(self.rows) if n not in self.hidden]

    def hide(self, door_no: int) -> None:
        """让这一门从后续快照里消失（它的行没被读到）。

        帧里没有的门，服务端**保留原状态**、不判成空闲——漏报一个门不等于那个门空了。
        """
        self.hidden.add(door_no)

    def hide_all(self, door_no: int = 0) -> None:
        """让所有门都不出现在后续快照里，模拟一次读到空结果的采集。

        空帧不是错误：设备读库失败时会照送一条空的，那本身就是一条事实。它留痕，
        但不改任何门的现态——空帧不等于所有门都空了。
        """
        self.hidden.update(self.rows)

    def show_all(self, door_no: int = 0) -> None:
        """取消隐藏：读库恢复正常，快照里重新有全部门。"""
        self.hidden.clear()


# ── 设备端采集 ────────────────────────────────────────────────────────────


@dataclass
class Frame:
    """一帧快照。captured_at 是真实时刻，等价于服务端轴。"""

    captured_at: float
    rows: list[Row]
    source: str = "db_watch"
    snapshot_id: str = ""


class Monitor:
    """1 秒防抖：窗口内的所有写库合并成一次读取。

    与 DoorMonitor 的实现一致——事件不丢（pending 机制），但窗口内的中间态
    只保留终态：睡眠结束后读的是当时的库。

    `online` 与 `network` 是两种不相干的失效，行为完全不同：

    - `online=False`：**采集端整体不工作**（进程没了、读库失败），期间一帧都没有；
      恢复后推一遍全貌，与设备端重启后的行为一致。
    - `network=False`：**网络不通，采集照常**。帧照采、照进发件箱，只是送不出去；
      恢复后按序补发。关键在 `captured_at` 在采集那一刻就已定稿（设备端
      DoorSnapshotSender 在读库之前取它），补发不改变它——所以断网只让服务端
      "晚知道"，不让它"记错时刻"。
    """

    def __init__(self, debounce: float = 1.0) -> None:
        self.debounce = debounce
        self.window_end: float | None = None
        self.online = True
        self.network = True

    def on_write(self, world: World) -> None:
        if not self.online:
            return
        if self.window_end is None:
            self.window_end = world.t + self.debounce

    def due(self, world: World) -> bool:
        return self.window_end is not None and world.t >= self.window_end

    def produce(self, world: World, app: BathApp) -> Frame:
        self.window_end = None
        return Frame(captured_at=world.t, rows=app.snapshot())


# ── 服务端派生 ────────────────────────────────────────────────────────────

OCCUPIED = {"waiting", "bathing", "paused"}


@dataclass
class Segment:
    opened_at: str
    opened_frame_at: float = 0.0
    closed_at: str | None = None
    flow_s: int = 0
    opened_device: str = ""
    closed_device: str = ""
    source: str = ""


@dataclass
class Session:
    door_no: int
    cert_no: str
    assigned_at: str
    assigned_seconds: float
    assigned_source: str
    anchor_device: str
    segments: list[Segment] = field(default_factory=list)
    ended_at: str | None = None
    last_seen_at: str = ""
    observed_span: str = "continuous"
    device_flow_s: int = 0
    segments_complete: bool = True
    elapsed_s: float = 0.0
    events: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def segment_sum(self) -> int:
        return sum(s.flow_s for s in self.segments)

    @property
    def flow_s(self) -> int:
        """会话出水总秒数 = 各段之和。

        设备累计值只用来判断段的完整性，不参与取值：它由设备钟算出，拨钟会让它
        偏大、且偏得"看起来合理"（仍在会话历时之内），无从分辨。而段之和要么精确
        （measured，同一口钟的两个读数之差），要么只差一个帧间隔（observed），
        任何时候都不比它差。
        """
        return self.segment_sum


class Deriver:
    """服务端：把帧序列派生成会话与段。"""

    def __init__(self, known_skew=lambda t: 0.0, link_gaps=()) -> None:
        self.known_skew = known_skew
        self.link_gaps = list(link_gaps)
        self.sessions: list[Session] = []
        self.open_session: dict[int, Session] = {}
        self.open_segment: dict[int, Segment] = {}
        self.prev: dict[int, Row] = {}
        self.prev_at: dict[int, float] = {}
        self.door_live: dict[int, dict[str, str]] = {}
        self.event_log: list[tuple[float, int, str, str]] = []
        self.seen: set[str] = set()

    # -- 换算 --------------------------------------------------------------

    def to_server(self, device_text: str, session: Session | None, at: float) -> str | None:
        """设备轴 → 服务端轴：用**此刻已知**的偏差平移。

        不用"整场会话只用一个基准"的锚点差法——它会把中途的拨钟摊到后续读数上：
        锚点是拨钟前写的、`in_time` 是拨钟后写的，两者相减就凭空多出一个拨钟量，
        段起点会偏出整个拨钟量。每个读数得用它**自己那一刻**的偏差，而这一点靠
        "帧带设备钟读数"保证（S24）。

        `session` 只在重算时用——`anchor_device` 记下了当时的设备轴原文。
        """
        moment = parse(device_text)
        if moment is None:
            return None
        return server_fmt(moment + self.known_skew(at))

    def _bounded(self, text: str | None, at: float) -> float:
        """把换算出的时刻收进"不晚于本帧"的界内；解析不出或越界就退回本帧。

        它管的是**物理有序性**：任何一次事件都不可能发生在我们看见它之后。拨钟把
        换算值推到未来时，退回本帧的观测时刻，比留一个未来时刻诚实。
        """
        moment = parse_server(text) if text else None
        if moment is None or moment > at:
            return at
        return moment

    def state_of(self, r: Row) -> str:
        if r.status == 1:
            return "repair"
        if r.status == 3:
            return "disabled"
        occupied = r.cardno != 0 or r.accno not in ("", "0")
        if occupied:
            if r.in_time:
                return "bathing"
            if r.bath_time > 0 and r.close_time:
                return "paused"
            return "waiting"
        if r.status == 2:
            return "selected"
        return "free"

    # -- 主循环 ------------------------------------------------------------

    def feed(self, frame: Frame) -> None:
        if frame.snapshot_id:
            # 重复投递只处理一次：设备在收到 ack 之前会重发同一条，而"重发"与
            # "它确实又报了一次"在协议上是同一件事（同一个 snapshot_id）。
            # 生产靠 door_snapshot 的唯一索引承担它，模型在这层做等价的事。
            if frame.snapshot_id in self.seen:
                return
            self.seen.add(frame.snapshot_id)
        at = frame.captured_at
        # 同一帧里重复门号取最后一条（与生产的 by_door 一致）：设备按门号整行读取，
        # 重复只可能来自拼装错误，取后者与"后来者覆盖"的直觉一致。
        by_door = {r.door_no: r for r in frame.rows}
        for door_no, r in by_door.items():
            state = self.state_of(r)
            prev = self.prev.get(door_no)
            prev_state = self.state_of(prev) if prev is not None else None
            session = self.open_session.get(door_no)

            if prev is not None and prev_state != state and frame.source != "startup":
                self.event_log.append((at, door_no, prev_state, state))

            # 观测缺口过大：上一帧到现在隔得比一次占用的物理上限还久，缺口前后的状态
            # **不能当成连续**。前一场在我们最后看见它的时刻收尾，本帧重新起算——
            # 否则库里的陈旧占用（浴室 App 停了、人早走了）会被当成"他还在洗"，
            # 那场会话永不结束、起点还落在几小时前。
            prev_at = self.prev_at.get(door_no)
            if prev is not None and prev_at is not None and at - prev_at > MAX_SEGMENT_S:
                if session is not None:
                    self._close_session(session, prev, prev_at, reason="观测缺口")
                    session = None

            if state in OCCUPIED:
                # 身份取"证件号，为空时退回账户号"（生产的 _occupant）。这台设备无卡，
                # cardno 实测恒为 0，拿它当人比等于没比。
                identity = r.certno or r.accno
                if session is None or identity != session.cert_no:
                    if session is not None:
                        self._close_session(session, r, at, reason="换人")
                    session = self._open_session(r, at)
                self._advance(session, r, at)
            elif session is not None:
                self._close_session(session, r, at, reason="离开占用态")

            self._write_live(r, at)
            self.prev[door_no] = r.copy()
            self.prev_at[door_no] = at

    # -- 会话 --------------------------------------------------------------

    def _open_session(self, r: Row, at: float) -> Session:
        anchor = r.open_time
        moment = parse(anchor)
        assigned: float | None = None
        if moment is not None:
            candidate = moment + self.known_skew(at)
            if self._start_agrees(candidate, r, at):
                assigned = candidate
        if assigned is None:
            assigned = at
            anchor = ""
            source = "observed"
        else:
            source = "inferred"
        session = Session(
            door_no=r.door_no,
            cert_no=r.certno or r.accno,
            assigned_at=server_fmt(assigned),
            assigned_seconds=assigned,
            assigned_source=source,
            anchor_device=anchor,
        )
        session.last_seen_at = session.assigned_at
        session.observed_span = "gap" if self._has_gap(r.door_no, assigned, at) else "continuous"
        self.sessions.append(session)
        self.open_session[r.door_no] = session
        return session

    def _has_gap(self, door_no: int, start: float, end: float) -> bool:
        """这场会话的观测里有没有断层。

        两种断层：显式声明的链路断开（`link_gaps`），以及**相邻两帧隔得太久**——
        超过一次占用的物理上限（`MAX_SEGMENT_S`）时，缺口前后不能当成连续：中间必然
        发生过我们没看见的事（可能是一次释放，也可能又是一次使用）。
        """
        prev_at = self.prev_at.get(door_no)
        if prev_at is not None and (end - prev_at) > MAX_SEGMENT_S:
            return True
        return any(start <= g <= end for g in self.link_gaps)

    def _start_agrees(self, start: float, r: Row, at: float) -> bool:
        """起点平移值的三条自洽检验，与生产代码 service._start_readings_agree 同源。

        1. 起点不晚于本帧——刷卡不可能发生在我们看见它之后；
        2. 起点不早于上次看见这个门的时刻——更早的会话已经见过了；
        3. 与同帧 `in_time` 的平移值相差不超过一个 maxbathtime——两个读数出自同一口
           钟，而同一次平移里偏差相互抵消，所以这条等价于纯设备轴比较，不受拨钟影响。

        任一不成立就退回观测下界（真实起点只会更晚，标成下界是诚实的）。
        """
        if start > at:
            return False
        prev_at = self.prev_at.get(r.door_no)
        if prev_at is not None and start < prev_at:
            return False
        opened = parse(r.open_time)
        in_s = parse(r.in_time)
        if opened is not None and in_s is not None and abs(in_s - opened) > MAX_SEGMENT_S:
            return False
        return True

    def _advance(self, session: Session, r: Row, at: float) -> None:
        session.last_seen_at = server_fmt(at)
        session.elapsed_s = at - session.assigned_seconds
        # bath_time 只在暂停那一刻累加、释放时归零，因此取历史最大值才是"设备已结算的段之和"
        session.device_flow_s = max(session.device_flow_s, r.bath_time)
        # 观测缺口可能在会话推进期间才发生，不能只在开会话时判一次
        if self._has_gap(session.door_no, session.assigned_seconds, at):
            session.observed_span = "gap"
        seg = self.open_segment.get(r.door_no)

        if r.in_time:
            if seg is None:
                opened_sec = self._bounded(self.to_server(r.in_time, session, at), at)
                # 物理有序性：开阀不可能早于会话起点，也不可能晚于本帧。换算值越界
                # 说明那一带的偏差被拨钟带偏了，退回本帧的观测时刻——诚实，且不再倒挂。
                if opened_sec < session.assigned_seconds:
                    opened_sec = at
                self.open_segment[r.door_no] = Segment(
                    opened_at=server_fmt(opened_sec),
                    opened_frame_at=at,
                    opened_device=r.in_time,
                )
        elif seg is not None:
            # 关阀：in_time 被清空
            if r.close_time:
                self._close_segment(session, seg, r.close_time, at)
            else:
                self._close_segment_observed(session, seg, at)
        elif not session.segments:
            # 冷启动接手：门停在暂停态，我们没看见那一段出水，但设备留下了痕迹
            derived = self._derive_segment(session, r, at)
            if derived is not None:
                session.segments.append(derived)
        self._check_completeness(session)

    def _derive_segment(self, session: Session, r: Row, at: float) -> Segment | None:
        """从设备留下的痕迹推回一段已经结束的出水（冷启动接手用）。

        我们上线时门已经在暂停态：`in_time` 被清空了，但设备留下了"关阀时刻"与
        "累计出水秒数"——从关阀时刻往前数那么多秒，就是那一段的开阀时刻。

        **减法必须在服务端轴上做**：`bath_time_s` 是"秒数"，与钟的读数无关，所以
        "时刻 − 秒数"换算到哪条轴上都成立；但若在设备轴上减完、把结果直接当服务端轴
        格式化，就是混轴——本文件 §8 第 6 条那个老毛病，症状一模一样：不报错，只是
        数字错。

        **只在这是全场第一次暂停时才成立**：第二段之后 `bath_time_s` 是多段之和，
        退出来会落在会话起点之前或本帧之后。所以推出后必须过区间检查——它同时也挡住
        了"`bath_time_s` 被拨钟放大"的情形（退回的时刻会跑到未来）。
        """
        if r.in_time or not r.close_time or r.bath_time <= 0:
            return None
        closed_sec = parse_server(self.to_server(r.close_time, session, at) or "")
        if closed_sec is None:
            return None
        opened_sec = closed_sec - r.bath_time
        if not (session.assigned_seconds <= opened_sec <= at):
            return None
        return Segment(
            opened_at=server_fmt(opened_sec),
            opened_frame_at=at,
            closed_at=server_fmt(closed_sec),
            # 设备轴原文留空：这一段是推的，不是设备写下的
            closed_device=r.close_time,
            flow_s=r.bath_time,
            source="derived",
        )

    def _close_segment(self, session: Session, seg: Segment, close_device: str, at: float) -> None:
        """关段：A（设备轴差值）与 B（服务端轴差值）交叉校验。"""
        in_s = parse(seg.opened_device)
        out_s = parse(close_device)
        a: int | None = None
        if in_s is not None and out_s is not None:
            a = int(abs(out_s - in_s))
        opened = parse(seg.opened_at)
        b = int(abs(at - opened)) if opened is not None else None

        seg.closed_device = close_device
        closed_sec = self._bounded(self.to_server(close_device, session, at), at)
        # 关阀不可能早于开阀：换算值越界同样退回本帧（与开阀那一处同一条物理约束）
        opened_sec = parse_server(seg.opened_at)
        if opened_sec is not None and closed_sec < opened_sec:
            closed_sec = at
        seg.closed_at = server_fmt(closed_sec)
        # B 取"本帧到开阀时刻"的距离。开阀时刻优先用**换算值**：它涵盖"我们没看见开阀
        # 动作"的情形（离线期间有人进场，看见时阀已经开着），此时用观测帧会让 B 偏小、
        # 反被交叉校验判成"不一致"。段内拨钟影响不到这个换算值——它是开阀那一帧算出的。
        opened_sec = parse_server(seg.opened_at)
        b = int(abs(at - opened_sec)) if opened_sec is not None else None
        if b is not None:
            b = min(b, MAX_SEGMENT_S)
        if a is not None and (b is None or abs(a - b) <= AB_TOLERANCE_S):
            seg.flow_s, seg.source = a, "measured"
        elif b is not None:
            # 截断到上界的那一段要单独标出来：它与"真实的 1200 秒"必须能分开
            seg.flow_s = min(b, MAX_SEGMENT_S)
            seg.source = "capped" if b > MAX_SEGMENT_S else "observed"
        else:
            seg.flow_s, seg.source = 0, "observed"
        session.segments.append(seg)
        self.open_segment.pop(session.door_no, None)

    def _close_segment_observed(self, session: Session, seg: Segment, at: float) -> None:
        """释放帧里 close_time 已被清空，只能用观测到的服务端时刻。

        两帧观测时刻之差是唯一可用的依据：它不经过换算，因此不会被中途的拨钟带偏。
        """
        seg.closed_at = server_fmt(at)
        span = int(abs(at - seg.opened_frame_at)) if seg.opened_frame_at else 0
        seg.flow_s = min(span, MAX_SEGMENT_S)
        seg.source = "capped" if span > MAX_SEGMENT_S else "observed"
        session.segments.append(seg)
        self.open_segment.pop(session.door_no, None)

    def _close_session(self, session: Session, r: Row, at: float, reason: str) -> None:
        seg = self.open_segment.pop(session.door_no, None)
        if seg is not None:
            self._close_segment_observed(session, seg, at)
        session.ended_at = server_fmt(at)
        session.elapsed_s = at - session.assigned_seconds
        self._check_completeness(session)
        self.open_session.pop(session.door_no, None)

    def _check_completeness(self, session: Session) -> None:
        """对齐点：设备累计值 vs 我们的段之和。

        **有 `derived` 段时恒为假**：那一段的长度**就是**设备的累计值（从它的痕迹推的），
        等式必然成立——拿它当"完整"的证据是循环论证。而且设备那个值本身可能被拨钟放大
        （S56：真实两段共 20 秒，设备说 220 秒）。
        """
        if any(s.source == "derived" for s in session.segments):
            session.segments_complete = False
            return
        session.segments_complete = session.segment_sum == session.device_flow_s

    # -- 进程重启 ----------------------------------------------------------

    def save(self) -> dict:
        """导出**重启后必须从库里读回来的**那份状态。

        真实对应关系：`sessions` 与 `open_session` 在 `usage_session` 与
        `usage_segment`；`prev` 与 `prev_at` 在 `door_live` 的现态；`open_segment`
        的段起点在 `door_live.segment_started_at`；偏差在 `device.clock_skew_s`。

        这份状态丢了就出大事：下一帧会把**已经在用**的门当成"刚进门"，凭空开出
        一条新会话，前一段的长度也永久丢掉。所以它们全部要落库，不能只放内存。
        """
        return {
            "sessions": self.sessions,
            "open_session": self.open_session,
            "open_segment": self.open_segment,
            "prev": self.prev,
            "prev_at": self.prev_at,
            "door_live": self.door_live,
            "event_log": self.event_log,
            "seen": self.seen,
        }

    def restore(self, state: dict) -> None:
        for key, value in state.items():
            setattr(self, key, value)

    # -- 现态 --------------------------------------------------------------

    def _write_live(self, r: Row, at: float) -> None:
        session = self.open_session.get(r.door_no)
        seg = self.open_segment.get(r.door_no)
        if session is not None:
            last_closed = session.segments[-1].closed_at if session.segments else ""
            self.door_live[r.door_no] = {
                "open_time": session.assigned_at,
                "in_time": seg.opened_at if seg is not None else "",
                "close_time": last_closed or "",
            }
        elif r.status == 2 and r.open_time:
            self.door_live[r.door_no] = {
                "open_time": server_fmt(parse(r.open_time) + self.known_skew(at)),
                "in_time": "",
                "close_time": "",
            }
        else:
            self.door_live[r.door_no] = {"open_time": "", "in_time": "", "close_time": ""}


# ── 场景驱动 ──────────────────────────────────────────────────────────────


class SkewTracker:
    """服务端对某台设备偏差的**认知**。

    偏差并不是在任意时刻都已知的：它由校时信息**离散更新**，两次之间用的还是上一次
    的值。设备钟刚被改过、而下一次校时还没到，这段空窗里做的平移就会偏掉整个钟差
    ——生产代码的注释里记着 2026-10-02 的真实事故（断链期间有人刷卡，重连前设备钟被
    拨快一小时，算出来的起点比开阀还早一小时）。

    两个校时源的空窗差一个量级：

    - **心跳 pong**：间隔长，空窗大；拨钟后最久要等一个心跳周期才纠正；
    - **帧带设备钟读数**：每帧一次，空窗缩到一个帧间隔——而"开会话"必然落在某一帧上，
      所以那一帧当场就把偏差对齐了。

    真值只有设备自己知道，由 `truth` 给出，代表"那一刻真实存在的偏差"。
    `known` 是服务端手上那个可能过期的数。
    """

    def __init__(self, truth) -> None:
        self._truth = truth
        self.known = 0.0

    def calibrate(self) -> None:
        """一次校时：把认知更新为此刻的真值。"""
        self.known = self._truth()

    def at(self, _t: float) -> float:
        return self.known


@dataclass
class Step:
    """场景的一步：推进到某个真实时刻，执行一个动作。

    `online` 不为 None 时切换采集端的在线状态；从离线恢复会立刻产生一帧
    startup 快照，与设备端重启后的行为一致。

    `network` 不为 None 时切换网络：断开只让帧堆在发件箱里，采集照常。

    `calibrate` 为真时触发一次校时（等价于收到一个心跳 pong），把服务端的偏差认知
    更新为此刻的真值。
    """

    at: float
    action: str
    door_no: int = 1
    args: tuple = ()
    jump: float | None = None
    online: bool | None = None
    network: bool | None = None
    calibrate: bool | None = None


def run_scenario(steps: list[Step], debounce: float = 1.0, drift: float = 0.0,
                 known_skew=None, link_gaps=(), door_count: int = 2,
                 poll: bool = True, auto_close_time: str = "", calibrate: str = "pong",
                 server_restarts: tuple[float, ...] = (), outbox_max: int = 100_000):
    """按步骤驱动三个部件，返回 (派生器, 帧列表)。

    设备侧的扫描线程按秒自行推进，与场景步骤互不依赖：它会自己写库（自动释放），
    写库同样触发采集。

    帧先进发件箱再投递：网络通时立即出箱，不通时留在箱里，恢复后**按采集顺序**
    补发。这与设备端 door_outbox 的行为一致，也是"断网只延迟、不记错时刻"的由来。

    `calibrate` 决定偏差在什么时候对齐：

    - `"pong"`：只在场景显式写 `calibrate=True` 的步骤上对齐（等价于心跳）；
    - `"frame"`：每一帧采集时对齐（等价于帧里带设备钟读数）。

    `known_skew` 显式给出时不做这套模拟，直接用给定的函数。

    `server_restarts` 给出服务端重启的时刻：那一刻换一个派生器实例，但状态从
    `save()` 里接过来（等价于从库读回在途会话、现态与段起点）。偏差认知不受影响，
    它落在 `device.clock_skew_s`，重启后照旧。

    `outbox_max` 是发件箱行数上限，超出丢**最旧的**——与设备端 trimOutbox 一致。
    被丢掉的帧永久缺失，是这套链路里唯一"数据真的少了"的路径。
    """
    world = World(drift=drift)
    tracker = SkewTracker(world.skew)
    skew_fn = tracker.at if known_skew is None else known_skew
    app = BathApp(world, door_count=door_count, params=BathParams(auto_close_time=auto_close_time))
    monitor = Monitor(debounce=debounce)
    holder = {"deriver": Deriver(known_skew=skew_fn, link_gaps=link_gaps)}
    frames: list[Frame] = []
    outbox: list[Frame] = []
    next_poll = [0.0]
    pending_restarts = sorted(server_restarts)

    def deliver() -> None:
        """把发件箱里积压的帧按序交给服务端；网络不通时一条都不出箱。"""
        while outbox and monitor.network:
            holder["deriver"].feed(outbox.pop(0))

    def capture(source: str) -> None:
        frame = monitor.produce(world, app)
        frame.source = source
        if calibrate == "frame":
            # 帧带设备钟读数：采集那一刻就把偏差对齐，不等下一次心跳
            tracker.calibrate()
        # 入箱即定稿：captured_at 就是产生它的那一刻，此后补发不再改动
        outbox.append(frame)
        frames.append(frame)
        while len(outbox) > outbox_max:
            # 发件箱满了：丢最旧的一条，那一段历史永久缺失
            outbox.pop(0)
        deliver()

    def restart_if_due(now: float) -> None:
        while pending_restarts and pending_restarts[0] <= now:
            pending_restarts.pop(0)
            state = holder["deriver"].save()
            fresh = Deriver(known_skew=skew_fn, link_gaps=link_gaps)
            fresh.restore(state)
            holder["deriver"] = fresh

    def drain(until: float) -> None:
        """把 until 之前所有到期的防抖窗口消化掉。

        窗口到期那一刻读库——这是采集端的真实时序：睡够防抖时长就立刻读，
        读到的必然不是更晚的状态。
        """
        while monitor.window_end is not None and until >= monitor.window_end:
            world.t = monitor.window_end
            capture("db_watch")

    def tick(until: float) -> None:
        if not poll:
            return
        while next_poll[0] <= until:
            world.t = next_poll[0]
            next_poll[0] += 1.0
            if app.poll():
                monitor.on_write(world)
            drain(world.t)

    for step in steps:
        tick(step.at)
        drain(step.at)
        restart_if_due(step.at)
        world.t = step.at
        if step.jump is not None:
            world.jump(step.jump)
        if step.online is not None:
            monitor.online = step.online
            if step.online:
                # 恢复在线：设备端重启后立刻推一遍全貌
                capture("startup")
        if step.network is not None:
            monitor.network = step.network
            if step.network:
                deliver()
        if step.calibrate:
            tracker.calibrate()
        action = getattr(app, step.action)
        action(step.door_no, *step.args)
        monitor.on_write(world)

    tick(steps[-1].at + 1.0)
    drain(float("inf"))
    monitor.network = True
    deliver()
    # 收尾：数据到此为止，仍在出水的段标成"未关闭"。既不丢弃（它代表此刻真的在
    # 出水，前端要说得出"正在洗"），也不强行结算（设备还没把这一段累加进
    # bath_time_s，硬给一个数就是编造）。
    final = holder["deriver"]
    for door_no, seg in list(final.open_segment.items()):
        session = final.open_session.get(door_no)
        if session is not None:
            # 未关闭的段单独标出来，不用空串表示——空串会被读成"忘了填"
            seg.source = "unclosed"
            session.segments.append(seg)
        final.open_segment.pop(door_no)
    return final, frames
