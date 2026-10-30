"""应用日志装配：stdout JSON 事件流，字段名照 OpenTelemetry Log Data Model。

显式配置是必须的：uvicorn 只配置它自己的 logger，应用 logger 没有 handler 时
INFO 级日志被静默丢弃。按 Twelve-Factor 写 stdout 不写文件，收集与归档由
运行环境（systemd/journald）负责。

每行一个 JSON 对象：ts（RFC 3339，唯一来源 core/clock）、level、logger、message、
event（给机器检索的稳定代号）、trace_id 与 request_id（有值才输出，与 api_call
审计行互通）、其余属性逐键并入顶层。

stdout 日志不写口令、令牌、完整学号与身份标识；审计表的原文保留与访问控制见 docs/SECURITY.md 与 core/audit.py。
"""

import contextvars
import json
import logging
import sys
import traceback
from typing import Any

from .clock import now_rfc3339

#: 应用 logger 的根名称。各模块用 `ntu-shower.<域>[.<模块>]` 作为子 logger。
ROOT_LOGGER_NAME = "ntu-shower"

#: 当前请求的关联标识：审计中间件置位，JSON 日志与审计行读它做事件关联。
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")

#: 允许作为属性的标量类型；容器类型拒收，嵌套结构无法检索。
_SCALARS = (str, int, float, bool)


class JsonFormatter(logging.Formatter):
    """一条日志一行 JSON；字段名照 OpenTelemetry Log Data Model。"""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": now_rfc3339(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        event = getattr(record, "event", "")
        if event:
            entry["event"] = event
        trace_id = trace_id_var.get()
        if trace_id:
            entry["trace_id"] = trace_id
        request_id = request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        attrs = getattr(record, "attrs", None)
        if isinstance(attrs, dict):
            entry.update(attrs)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    """给应用 logger 装配 stdout handler。幂等：应用工厂可能被多次执行。"""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level.upper())
    # 自带 handler 后不再向根 logger 冒泡，避免与 uvicorn 的 handler 重复输出
    root.propagate = False
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


def log_event(level: str, event: str, message: str, /, **attrs: Any) -> None:  # noqa: ANN401
    """按稳定事件名写一条结构化日志。

    `event` 是给机器检索的稳定代号（kebab-case），`message` 是给人读的中文，两者
    分开，改文案不破坏检索。属性必须全是标量：嵌套结构无法检索，展开成平铺的
    键值才查得动。签名收 Any 是设计本身，约束在运行时执行。
    """
    for key, value in attrs.items():
        if not isinstance(value, _SCALARS):
            detail = f"log attribute {key!r} must be a scalar, got {type(value).__name__}"
            raise TypeError(detail)
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.log(logging.getLevelName(level.upper()), message, extra={"event": event, "attrs": attrs})


def exc_info_text() -> str:
    """当前异常的单行摘要：给结构化日志的 `error` 属性，多行堆栈压成一行可检索。

    它是日志设施的一部分而不是某个调用方的：`error=` 这个属性就是按"一行可检索"设计的，
    凡是把异常写进日志的地方都需要它。此前它住在 `core/audit`，那是它第一个用处的所在，
    而不是它该在的地方——第二个调用方出现时就会变成两处实现。
    """
    return traceback.format_exc(limit=8).replace("\n", " | ")
