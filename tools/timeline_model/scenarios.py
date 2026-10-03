"""场景验证：用参考模型跑各种边界，比对派生结果与预期。

每个场景给出驱动步骤与"正确答案应该是什么"，跑完自动比对。预期里同时标出
可信度标记——验证的目标不只是数值对不对，还包括标记是否正确：一个数值对而
标记错的系统，统计时会把不可信的数据当真。

运行：python -m tools.timeline_model.scenarios   （在 server 之外直接 python 本文件亦可）
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from model import MAX_BATH_S, Deriver, Frame, Row, Step, row_from_payload, run_scenario  # noqa: E402

#: 段时长的预期容许差（秒）。observed 段按帧间隔估算，误差不该超过防抖窗口加余量。
SLACK = 2


@dataclass
class Expect:
    """一个场景的预期。None 表示该项不检查。"""

    sessions: int | None = None
    segments: int | None = None
    flows: list[int] | None = None
    sources: list[str] | None = None
    complete: bool | None = None
    assigned_source: str | None = None
    span: str | None = None
    door_no: int = 1
    session_index: int = 0
    note: str = ""


@dataclass
class Case:
    name: str
    steps: list[Step]
    expect: Expect
    drift: float = 0.0
    known_skew: object = None
    link_gaps: tuple = ()
    door_count: int = 2
    auto_close_time: str = ""
    calibrate: str = "pong"
    server_restarts: tuple = ()
    outbox_max: int = 100_000
    poll: bool = True
    extra: dict = field(default_factory=dict)


# ── 场景 ──────────────────────────────────────────────────────────────────

#: S23 与 S24 共用同一串步骤：拨钟后立刻开会话。两者唯一的差别是偏差何时对齐
#: （心跳空窗 vs 帧带读数），用它量化"那一层校时"到底值多少。
_SKEW_LAG_STEPS = [
    Step(40, "noop", 1, (), 3600.0),       # 设备钟被拨快一小时
    Step(50, "check_in", 1, (1002, "B")),  # 刷卡；服务端手上的偏差可能还是旧的
    Step(60, "valve_open", 1),
    Step(70, "valve_close", 1),
    Step(80, "release", 1),
]

#: 两个喷头错开几十秒同时有人。逐门独立派生，互不影响——同一串步骤由 S29 与 S30
#: 分别检查门 1 与门 2。
_TWO_DOORS_STEPS = [
    Step(0, "check_in", 1, (1001, "A")),
    Step(5, "check_in", 2, (1002, "B")),
    Step(10, "valve_open", 1),
    Step(15, "valve_open", 2),
    Step(30, "valve_close", 1),
    Step(35, "valve_close", 2),
    Step(40, "release", 1),
    Step(45, "release", 2),
]

#: S37 与 S38 共用：一场会话里拨一次钟，落在两次出水**之间**。pong 模式下偏差跟不上，
#: 靠物理有序性把段起点拉回；帧带读数时当场算对。两者唯一的差别又是"偏差何时对齐"。
_MULTI_JUMP_STEPS = [
    Step(0, "check_in", 1, (1001, "A")),
    Step(10, "valve_open", 1),
    Step(30, "valve_close", 1),
    Step(40, "noop", 1, (), 200.0),    # 拨快两百秒：不足以撞上任何自动释放阈值
    Step(50, "valve_open", 1),
    Step(70, "valve_close", 1),
    Step(80, "release", 1),
]

#: S40 与 S41 共用：前人**正在出水**时被后人刷卡覆盖。一个喷头同一时刻只能一个人用，
#: 所以那一刻前人的资格就结束了；两个视角分别检查前人与后人的账。
_HANDOVER_MID_STEPS = [
    Step(0, "check_in", 1, (1001, "A")),
    Step(10, "valve_open", 1),
    Step(30, "check_in", 1, (1002, "B")),
    Step(40, "valve_open", 1),
    Step(60, "valve_close", 1),
    Step(70, "release", 1),
]

#: S42 与 S43 共用：同一个人洗完 1 号喷头又去 2 号。两扇门各自成会话，互不牵连。
_SWITCH_DOOR_STEPS = [
    Step(0, "check_in", 1, (1001, "A")),
    Step(10, "valve_open", 1),
    Step(30, "valve_close", 1),
    Step(40, "release", 1),
    Step(50, "check_in", 2, (1001, "A")),
    Step(60, "valve_open", 2),
    Step(80, "valve_close", 2),
    Step(90, "release", 2),
]

#: S44 与 S45 共用：前一个人出门与前一个人进门落在**同一个防抖窗口**（间隔 0.3 秒），
#: 合并成一帧后只看得见终态。两个视角检查两人的账有没有混。
_SAME_WINDOW_STEPS = [
    Step(0, "check_in", 1, (1001, "A")),
    Step(10, "valve_open", 1),
    Step(30, "valve_close", 1),
    Step(40, "release", 1),
    Step(40.3, "check_in", 1, (1002, "B")),
    Step(50, "noop", 1),
]

CASES: list[Case] = [
    Case(
        name="S1 标准一次：点选→刷卡→开→关→开→关→出门",
        steps=[
            Step(0, "select", 1),
            Step(10, "check_in", 1, (1001, "A")),
            Step(20, "valve_open", 1),
            Step(200, "valve_close", 1),
            Step(250, "valve_open", 1),
            Step(400, "valve_close", 1),
            Step(450, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=2, flows=[180, 150], sources=["measured", "measured"],
            complete=True, assigned_source="inferred", span="continuous",
            note="两段各由设备写下的两个时刻界定，精确到秒",
        ),
    ),
    Case(
        name="S2 反复开关三次",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1), Step(30, "valve_close", 1),
            Step(40, "valve_open", 1), Step(60, "valve_close", 1),
            Step(70, "valve_open", 1), Step(90, "valve_close", 1),
            Step(100, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=3, flows=[20, 20, 20],
            sources=["measured"] * 3, complete=True,
            note="段数即开阀次数",
        ),
    ),
    Case(
        name="S3 秒开秒关：两次写库落在同一防抖窗口",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(10.5, "valve_close", 1),
            Step(60, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=0, complete=True,
            note="合并后只剩终态；被吃掉的段不足 1 秒，设备侧同样按 0 累计",
        ),
    ),
    Case(
        name="S4 关阀后立刻出门：close_time 与释放合并",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(30.2, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[21], sources=["observed"], complete=False,
            note="释放帧里 close_time 已清空，只能用观测时刻；偏大约一个窗口",
        ),
    ),
    Case(
        name="S5 直接释放：没暂停过就出门",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(60, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[51], sources=["observed"], complete=False,
            note="全程看不到 close_time",
        ),
    ),
    Case(
        name="S6 换人：暂停中被后一人刷卡覆盖",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(60, "check_in", 1, (2002, "B")),
            Step(70, "valve_open", 1),
            Step(90, "valve_close", 1),
            Step(100, "release", 1),
        ],
        expect=Expect(
            sessions=2, note="两个人的账各自独立；A 的会话在换人那一帧收尾",
        ),
    ),
    Case(
        name="S7 会话中途拨钟：设备钟被拨快 2 分钟",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "valve_close", 1, jump=120.0),
            Step(60, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[11], sources=["observed"], complete=False,
            note="A 值被拨钟污染（140 秒），交叉校验识破后取服务端轴差值",
        ),
    ),
    Case(
        name="S8 采集端离线 10 分钟，期间学生洗完走了",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(11, "dummy_noop", 1, online=False),
            Step(610, "release", 1),
            Step(620, "dummy_noop", 1, online=True),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[610], sources=["observed"], complete=False,
            span="gap",
            note="唯一不可恢复的缺口：没有旁观者，设备也已把痕迹清零",
        ),
        link_gaps=(11.0,),
    ),
    Case(
        name="S9 点选后不刷卡就走",
        steps=[
            Step(0, "select", 1),
            Step(300, "dummy_noop", 1),
        ],
        expect=Expect(sessions=0, note="selected 不进占用态，不产生会话"),
    ),
    Case(
        name="S10 快速报修：占用期间被打成报修态",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(30, "repair", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, sources=["observed"], complete=False,
            note="报修清空全部占用字段并置 status=1，会话随之收尾",
        ),
    ),
    Case(
        name="S11 浴室 APP 离线：库冻结期间学生关了阀",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            # 学生 20 秒时关阀，但浴室 APP 不在，直到 100 秒才写进库
            Step(100, "valve_close", 1),
            Step(110, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[90], sources=["measured"], complete=True,
            note="设备恢复后按 |inTime − now| 写 bathTime，与我们取同一口钟的差值，两边一致",
        ),
    ),
    Case(
        name="S12 段内拨钟：设备钟被拨快 5 秒",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, (), 5.0),
            Step(30, "valve_close", 1),
            Step(40, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["observed"], complete=False,
            note="A 值含拨钟量（25 秒），交叉校验识破后取观测差值 20 秒",
        ),
    ),
    Case(
        name="S13 入门等待期间拨钟：段在拨钟之后才开",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(5, "noop", 1, (), 5.0),
            Step(10, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(40, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            note="锚点早已固化，段的两个读数都在拨钟之后，差值仍然正确",
        ),
    ),
    Case(
        name="S14 洗浴超时自动释放：学生不再操作",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(1210, "noop", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[1190], sources=["observed"], complete=False,
            note="设备满 maxbathtime 自行释放；释放帧已无 close_time，只能按观测差给时长",
        ),
    ),
    Case(
        name="S15 关阀空闲自动释放：暂停后一直没回来",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(340, "noop", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            note="关阀那一刻设备的 close_time 已把段定格，此后多久释放都不影响时长",
        ),
    ),
    Case(
        name="S16 进门空闲自动释放：刷了卡始终没开阀",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(310, "noop", 1),
        ],
        expect=Expect(
            sessions=1, segments=0, complete=True,
            note="全程没有出水，不产生段",
        ),
    ),
    Case(
        name="S17 大幅拨钟触发自动释放：设备钟被拨快 1 小时",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, (), 3600.0),
            Step(40, "noop", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[11], sources=["observed"], complete=False,
            note="usetime=|openTime−now| 跳到 3600 秒以上、越过 maxbathtime，"
                 "设备在下一轮扫描就自行释放——时间突变真正的后果在这里",
        ),
    ),
    Case(
        name="S18 断电重启：占用行原样保留，恢复后接着洗",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, online=False),
            Step(300, "noop", 1, online=True),
            Step(400, "valve_close", 1),
            Step(500, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[390], sources=["measured"], complete=True,
            span="gap",
            note="initDoorInfos 不清占用字段，重启后那一行还是原样；"
                 "段的两个读数都在断电之前与之后各自完整，差值仍然精确",
        ),
        link_gaps=(20.0,),
    ),
    Case(
        name="S19 无卡直接开阀：水控板自愈写 inTime，但没人占用的行",
        steps=[
            Step(10, "valve_sync_open", 1),
            Step(60, "noop", 1),
        ],
        expect=Expect(
            sessions=0,
            note="库里出现 in_time 有值而无 open_time、cardno=0 的行；"
                 "占用判据看不到它，自动释放的筛选条件（openTime<>''）也扫不到它",
        ),
    ),
    Case(
        name="S20 到达自动关机时间：设备释放所有在用的门",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(400, "noop", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[290], sources=["observed"], complete=False,
            note="条件二 |当日关机时刻 − now| <= 300 命中后同样只 closeDoor 加清零，"
            "与手动关阀不同，不写 close_time，段只能按观测差取",
        ),
        auto_close_time="00:10",
    ),
    Case(
        name="S21 关机时刻未到：条件二不触发，照常手动关阀",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(100, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            note="关机时刻远在 6 小时后，条件二不命中；手动关阀照常把段定格",
        ),
        auto_close_time="06:00",
    ),
    Case(
        name="S22 断网期间洗完全程：帧堆在发件箱，恢复后按序补发",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, network=False),
            Step(30, "valve_close", 1),
            Step(40, "valve_open", 1),
            Step(50, "valve_close", 1),
            Step(60, "release", 1),
            Step(70, "noop", 1, network=True),
        ],
        expect=Expect(
            sessions=1, segments=2, flows=[20, 10], sources=["measured", "measured"],
            complete=True, assigned_source="inferred", span="continuous",
            note="断网只延迟、不记错时刻：captured_at 在采集那一刻定稿，补发不改它，"
            "所以段仍然 measured 且时长精确（若补发用送达时刻，两段都会退成 observed）",
        ),
    ),
    Case(
        name="S23 拨钟后立即开会话、偏差尚未更新：起点被识破退回下界",
        steps=_SKEW_LAG_STEPS,
        expect=Expect(
            sessions=1, segments=1, flows=[10], sources=["measured"],
            complete=True, assigned_source="observed",
            note="设备钟刚被拨快一小时、而手上的偏差还是旧值：平移出的起点落在未来，"
            "被'起点不晚于本帧'识破，退回观测下界——诚实，但这一场丢了精确起点",
        ),
    ),
    Case(
        name="S24 同一场景 + 帧带设备钟读数：起点当场算对",
        steps=_SKEW_LAG_STEPS,
        calibrate="frame",
        expect=Expect(
            sessions=1, segments=1, flows=[10], sources=["measured"],
            complete=True, assigned_source="inferred",
            note="帧自带设备钟读数，偏差在采集那一刻就对齐，不经过心跳空窗，"
            "起点算成真实刷卡时刻",
        ),
    ),
    Case(
        name="S25 服务端重启落在出水中间：段起点从库里读回，段仍精确",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(40, "release", 1),
        ],
        server_restarts=(20.0,),
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"],
            complete=True, assigned_source="inferred",
            note="重启时正在出水，段起点只在内存里就丢了：下一帧会把它当成新开的段，"
            "或干脆没有段。它落在 door_live.segment_started_at，所以照旧算得精确",
        ),
    ),
    Case(
        name="S26 服务端重启落在两段之间：在途会话从库里读回",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(50, "valve_open", 1),
            Step(70, "valve_close", 1),
            Step(80, "release", 1),
        ],
        server_restarts=(40.0,),
        expect=Expect(
            sessions=1, segments=2, flows=[20, 20], sources=["measured", "measured"],
            complete=True, assigned_source="inferred",
            note="会话跨重启续算：在途会话若只放内存，重启后这一场会被拆成两条，"
            "且第二条的起点会是重启后的时刻",
        ),
    ),
    Case(
        name="S27 断网积压超上限：丢最旧，两段被合成一段且被完整性判据抓到",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, network=False),
            Step(30, "valve_close", 1),
            Step(40, "valve_open", 1),
            Step(50, "valve_close", 1),
            Step(60, "release", 1),
            Step(70, "noop", 1, network=True),
        ],
        outbox_max=2,
        expect=Expect(
            sessions=1, segments=1, flows=[40], sources=["measured"], complete=False,
            note="发件箱满时丢最旧（与设备端 trimOutbox 一致）。丢掉的两帧让中间那次"
            "开关消失，A 与 B 都变成 40 秒、交叉校验看不出问题——"
            "唯一能发现它的是设备累计值：段和 40 != 设备值 30，完整性标成假",
        ),
    ),
    Case(
        name="S28 断网期间采集端重启：发件箱留着，恢复后先补旧帧再推全貌",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, network=False),
            Step(30, "valve_close", 1),
            Step(40, "noop", 1, online=False),
            Step(50, "noop", 1, online=True),
            Step(60, "noop", 1, network=True),
            Step(70, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"],
            complete=True, assigned_source="inferred",
            note="两种失效叠加：断网让帧堆在发件箱，采集端重启期间一帧都没有。恢复后"
            "先按序补旧帧、再推一遍全貌，旧帧一条不少，段的两个读数都还在",
        ),
    ),
    Case(
        name="S29 两个喷头同时有人（查门 1）：各自成会话",
        steps=_TWO_DOORS_STEPS,
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            note="门 1 的会话不受门 2 影响：逐门独立派生，段仍由自己那两口钟的差值定",
        ),
    ),
    Case(
        name="S30 两个喷头同时有人（查门 2）：另一条会话同样精确",
        steps=_TWO_DOORS_STEPS,
        expect=Expect(
            door_no=2, sessions=1, segments=1, flows=[20], sources=["measured"],
            complete=True,
            note="同一串步骤的另一半：门 2 的会话与门 1 完全独立",
        ),
    ),
    Case(
        name="S31 疫情管控停用：非占用态，不产生会话",
        steps=[
            Step(0, "set_managed", 1),
            Step(10, "noop", 1),
        ],
        expect=Expect(
            sessions=0,
            note="status=3 是停用态，不进占用判据；全程没有会话",
        ),
    ),
    Case(
        name="S32 报修与恢复：报修给在用会话收尾，恢复不开新会话",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "repair", 1),
            Step(30, "noop", 1),
            Step(40, "clear_repair", 1),
            Step(50, "noop", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[10], sources=["observed"], complete=False,
            note="报修清空全部占用字段并置 status=1，在用的会话随之收尾；段没有"
            "close_time，只能按观测差取。恢复只把状态置回 0，不开新会话",
        ),
    ),
    Case(
        name="S33 水控自愈的 NPE 分支：in_time 为空时什么都不做",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_sync_close", 1),
            Step(20, "noop", 1),
        ],
        expect=Expect(
            sessions=1, segments=0, complete=True,
            note="硬件说阀门关着、库以为开着，本该按 |inTime−now| 结算；但这一行"
            "inTime 是空的（刷了卡没开阀），真实代码在这里抛 NPE 被外层吞掉——"
            "等价于什么都不做。我们照搬，不替它补一个结算",
        ),
    ),
    Case(
        name="S34 会话悬空：数据到此为止，在途段保留为未关闭",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(30, "noop", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[0], sources=["unclosed"], complete=True,
            note="数据窗口在这里结束、学生还在洗：会话不收尾，在途段以未关闭的形态"
            "保留（closed_at 为空、不计时长），既不丢弃也不强行结算",
        ),
    ),
    Case(
        name="S35 门从快照里消失：保留原状态，不判成空闲",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "hide", 1),
            Step(60, "noop", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[0], sources=["unclosed"], complete=True,
            note="门在后续帧里不出现，只说明这一帧没读到它，不等于它空了：现态保持原样、"
            "会话不结、在途段也不撤",
        ),
    ),
    Case(
        name="S36 拨钟后拨回：段内那次拨钟被交叉校验识破，取观测差",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, (), 600.0),
            Step(30, "valve_close", 1),
            Step(40, "noop", 1, (), -600.0),
            Step(50, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["observed"], complete=False,
            note="拨快十分钟后又拨回（幅度不足 maxbathtime，不会触发自动释放）：偏差回到"
            "原位不代表那一段没被污染——设备写下的关阀时刻含那十分钟，A 变成 620 秒，"
            "被交叉校验识破后改取观测差 20 秒",
        ),
    ),
    Case(
        name="S37 会话内两次拨钟（偏差未跟上）：有序性拉回起点，时长仍精确",
        steps=_MULTI_JUMP_STEPS,
        expect=Expect(
            sessions=1, segments=2, flows=[20, 19], sources=["measured", "measured"],
            complete=True, assigned_source="inferred",
            note="拨钟落在两次出水之间：第二段的两个读数都在拨钟后，段内差值不受影响；"
            "而它的起点按旧偏差平移会落到未来，被'不晚于本帧'拉回本帧",
        ),
    ),
    Case(
        name="S38 同一场景 + 帧带设备钟读数：第二段起点也精确",
        steps=_MULTI_JUMP_STEPS,
        calibrate="frame",
        expect=Expect(
            sessions=1, segments=2, flows=[20, 19], sources=["measured", "measured"],
            complete=True, assigned_source="inferred",
            note="帧带读数让第二段用它自己那一刻的偏差平移，起点落到真实开阀时刻，"
            "不必靠拉回",
        ),
    ),
    Case(
        name="S39 拨钟触发关阀空闲释放：同一个拨钟量在暂停中撞上另一条阈值",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(40, "noop", 1, (), 600.0),
            Step(50, "noop", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            note="同一个拨钟量，落在出水里会撞上 maxbathtime（见 S17），落在暂停里会撞上"
            "maxbathspacetime：closediff 从十几秒跳到六百多秒，设备下一轮扫描就释放了。"
            "设备因此提前结束，我们如实记下，不改写它的行为",
        ),
    ),
    Case(
        name="S40 出水期间换人（查前人）：他的段在他被覆盖那一刻收尾",
        steps=_HANDOVER_MID_STEPS,
        expect=Expect(
            session_index=0, sessions=2, segments=1, flows=[20], sources=["observed"],
            complete=False,
            note="一个喷头同一时刻只能一个人用：新人刷卡覆盖的那一刻，前人的资格就结束了，"
            "他名下那段水随之收尾。硬件当时没关阀，但那段水不再是他的账",
        ),
    ),
    Case(
        name="S41 出水期间换人（查新人）：他的段从他自己开阀起算",
        steps=_HANDOVER_MID_STEPS,
        expect=Expect(
            session_index=1, sessions=2, segments=1, flows=[20], sources=["measured"],
            complete=True,
            note="新人不继承前人的段：他的账从他被证明开着阀那一刻起算。"
            "若把前人的段起点延续过来，同一段水会被两个人各算一次",
        ),
    ),
    Case(
        name="S42 同一人洗完再换喷头（查 1 号）：前一场已结束",
        steps=_SWITCH_DOOR_STEPS,
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            note="同一个人先洗 1 号、再洗 2 号：两个喷头各自成会话，互不牵连——"
            "门与门之间没有「同一人所以合并」这回事",
        ),
    ),
    Case(
        name="S43 同一人洗完再换喷头（查 2 号）：另一场从头计",
        steps=_SWITCH_DOOR_STEPS,
        expect=Expect(
            door_no=2, sessions=1, segments=1, flows=[20], sources=["measured"],
            complete=True,
            note="换过去的那个喷头，会话从他的刷卡时刻起算，与 1 号那场无关",
        ),
    ),
    Case(
        name="S44 出门与进门落在同一窗口（查前人）：他的账在合并帧前已结",
        steps=_SAME_WINDOW_STEPS,
        expect=Expect(
            session_index=0, sessions=2, segments=1, flows=[20], sources=["measured"],
            complete=True,
            note="前一个人出门与后一个人进门只隔 0.3 秒，合并成一帧后只看得见终态；"
            "但前人的账在关阀那一帧就已经结清了，合并不会让他少记",
        ),
    ),
    Case(
        name="S45 出门与进门落在同一窗口（查后人）：他的账从合并帧起",
        steps=_SAME_WINDOW_STEPS,
        expect=Expect(
            session_index=1, sessions=2, segments=0, complete=True,
            note="后人在合并帧里是唯一可见的占用者，他的会话从那一刻起算，"
            "一秒钟都没借前人的",
        ),
    ),
    Case(
        name="S46 网络反复通断：每次恢复都按序补发，帧一条不丢",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(15, "noop", 1, network=False),
            Step(20, "valve_close", 1),
            Step(25, "noop", 1, network=True),
            Step(30, "valve_open", 1),
            Step(35, "noop", 1, network=False),
            Step(40, "valve_close", 1),
            Step(45, "noop", 1, network=True),
            Step(50, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=2, flows=[10, 10], sources=["measured", "measured"],
            complete=True, assigned_source="inferred",
            note="断两次、通两次：每次恢复都按采集顺序补发，没有一条帧被后面的追过，"
            "两段仍各自精确",
        ),
    ),
    Case(
        name="S47 采集端与服务端同时重启，而网络也断着",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, network=False),
            Step(30, "valve_close", 1),
            Step(40, "noop", 1, online=False),
            Step(50, "noop", 1, online=True),
            Step(60, "noop", 1, network=True),
            Step(70, "release", 1),
        ],
        server_restarts=(45.0,),
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            assigned_source="inferred",
            note="三种失效叠在一起：网络断、采集端 40-50 不工作、服务端 45 重启。"
            "恢复后帧仍按序送达、段起点仍在、账仍精确",
        ),
    ),
    Case(
        name="S48 段内拨钟恰好等于容差：A 被采信，容差的边界在这一侧",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(15, "noop", 1, (), 3.0),
            Step(30, "valve_close", 1),
            Step(40, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[23], sources=["measured"], complete=True,
            note="段内拨 3 秒，正好落在容差里（|A − B| = 3 不大于 3）：A 被采信，"
            "记成 23 秒而非真实的 20 秒。这是容差的代价，换来的是「三秒级的钟漂不被"
            "误判成拨钟」",
        ),
    ),
    Case(
        name="S49 段内拨钟明确超过容差：交叉校验识破，改取观测差",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(15, "noop", 1, (), 10.0),
            Step(30, "valve_close", 1),
            Step(40, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[21], sources=["observed"], complete=False,
            note="段内拨 10 秒：A 变成 30 秒，与观测差差 9 秒，远超容差，A 被识破、改取观测差。"
            "**拨钟量接近容差时判定会不确定**——那时 |A − B| 只剩「看见关阀」的那点延迟，"
            "落在容差哪一侧取决于时序。这是容差的固有边界，不是可以调掉的东西",
        ),
    ),
    Case(
        name="S50 同一喷头被三人轮流用：第三场独立成账",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(40, "release", 1),
            Step(100, "check_in", 1, (1002, "B")),
            Step(110, "valve_open", 1),
            Step(130, "valve_close", 1),
            Step(140, "release", 1),
            Step(200, "check_in", 1, (1003, "C")),
            Step(210, "valve_open", 1),
            Step(230, "valve_close", 1),
            Step(240, "release", 1),
        ],
        expect=Expect(
            session_index=2, sessions=3, segments=1, flows=[20], sources=["measured"],
            complete=True, assigned_source="inferred",
            note="同一扇门一天里被三个人轮流用：三场会话各自独立，起点分别是三次刷卡的"
            "时刻，谁都不占谁的便宜",
        ),
    ),
    Case(
        name="S51 设备钟恒偏（自然漂移量级）：段时长不受影响",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(40, "release", 1),
        ],
        drift=8.5,
        calibrate="frame",
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            assigned_source="inferred",
            note="设备钟恒快 8.5 秒（实测的自然漂移量级）：段长取的是同一口钟两个读数之差，"
            "固定偏移在里面相减抵消，所以仍精确到秒",
        ),
    ),
    Case(
        name="S52 拨钟发生在我们离线期间：段靠交叉校验自证，起点不受影响",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, online=False),
            Step(30, "noop", 1, (), 600.0),
            Step(40, "valve_close", 1),
            Step(50, "noop", 1, online=True),
            Step(60, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[39], sources=["observed"], complete=False,
            assigned_source="inferred",
            note="最难的一种组合：我们不在的那段时间里设备钟被拨了十分钟，关阀帧一回来就"
            "带着被污染的 close_time。A 变成 630 秒、与观测差 39 秒对不上，交叉校验识破后"
            "取观测差；而会话起点早在拨钟之前就锚定了，不受影响",
        ),
    ),
    Case(
        name="S53 第三扇门：门号只是个编号，不参与任何推导",
        steps=[
            Step(0, "check_in", 3, (1003, "C")),
            Step(10, "valve_open", 3),
            Step(30, "valve_close", 3),
            Step(40, "release", 3),
        ],
        door_count=3,
        expect=Expect(
            door_no=3, sessions=1, segments=1, flows=[20], sources=["measured"],
            complete=True, assigned_source="inferred",
            note="多扇门各自独立记账，门号不参与任何推导——它只决定这一行属于哪个喷头",
        ),
    ),
    Case(
        name="S54 离线期间有人进场：全貌帧不记迁移，但会话与段照常成立",
        steps=[
            Step(20, "noop", 1, online=False),
            Step(30, "check_in", 1, (1001, "A")),
            Step(40, "valve_open", 1),
            Step(50, "noop", 1, online=True),
            Step(60, "valve_close", 1),
            Step(70, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            assigned_source="inferred",
            note="我们离线的那段时间里有人进场、开阀。恢复时推的全貌帧只答「现在什么样」，"
            "所以不记那条进门迁移——记了会摆到重连这一刻。但会话照常从 open_time 平移出"
            "真实起点，段的开阀时刻也从 in_time 平移出来，所以时长仍然精确",
        ),
    ),
    Case(
        name="S55 读库失败送来一帧空的：留痕但不改任何状态",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "hide_all", 1),
            Step(25, "show_all", 1),
            Step(30, "valve_close", 1),
            Step(40, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            assigned_source="inferred",
            note="设备读库失败时照送一条空的——那本身就是一条事实。它留痕，但不改任何"
            "门的现态、不结会话、不关段：空帧不等于所有门都空了。读库恢复后一切照旧",
        ),
    ),
    Case(
        name="S56 半路上线：人已进场并反复暂停，期间还拨了钟",
        steps=[
            Step(0, "noop", 1, online=False),
            Step(10, "check_in", 1, (1001, "A")),
            Step(20, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(40, "valve_open", 1),
            Step(45, "noop", 1, (), 200.0),
            Step(50, "valve_close", 1),
            Step(60, "noop", 1, online=True),
            Step(70, "valve_open", 1),
            Step(90, "valve_close", 1),
            Step(100, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=2, flows=[220, 20],
            sources=["derived", "measured"], complete=False,
            assigned_source="inferred",
            note="我们上线时人已经洗了一轮、且设备钟被拨过。全貌帧把会话开起来、起点从 "
                 "open_time 平移；离线期间那一轮只能从设备的痕迹推——**但设备的累计值已被"
                 "拨钟放大**（真实两段共 20 秒，它说 220 秒），推出来也就是 220 秒。"
                 "系统分辨不了这一点：库里每一处痕迹都在说 220。它能做的是标明这段是推的、"
                 "并把完整性置假，让统计自己决定信不信",
        ),
    ),
    Case(
        name="S57 库停留在很久以前的占用行（浴室 App 没在跑）",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, online=False),
            Step(5000, "noop", 1, online=True),
        ],
        poll=False,
        expect=Expect(
            session_index=1, sessions=2, segments=1, flows=[0], sources=["unclosed"],
            complete=True, assigned_source="observed", span="gap",
            note="浴室 App 停了、我们的软件也不在，库停在一小时前那一行上。上线时看到的"
            "是**陈旧的占用**：缺口 4980 秒远超一次占用的物理上限，所以前一场在我们最后"
            "看见它的时刻收尾，本帧另起一场——起点只能是观测时刻（陈旧行的 open_time 被"
            "判据挡在外面），跨度标成 gap。真实的人早就走了，我们如实说不出来他什么时候走的",
        ),
    ),
    Case(
        name="S58 刚刷卡就卡住不释放：会话一直开着，但库里那一行始终是同一行",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(3000, "noop", 1),
        ],
        poll=False,
        expect=Expect(
            session_index=1, sessions=2, segments=0, complete=True,
            assigned_source="observed", span="gap",
            note="学生刷了卡，浴室 App 就此卡住、永不释放。库里的行一动不动，我们只能看到"
            "两帧——中间三小时**没有任何证据**。缺口超过一次占用的上限，所以切开：前一场"
            "在我们最后看见它的时刻收尾，新的那场只取观测时刻当起点",
        ),
    ),
    Case(
        name="S59 卡在出水态期间我们重连：那一段水没有任何长度证据",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, online=False),
            Step(3000, "noop", 1, online=True),
        ],
        poll=False,
        expect=Expect(
            session_index=1, sessions=2, segments=1, flows=[0], sources=["unclosed"],
            complete=True, assigned_source="observed", span="gap",
            note="阀门卡在开的位置：in_time 一直是那个值、close_time 永远不来。这一段水的"
            "长度**我们拿不到**——没有任何一帧能证明它什么时候停过。所以给 0、标 gap，"
            "不去拿『到现在为止』当长度（那会随重连次数一直涨）",
        ),
    ),
    Case(
        name="S60 卡住之后设备重启把它清零：那一场终于收尾",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, online=False),
            Step(3000, "release", 1),
            Step(3010, "noop", 1, online=True),
        ],
        poll=False,
        expect=Expect(
            sessions=1, segments=1, flows=[0], sources=["observed"], complete=True,
            note="设备重启后把那一行清零了，我们上线时看到的是空闲。于是那一场在我们最后"
            "看见它的时刻收尾——真实释放发生在三小时前，但那条证据我们永远拿不到",
        ),
    ),
    Case(
        name="S61 APP 升级期间有人来洗过又走了：那段使用我们永远不知道",
        steps=[
            Step(0, "noop", 1, online=False),
            Step(10, "check_in", 1, (1001, "A")),
            Step(20, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(40, "release", 1),
            Step(50, "noop", 1, online=True),
            Step(60, "noop", 1),
        ],
        expect=Expect(
            sessions=0,
            note="升级期间有人完整地洗了一场、然后走了。升级完成后我们看到的是一行空的行"
            "——**那段使用我们永远不会知道**。它不是丢失，是从未获得：没有任何一帧记过它",
        ),
    ),
    Case(
        name="S62 APP 升级期间有人进来洗：升级后接得上，缺的那一块在设备值里",
        steps=[
            Step(0, "noop", 1, online=False),
            Step(10, "check_in", 1, (1001, "A")),
            Step(20, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(35, "valve_open", 1),
            Step(40, "noop", 1, online=True),
            Step(60, "valve_close", 1),
            Step(70, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[25], sources=["measured"], complete=False,
            assigned_source="inferred",
            note="升级完成时他正在洗第二段。全貌帧带着完整的设备读数：起点从 open_time 平移、"
            "这一段的开阀时刻从 in_time 平移，所以这 25 秒是精确的；升级期间那第一段的 10 秒"
            "只在设备的累计值里（35 秒），段表里没有——完整性地标假，如实说出缺了哪一块",
        ),
    ),
    Case(
        name="S63 设备钟被拨慢一整年：偏差以天计，但起点与段仍要对",
        steps=[
            Step(0, "noop", 1, (), -31_536_000.0),
            Step(10, "check_in", 1, (1001, "A")),
            Step(20, "valve_open", 1),
            Step(40, "valve_close", 1),
            Step(50, "release", 1),
        ],
        calibrate="frame",
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            assigned_source="inferred",
            note="设备钟被拨慢一整年。偏差只是加减法里的一个常数，**只要它已知**（帧带读数"
            "当场给出），起点与段就都对——与偏差的大小无关。这正是校时那一层的意义："
            "它管的不是偏差多大，而是它什么时候被知道",
        ),
    ),
    Case(
        name="S65 一场会话里开关十次：十段各自独立",
        steps=[Step(0, "check_in", 1, (1001, "A"))]
        + [
            step
            for i in range(10)
            for step in (Step(10 + i * 20, "valve_open", 1), Step(20 + i * 20, "valve_close", 1))
        ]
        + [Step(300, "release", 1)],
        expect=Expect(
            sessions=1, segments=10, flows=[10] * 10, sources=["measured"] * 10,
            complete=True,
            note="反复开关十次：每停一次就是独立一段，设备逐段累加、我们逐段取那两个读数"
            "之差。十段全精确，合计与设备累计值一致——段数的多少不影响任何一段的准确性",
        ),
    ),
    Case(
        name="S66 观测缺口恰好 1200 秒：判据用「大于」，恰好不算断层",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "noop", 1, online=False),
            Step(1211, "noop", 1, online=True),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[1200], sources=["observed"], complete=False,
            note="缺口恰好 1200 秒：设备本该在满 1200 那一刻释放它，而那一刻我们不在，"
            "所以只看到一个跨缺口的观测差——恰好压在上界的边界上被截断。缺口的判据是"
            "「大于」而不是「不小于」：恰好 1200 不算断层，会话没被切开",
        ),
    ),
    Case(
        name="S68 门停在孤立占用上，之后有人正常刷卡",
        steps=[
            Step(10, "valve_sync_open", 1),
            Step(20, "check_in", 1, (1001, "A")),
            Step(30, "valve_open", 1),
            Step(50, "valve_close", 1),
            Step(60, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[20], sources=["measured"], complete=True,
            note="前一段是水控自愈留下的孤立占用（有 in_time、无 open_time、无人认领），"
            "它不进占用判据、没有会话。真正的用户刷卡时设备清空了 in_time，所以那段"
            "无人认领的水一秒都不会算到新人头上",
        ),
    ),
    Case(
        name="S69 证件号为空、只有账户号：人从账户号认，换人照样分得开",
        steps=[
            Step(0, "check_in", 1, (0, "", "ACC-A")),
            Step(10, "valve_open", 1),
            Step(30, "valve_close", 1),
            Step(40, "check_in", 1, (0, "", "ACC-B")),
            Step(50, "valve_open", 1),
            Step(70, "valve_close", 1),
            Step(80, "release", 1),
        ],
        expect=Expect(
            session_index=1, sessions=2, segments=1, flows=[20], sources=["measured"],
            complete=True,
            note="两个人都不带证件号、只有账户号。换人判据因此要先退回账户号——少这一步，"
                 "一条只有 acc_no 的帧会被当成无人认领，两场会话就粘成一场",
        ),
    ),
    Case(
        name="S71 两个人先后刷卡、都没开阀就走：两笔空账",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(100, "check_in", 1, (1002, "B")),
            Step(200, "release", 1),
        ],
        expect=Expect(
            session_index=1, sessions=2, segments=0, complete=True,
            note="等待态换人：从状态矩阵上看是 waiting→waiting，看不出任何异常，但那是两个"
                 "人、两笔账。两场都没有出水，所以都没有段——空账也要记，它回答的是"
                 "『谁在什么时候来过』",
        ),
    ),
    Case(
        name="S72 占用中被打成管控停用：状态优先级说了算",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(20, "set_managed", 1),
            Step(30, "noop", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[10], sources=["observed"], complete=False,
            note="管控停用只写 status=3，占用字段一个字不动——库里那一行仍然带着 in_time 与"
                 "证书号，看起来还在洗。但派生状态优先看 status：它变成 disabled 即离开占用态，"
                 "会话随之收尾；段没有 close_time，只能按观测差取",
        ),
    ),
    Case(
        name="S73 观测跨度越过上限被截断：必须与真实的 1200 秒分开",
        steps=[
            Step(0, "check_in", 1, (1001, "A")),
            Step(10, "valve_open", 1),
            Step(600, "hide_all", 1),
            Step(605, "show_all", 1),
            Step(1300, "release", 1),
        ],
        poll=False,
        expect=Expect(
            sessions=1, segments=1, flows=[1200], sources=["capped"], complete=False,
            note="那一段水的观测跨度是 1290 秒，越过一次占用的物理上限，被截到 1200。"
                 "**截断过的必须与真实的 1200 秒分开**——否则统计会把『我们不知道有多长』"
                 "与『它就洗了 1200 秒』当成同一回事。中间那一帧（读到空）把缺口压在 700 秒，"
                 "所以会话没被切开，段才走到这一步",
        ),
    ),
    Case(
        name="S74 冷启动接手：人已洗到一半并暂停，从设备留下的痕迹推回那一段",
        steps=[
            Step(0, "noop", 1, online=False),
            Step(10, "check_in", 1, (1001, "A")),
            Step(20, "valve_open", 1),
            Step(50, "valve_close", 1),
            Step(60, "noop", 1, online=True),
            Step(70, "release", 1),
        ],
        expect=Expect(
            sessions=1, segments=1, flows=[30], sources=["derived"], complete=False,
            assigned_source="inferred",
            note="我们上线时人已经洗了三十分钟、正停在暂停态。in_time 早被清空，但设备留下了"
                 "『关阀时刻』与『累计出水秒数』，两者相减就是那一段的开阀时刻——所以它的长度是"
                 "**从设备的痕迹里推回来的**，不是我们看出来的。完整性地因此恒为假："
                 "推出来的长度**就是**设备那个值，拿它证明自己完整是循环论证",
        ),
    ),
    Case(
        name="S75 有偏差时推回那一段：减法必须在同一条轴上做完",
        steps=[
            Step(0, "noop", 1, online=False),
            Step(10, "check_in", 1, (1001, "A")),
            Step(20, "valve_open", 1),
            Step(50, "valve_close", 1),
            Step(60, "noop", 1, online=True),
            Step(70, "release", 1),
        ],
        drift=600.0,
        calibrate="frame",
        expect=Expect(
            sessions=1, segments=1, flows=[30], sources=["derived"], complete=False,
            assigned_source="inferred",
            note="与 S74 同一件事，只是设备钟快了十分钟。**减法必须在服务端轴上做**——"
                 "在设备轴上减完再当成服务端轴用，推出来的时刻会跑到未来、被区间检查丢掉，"
                 "结果是这一段整个消失（不报错，只是没段）",
        ),
    ),
]


# ── 跑与比对 ──────────────────────────────────────────────────────────────

#: 场景里用的别名 → BathApp 的真实方法（带参数）
ALIASES = {
    "valve_sync_open": ("valve_sync", (True,)),
    "valve_sync_close": ("valve_sync", (False,)),
}


def run_case(case: Case):
    steps = []
    for s in case.steps:
        if s.action == "dummy_noop":
            # 只用来切换在线状态或推进时间，不写库
            steps.append(Step(s.at, "noop", s.door_no, (), s.jump, s.online, s.network, s.calibrate))
        elif s.action in ALIASES:
            name, args = ALIASES[s.action]
            steps.append(Step(s.at, name, s.door_no, args, s.jump, s.online, s.network, s.calibrate))
        else:
            steps.append(s)
    return run_scenario(
        steps,
        drift=case.drift,
        known_skew=case.known_skew,
        link_gaps=case.link_gaps,
        door_count=case.door_count,
        auto_close_time=case.auto_close_time,
        calibrate=case.calibrate,
        server_restarts=case.server_restarts,
        outbox_max=case.outbox_max,
        poll=case.poll,
    )


def compare(case: Case, deriver) -> list[str]:
    """返回失败项；空列表表示通过。"""
    e = case.expect
    problems: list[str] = []
    sessions = [s for s in deriver.sessions if s.door_no == e.door_no]

    if e.sessions is not None and len(sessions) != e.sessions:
        problems.append(f"会话数 {len(sessions)} != {e.sessions}")

    # 逐项检查的是第 session_index 条会话（默认第一条）——换人场景里一个门会有两条，
    # 要看后一条就得指出来。
    target = sessions[e.session_index] if len(sessions) > e.session_index else None

    if target is not None and e.segments is not None:
        got = len(target.segments)
        if got != e.segments:
            problems.append(f"段数 {got} != {e.segments}")

    if target is not None and e.flows is not None:
        got = [s.flow_s for s in target.segments]
        if len(got) != len(e.flows):
            problems.append(f"段时长项数 {len(got)} != {len(e.flows)}")
        else:
            for i, (g, w) in enumerate(zip(got, e.flows)):
                if abs(g - w) > SLACK:
                    problems.append(f"第{i + 1}段时长 {g} != {w}（容许 ±{SLACK}）")

    if target is not None and e.sources is not None:
        got = [s.source for s in target.segments]
        if got != e.sources:
            problems.append(f"来源标记 {got} != {e.sources}")

    if target is not None and e.complete is not None:
        if target.segments_complete != e.complete:
            problems.append(f"段完整性 {target.segments_complete} != {e.complete}")

    if target is not None and e.assigned_source is not None:
        if target.assigned_source != e.assigned_source:
            problems.append(f"起点来源 {target.assigned_source} != {e.assigned_source}")

    if target is not None and e.span is not None:
        if target.observed_span != e.span:
            problems.append(f"观测连续性 {target.observed_span} != {e.span}")

    return problems


def check_cleaning() -> list[str]:
    """清洗规则检查：畸形载荷该丢的丢、该收敛的收敛。

    这些不是场景——它们不涉及时间推进，只是问"一行载荷进得来吗"。判据与生产
    service._clean / _as_door_no 同源。
    """
    problems: list[str] = []

    def row(**kwargs):
        return row_from_payload(kwargs)

    # 门号非法：丢整行（它决定这一行属于哪个喷头，不能猜）
    for bad in ({"door_no": 0}, {"door_no": 49}, {"door_no": 3.7}, {"door_no": "x"},
                {"door_no": True}, {"door_no": None}, {}):
        if row(**bad) is not None:
            problems.append(f"非法门号未被丢弃：{bad}")

    # 门号合法：保留；能精确表示的浮点与纯数字串都接受
    for good in (1, 48, 3.0, "3"):
        if row(**{"door_no": good}) is None:
            problems.append(f"合法门号被丢弃：{good!r}")

    # 数值字段收敛到合法域
    cleaned = row(**{"door_no": 1, "card_no": -5, "bath_time_s": -1})
    if cleaned is not None and (cleaned.cardno != 0 or cleaned.bath_time != 0):
        problems.append("负卡号或负时长未被收敛到 0")

    cleaned = row(**{"door_no": 1, "bath_time_s": 999_999})
    if cleaned is not None and cleaned.bath_time != MAX_BATH_S:
        problems.append("超上界的时长未被夹住")

    # 坏时间置空，且不波及同一行里合法的字段
    cleaned = row(**{"door_no": 1, "open_time": "not-a-time",
                     "in_time": "2026-10-03T00:00:00.000Z"})
    if cleaned is not None and (cleaned.open_time != "" or not cleaned.in_time):
        problems.append("脏时间字段未被置空，或波及了合法的字段")

    return problems


def check_idempotence() -> list[str]:
    """重复投递检查：同一个 snapshot_id 只处理一次。

    设备在收到 ack 之前会重发同一条（连接断开、服务端重启都会触发），而"重发"与
    "它确实又报了一次"在协议上是同一件事。生产靠 `door_snapshot` 的唯一索引挡住它。
    """
    problems: list[str] = []
    deriver = Deriver()
    frame = Frame(
        captured_at=0.0,
        rows=[Row(door_no=1, cardno=1001, certno="A", open_time="20261003000000")],
        source="db_watch",
        snapshot_id="dup-1",
    )
    for _ in range(3):
        deriver.feed(frame)
    sessions = [s for s in deriver.sessions if s.door_no == 1]
    if len(sessions) != 1:
        problems.append(f"同一 snapshot_id 投递三次，开出 {len(sessions)} 条会话")
    return problems


def check_duplicate_door() -> list[str]:
    """同一帧里重复门号：取最后一条（与生产的 `by_door` 一致）。

    设备按门号整行读取，重复只可能来自拼装错误。取后者与"后来者覆盖"的直觉一致；
    取前者则会让一条陈旧读数压住真实读数。
    """
    problems: list[str] = []
    deriver = Deriver()
    deriver.feed(
        Frame(
            captured_at=0.0,
            rows=[
                Row(door_no=1, cardno=0, certno="", open_time=""),
                Row(door_no=1, cardno=1001, certno="A", open_time="20261003000000"),
            ],
            source="db_watch",
            snapshot_id="dup-door",
        )
    )
    sessions = [s for s in deriver.sessions if s.door_no == 1]
    if len(sessions) != 1:
        problems.append(f"同一帧重复门号：期望开出一条会话，实际 {len(sessions)} 条")
    elif sessions[0].cert_no != "A":
        problems.append(f"同一帧重复门号未取最后一条：占用者是 {sessions[0].cert_no!r}")
    return problems


def describe(deriver, door_no: int = 1) -> str:
    sessions = [s for s in deriver.sessions if s.door_no == door_no]
    if not sessions:
        return "    无会话"
    lines = []
    for i, s in enumerate(sessions, 1):
        segs = "，".join(f"{g.flow_s}s/{g.source}" for g in s.segments) or "无段"
        lines.append(
            f"    会话{i}: 起点={s.assigned_at} ({s.assigned_source})"
            f" 跨度={s.observed_span} 段=[{segs}]"
            f" 时长={s.flow_s} 设备值={s.device_flow_s}"
            f" 完整={s.segments_complete}"
        )
    return "\n".join(lines)


def main() -> int:
    total = 0
    failed = 0
    print("=" * 78)
    print("时间轴与口径 · 参考模型场景验证")
    print("=" * 78)
    for case in CASES:
        total += 1
        deriver, frames = run_case(case)
        problems = compare(case, deriver)
        status = "通过" if not problems else "失败"
        if problems:
            failed += 1
        print(f"\n【{case.name}】 {status}   帧数={len(frames)}")
        print(f"    预期：{case.expect.note}")
        print(describe(deriver, case.expect.door_no))
        for p in problems:
            print(f"    ✗ {p}")

    print("\n" + "=" * 78)
    print(f"共 {total} 个场景，通过 {total - failed}，失败 {failed}")
    cleaning = check_cleaning()
    idempotence = check_idempotence()
    duplicates = check_duplicate_door()
    print(f"清洗规则检查：{'通过' if not cleaning else '失败'}")
    print(f"重复投递检查：{'通过' if not idempotence else '失败'}")
    print(f"重复门号检查：{'通过' if not duplicates else '失败'}")
    for problem in cleaning + idempotence + duplicates:
        print(f"    ✗ {problem}")
    print("=" * 78)
    return 1 if failed else 0


if __name__ == "__main__":
    # dummy action：只推进时间/切换在线状态，不触碰数据库
    from model import BathApp  # noqa: E402

    setattr(BathApp, "noop", lambda self, door_no, *a: None)
    raise SystemExit(main())
