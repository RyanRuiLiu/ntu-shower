"""后台任务登记表：哪几个、叫什么、开关与名单各是哪两个配置键。

四个后台任务各自的配置规格在 `config/spec.py`（`*_enabled` 开关与 `*_students` 名单），
判据在前三个的 `sessions/decision.py` 与 `extend_hours`。这张表只回答"有哪几个后台任务、
看板上叫什么、键是哪两个"——它**不参与判据**，也是看板与配置页之外唯一的任务清单出处。

## 键不在这里写第二遍

`key` / `enabled_key` / `students_key` 三样，前三个任务直接从 `sessions/decision.py` 的
`TASKS` 派生——判据的登记表已经持有，再抄一遍就是同一件事的第二份，改一处必漏一处。
`extend_hours` 不在 `TASKS` 里，而且**有意**不在：它的判据是设备级的（问的是"这台
设备上还有没有名单里的人"），塞进那张表要放一个不成立的形状（见 extend_hours/service.py
的模块注释）。四个任务里只有它这一个的键写在这里。

中文名不另写：与 `config/spec.py` 的 `GROUPS` 同名同义，直接取那份的标题。
`tests/test_tasks.py` 把这几处的对应关系钉住。

"""

from dataclasses import dataclass

from app.modules.config.spec import GROUPS
from app.modules.sessions import decision

#: 任务 key → 中文名。取自配置页的分组标题——同一个概念只许一个中文说法。
_GROUP_TITLES: dict[str, str] = {group.key: group.title for group in GROUPS}


@dataclass(frozen=True)
class TaskSpec:
    """一条看板登记行：看板与配置页共用的任务元数据。"""

    key: str
    """任务标识。与 `config/spec.py` 的 `GROUPS` 同名，也是它那两份配置键的前缀。"""

    label: str
    """中文名。取自同一份 `GROUPS` 的标题。"""

    enabled_key: str
    """全局开关。**用户碰不到它**——它决定这个功能现在到底跑不跑。"""

    students_key: str
    """名单键。内容是 `学号=浴室口令` 的映射，在管理台配置页编辑。"""


#: [extend_hours] 不在 `TASKS` 里，所以它的三个键只有这一处。
_EXTEND_HOURS_KEY = "extend_hours"


def _spec(key: str, enabled_key: str, students_key: str) -> TaskSpec:
    return TaskSpec(key, _GROUP_TITLES[key], enabled_key, students_key)


#: 登记顺序即接口返回顺序，与配置页「后台任务」那个页签的顺序一致。
TASKS: tuple[TaskSpec, ...] = (
    *(_spec(spec.key, spec.enabled_key, spec.students_key) for spec in decision.TASKS),
    _spec(_EXTEND_HOURS_KEY, "extend_hours_enabled", "extend_hours_students"),
)

TASKS_BY_KEY: dict[str, TaskSpec] = {spec.key: spec for spec in TASKS}
