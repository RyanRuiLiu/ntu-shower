"""设备远程配置：全局默认 + 设备级覆盖。

库里只存与默认值不同的差异项，effective 每次现算"默认 ← 全局覆盖 ← 设备覆盖"：
改默认值即刻对所有设备生效，调用方提交整个表单也不会把未改动的键固化成覆盖。
存完整配置的做法会固化当时的默认值，新增键永远下不下去。

入参按 spec.CONFIG_SPECS 校验类型与范围；越界值会让设备进入异常状态（如字号
低于下限会让文字不可见），在此拒绝而非下发。
"""

import threading
from collections.abc import Callable
from typing import NoReturn

from sqlalchemy.orm import Session

from . import crud
from .schemas import ConfigSpecView, ConfigValues
from .spec import (
    CONFIG_SPECS,
    SPECS_BY_KEY,
    ConfigKind,
    ConfigScope,
    defaults,
    format_weekdays,
    parse_time,
    parse_weekdays,
)

#: 全局覆盖的写锁。`put_global_overrides` 是**覆盖式**写入——收整份配置、剔掉等于默认值
#: 的项、整体写回——所以"读现状 → 改一项 → 写回"这三步之间必须串行：否则两个改动各自
#: 读到旧值，后写的把先写的抹掉（A 刚登记、B 同时登记，名单里只剩 B）。
#:
#: 可重入：`update_global` 在锁内还要调 `put_global_overrides`。锁内**不许 await**，
#: 否则把事件循环卡住。
_global_lock = threading.RLock()


