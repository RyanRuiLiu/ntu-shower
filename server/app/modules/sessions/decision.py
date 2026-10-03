"""会话维护的判据：什么时候该对哪个喷头动手。

**纯函数，不做任何 IO**，所以判据可以逐条单测——这里出错的代价是"在错的时候下了一条
改数据的命令"，比机制出错严重得多。

三个功能各管一种状态，只有**已暂停**同时落进两条判据（会话续期与自动结算）。两者由
**起算点**分开：自动结算从关阀那一刻算（默认 60 秒），会话续期从分配时刻算（默认 1140
秒）。所以只有"会话快满 20 分钟时才关阀"这一种情形会撞上，而那时**续期优先**——保住
他这一场会话，比结束它更保守。`decide` 取最早的那条，同刻时按 [FEATURES] 的登记顺序取，
也就是会话续期在前。

时刻一律用服务端轴的 RFC 3339 字符串；判据不碰设备轴（理由见 `core/device_clock`）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.clock import seconds_between
from app.core.protocol import JsonObject, as_int
from app.modules.doors.schemas import DoorState

FEATURE_RENEW_WAIT = "renew_wait"
FEATURE_RENEW_SESSION = "renew_session"
FEATURE_AUTO_SETTLE = "auto_settle"

#: 功能 → (开关键, 名单键, 阈值键)。
#: 名单在配置里、三份各自独立——同一个人可以只开其中一个功能。名单是"允许对谁动手"的
#: 唯一出处：不在名单里就什么都不做。
FEATURES: dict[str, tuple[str, str, str]] = {
    FEATURE_RENEW_WAIT: ("renew_wait_enabled", "renew_wait_students", "renew_wait_threshold_s"),
    FEATURE_RENEW_SESSION: ("renew_session_enabled", "renew_session_students", "renew_session_threshold_s"),
    FEATURE_AUTO_SETTLE: ("auto_settle_enabled", "auto_settle_students", "auto_settle_pause_threshold_s"),
}

#: 需要"结算之后再分配"的功能；自动结算只需要前半步。
RENEWING_FEATURES = frozenset({FEATURE_RENEW_WAIT, FEATURE_RENEW_SESSION})


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

    取自 `door_live.close_time`（正名就是「关阀时刻」，与 `REFERENCE.md` 的暂停时长口径
    同一个量）。它要经钟偏差换算，所以**设备还没有偏差记录时是空的**——真实设备连上后
    一个往返内就有（网关在 `link.hello` 之后立刻主动 ping），而且偏差跨重启持久化
    （`device_clock.load`），所以线上不会落在这个空窗里。测试里要显式建立它，否则测的
    不是线上的样子。"""
    renew_count: int
    """这一串里我们已经续过的几次。第一场是学生自己的分配，不算续。"""


@dataclass(frozen=True)
class Trigger:
    """一条该动手的判据：哪个功能、还有多久到点。"""

    feature: str
    delay_s: float


def roster(config: JsonObject, key: str) -> dict[str, str]:
    """取一份名单：`学号 → 密码`。

    **凭证跟着名单走**——三份名单各自完整，所以"在名单里"与"有凭证"是同一件事：不可能
    出现"名单里有他、却没有他的密码"那种静默不执行。代价是同一个学号开两个功能要在两份
    名单里各写一遍，那是刻意的：**配一份就读得懂一份**，不必再去别处对照。

    学号必须**逐字**对上门上报的 `cert_no`——前导零、空格对不上就是不在名单里（我们会
    静默不动手）。去 `door_state_event.cert_no` 里抄原样最稳。
    """
    value = config.get(key)
    if not isinstance(value, dict):
        return {}
    return {str(item).strip(): str(password) for item, password in value.items()}


def password_of(config: JsonObject, feature: str, cert_no: str) -> str:
    """这个功能那份名单里，这个学号的凭证；不在名单里就是空的，动作不会下发。"""
    roster_key = FEATURES[feature][1]
    return roster(config, roster_key).get(cert_no, "")


def decide(candidate: Candidate, config: JsonObject, now: str) -> Trigger | None:
    """这个喷头此刻下一个该动手的是什么；没有则 None。

    返回值的 `delay_s` 是"还有多久到点"，**负数表示已经过点了**——调用方仍然要动手，
    因为到点只说明"该看一眼了"，成不成立由 [recheck] 判。
    """
    hits: list[Trigger] = []

    for feature, (enabled_key, students_key, threshold_key) in FEATURES.items():
        if not config.get(enabled_key):
            continue
        if candidate.cert_no not in roster(config, students_key):
            continue
        threshold = as_int(config[threshold_key])
        started = _started_at(candidate, feature, config)
        if started is None:
            continue
        hits.append(Trigger(feature=feature, delay_s=threshold - seconds_between(started, now)))

    if not hits:
        return None
    return min(hits, key=lambda hit: hit.delay_s)


def _started_at(candidate: Candidate, feature: str, config: JsonObject) -> str | None:
    """这条判据从哪一刻起算；状态对不上或次数用尽就返回 None（不适用）。

    自动结算之外的两个都从**分配时刻**起算，因为浴室 App 那两条上限（20 分钟整场、
    300 秒无水）都是在它上面走的。
    """
    if feature == FEATURE_RENEW_WAIT:
        # 等待开阀：他还没出水，浴室端会在 300 秒后把这场会话放掉。续它。
        if candidate.state is not DoorState.WAITING:
            return None
        if candidate.renew_count >= as_int(config["renew_wait_max_count"]):
            return None  # 续满了：停止续期，让浴室端正常释放
        return candidate.assigned_at
    if feature == FEATURE_RENEW_SESSION:
        # 占用中：他在淋浴中。续它，别让浴室端在 20 分钟上把这场掐断；次数无上限。
        if candidate.state not in (DoorState.BATHING, DoorState.PAUSED):
            return None
        return candidate.assigned_at
    # 自动结算：他关了阀且一直没再开，替他结账。从关阀那一刻起算。
    if candidate.state is not DoorState.PAUSED or not candidate.paused_at:
        return None
    return candidate.paused_at


def recheck(fresh: Candidate | None, expected: Candidate, trigger: Trigger, config: JsonObject) -> str | None:
    """到点之后再确认一遍它是不是还成立。返回不通过的理由，通过则 None。

    定时器是**到点提醒**，不是到时执行——到点那一刻现态可能早就变了（人走了、门被别人
    占了、名单刚被改）。逐条对下来，任一条不成立就放弃这一次，**不告警**：那是正常的
    "到时候情况变了"。
    """
    if fresh is None:
        return "会话已结束"
    if fresh.session_id != expected.session_id:
        return "换了会话"
    if fresh.cert_no != expected.cert_no:
        return "换了人"
    if fresh.state is not expected.state:
        return f"状态已变（{expected.state.value} → {fresh.state.value}）"
    return still_allowed(fresh.cert_no, trigger.feature, config)


def still_allowed(cert_no: str, feature: str, config: JsonObject) -> str | None:
    """名单与开关是不是还允许对这个学号做这个功能。

    下单之前还要再问一次：复查到下单之间隔着一次取数与一次排队，名单可能刚被改。
    """
    enabled_key, students_key, _threshold_key = FEATURES[feature]
    if not config.get(enabled_key):
        return "功能已关"
    if cert_no not in roster(config, students_key):
        return "不在名单里"
    return None
