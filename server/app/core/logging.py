"""应用日志装配（依 The Twelve-Factor App §XI：日志是事件流）。

## 为什么必须显式配置

uvicorn 启动时只配置它自己的 logger，应用的 logger 没有 handler。Python 的
logging 对"没有 handler 的 logger"只在 WARNING 及以上经 `lastResort` 输出，
于是 `logger.info(...)` 会被静默丢弃——日志写了却看不到，等于没写。

## 落点

按 12-Factor 要求写 stdout，**不写文件、不做轮转**：收集与归档由运行环境
（systemd/journald）负责。这是应用不该操心的职责。
"""

import logging
import sys

#: 应用 logger 的根名称。各模块用 `ntu-shower.<域>[.<模块>]` 作为子 logger。
ROOT_LOGGER_NAME = "ntu-shower"

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s %(message)s"


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
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
