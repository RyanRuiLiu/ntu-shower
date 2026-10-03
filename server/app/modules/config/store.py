"""设备远程配置：全局默认 + 设备级覆盖。

库里只存与默认值不同的差异项，effective 每次现算"默认 ← 全局覆盖 ← 设备覆盖"：
改默认值即刻对所有设备生效，调用方提交整个表单也不会把未改动的键固化成覆盖。
存完整快照的做法会固化当时的默认值，新增键永远下不下去。

入参按 spec.CONFIG_SPECS 校验类型与范围；越界值会让设备进入异常状态（如心跳
间隔为 0 会导致心跳风暴），在此拒绝而非下发。
"""

import json
from typing import NoReturn

from app.core.db import get_conn, write

from .schemas import ConfigSpecView, ConfigValues
from .spec import CONFIG_SPECS, SPECS_BY_KEY, ConfigKind, ConfigScope, defaults


class InvalidConfigError(ValueError):
    """配置入参不合法：未知键、类型不符或超出范围。

    领域层不依赖 HTTP 语义，由路由层转成 problem+json。
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
    # config 存的是与默认值的差异，空对象即全部沿用默认，无需预置快照
    row = get_conn().execute("SELECT revision FROM global_config WHERE id = 1").fetchone()
    if row is None:
        write("INSERT INTO global_config (id, revision, config) VALUES (1, 1, '{}')")


def _reject(reason: str) -> NoReturn:
    """统一以 InvalidConfigError 拒绝一份配置，校验分支只表达拒绝原因。"""
    raise InvalidConfigError(reason)


def validate(values: ConfigValues) -> ConfigValues:
    """校验一份配置改动，返回规范化后的结果；不合法时抛 InvalidConfigError。

    未知键在此拒绝而非静默丢弃：静默丢弃会让调用方以为提交生效了。
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
        # bool 是 int 的子类，先排除，否则整数键传 true 也能通过
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
    """剔掉与默认值相同的项：只存差异，默认值才保持可演进。"""
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
    write("UPDATE global_config SET revision = ?, config = ? WHERE id = 1", (new_revision, json.dumps(overrides)))
    return new_revision, overrides


def get_device_overrides(device_id: str) -> tuple[int, ConfigValues] | None:
    row = get_conn().execute("SELECT revision, config FROM device_config WHERE device_id = ?", (device_id,)).fetchone()
    return (int(row["revision"]), json.loads(row["config"])) if row else None


def put_device_overrides(device_id: str, values: ConfigValues) -> tuple[int, ConfigValues]:
    existing = get_device_overrides(device_id)
    overrides = _as_overrides(values)
    new_revision = (existing[0] if existing else 0) + 1
    write(
        "INSERT OR REPLACE INTO device_config (device_id, revision, config) VALUES (?,?,?)",
        (device_id, new_revision, json.dumps(overrides)),
    )
    return new_revision, overrides


def delete_device_overrides(device_id: str) -> bool:
    return write("DELETE FROM device_config WHERE device_id = ?", (device_id,)).rowcount > 0


def global_effective() -> ConfigValues:
    """全局有效配置：默认 ← 全局覆盖，不含任何设备覆盖。"""
    merged = defaults()
    _, global_overrides = get_global_overrides()
    merged.update(global_overrides)
    return merged


def effective(device_id: str) -> ConfigValues:
    """合并后的有效配置：默认 ← 全局覆盖 ← 设备覆盖。每次现算，见模块注释。"""
    merged = global_effective()
    device_overrides = get_device_overrides(device_id)
    if device_overrides is not None:
        merged.update(device_overrides[1])
    return merged


def device_effective(device_id: str) -> ConfigValues:
    """下发给设备的配置：只含设备侧消费的键，服务端自己的键不占设备的解析路径。"""
    full = effective(device_id)
    return {key: value for key, value in full.items() if SPECS_BY_KEY[key].scope is ConfigScope.DEVICE}


def describe() -> list[ConfigSpecView]:
    """配置登记表的外部描述，供 GET /api/v1/config/spec（客户端据此渲染控件）。"""
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
