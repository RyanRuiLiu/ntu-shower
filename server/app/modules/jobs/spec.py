"""后台任务登记表：哪几个、叫什么、键是哪两个。

四个后台任务各自的配置规格在 `config/spec.py`（`*_enabled` 开关与 `*_students` 名单），
判据在前三个的 `sessions/decision.py` 与 `power`。这张表只回答"有哪几个后台任务、
看板上叫什么、键是哪两个"——它**不参与判据**，也是看板之外唯一的任务清单出处。

## 键不在这里写第二遍

`key` / `enabled_key` / `students_key` 三样，前三个任务直接从 `sessions/decision.py` 的
`JOBS` 派生——判据的登记表已经持有，再抄一遍就是同一件事的第二份，改一处必漏一处。
`power` 不在 `JOBS` 里，而且**有意**不在：它的判据是设备级的（问的是"这台设备上还有
没有名单里的人"），塞进那张表要放一个不成立的形状（见 power/service.py 的模块注释）。
四个任务里只有它这一个的键写在这里。

中文名不另写：与 `config/spec.py` 的 `GROUPS` 同名同义，直接取那份的标题。
`tests/test_jobs.py` 把这几处的对应关系钉住。
"""

from app.modules.config.spec import GROUPS, HOLD_ENABLED_KEY, HOLD_STUDENTS_KEY
from app.modules.sessions import decision

_TITLES: dict[str, str] = {group.key: group.title for group in GROUPS}

#: [power] 不在 `JOBS`（判据表）里，所以它的开关键只有这一处。
_POWER_KEY = "power"

#: 看板行 = (任务键, 开关键, 名单键)。登记顺序即接口返回顺序，与配置页
# 「后台任务」页签的顺序一致。名单键取 spec 的常量（跨模块引用不写字面量）。
JOBS: tuple[tuple[str, str, str], ...] = (
    *((spec.key, spec.enabled_key, spec.students_key) for spec in decision.JOBS),
    (_POWER_KEY, HOLD_ENABLED_KEY, HOLD_STUDENTS_KEY),
)
