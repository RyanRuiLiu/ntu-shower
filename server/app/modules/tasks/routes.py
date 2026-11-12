"""后台任务登记域的 API：用户读四项状态、增删某个学号在名单里的那一行。

## 两道分开的门

- **能力位**（`tasks`）决定谁调得动登记端点。名单是不分设备的全局配置，所以判定走
  `require_capability` 而不是 `enforce`——作用域这一维对它不成立，理由见 capabilities。
- **管理员设的 `*_enabled`** 决定登记的人到底会不会被服务。本域**没有任何端点能改它**，
  用户碰不到。

另有第三道门 [admin_router]（admin 能力位）：后台任务的当前态看板，只读、不改任何东西。

## 为什么写入口是 async

写完名单要让后台任务的定时器按新名单重算，而 `sessions_scheduler.reload()` 内部取
`asyncio.get_running_loop()`；同步端点在 FastAPI 里跑线程池，那里没有运行中的循环。
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.problems import TASK_NOT_FOUND, ApiProblemError
from app.db.deps import get_db
from app.modules.access.capabilities import Capability
from app.modules.access.dependencies import require_admin, require_capability, require_identity
from app.modules.access.schemas import IdentityRecord
from app.modules.config import store as config_store
from app.modules.extend_hours import service as extend_hours_service
from app.modules.sessions import scheduler as sessions_scheduler

from . import schemas, service
from .schemas import TaskMembershipRequest, TaskView
from .spec import TASKS, TASKS_BY_KEY

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])

#: 运维可见性：概览页的后台任务卡。与登记域同一模块，但走 admin 能力位——
#: 它回答"现在系统在替谁做什么"，是管理面的问题，不涉及任何名单改动。
admin_router = APIRouter(
    prefix="/api/v1/background-tasks",
    tags=["tasks"],
    dependencies=[Depends(require_admin)],
)


@admin_router.get("/status", response_model=schemas.BackgroundTasksStatus)
def background_tasks_status(session: Session = Depends(get_db)) -> schemas.BackgroundTasksStatus:
    """后台任务的当前态：开关与名单、延长中的设备、调度器的即时计划。

    纯只读的组合：三处（全局配置、extend_hours 的落库状态、调度器内存里的定时器）
    各自公开自己的只读读数，这里不做任何判断——判据只有一个出处，看板只报告状态。
    """
    config = config_store.global_effective(session)
    tasks = []
    for spec in TASKS:
        roster = config[spec.students_key]
        tasks.append(
            schemas.BackgroundTaskStatus(
                key=spec.key,
                title=spec.label,
                enabled=bool(config[spec.enabled_key]),
                roster=len(roster) if isinstance(roster, dict) else 0,
            )
        )
    return schemas.BackgroundTasksStatus(
        tasks=tasks,
        extending=[schemas.ExtendingDevice.model_validate(row) for row in extend_hours_service.current_shifts(session)],
        scheduled=[schemas.ScheduledAction.model_validate(row) for row in sessions_scheduler.snapshot(session)],
    )


@router.get("")
def list_tasks(
    cert_no: str = Query(min_length=1, description="要查的学号"),
    caller: IdentityRecord = Depends(require_identity),
    session: Session = Depends(get_db),
) -> list[TaskView]:
    """四项后台任务的状态；`joined` 按传入的学号回答。

    `enabled` 是管理员设的全局开关，**只读**——它关着的时候登记照样成立，只是不生效。
    """
    require_capability(caller, Capability.TASKS)
    return service.list_tasks(session, cert_no)


@router.put("/{task}")
async def set_membership(
    task: str,
    req: TaskMembershipRequest,
    caller: IdentityRecord = Depends(require_identity),
    session: Session = Depends(get_db),
) -> TaskView:
    """登记（`joined=true`）或取消登记（`joined=false`）这个学号。

    **不看 `enabled`**：登记与生效是两层，管理员没开也允许登记。写完立刻让后台任务的
    定时器按新名单重算——它们原本是按旧名单挂着的。
    """
    require_capability(caller, Capability.TASKS)
    if task not in TASKS_BY_KEY:
        raise ApiProblemError(TASK_NOT_FOUND, f"后台任务 {task}")
    view = service.set_membership(session, task, req.cert_no, req.password, req.joined)
    sessions_scheduler.reload()
    return view
