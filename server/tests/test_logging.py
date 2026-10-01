"""结构化 JSON 日志：字段模型（OpenTelemetry）、关联标识与属性约束。"""

import io
import json
import logging

import pytest

from app.core import audit
from app.core.clock import now_rfc3339
from app.core.logging import ROOT_LOGGER_NAME, JsonFormatter, configure_logging, log_event


@pytest.fixture
def output() -> io.StringIO:
    """装配 JSON 格式的独立捕获流；用例结束后恢复原 handler。"""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    handlers_before = list(root.handlers)
    root.handlers.clear()

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    try:
        yield stream
    finally:
        root.handlers.clear()
        root.handlers.extend(handlers_before)


def lines(stream: io.StringIO) -> list[str]:
    return [line for line in stream.getvalue().splitlines() if line]


def test_plain_logger_call_still_produces_json(output: io.StringIO) -> None:
    configure_logging("INFO")
    logging.getLogger(ROOT_LOGGER_NAME).info("普通调用也是一行合法 JSON")

    entry = json.loads(lines(output)[-1])
    assert entry["level"] == "INFO"
    assert entry["message"] == "普通调用也是一行合法 JSON"
    assert entry["ts"] == now_rfc3339()  # 时间唯一来源 core/clock
    assert "event" not in entry  # 没给事件代号就不输出空字段


def test_log_event_carries_event_and_scalar_attrs(output: io.StringIO) -> None:
    log_event("WARNING", "demo.event", "演示事件", device_id="dev-1", retries=2)

    entry = json.loads(lines(output)[-1])
    assert entry["event"] == "demo.event"
    assert entry["level"] == "WARNING"
    assert entry["device_id"] == "dev-1"
    assert entry["retries"] == 2


def test_log_event_rejects_container_attrs() -> None:
    """属性必须是标量：嵌套结构会让人检索不到任何键，宁可在写日志处报错。"""
    with pytest.raises(TypeError, match="must be a scalar"):
        log_event("INFO", "demo.bad", "容器属性", payload={"a": 1})


def test_trace_id_and_request_id_join_the_entry(output: io.StringIO) -> None:
    token = audit.trace_id_var.set("4bf92f3577b34da6a3ce929d0e0e4736")
    request_token = audit.request_id_var.set("req-1")
    try:
        log_event("INFO", "demo.traced", "带关联的事件")
    finally:
        audit.trace_id_var.reset(token)
        audit.request_id_var.reset(request_token)

    entry = json.loads(lines(output)[-1])
    assert entry["trace_id"] == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert entry["request_id"] == "req-1"


def test_exception_is_formatted_into_the_entry(output: io.StringIO) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger(ROOT_LOGGER_NAME).exception("出事了")

    entry = json.loads(lines(output)[-1])
    assert "ValueError: boom" in entry["exception"]
