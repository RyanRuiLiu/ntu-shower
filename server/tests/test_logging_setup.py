"""应用日志装配测试。

这条链路曾经断过：uvicorn 只配置自己的 logger，应用 logger 没有 handler，
于是 `logger.info(...)` 被静默丢弃——日志写了却看不到。测试锁住"装配后
确实有 handler 且级别生效"，避免再次静默失效。
"""

import logging

import pytest

from app.core.logging import ROOT_LOGGER_NAME, configure_logging


@pytest.fixture(autouse=True)
def _clean_root_logger() -> None:
    """每条用例前后清掉装配，避免相互影响（模块级对象跨用例存活）。"""
    logger = logging.getLogger(ROOT_LOGGER_NAME)
    saved = list(logger.handlers)
    saved_level = logger.level
    logger.handlers.clear()
    yield
    logger.handlers.clear()
    logger.handlers.extend(saved)
    logger.setLevel(saved_level)


def test_installs_a_handler_on_the_application_logger() -> None:
    configure_logging("INFO")

    assert logging.getLogger(ROOT_LOGGER_NAME).handlers, "应用 logger 必须有 handler，否则日志被丢弃"


def test_child_logger_reaches_the_application_handler(caplog: pytest.LogCaptureFixture) -> None:
    # 各模块用的是 ntu-shower.<域> 子 logger，必须能冒泡到装配的 handler
    configure_logging("INFO")
    with caplog.at_level(logging.INFO, logger=ROOT_LOGGER_NAME):
        logging.getLogger(f"{ROOT_LOGGER_NAME}.probe").info("hello")

    assert any("hello" in record.message for record in caplog.records)


def test_level_is_applied_case_insensitively() -> None:
    configure_logging("warning")

    assert logging.getLogger(ROOT_LOGGER_NAME).level == logging.WARNING


def test_is_idempotent() -> None:
    # 应用工厂可能被多次执行（测试尤甚），重复装配不得叠加 handler
    configure_logging("INFO")
    first = len(logging.getLogger(ROOT_LOGGER_NAME).handlers)
    configure_logging("INFO")

    assert len(logging.getLogger(ROOT_LOGGER_NAME).handlers) == first


def test_does_not_propagate_to_the_root_logger() -> None:
    # 自带 handler 后若仍冒泡，会与 uvicorn 的 handler 重复输出同一行
    configure_logging("INFO")

    assert logging.getLogger(ROOT_LOGGER_NAME).propagate is False
