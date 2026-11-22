"""stdout JSON 日志的形状：OTel 官方键名、标量属性约束、装配幂等。

其余测试大多把 log_event 换成探针，真实 formatter 从未被执行——这里直接驱动
formatter 与装配函数，钉住键名形状：日志收集与排障检索都吃这份契约。
"""

import json
import logging
import sys

import pytest

from app.core.clock import parse_rfc3339
from app.core.logging import (
    ROOT_LOGGER_NAME,
    JsonFormatter,
    configure_logging,
    log_event,
    request_id_var,
    trace_id_var,
)


def _record(
    level: int,
    message: str,
    *,
    event: str = "",
    attrs: dict[str, object] | None = None,
    exc_info: object = None,
) -> logging.LogRecord:
    record = logging.LogRecord(ROOT_LOGGER_NAME, level, __file__, 1, message, (), exc_info)
    record.event = event
    record.attrs = attrs or {}
    return record


def test_entry_uses_the_official_otel_keys() -> None:
    """键名是 OTel Logs Data Model 的官方字段：收集侧按它建索引，改名即断采集。"""
    request_token = request_id_var.set("req-1")
    trace_token = trace_id_var.set("trace-1")
    try:
        entry = json.loads(
            JsonFormatter().format(
                _record(logging.WARNING, "门帧滞后", event="doors.lag", attrs={"device_id": "dev-1"})
            )
        )
    finally:
        request_id_var.reset(request_token)
        trace_id_var.reset(trace_token)

    assert parse_rfc3339(entry["timestamp"]) is not None
    assert entry["severity_text"] == "WARNING"
    assert entry["body"] == "门帧滞后"
    assert entry["event_name"] == "doors.lag"
    assert entry["resource"] == {"service.name": "ntu-shower"}
    assert entry["trace_id"] == "trace-1"
    assert entry["attributes"] == {"request_id": "req-1", "device_id": "dev-1"}


def test_empty_optional_fields_are_omitted() -> None:
    """没有关联标识、事件名与属性时不产空键：行保持最短，收集侧不必判空。"""
    entry = json.loads(JsonFormatter().format(_record(logging.INFO, "你好")))
    assert "event_name" not in entry
    assert "trace_id" not in entry
    assert "attributes" not in entry


def test_exception_stack_is_flattened_into_attributes() -> None:
    """异常进 attributes 的官方键，多行堆栈压成一行可检索。"""
    message = "boom"

    def boom() -> None:
        raise ValueError(message)

    try:
        boom()
    except ValueError:
        entry = json.loads(JsonFormatter().format(_record(logging.ERROR, "失败", exc_info=sys.exc_info())))

    stack = entry["attributes"]["exception.stacktrace"]
    assert "ValueError: boom" in stack
    assert "\n" not in stack


def test_log_event_rejects_non_scalar_attributes() -> None:
    """属性必须全是标量：嵌套结构无法检索，运行时拒收。"""
    with pytest.raises(TypeError, match="must be a scalar"):
        log_event("INFO", "test.bad-attr", "嵌套属性", bad={"nested": 1})


def test_log_event_flows_through_the_formatter(caplog: pytest.LogCaptureFixture) -> None:
    """端到端：log_event 的一行经真实管线（logger → extra → formatter）落成官方键名。

    捕获用 pytest 官方的 caplog：pytest 会把捕获 handler 挂到非传播 logger 上，
    应用的 propagate=False 不挡路。级别经 at_level 显式给定，用例结束后还原。
    """
    with caplog.at_level(logging.INFO, logger=ROOT_LOGGER_NAME):
        log_event("WARNING", "test.flow", "端到端", device_id="dev-1")

    record = next(record for record in caplog.records if record.event == "test.flow")
    entry = json.loads(JsonFormatter().format(record))
    assert entry["event_name"] == "test.flow"
    assert entry["attributes"] == {"device_id": "dev-1"}
    assert entry["body"] == "端到端"
    assert entry["severity_text"] == "WARNING"


def test_configure_logging_is_idempotent() -> None:
    """应用工厂可能多次执行：重复装配不得叠出多个 handler。"""
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    configure_logging()
    count = len(logger.handlers)
    assert count >= 1
    configure_logging()
    assert len(logger.handlers) == count
    assert logger.level == logging.INFO
