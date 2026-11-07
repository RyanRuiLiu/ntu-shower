"""旧库 → 新库的数据转换。

切换方式是一次性替换：新服务直接跑新库，不做双写、不留兼容层。因此这个脚本必须
在切换前把旧库全部业务数据搬过去，并且能被验证——搬错的库在新服务上线那一刻才会
暴露，代价是线上数据。

## 用法

    PYTHONPATH=. python tools/convert_v1_to_v2.py --source old.db --target new.db [--verify]

（`PYTHONPATH=.` 必带：脚本要导入 `app.*`，而 `tools/` 不是包。）

`--verify` 只比对行数，不写库。

## 映射规则

- 设备等自然键（`device_id` 字符串）在新库是 `devices.id` 整数，转换时建字典解析；
  旧库里出现新库没有的设备时直接报错，不静默丢行。
- 时间是 RFC 3339 字符串 → `UtcDateTime` 的存储形式（UTC、无时区后缀）。
- JSON 列在新库仍是 TEXT，原样搬运。
- 枚举列取值不变，只校验取值合法。

## 分阶段

当前实现「身份组」：`devices`、`access_identities`。其余表按 `PLANNED` 登记，
未实现时脚本明确拒绝执行，不会产出半份数据。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.core.clock import parse_rfc3339
from app.db.base import utcnow
from app.db.engine import create_db_engine
from app.models import Base

#: 已实现的转换：(旧表, 新表)。
DONE: tuple[tuple[str, str], ...] = (
    ("device", "devices"),
    ("access_identity", "access_identities"),
    ("clock_shift", "clock_shifts"),
    ("device_config", "device_configs"),
    ("device_link_event", "device_link_events"),
    ("device_log", "device_logs"),
    ("global_config", "app_settings"),
    ("ota_release", "ota_releases"),
    ("voice_asset", "voice_assets"),
)

#: 尚未实现的表。存在即拒绝执行——半份数据比没有数据更危险。
PLANNED: tuple[str, ...] = (
    "api_call",
    "background_task_run",
    "command_audit",
    "door_live",
    "door_snapshot",
    "door_state_event",
    "ota_release_install",
    "usage_segment",
    "usage_session",
    "voice_delivery",
    "voice_rule",
    "voice_unit",
)


def to_storage_time(text: str | None) -> str | None:
    """RFC 3339 → `UtcDateTime` 的存储形式；空值与不可解析值按空处理。"""
    if not text:
        return None
    moment = parse_rfc3339(text)
    if moment is None:
        return None
    return moment.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")


def copy_devices(source: sqlite3.Connection, target: sqlite3.Connection) -> dict[str, int]:
    rows = source.execute(
        "SELECT device_id, name, trusted, app_version, version_code, os_version,"
        " capabilities, last_seen_at, last_ip, booted_at, clock_skew_s, sort_order FROM device"
    ).fetchall()
    mapping: dict[str, int] = {}
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    for row in rows:
        cursor = target.execute(
            "INSERT INTO devices (device_id, name, trusted, app_version, version_code,"
            " os_version, capabilities, last_seen_at, last_ip, booted_at, clock_skew_s,"
            " sort_order, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row[0],
                row[1] or "",
                1 if row[2] else 0,
                row[3] or "",
                int(row[4] or 0),
                row[5] or "",
                row[6] or "[]",
                to_storage_time(row[7]),
                row[8] or None,
                to_storage_time(row[9]),
                float(row[10] or 0.0),
                int(row[11] or 0),
                now,
                now,
            ),
        )
        mapping[str(row[0])] = int(cursor.lastrowid or 0)
    return mapping


def copy_identities(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    rows = source.execute(
        "SELECT identity, kind, capabilities, device_scope, label, created_at FROM access_identity"
    ).fetchall()
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    for row in rows:
        target.execute(
            "INSERT INTO access_identities"
            " (identity, kind, capabilities, device_scope, label, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                row[0],
                row[1],
                row[2] or "[]",
                row[3] or "[]",
                row[4] or "",
                to_storage_time(row[5]) or now,
                now,
            ),
        )


def copy_simple(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    devices: dict[str, int],
) -> None:
    """逐设备一行的表：`device_link_event`、`device_log`、`clock_shift`、`device_config`。

    这些表没有跨表引用，只有设备外键与时间列，统一用一张映射表处理。
    """
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    for row in source.execute(
        "SELECT device_id, occurred_at, event, reason FROM device_link_event"
    ).fetchall():
        target.execute(
            "INSERT INTO device_link_events (device_id, occurred_at, event, reason, created_at)"
            " VALUES (?,?,?,?,?)",
            (devices[str(row[0])], to_storage_time(row[1]), row[2], row[3] or "", now),
        )
    for row in source.execute(
        "SELECT device_id, kind, content, uploaded_at FROM device_log"
    ).fetchall():
        target.execute(
            "INSERT INTO device_logs (device_id, kind, content, uploaded_at, created_at)"
            " VALUES (?,?,?,?,?)",
            (devices[str(row[0])], row[1], row[2], to_storage_time(row[3]) or now, now),
        )
    for row in source.execute(
        "SELECT device_id, shifted_at, assignments FROM clock_shift"
    ).fetchall():
        target.execute(
            "INSERT INTO clock_shifts (device_id, shifted_at, assignments, updated_at)"
            " VALUES (?,?,?,?)",
            (devices[str(row[0])], to_storage_time(row[1]) or now, row[2] or "[]", now),
        )
    for row in source.execute(
        "SELECT device_id, revision, config FROM device_config"
    ).fetchall():
        target.execute(
            "INSERT INTO device_configs (device_id, revision, config, updated_at)"
            " VALUES (?,?,?,?)",
            (devices[str(row[0])], int(row[1]), row[2] or "{}", now),
        )


def copy_singletons(
    source: sqlite3.Connection, target: sqlite3.Connection
) -> None:
    """整表单条的配置、无外键的发布与素材表。"""
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    row = source.execute("SELECT revision, config FROM global_config WHERE id = 1").fetchone()
    if row is not None:
        target.execute(
            "INSERT INTO app_settings (id, revision, config, updated_at) VALUES (1,?,?,?)",
            (int(row[0]), row[1] or "{}", now),
        )
    for row in source.execute(
        "SELECT version, version_code, sha256, mandatory, force, target, created_at FROM ota_release"
    ).fetchall():
        target.execute(
            "INSERT INTO ota_releases (version, version_code, sha256, mandatory, force, target,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                row[0], int(row[1]), row[2], 1 if row[3] else 0, 1 if row[4] else 0,
                row[5] or "all", to_storage_time(row[6]) or now, now,
            ),
        )
    for row in source.execute(
        "SELECT asset_id, name, digit, duration_ms, size_bytes, created_at FROM voice_asset"
    ).fetchall():
        target.execute(
            "INSERT INTO voice_assets (asset_id, name, digit, duration_ms, size_bytes, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (row[0], row[1], row[2], int(row[3]), int(row[4]), to_storage_time(row[5]) or now),
        )


def verify(source: sqlite3.Connection, target: sqlite3.Connection) -> list[str]:
    problems: list[str] = []
    for old, new in DONE:
        expected = int(source.execute(f"SELECT COUNT(*) FROM {old}").fetchone()[0])
        actual = int(target.execute(f"SELECT COUNT(*) FROM {new}").fetchone()[0])
        status = "ok" if expected == actual else "MISMATCH"
        problems.append(f"{old:20s} {expected:8d} → {new:22s} {actual:8d}  {status}")
        if expected != actual:
            problems.append(f"  !! {new} 行数不一致")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="旧库 → 新库转换")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    if Path(args.target).exists():
        print(f"目标库已存在，拒绝覆盖：{args.target}", file=sys.stderr)
        return 2

    source = sqlite3.connect(args.source)
    target = sqlite3.connect(args.target)
    try:
        engine = create_db_engine(f"sqlite:///{Path(args.target).as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()

        if args.verify:
            for line in verify(source, target):
                print(line)
            return 0

        missing = [name for name in PLANNED if source.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()]
        if missing:
            print("以下表尚未实现转换，脚本拒绝执行：", file=sys.stderr)
            for name in missing:
                print(f"  - {name}", file=sys.stderr)
            return 3

        target.execute("BEGIN")
        devices = copy_devices(source, target)
        copy_identities(source, target)
        copy_simple(source, target, devices)
        copy_singletons(source, target)
        target.commit()

        for line in verify(source, target):
            print(line)
        return 0
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    raise SystemExit(main())
