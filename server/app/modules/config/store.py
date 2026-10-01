"""设备远程配置：全局默认 + 设备级覆盖（protocol/schema/）。

## 存储什么

**只存与默认值不同的项**。此前存的是"默认值 + 改动"的完整快照，后果是：

- 快照固化了当时的默认值，此后修改默认值对已有记录**完全无效**；
- 生产库的快照是 14 个键的旧版本，后加的 7 个键（崩溃守护、自动结算）
  从未下发过，设备一直靠本地默认值兜着。

只存差异后，`effective` 每次按 `默认 ← 全局覆盖 ← 设备覆盖` 现算：改默认值
即刻对所有设备生效，调用方提交整个表单也不会把未改动的键固化成覆盖。

## 校验

入参按 `spec.CONFIG_SPECS` 校验类型与范围：越界值会让设备进入异常状态
（例如 `heartbeat_interval_s = 0` 会导致心跳风暴），因此在这里拒绝而非下发。
"""

import json
from typing import NoReturn

from app.core.db import get_conn

from .schemas import ConfigSpecView, ConfigValues
from .spec import CONFIG_SPECS, SPECS_BY_KEY, ConfigKind, ConfigScope, defaults


class InvalidConfigError(ValueError):
    """配置入参不合法：未知键、类型不符或超出范围。

    领域层不依赖 HTTP 语义，由路由层把它转成 problem+json。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
    # config 存的是"与默认值的差异"，空对象即全部沿用默认，无需预置快照
    row = get_conn().execute("SELECT revision FROM global_config WHERE id = 1").fetchone()
    if row is None:
        get_conn().execute("INSERT INTO global_config (id, revision, config) VALUES (1, 1, '{}')")
        get_conn().commit()


def _reject(reason: str) -> NoReturn:
    """以统一的异常类型拒绝一份配置。

    集中一处是为了让各校验分支只表达"为什么拒绝"，异常构造不必重复多遍。
    """
    raise InvalidConfigError(reason)


def validate(values: ConfigValues) -> ConfigValues:
    """校验一份配置改动，返回规范化后的结果；不合法时抛 InvalidConfigError。

    只接受的键见 `spec.CONFIG_SPECS`；未知键在此拒绝，而不是静默丢弃——
    静默丢弃会让调用方以为提交生效了。
    """
    unknown = sorted(set(values) - set(SPECS_BY_KEY))
    if unknown:
        _reject(f"未知配置键：{unknown}")

    checked: ConfigValues = {}
    for key, value in values.items():
        spec = SPECS_BY_KEY[key]
        checked[key] = _check_value(spec.key, spec.kind, spec.minimum, spec.maximum, value)
    return checked


def _check_value(
    key: str,
    kind: ConfigKind,
    minimum: int | None,
    maximum: int | None,
    value: object,
) -> int | bool | str | dict[str, str]:
    if kind is ConfigKind.INT:
        # bool 是 int 的子类，isinstance(True, int) 为真；先排除它，
        # 否则用整数校验的键传 true 也会通过
        if isinstance(value, bool) or not isinstance(value, int):
            _reject(f"{key} 需要整数，收到 {type(value).__name__}")
        if minimum is not None and value < minimum:
            _reject(f"{key} 不得小于 {minimum}（收到 {value}）")
        if maximum is not None and value > maximum:
            _reject(f"{key} 不得大于 {maximum}（收到 {value}）")
        return value

    if kind is ConfigKind.BOOL:
        if not isinstance(value, bool):
            _reject(f"{key} 需要布尔值，收到 {type(value).__name__}")
        return value

    if kind is ConfigKind.TEXT:
        if not isinstance(value, str):
            _reject(f"{key} 需要文本，收到 {type(value).__name__}")
        return value

    if not isinstance(value, dict):
        _reject(f"{key} 需要映射，收到 {type(value).__name__}")
    result: dict[str, str] = {}
    for map_key, map_value in value.items():
        if not isinstance(map_key, str) or not isinstance(map_value, str):
            _reject(f"{key} 的键与值都必须是文本")
        result[map_key] = map_value
    return result


def _as_overrides(values: ConfigValues) -> ConfigValues:
    """剔掉与默认值相同的项。

    调用方通常会提交整个表单；若原样入库，等于把所有键都固定成覆盖，
    默认值此后就改不动了。只留差异才能让"默认值"保持可演进。
    """
    current = defaults()
    return {key: value for key, value in values.items() if current[key] != value}


def get_global_overrides() -> tuple[int, ConfigValues]:
    """全局覆盖项（不含默认值）与修订号。"""
    row = get_conn().execute("SELECT revision, config FROM global_config WHERE id = 1").fetchone()
    return (int(row["revision"]), json.loads(row["config"]))


def put_global_overrides(values: ConfigValues) -> tuple[int, ConfigValues]:
    """覆盖式写入全局覆盖项；返回新修订号与入库内容。"""
    revision, _previous = get_global_overrides()
    overrides = _as_overrides(values)
    new_revision = revision + 1
    get_conn().execute(
        "UPDATE global_config SET revision = ?, config = ? WHERE id = 1",
        (new_revision, json.dumps(overrides)),
    )
    get_conn().commit()
    return new_revision, overrides


def get_device_overrides(device_id: str) -> tuple[int, ConfigValues] | None:
    row = get_conn().execute("SELECT revision, config FROM device_config WHERE device_id = ?", (device_id,)).fetchone()
    return (int(row["revision"]), json.loads(row["config"])) if row else None


def put_device_overrides(device_id: str, values: ConfigValues) -> tuple[int, ConfigValues]:
    existing = get_device_overrides(device_id)
    overrides = _as_overrides(values)
    new_revision = (existing[0] if existing else 0) + 1
    get_conn().execute(
        "INSERT OR REPLACE INTO device_config (device_id, revision, config) VALUES (?,?,?)",
        (device_id, new_revision, json.dumps(overrides)),
    )
    get_conn().commit()
    return new_revision, overrides


def delete_device_overrides(device_id: str) -> bool:
    cursor = get_conn().execute("DELETE FROM device_config WHERE device_id = ?", (device_id,))
    get_conn().commit()
    return cursor.rowcount > 0


def global_effective() -> ConfigValues:
    """全局有效配置 = 默认 ← 全局覆盖（不含任何设备覆盖）。"""
    merged = defaults()
    _, global_overrides = get_global_overrides()
    merged.update(global_overrides)
    return merged


def effective(device_id: str) -> ConfigValues:
    """合并后的有效配置 = 默认 ← 全局覆盖 ← 设备覆盖。

    每次现算而非读快照：默认值改动即刻对所有设备生效，这是"只存差异"的收益。
    """
    merged = global_effective()
    device_overrides = get_device_overrides(device_id)
    if device_overrides is not None:
        merged.update(device_overrides[1])
    return merged


def device_effective(device_id: str) -> ConfigValues:
    """下发给设备的配置：只含设备侧消费的键。

    服务端自己的键（自动结算阈值等）没有理由占用设备的解析路径。
    """
    full = effective(device_id)
    return {key: value for key, value in full.items() if SPECS_BY_KEY[key].scope is ConfigScope.DEVICE}


def describe() -> list[ConfigSpecView]:
    """配置登记表的外部描述，供 `GET /ops/config/spec`（客户端据此渲染控件）。"""
    return [
        {
            "key": spec.key,
            "kind": spec.kind,
            "scope": spec.scope,
            "default": spec.default,
            "title": spec.title,
            "description": spec.description,
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "unit": spec.unit,
            "group": spec.group,
        }
        for spec in CONFIG_SPECS
    ]
