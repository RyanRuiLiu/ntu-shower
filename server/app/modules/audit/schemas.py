"""审计领域的 API 契约。

两张表的行形状各只有一个读者面：command_audit → `GET /audit/commands`、
background_task_run → `GET /audit/background-task-runs`（都是 admin）。字段与表列
一一对应；params/data 是入库的 JSON 原文，由调用方自行解析。

形状定义在 `app.contracts`：持久化层与路由层共同消费，避免持久化反向依赖本模块。
"""

from app.contracts import BackgroundTaskRun as BackgroundTaskRun
from app.contracts import CommandAuditRecord as CommandAuditRecord
