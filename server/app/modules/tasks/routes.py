"""后台任务看板域的 API：概览页的后台任务当前态（admin，只读）。

名单的登记入口只有管理台配置页（业主裁定 2026-11-14：用户自助登记接口删除），
本域不再有登记端点；四份名单的增删由管理员在配置页对 `*_students` 键直接操作，
写后同样触发 `sessions_scheduler.reload()`（见 config/routes.py 的推送编排）。

看板是纯只读的组合：三处（全局配置、extend_hours 的落库状态、调度器内存里的
定时器）各自公开自己的只读读数，这里不做任何判断——判据只有一个出处，看板只
报告状态。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.modules.access.dependencies import require_admin
from app.modules.config import store as config_store
from app.modules.extend_hours import service as extend_hours_service
from app.modules.sessions import scheduler as sessions_scheduler

from . import schemas
from .spec import TASKS

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
