"""后台任务的判据：什么时候该对哪个喷头动手。

**纯函数，不做任何 IO**，所以判据可以逐条单测——这里出错的代价是"在错的时候下了一条
改数据的命令"，比机制出错严重得多。

三个功能各认领**一个互不相交的状态**：等待中的预约、正在淋浴的会话、关了阀的暂停。一个
状态只有一个主人，所以同一个喷头上不会出现两条判据互相矛盾的动作——`decide` 仍取最早的
那条，但"最早"只在同一个状态内部才有意义。

**这条边界是刻意划的，不是巧合**。先前 `renew_session` 也认领 `PAUSED`，于是它与
`auto_settle` 抢同一个状态，靠"续期在前"的静态优先级分胜负。结果是"关阀后释放喷头"对
名单里的人永远走不通：洗完关阀的会话被当成"还在淋浴"续下去，喷头不释放，延长服务时间
也因为"还有未结束的会话"迟迟不拨回。关阀之后该发生什么，由 `auto_settle` 独断；续期只管
"水还在流、会话逼近浴室 App 的强制释放点"。这也正是 `renew_session_enabled` 配置说明写的
"会话处于淋浴中"。

时刻一律用服务端轴的 RFC 3339 字符串；判据不碰设备轴（理由见 `core/device_clock`）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.core.clock import seconds_between
from app.core.protocol import JsonObject, as_int
from app.models.enums import DoorState

FEATURE_RENEW_WAIT = "renew_wait"
FEATURE_RENEW_SESSION = "renew_session"
FEATURE_AUTO_SETTLE = "auto_settle"


@dataclass(frozen=True)
class TaskSpec:
    """一个后台任务的全部参数。**三个功能的差异只有这些**——判据本身是同一个。

    写成数据而不是在每个功能上各判一次，是因为下一个功能（如果真有）只该是加一条登记，
    而不是再抄一遍判据：抄第二遍的时候没人会记得同步第一遍改过的东西。
    """

    key: str
    enabled_key: str
    students_key: str
    """名单在配置里、三份各自独立——同一个人可以只开其中一个功能。名单是"允许对谁动手"的
    唯一出处：不在名单里就什么都不做；凭证也跟着这份名单走（见 [students]）。"""
    threshold_key: str
    states: tuple[DoorState, ...]
    """哪些状态下这个任务适用。"""
    start: Literal["assigned_at", "paused_at"]
    """从哪一刻起算：整场会话（浴室 App 那两条上限管着它）还是关阀那一刻。"""
    max_duration_key: str | None
    """时长上限的配置键；None 表示不限。从这一串第一场起算，到了就停。"""
    two_step: bool
    """续期是两步（先结算、等门空的那一帧再分配回同一个喷头）；自动结算只做前半步。"""


#: 三个功能的状态互不相交，所以登记顺序不决定任何胜负；保留这个顺序只是为了让"读这份表
#: 就看得见有哪几个功能"。
TASKS: tuple[TaskSpec, ...] = (
    TaskSpec(
        key=FEATURE_RENEW_WAIT,
        enabled_key="renew_wait_enabled",
        students_key="renew_wait_students",
        threshold_key="renew_wait_threshold_s",
        states=(DoorState.WAITING,),
        start="assigned_at",
        max_duration_key="renew_wait_max_duration_s",
        two_step=True,
    ),
    TaskSpec(
        key=FEATURE_RENEW_SESSION,
        enabled_key="renew_session_enabled",
        students_key="renew_session_students",
        threshold_key="renew_session_threshold_s",
        states=(DoorState.BATHING,),
        start="assigned_at",
        max_duration_key=None,
        two_step=True,
    ),
    TaskSpec(
        key=FEATURE_AUTO_SETTLE,
        enabled_key="auto_settle_enabled",
        students_key="auto_settle_students",
        threshold_key="auto_settle_pause_threshold_s",
        states=(DoorState.PAUSED,),
        start="paused_at",
        max_duration_key=None,
        two_step=False,
    ),
)

TASKS_BY_KEY: dict[str, TaskSpec] = {spec.key: spec for spec in TASKS}


@dataclass(frozen=True)
class Candidate:
    """判据的全部输入。"""

    session_id: int
    device_id: str
    door_no: int
    cert_no: str
    state: DoorState
    assigned_at: str
    """分配时刻：会话从什么时候开始。预约续期与会话续期都从它起算——**不是从开阀起算**，
    因为浴室 App 那条上限管的是整场会话。"""
    paused_at: str
    """当前这一段的**关阀时刻**；只有已暂停时有值。自动结算从它起算。

    取自 `door_live.close_time`（正名就是「关阀时刻」，与 `reference.md` 的暂停时长口径
    同一个量）。它要经钟偏差换算，所以**设备还没有偏差记录时是空的**——真实设备连上后
    一个往返内就有（网关在 `link.hello` 之后立刻主动 ping），而且偏差跨重启持久化
    （`device_clock.load`），所以线上不会落在这个空窗里。测试里要显式建立它，否则测的
    不是线上的样子。"""
    run_started_at: str
    """这一串的第一场是什么时候分配的——**学生自己那一次**，之后每一场都是我们替他续的。

    预约续期的时长上限从它起算，而不是从当前这一场的 `assigned_at`：后者每续一次就往后
    挪一次，拿它算永远到不了头。"""


@dataclass(frozen=True)
class Trigger:
    """一条该动手的判据：哪个功能、还有多久到点、当时生效的阈值是多少。

    `threshold_s` 一起带出来，是为了让它能落进审计表——配置没有历史，事后再去查"当时
    阈值多少"是查不到的，而那一行要能自己说清"为什么是这一刻"。
    """

    task: str
    delay_s: float
    threshold_s: int


def students_of(config: JsonObject, key: str) -> dict[str, str]:
    """取一份名单：`学号 → 密码`。

    **凭证跟着名单走**——三份名单各自完整，所以"在名单里"与"有凭证"是同一件事：不可能
    出现"名单里有他、却没有他的密码"那种静默不执行。代价是同一个学号开两个功能要在两份
    名单里各写一遍，那是刻意的：**配一份就读得懂一份**，不必再去别处对照。

    学号必须**逐字**对上门上报的 `cert_no`（前导零就是前导零）——对不上就是不在名单里，
    我们会静默不动手。**两端空格则一律去掉**：配置页那侧提交时已经去过一次，这里再去
    一次覆盖"直接调接口"的情形，两处的处理必须一致——同一份名单在两处解释成不同的键，
    表现是"配了却不生效"。
    """
    value = config.get(key)
    if not isinstance(value, dict):
        return {}
    return {str(item).strip(): str(password).strip() for item, password in value.items()}


def password_of(config: JsonObject, task: str, cert_no: str) -> str:
    """这个功能那份名单里，这个学号的凭证；不在名单里就是空的，动作不会下发。"""
    return students_of(config, TASKS_BY_KEY[task].students_key).get(cert_no, "")


def decide(candidate: Candidate, config: JsonObject, now: str) -> Trigger | None:
    """这个喷头此刻下一个该动手的是什么；没有则 None。

    返回值的 `delay_s` 是"还有多久到点"，**负数表示已经过点了**——调用方仍然要动手，
    因为到点只说明"该看一眼了"，成不成立由 [recheck] 判。
    """
    hits: list[Trigger] = []

    for spec in TASKS:
        if not config.get(spec.enabled_key):
            continue
        if candidate.cert_no not in students_of(config, spec.students_key):
            continue
        started = _started_at(candidate, spec, config, now)
        if started is None:
            continue
        threshold = as_int(config[spec.threshold_key])
        hits.append(Trigger(task=spec.key, delay_s=threshold - seconds_between(started, now), threshold_s=threshold))

    if not hits:
        return None
    return min(hits, key=lambda hit: hit.delay_s)


def _started_at(candidate: Candidate, spec: TaskSpec, config: JsonObject, now: str) -> str | None:
    """这条判据从哪一刻起算；状态对不上或时长用尽就返回 None（不适用）。

    **三个功能的差异全在 [TaskSpec] 里**，所以这里一处按功能的分叉都没有：适用状态、
    起算点、上限都是数据。起算点为空（已暂停却拿不到关阀时刻）就是判不了，返回 None。

    时长上限比的是 [Candidate.run_started_at] 到此刻的距离，也就是"他这一次从头到现在
    已经多久"，不是"我们续了几次"——阈值一改，按次数算出来的时长会跟着漂。
    """
    if candidate.state not in spec.states:
        return None
    if (
        spec.max_duration_key is not None
        and candidate.run_started_at
        and seconds_between(candidate.run_started_at, now) >= as_int(config[spec.max_duration_key])
    ):
        return None  # 续够了：停止续期，让浴室端正常释放
    started = candidate.assigned_at if spec.start == "assigned_at" else candidate.paused_at
    return started or None


def recheck(fresh: Candidate | None, expected: Candidate, trigger: Trigger, config: JsonObject, now: str) -> str | None:
    """到点之后再确认一遍它是不是还成立。返回不通过的理由，通过则 None。

    定时器是**到点提醒**，不是到时执行——到点那一刻现态可能早就变了（人走了、门被别人
    占了、名单刚被改）。逐条对下来，任一条不成立就放弃这一次，**不告警**：那是正常的
    "到时候情况变了"。

    **适用性要整条重算，不能只比对"变没变"**：`max_duration` 这类上限正是"挂表时还没到、
    到点已经过了"的量，而逐条比对永远看不见它——触发时合格，于是表挂上；到点执行时早已
    超限，却照样动手。这里调 [_started_at] 复算一次，所以以后往它里面再加条件，这条复查
    自动跟上，不必在另一处补一个特例。
    """
    if fresh is None:
        return "会话已结束"
    if fresh.session_id != expected.session_id:
        return "换了会话"
    if fresh.cert_no != expected.cert_no:
        return "换了人"
    if fresh.state is not expected.state:
        return f"状态已变（{expected.state.value} → {fresh.state.value}）"
    if _started_at(fresh, TASKS_BY_KEY[trigger.task], config, now) is None:
        return "判据已不适用（起算点为空或时长上限用尽）"
    return still_allowed(fresh.cert_no, trigger.task, config)


def still_allowed(cert_no: str, task: str, config: JsonObject) -> str | None:
    """名单与开关是不是还允许对这个学号做这个功能。

    下单之前还要再问一次：复查到下单之间隔着一次取数与一次排队，名单可能刚被改。
    """
    spec = TASKS_BY_KEY[task]
    if not config.get(spec.enabled_key):
        return "功能已关"
    if cert_no not in students_of(config, spec.students_key):
        return "不在名单里"
    return None