class InvalidConfigError(ValueError):
    """配置入参不合法：未知键、类型不符或超出范围。

    领域层不依赖 HTTP 语义，由路由层转成 problem+json。
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
        checked[key] = _check_value(spec.key, spec.kind, spec.minimum, spec.maximum, spec.choices, value)
    return checked


def _check_value(
    key: str,
    kind: ConfigKind,
    minimum: int | None,
    maximum: int | None,
    choices: tuple[str, ...] | None,
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
        if choices is not None and value not in choices:
            allowed = "、".join(choices)
            _reject(f"{key} 只允许 {allowed}（收到 {value}）")
        return value

    if kind is ConfigKind.TIME:
        if not isinstance(value, str):
            _reject(f"{key} 需要时刻文本，收到 {type(value).__name__}")
        # 先按格式收下：`23:99` 不能被当成整数收走，报错要说清是格式不对。
        minutes = parse_time(value)
        if minutes is None:
            _reject(f"{key} 需要 HH:MM 形式的时刻，收到 {value!r}")
        return value

    if kind is ConfigKind.WEEKDAYS:
        if not isinstance(value, str):
            _reject(f"{key} 需要星期文本，收到 {type(value).__name__}")
        days = parse_weekdays(value)
        if days is None:
            _reject(f"{key} 至少选择一天，取值必须是 1–7，逗号分隔（收到 {value!r}）")
        return format_weekdays(days)

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


def _known(values: ConfigValues) -> ConfigValues:
    """丢掉登记表里已经不存在的项。

    登记表是唯一事实来源：**表里没有的键就是不存在的键**。库里的覆盖可能比表旧——
    删掉一个键之后生产上那份覆盖还在，而下游按键查表（`device_effective`、`_device_scope`）
    查不到就是 KeyError；那两处都在设备上线的路上，表现是设备连上就被断开。
    """
    return {key: value for key, value in values.items() if key in SPECS_BY_KEY}


def get_global_overrides(session: Session) -> tuple[int, ConfigValues]:
    """全局覆盖项（不含默认值）与修订号。"""
    revision, config = crud.get_global(session)
    return (revision, _known(config))


def put_global_overrides(session: Session, values: ConfigValues) -> tuple[int, ConfigValues]:
    """覆盖式写入全局覆盖项；返回新修订号与入库内容。"""
    with _global_lock:
        revision, _previous = get_global_overrides(session)
        overrides = _as_overrides(values)
        new_revision = revision + 1
        crud.put_global(session, new_revision, overrides)
        return new_revision, overrides


def update_global(session: Session, augment: Callable[[ConfigValues], ConfigValues]) -> tuple[int, ConfigValues]:
    """在一个键上做读—改—写，整段串行；返回新的修订号与入库内容。

    `augment` 收到的是**全局有效配置**（默认 ← 全局覆盖，不含设备覆盖），必须返回一份
    同样完整的配置。**只回传要改的那一项是不行的**：`_as_overrides` 会把"没出现在输入
    里"的键当成等于默认值而剔掉，于是管理员设过的开关被一次不相干的改动悄悄清掉。

    读的为什么不是 `effective(device_id)`：那会把设备覆盖一起合并进来，而设备覆盖属于
    另一张表，混进来就会在写全局的时候把某台设备的覆盖当成全局值固化。

    这是 `*_students` 名单增删的**原子入口**（判据不是死代码）：名单是覆盖式整份写回，
    "先读后写"若不在同一把锁里，两个人同时登记会各写各读到的旧名单，后写的把先写的
    抹掉。名单写侧接手时必须走这里，不得绕过锁自拼读改写。
    """
    with _global_lock:
        return put_global_overrides(session, augment(global_effective(session)))


def _device_scoped(values: ConfigValues) -> ConfigValues:
    """丢掉服务端的键：设备覆盖里不该有它们。

    服务端的键在服务器上运行、不按设备区分，写入侧（`config/routes.py` 的
    `_validated_device_overrides`）已经拒掉。读侧再滤一遍，是为了让 `effective(device)`
    与 `global_effective()` 对同一个服务端键**构造上**给出同一个值：后台任务按前者判、
    接口按后者报状态，两者一旦分叉，表现是"开关明明开着却没生效"，而库里看不出异常。
    """
    return {key: value for key, value in values.items() if SPECS_BY_KEY[key].scope is not ConfigScope.SERVER}


def get_device_overrides(session: Session, device_id: str) -> tuple[int, ConfigValues] | None:
    row = crud.get_device(session, device_id)
    return (row[0], _device_scoped(_known(row[1]))) if row else None


def put_device_overrides(session: Session, device_id: str, values: ConfigValues) -> tuple[int, ConfigValues]:
    """覆盖式写入设备覆盖项；返回新修订号与入库内容。

    不加锁：唯一写侧是 HTTP 路由（async，写段无 await），读—改—写在事件循环线程上
    天然原子。全局那份要锁，是因为名单增删经 `update_global` 从后台任务线程进来。
    """
    existing = get_device_overrides(session, device_id)
    overrides = _as_overrides(values)
    new_revision = (existing[0] if existing else 0) + 1
    crud.put_device(session, device_id, new_revision, overrides)
    return new_revision, overrides


def global_effective(session: Session) -> ConfigValues:
    """全局有效配置：默认 ← 全局覆盖，不含任何设备覆盖。"""
    merged = defaults()
    _, global_overrides = get_global_overrides(session)
    merged.update(global_overrides)
    return merged


def effective(session: Session, device_id: str) -> ConfigValues:
    """合并后的有效配置：默认 ← 全局覆盖 ← 设备覆盖。每次现算，见模块注释。"""
    merged = global_effective(session)
    device_overrides = get_device_overrides(session, device_id)
    if device_overrides is not None:
        merged.update(device_overrides[1])
    return merged


def device_effective(session: Session, device_id: str) -> ConfigValues:
    """下发给设备的配置：只含设备侧消费的键，服务端自己的键不占设备的解析路径。"""
    full = effective(session, device_id)
    return {key: value for key, value in full.items() if SPECS_BY_KEY[key].scope is ConfigScope.DEVICE}


def describe() -> list[ConfigSpecView]:
    """配置登记表的外部描述，供 GET /api/v1/config/spec（客户端据此渲染控件）。"""
    current = defaults()
    return [
        {
            "key": spec.key,
            "kind": spec.kind,
            "scope": spec.scope,
            # 取 defaults() 的副本而不是 spec.default 本身：TEXT_MAP 的默认值是可变对象，
            # 同一个对象漏出去一次，被谁就地改一下就污染了登记表（defaults 的注释讲了同一件事）
            "default": current[spec.key],
            "title": spec.title,
            "description": spec.description,
            "minimum": spec.minimum,
            "maximum": spec.maximum,
            "unit": spec.unit,
            "group": spec.group,
            "choices": list(spec.choices) if spec.choices is not None else None,
        }
        for spec in CONFIG_SPECS
    ]
