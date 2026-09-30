"""设备远程配置：全局默认 + 设备级覆盖（docs/PROTOCOL.md §5）。

- 全局配置一份，设备可按 device_id 覆盖部分键；
- 设备连接（hello_ack）与变更推送均下发"合并后的有效配置"；
- 键集合可扩展：未知键由设备端忽略，新版本可增加键而不破坏旧设备。
"""

import json

from app.core.db import get_conn

# 远程配置的取值类型：标量，或 auto_settle_students 这样的 学号→密码 映射
ConfigValue = int | bool | str | dict[str, str]

# 默认值沿用旧项目真机验证过的取值
DEFAULT_CONFIG: dict[str, ConfigValue] = {
    # 通信时序
    "heartbeat_interval_s": 30,
    "server_ping_interval_s": 60,
    "reconnect_base_ms": 5000,
    "reconnect_max_ms": 60000,
    "watchdog_interval_s": 60,
    # doors 采集
    "report_debounce_ms": 1000,
    # 自动化
    "overlay_enabled": True,
    "overlay_message": "自动操作中…",
    "server_response_timeout_ms": 10000,
    "id_verify_wait_ms": 5000,
    "home_budget_ms": 10000,
    "poll_interval_ms": 200,
    "ui_click_delay_ms": 200,
    # OTA
    "ota_check_interval_s": 3600,
    # 浴室 App 崩溃守护（ADR-0024，参数化扩展）
    "bath_guard_enabled": True,
    "bath_exit_window_ms": 30000,
    "bath_exit_max_count": 3,
    "bath_exit_cooldown_ms": 600000,
    # 自动结算（服务器侧）：占用超过阈值自动结算（旧项目 AUTO_SETTLE_THRESHOLD=1140s）
    "auto_settle_enabled": False,
    "auto_settle_threshold_s": 1140,
    "auto_settle_students": {},
}

CONFIG_KEYS = frozenset(DEFAULT_CONFIG.keys())


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS global_config (
            id       INTEGER PRIMARY KEY CHECK (id = 1),
            revision INTEGER NOT NULL,
            config   TEXT    NOT NULL
        );
        CREATE TABLE IF NOT EXISTS device_config (
            device_id TEXT    PRIMARY KEY,
            revision  INTEGER NOT NULL,
            config    TEXT    NOT NULL
        );
        """
    )
    row = get_conn().execute("SELECT revision FROM global_config WHERE id = 1").fetchone()
    if row is None:
        get_conn().execute(
            "INSERT INTO global_config (id, revision, config) VALUES (1, 1, ?)",
            (json.dumps(DEFAULT_CONFIG),),
        )
        get_conn().commit()


def _merge(over: dict[str, ConfigValue]) -> dict[str, ConfigValue]:
    """把覆盖项合到默认值上；未知键被丢弃（客户端只能改已登记的键）。"""
    merged = dict(DEFAULT_CONFIG)
    for key, value in over.items():
        if key in CONFIG_KEYS:
            merged[key] = value
    return merged


def get_global() -> tuple[int, dict[str, ConfigValue]]:
    row = get_conn().execute("SELECT revision, config FROM global_config WHERE id = 1").fetchone()
    return (int(row["revision"]), dict(json.loads(row["config"])))


def put_global(config: dict[str, ConfigValue]) -> tuple[int, dict[str, ConfigValue]]:
    revision, _previous = get_global()
    merged = _merge(config)
    new_revision = revision + 1
    get_conn().execute(
        "UPDATE global_config SET revision = ?, config = ? WHERE id = 1",
        (new_revision, json.dumps(merged)),
    )
    get_conn().commit()
    return new_revision, merged


def get_device_override(device_id: str) -> tuple[int, dict[str, ConfigValue]] | None:
    row = get_conn().execute("SELECT revision, config FROM device_config WHERE device_id = ?", (device_id,)).fetchone()
    return (int(row["revision"]), dict(json.loads(row["config"]))) if row else None


def put_device_override(device_id: str, config: dict[str, ConfigValue]) -> tuple[int, dict[str, ConfigValue]]:
    existing = get_device_override(device_id)
    merged = _merge(config)
    new_revision = (existing[0] if existing else 0) + 1
    get_conn().execute(
        "INSERT OR REPLACE INTO device_config (device_id, revision, config) VALUES (?,?,?)",
        (device_id, new_revision, json.dumps(merged)),
    )
    get_conn().commit()
    return new_revision, merged


def delete_device_override(device_id: str) -> bool:
    cursor = get_conn().execute("DELETE FROM device_config WHERE device_id = ?", (device_id,))
    get_conn().commit()
    return cursor.rowcount > 0


def effective(device_id: str) -> dict[str, ConfigValue]:
    """合并后的有效配置 = 全局默认 ← 设备覆盖。"""
    _, global_config = get_global()
    override = get_device_override(device_id)
    if override is None:
        return global_config
    merged = dict(global_config)
    merged.update(override[1])
    return merged
