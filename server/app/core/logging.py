"""应用日志装配：stdout JSON 事件流（依 The Twelve-Factor App §XI 与
OpenTelemetry Log Data Model 的字段命名）。

## 为什么必须显式配置

uvicorn 启动时只配置它自己的 logger，应用的 logger 没有 handler。Python 的
logging 对"没有 handler 的 logger"只在 WARNING 及以上经 `lastResort` 输出，
于是 `logger.info(...)` 会被静默丢弃——日志写了却看不到，等于没写。

## 落点与格式

按 12-Factor 要求写 stdout，**不写文件、不做轮转**：收集与归档由运行环境
（systemd/journald）负责。这是应用不该操心的职责。

每行一个 JSON 对象，字段名照 OpenTelemetry Log Data Model：

- `ts`：RFC 3339 UTC（唯一来源 core/clock）
- `level`：SeverityText（INFO/WARNING/...）
- `logger`：发出者（`ntu-shower.<域>`）
- `message`：人读的一句话
- `event`：给机器检索的稳定事件代号（有值才输出）
- `trace_id` / `request_id`：有值才输出——HTTP 审计中间件置位，事件由此与
  `api_call` 审计行互通
- 其余 `**attrs` 逐键并入顶层（只允许标量，见 [log_event]）

## 什么不许进日志

令牌、学号密码、身份全量值——记录前先脱敏（core.audit.mask_actor），这是
OWASP Logging Cheat Sheet 的硬规则。本模块不做脱敏：调用方给什么写什么，
所以调用方必须先脱敏。
"""

import contextvars
import json
import logging
import sys
from typing import Any

from .clock import now_rfc3339

#: 应用 logger 的根名称。各模块用 `ntu-shower.<域>[.<模块>]` 作为子 logger。
ROOT_LOGGER_NAME = "ntu-shower"

#: 当前请求的关联标识：审计中间件置位，JSON 日志与审计行读它做事件关联。
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
trace_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("trace_id", default="")

#: 允许作为属性的标量类型；容器类型拒收——嵌套结构会让人检索不到任何键。
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
    """给应用 logger 装配 stdout handler。

    幂等：重复调用不会叠加 handler（应用工厂可能被多次执行，测试尤甚）。
    """
    root = logging.getLogger(ROOT_LOGGER_NAME)
    root.setLevel(level.upper())
    # 自带 handler 后不再向根 logger 冒泡，避免与 uvicorn 的 handler 重复输出
    root.propagate = False
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


def log_event(level: str, event: str, message: str, /, **attrs: Any) -> None:
    """按稳定事件名写一条结构化日志。

    `level` 是标准级别名（INFO/WARNING/ERROR/...）；`event` 是给机器检索的
    稳定代号（kebab-case），`message` 是给人读的中文——两者分开，改文案不能
    破坏检索。属性必须全是标量，容器类型直接拒收：嵌套结构会让人检索不到
    任何键，展开成平铺的键值才查得动。
    """
    for key, value in attrs.items():
        if not isinstance(value, _SCALARS):
            raise TypeError(f"log attribute {key!r} must be a scalar, got {type(value).__name__}")
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    logger.log(logging.getLevelName(level.upper()), message, extra={"event": event, "attrs": attrs})
