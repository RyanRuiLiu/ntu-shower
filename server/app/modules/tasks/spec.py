"""可由用户自助登记的后台任务：哪几个、叫什么、对用户怎么说、读写哪两份配置键。

## 这张表为什么存在

四个后台任务各自的配置规格在 `config/spec.py`（`*_enabled` 开关与 `*_students` 名单），
判据在前三个的 `sessions/decision.py` 与 `extend_hours`。但"哪几个可以由用户自己登记、
在设置页上怎么称呼、怎么说明它"这件事原先没有出处——各处都只知道自己那一半。接口要列出
它们，就需要这一处。它**只描述可登记的任务，不参与判据**：判据那条路一个字节都不用改。

## 两份文案，一份数值

管理员在配置页读的那份说明留在 `config/spec.py`，读者是管理员，立足点是"对名单里的人
做什么"。用户在小程序设置页读的这一份在这里，读者是他自己。**两份是刻意的**：受众不同，
措辞的立足点就不同，硬合并成一份两边都不好读。

但**数值只有一份**。文案里出现的时长与时刻一律是占位符（`{idle}` / `{threshold}` / …），
取值来自当前生效的配置与已登记的设备侧常量，运行时填进去。写死在文案里必然出错——
`auto_settle_pause_threshold_s` 与 `renew_*_threshold_s` 都是可配的，管理员改一次阈值，
用户在设置页读到的就成了假说明，而两处都不会报错。

## 前三个的键不在这里写第二遍

`key` / `enabled_key` / `students_key` 这三样，`sessions/decision.py` 的 `FEATURES`
已经持有——那是判据的登记表。在这里再抄一遍就是同一件事的第二份，改一处必漏一处，
所以直接从它派生。

`extend_hours` 不在 `FEATURES` 里，而且是**有意**不在：它的判据是设备级的（问的是
"这台设备上还有没有名单里的人"），塞进那张表要在里面放一个不成立的形状（见
`extend_hours/service.py` 的模块注释）。四个任务里只有它这一个的键写在这里。

中文名同理不另写：它与 `config/spec.py` 的 `GROUPS` 同名同义，直接取那份的标题。

`tests/test_tasks.py` 把这几处的对应关系钉住。
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.core.protocol import JsonObject, as_int
from app.modules.config.spec import GROUPS
from app.modules.doors.service import NO_WATER_RELEASE_S, SESSION_MAX_S
from app.modules.sessions.decision import FEATURES

#: 任务 key → 中文名。取自配置页的分组标题——同一个概念只许一个中文说法。
_GROUP_TITLES: dict[str, str] = {group.key: group.title for group in GROUPS}


def format_duration(seconds: int) -> str:
    """秒 → 句子里的中文量词；**数字与单位之间不加空格**（中文排版的常规）。

    日志与审计那边的时长是紧凑写法（`4m30s`），受众不同，所以各有一份，不必强求一致。
    """
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600}小时"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60}分钟"
    if seconds >= 60:
        return f"{seconds // 60}分{seconds % 60}秒"
    return f"{seconds}秒"


#: 用户面说明的模板。占位符由同名的取值函数填上**当前生效的数值**，见 [FILLS]。
#:
#: 写这一份时的四条规矩：
#: - **用第二人称**：读者就是用户本人，不是管理员。
#: - **主体必须说出来**：这一句讲的是"**你登记了**之后系统会替你做什么"，不是"这个功能对所有
#:   人做什么"。原先写成"开启后"，那是个没有主体的悬空条件——用户完全可能读成对全设备生效。
#:   「登记」也是界面上那个开关的说法（`我：已登记`），两边同一个词。
#: - **不用行话**："无水释放门槛""强制释放上限"是设备侧的说法，用户读不懂，要翻成它会做什么。
#: - **占位符与中文之间不留空格**。
_TEMPLATES: dict[str, str] = {
    "renew_wait": (
        "选好喷头后，如果{idle}内一直没有出水，浴室 App 会自动收回这个喷头，这次使用随之结束。"
        "你登记后，系统会在收回之前替你重新占用同一个喷头，使这次选择保持有效；"
        "从第一次选好算起，最长连续保留{limit}。"
    ),
    "renew_session": (
        "淋浴过程中，一次使用最长只能持续{ceiling}，到点会自动结算并结束。你登记后，系统会在"
        "分配满{threshold}时替你结算并重新占用同一个喷头，使淋浴得以继续。"
    ),
    "auto_settle": (
        "关阀之后如果一直没有再出水，这个喷头会持续被你占着，别人无法使用。你登记后，系统会在"
        "关阀满{threshold}时替你结算，把喷头释放出来。"
    ),
    "extend_hours": (
        "浴室 App 每天到点会自动关机，并在关机前释放全部喷头。你登记后，系统会在{at}把"
        "设备时钟拨快并清空喷头，再把名单里的人分配回去；当晚 {off} 的断电顺延到第二天。"
        "喷头全部空闲后设备时钟即恢复原值。"
    ),
}


def _renew_wait_values(config: JsonObject) -> dict[str, str]:
    return {
        "idle": format_duration(NO_WATER_RELEASE_S),
        "limit": format_duration(as_int(config["renew_wait_max_duration_s"])),
    }


def _renew_session_values(config: JsonObject) -> dict[str, str]:
    return {
        "ceiling": format_duration(SESSION_MAX_S),
        "threshold": format_duration(as_int(config["renew_session_threshold_s"])),
    }


def _auto_settle_values(config: JsonObject) -> dict[str, str]:
    return {"threshold": format_duration(as_int(config["auto_settle_pause_threshold_s"]))}


def _extend_hours_values(config: JsonObject) -> dict[str, str]:
    return {
        "at": str(config["extend_hours_at"]),
        "off": str(config["power_off_at"]),
    }


#: 任务 key → 取模板占位符值的函数。**值一律现取**：配置改一次，用户下次打开设置页读到的
#: 就是新的，不需要改文案、也不需要重新部署。
FILLS: dict[str, Callable[[JsonObject], dict[str, str]]] = {
    "renew_wait": _renew_wait_values,
    "renew_session": _renew_session_values,
    "auto_settle": _auto_settle_values,
    "extend_hours": _extend_hours_values,
}


@dataclass(frozen=True)
class TaskSpec:
    """一个可由用户自助登记的后台任务。"""

    key: str
    """任务标识。与 `config/spec.py` 的 `GROUPS` 同名，也是它那两份配置键的前缀。"""

    label: str
    """中文名。取自同一份 `GROUPS` 的标题。"""

    template: str
    """用户面说明的模板，占位符见 [FILLS]。"""

    fill: Callable[[JsonObject], dict[str, str]]
    """按当前配置产出占位符的值。"""

    enabled_key: str
    """全局开关。**用户碰不到它**——它决定这个功能现在到底跑不跑。"""

    students_key: str
    """名单。登记就是往里加一行 `学号=密码`，取消登记就是删掉那一行。"""


#: [extend_hours] 不在 `FEATURES` 里，所以它的三个键只有这一处。
_EXTEND_HOURS_KEY = "extend_hours"


def _spec(key: str, enabled_key: str, students_key: str) -> TaskSpec:
    return TaskSpec(key, _GROUP_TITLES[key], _TEMPLATES[key], FILLS[key], enabled_key, students_key)


#: 登记顺序即接口返回顺序，与配置页「服务端业务」那个页签的顺序一致。
TASKS: tuple[TaskSpec, ...] = (
    *(_spec(spec.key, spec.enabled_key, spec.students_key) for spec in FEATURES),
    _spec(_EXTEND_HOURS_KEY, "extend_hours_enabled", "extend_hours_students"),
)

TASKS_BY_KEY: dict[str, TaskSpec] = {spec.key: spec for spec in TASKS}
