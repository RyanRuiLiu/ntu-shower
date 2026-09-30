"""统一时间：服务端对外输出的时间一律为 UTC RFC 3339 毫秒精度。"""

from datetime import datetime, timezone


def now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_epoch_s() -> int:
    return int(datetime.now(timezone.utc).timestamp())
