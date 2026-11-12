"""后台任务登记：读四项的状态、增删名单里的一行。

名单是 SERVER scope 的配置（`auto_settle_students` 那些），所以一行的增删就是对**全局
配置的一个键**做读—改—写，经 [config_store.update_global]——那三步在同一把锁里，两个人
同时登记不会各自读到旧名单、后写的把先写的抹掉。

"读一份名单"不在这里实现：`sessions.decision.roster` 是它唯一那处，判据看到的名单与
这里写的必须是同一份（键与值都去两端空格）。

## 门槛是能力位，不是密码

服务端**不校验学号归属，也不校验密码真伪**。校验密码要真跑一次分配量级的命令，而浴室端
在下发命令时本来就会发现它不对，代价与收益不成比例。所以这个能力的门是
`Capability.TASKS`——授予它等于允许持有者增删任意学号的登记，理由见 capabilities 的注释。
"""

from sqlalchemy.orm import Session

from app.core.protocol import JsonObject
from app.modules.config import store as config_store
from app.modules.config.schemas import ConfigValues
from app.modules.sessions.decision import roster

from .schemas import TaskView
from .spec import TASKS, TASKS_BY_KEY, TaskSpec


def list_tasks(session: Session, cert_no: str) -> list[TaskView]:
    """四项的状态。`joined` 按**传入的学号**回答——服务端不知道这个学号是谁的。"""
    config = config_store.global_effective(session)
    return [_view(spec, cert_no.strip(), config) for spec in TASKS]


def set_membership(session: Session, task: str, cert_no: str, password: str, joined: bool) -> TaskView:
    """登记或取消登记这个学号，返回这一步之后它在该任务上的状态。

    `augment` 必须回传**完整的全局有效配置**，理由写在 [config_store.update_global]：
    `_as_overrides` 判"哪些偏离默认值"看的是递进去的那份配置本身，只回传名单那一项，
    管理员设过的开关会被这次登记顺手清掉。
    """
    spec = TASKS_BY_KEY[task]
    cert = cert_no.strip()

    def augment(config: ConfigValues) -> ConfigValues:
        students = dict(roster(config, spec.students_key))
        if joined:
            students[cert] = password.strip()
        else:
            students.pop(cert, None)
        return {**config, spec.students_key: students}

    config_store.update_global(session, augment)
    return _view(spec, cert, config_store.global_effective(session))


def describe(spec: TaskSpec, config: JsonObject) -> str:
    """按**当前生效的配置**把说明里的数值填上。

    公开而不是私有：它是"文案里的数值不写死"这件事的落点，测试要拿它验"改了阈值说明就跟着变"。
    """
    return spec.template.format(**spec.fill(config))


def _view(spec: TaskSpec, cert_no: str, config: JsonObject) -> TaskView:
    """一个任务的状态行。三处来源各一份：说明按现配置填，开关取配置，在不在名单取 [roster]。"""
    return {
        "key": spec.key,
        "label": spec.label,
        "description": describe(spec, config),
        "enabled": bool(config[spec.enabled_key]),
        "joined": cert_no in roster(config, spec.students_key),
    }
