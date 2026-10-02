"""命令结果契约的三处对账：登记表、协议 schema、设备端实现。

`status` 与 `data` 的字段名是跨端契约，定义权在 `app/core/outcomes.py`，但必须与
协议 schema（读协议的人看到的第一手材料）和设备端（实际产出方）一致。任一处单独
改动都会让契约悄悄分叉，分叉的表现是"某类结果状态永远不出现"或"前端取到空值"。
"""

import json
import re
from pathlib import Path
from typing import Any, cast

from app.core import outcomes

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "protocol" / "schema" / "session.command_result.json"
ANDROID_SRC = REPO_ROOT / "android" / "app" / "src" / "main" / "java" / "com" / "ryanliu" / "ntushower"

#: 设备端构造结果状态用的字面量，形如 `CommandOutcome(id, "ok", ...)`。
_STATUS = re.compile(r'CommandOutcome\([^,]+,\s*"([a-z_]+)"')

#: 设备端塞进结果 data 的字段名（具名参数或位置参数两种写法）。必须限定在
#: CommandOutcome 的构造里：JSONObject().put 在协议各处都在用，不全是命令结果。
_DATA_KEY = re.compile(r'(?:data\s*=\s*|CommandOutcome\([^)]*?)JSONObject\(\)\.put\("([a-z_]+)"')


def _android_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in ANDROID_SRC.rglob("*.kt"))


def test_schema_enum_matches_registry() -> None:
    """协议 schema 的 enum 必须与登记表逐字一致。"""
    schema = cast("dict[str, Any]", json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))
    status = schema["allOf"][1]["properties"]["payload"]["properties"]["status"]

    assert set(status["enum"]) == set(outcomes.VALUES)


def test_every_status_has_a_chinese_label() -> None:
    for item in outcomes.ALL:
        assert any("\u4e00" <= ch <= "\u9fff" for ch in item.label), item.value


def test_status_values_are_unique() -> None:
    values = [item.value for item in outcomes.ALL]
    assert len(values) == len(set(values))


def test_device_statuses_are_registered() -> None:
    """设备端写的每个结果状态都要在登记表里。"""
    unknown = sorted(set(_STATUS.findall(_android_source())) - outcomes.VALUES)
    assert not unknown, f"设备端用了未登记的结果状态：{unknown}"


def test_device_data_keys_are_registered() -> None:
    """设备端塞进 data 的字段名要与登记表一致；写错一个键前端就取到空值。"""
    registered = {key for keys in outcomes.ACTION_DATA.values() for key in keys}
    unknown = sorted(set(_DATA_KEY.findall(_android_source())) - registered)
    assert not unknown, f"设备端返回了未登记的 data 字段：{unknown}"
