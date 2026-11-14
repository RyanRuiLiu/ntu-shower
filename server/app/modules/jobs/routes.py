"""后台任务看板域的 API：概览页的后台任务当前态（`jobs:read`，只读）。

名单的登记入口只有管理台配置页，本域不设登记端点；四份名单的增删由管理员在
配置页对 `*_students` 键直接操作，写后同样触发 `sessions_scheduler.reload()`
（见 config/routes.py 的推送编排）。

看板是纯只读的组合：三处（全局配置、extend_hours 的落库状态、调度器内存里的
定时器）各自公开自己的只读读数，这里不做任何判断——判据只有一个出处，看板只
报告状态。
"""

from typing import cast

from fastapi import APIRouter, Depends, Security
from sqlalchemy.orm import Session

from app.auth.dependencies import caller
from app.db.deps import get_db
from app.modules.config import store as config_store
from app.modules.extend_hours import service as extend_hours_service
from app.modules.sessions import scheduler as sessions_scheduler

from . import schemas
from .spec import _TITLES, JOBS

admin_router = APIRouter(
    prefix="/api/v1/jobs",
    tags=["jobs"],
    dependencies=[Security(caller, scopes=["jobs:read"])],
)


@admin_router.get("/status", response_model=schemas.JobsStatus)
def jobs_status(session: Session = Depends(get_db)) -> schemas.JobsStatus:
    """后台任务的当前态：开关与名单、延长中的设备、调度器的即时计划。

    纯只读的组合：三处（全局配置、extend_hours 的落库状态、调度器内存里的定时器）
    各自公开自己的只读读数，这里不做任何判断——判据只有一个出处，看板只报告状态。
    """
    config = config_store.global_effective(session)
    jobs = []
    for key, enabled_key, students_key in JOBS:
        students = config[students_key]
        jobs.append(
            schemas.JobStatus(
                key=key,
                title=_TITLES[key],
                enabled=bool(config[enabled_key]),
                students_count=len(students) if isinstance(students, dict) else 0,
            )
        )
    # 行的形状由来源模块保证（模块注释），这里只做类型收口；字段断言在看板测试
    return schemas.JobsStatus(
        jobs=jobs,
        extending=cast("list[schemas.ExtendingDevice]", extend_hours_service.current_shifts(session)),
        scheduled=cast("list[schemas.ScheduledAction]", sessions_scheduler.snapshot(session)),
    )
