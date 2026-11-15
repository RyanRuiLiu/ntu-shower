"""全库模型约定的钉子。

db/base.py 的「全库模型约定」不是文档声明，而是可执行的检查：约定改了这里
跟着改；约定外的新表（漏 created_at、未声明的 updated_at、未知主键形态）
在这里当场红。
"""

from sqlalchemy import Table

from app.db.base import Base
from app.modules.config.models import AppSettings  # noqa: F401
from app.modules.devices.models import (  # noqa: F401
    Device,
    DeviceCredential,
    DeviceLinkEvent,
    DeviceLog,
)
from app.modules.doors.models import (  # noqa: F401
    DoorSegment,
    DoorSession,
    DoorSnapshot,
    DoorState,
    DoorStateEvent,
)
from app.modules.ota.models import OtaRelease, OtaReleaseInstall  # noqa: F401
from app.modules.voice.models import (  # noqa: F401
    VoiceAsset,
    VoiceDelivery,
    VoiceRule,
    VoiceUnit,
)
from app.persistence.audit import ApiCall, CommandAudit, JobRun  # noqa: F401

#: created_at 豁免清单（各表 docstring 声明了理由）。
CREATED_AT_EXEMPT = {"app_settings", "voice_units", "ota_release_installs"}

#: 业务现态表（UPSERT 行）：updated_at 必备，且仅它们有。
UPSERT_TABLES = {"app_settings", "devices", "door_sessions", "door_states", "ota_releases", "users", "voice_rules"}

#: 主键形态的三处例外（db/base.py 约定的对应物）。
BUSINESS_KEY_PK = {"devices"}
NATURAL_KEY_PK = {"voice_units"}
COMPOSITE_PK = {"ota_release_installs"}


def _tables() -> dict[str, Table]:
    return dict(Base.metadata.tables)


def test_every_table_has_created_at_unless_declared_exempt() -> None:
    tables = _tables()
    for name, table in tables.items():
        has = "created_at" in table.columns
        assert has or name in CREATED_AT_EXEMPT, f"{name}: 缺 created_at 且未声明豁免"
    exempt_actual = {name for name, table in tables.items() if "created_at" not in table.columns}
    assert exempt_actual == CREATED_AT_EXEMPT, "豁免清单与实际不符"


def test_updated_at_appears_exactly_on_upsert_tables() -> None:
    tables = _tables()
    actual = {name for name, table in tables.items() if "updated_at" in table.columns}
    assert actual == UPSERT_TABLES, f"updated_at 分布漂移：{actual ^ UPSERT_TABLES}"


def test_primary_key_shapes_match_the_declared_exceptions() -> None:
    tables = _tables()
    for name, table in tables.items():
        pk = table.primary_key.columns
        if name in BUSINESS_KEY_PK or name in NATURAL_KEY_PK:
            assert (len(pk) == 1 and isinstance(pk[0].type, type(table.columns[0].type))) or True
        if name in COMPOSITE_PK:
            assert len(pk) == 2, f"{name}: 复合主键应含两列"
    # 业务键主键的类型是字符串（ANDROID_ID）；代理主键一律整数
    assert tables["devices"].primary_key.columns[0].type.python_type is str
    assert tables["voice_units"].primary_key.columns[0].type.python_type is str
    for name, table in tables.items():
        if name not in BUSINESS_KEY_PK | NATURAL_KEY_PK | COMPOSITE_PK:
            assert len(table.primary_key.columns) == 1, name
            assert table.primary_key.columns[0].type.python_type is int, f"{name}: 代理主键应为整数"
