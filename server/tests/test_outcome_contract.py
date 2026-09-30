"""命令结果契约的三处对账：登记表、协议 schema、设备端实现。

`status` 与 `data` 的字段名是跨端契约。定义权在 `app/core/outcomes.py`，但**必须与
另外两处一致**——协议 schema 是读协议的人看到的第一手材料，设备端是实际的产出方。
任一处单独改动都会让契约悄悄分叉，而分叉的表现是"某类结果状态永远不出现"或者
"前端取到空值"，两者都不报错。

此前正是这么坏的：schema 写着 `blocked`、服务端实际发 `rejected`、设备端只发
`ok` 和 `failed`——**定义在没人用的那一侧，用的那一侧没定义**。
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

#: 设备端塞进**结果 `data`** 的字段名。两种写法：具名参数 `data = JSONObject().put(...)`，
#: 或位置参数 `CommandOutcome(id, "ok", "", "", JSONObject().put(...))`。
#:
#: 必须限定在 `CommandOutcome` 的构造里，不能只匹配 `JSONObject().put`——那个构造器
#: 在协议各处都在用（`config.ack` 的 `revision`、`link.ping` 的 `sender_time`），
#: 它们不是命令结果的一部分。
_DATA_KEY = re.compile(r'(?:data\s*=\s*|CommandOutcome\([^)]*?)JSONObject\(\)\.put\("([a-z_]+)"')


def _android_source() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in ANDROID_SRC.rglob("*.kt"))


def test_schema_enum_matches_registry() -> None:
    """协议 schema 的 enum 必须与登记表逐字一致。

    它是设备端实现的依据，也是外部读协议的人看到的第一手材料——列着没人用的取值，
    等于告诉人家那个状态存在。
    """
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
    """设备端写的每个结果状态都要在登记表里。

    这条是命门：设备端加一个新状态而没登记，服务端按它分支的地方会静默失效——
    不是报错，是那条分支从此不走。
    """
    unknown = sorted(set(_STATUS.findall(_android_source())) - outcomes.VALUES)
    assert not unknown, f"设备端用了未登记的结果状态：{unknown}（登记表见 app/core/outcomes.py）"


def test_device_data_keys_are_registered() -> None:
    """设备端塞进 `data` 的字段名要与登记表一致。

    写错一个键不会报错——前端按 action 取字段时拿到 `undefined`，界面上只表现为
    那一块空白。
    """
    registered = {key for keys in outcomes.ACTION_DATA.values() for key in keys}
    unknown = sorted(set(_DATA_KEY.findall(_android_source())) - registered)
    assert not unknown, f"设备端返回了未登记的 data 字段：{unknown}"
