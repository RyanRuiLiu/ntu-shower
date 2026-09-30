"""配置存储测试：默认值、覆盖合并、入参校验。

重点覆盖"只存差异"这一条——此前的实现把"默认值 + 改动"的完整快照存进库，
导致快照固化当时的默认值，此后改默认值对已有记录完全无效。生产库因此停在
14 个键的旧版本，后加的 7 个键从未下发过。
"""

from types import ModuleType

import pytest

from app.modules.config import store as config_store
from app.modules.config.spec import CONFIG_SPECS


@pytest.fixture
def store(fresh_db: str) -> ModuleType:
    """初始化表后返回配置存储模块。"""
    config_store.init_tables()
    return config_store


# ── 默认值与合并 ──


def test_defaults_come_from_spec_registry(store: ModuleType) -> None:
    revision, overrides = store.get_global_overrides()

    assert revision == 1
    assert overrides == {}  # 全新库没有任何覆盖
    effective = store.global_effective()
    assert effective["heartbeat_interval_s"] == 30
    assert effective["overlay_enabled"] is True
    assert "bath_guard_enabled" in effective  # 后加的键同样在有效配置里


def test_put_global_stores_only_differences(store: ModuleType) -> None:
    """提交整个表单也只留下真正的差异，未改动的键继续跟随默认值。"""
    revision, overrides = store.put_global_overrides(
        {"heartbeat_interval_s": 15, "ota_check_interval_s": 3600}
    )

    assert revision == 2
    assert overrides == {"heartbeat_interval_s": 15}  # 3600 与默认相同，不固化


def test_default_change_reaches_devices_with_overrides(store: ModuleType) -> None:
    """回归：设备覆盖不得冻结全局变更。

    此前设备覆盖存的是完整快照，里面固化了当时的默认值，于是同一个全局改动
    对有覆盖的设备无效、对没覆盖的设备生效——两台设备行为不同且无人察觉。
    """
    store.put_device_overrides("dev-A", {"overlay_enabled": False})
    store.put_global_overrides({"heartbeat_interval_s": 20})

    assert store.effective("dev-A")["heartbeat_interval_s"] == 20
    assert store.effective("dev-B")["heartbeat_interval_s"] == 20
    assert store.effective("dev-A")["overlay_enabled"] is False  # 设备自己的覆盖仍在


def test_device_override_wins_over_global(store: ModuleType) -> None:
    store.put_global_overrides({"heartbeat_interval_s": 15})
    store.put_device_overrides("dev-1", {"heartbeat_interval_s": 10})

    assert store.effective("dev-1")["heartbeat_interval_s"] == 10
    assert store.effective("dev-other")["heartbeat_interval_s"] == 15


def test_device_override_beats_global_for_other_keys(store: ModuleType) -> None:
    """设备只覆盖一个键时，其余键仍应跟随全局，而不是回落到默认。"""
    store.put_global_overrides({"ota_check_interval_s": 7200})
    store.put_device_overrides("dev-1", {"heartbeat_interval_s": 10})

    effective = store.effective("dev-1")

    assert effective["heartbeat_interval_s"] == 10
    assert effective["ota_check_interval_s"] == 7200


def test_delete_override_falls_back(store: ModuleType) -> None:
    store.put_device_overrides("dev-1", {"heartbeat_interval_s": 10})

    assert store.delete_device_overrides("dev-1") is True
    assert store.effective("dev-1")["heartbeat_interval_s"] == 30
    assert store.delete_device_overrides("dev-1") is False


def test_device_effective_excludes_server_only_keys(store: ModuleType) -> None:
    """服务端自己的键不下发给设备，免得占设备侧的解析路径。"""
    device_view = store.device_effective("dev-1")

    assert "auto_settle_threshold_s" not in device_view
    assert "server_ping_interval_s" not in device_view
    assert "heartbeat_interval_s" in device_view


def test_put_device_override_bumps_revision(store: ModuleType) -> None:
    rev1, _ = store.put_device_overrides("dev-1", {"heartbeat_interval_s": 10})
    rev2, _ = store.put_device_overrides("dev-1", {"heartbeat_interval_s": 11})

    assert rev2 > rev1


# ── 入参校验 ──


def test_unknown_key_is_rejected(store: ModuleType) -> None:
    """未知键此前被静默丢弃：调用方以为提交成功，实际什么都没发生。"""
    with pytest.raises(store.InvalidConfigError, match="未知配置键"):
        store.validate({"bogus_key": 1})


def test_out_of_range_is_rejected(store: ModuleType) -> None:
    # 0 会让设备立刻重发心跳，形成风暴
    with pytest.raises(store.InvalidConfigError, match="不得小于"):
        store.validate({"heartbeat_interval_s": 0})
    with pytest.raises(store.InvalidConfigError, match="不得大于"):
        store.validate({"heartbeat_interval_s": 99999})


def test_boolean_is_not_accepted_as_integer(store: ModuleType) -> None:
    """bool 是 int 的子类，不显式排除就会让 true 通过整数校验。"""
    with pytest.raises(store.InvalidConfigError, match="需要整数"):
        store.validate({"heartbeat_interval_s": True})


def test_wrong_type_is_rejected(store: ModuleType) -> None:
    with pytest.raises(store.InvalidConfigError, match="需要布尔值"):
        store.validate({"overlay_enabled": "yes"})
    with pytest.raises(store.InvalidConfigError, match="需要文本"):
        store.validate({"overlay_message": 42})


def test_text_map_requires_string_pairs(store: ModuleType) -> None:
    with pytest.raises(store.InvalidConfigError, match="需要映射"):
        store.validate({"auto_settle_students": ["not", "a", "map"]})
    with pytest.raises(store.InvalidConfigError, match="都必须是文本"):
        store.validate({"auto_settle_students": {"2021001": 12345}})


def test_valid_values_pass_through(store: ModuleType) -> None:
    checked = store.validate(
        {
            "heartbeat_interval_s": 45,
            "overlay_enabled": False,
            "overlay_message": "处理中",
            "auto_settle_students": {"2021001": "pw"},
        }
    )

    assert checked == {
        "heartbeat_interval_s": 45,
        "overlay_enabled": False,
        "overlay_message": "处理中",
        "auto_settle_students": {"2021001": "pw"},
    }


# ── 登记表 ──


def test_describe_covers_every_spec(store: ModuleType) -> None:
    described = store.describe()

    assert len(described) == len(CONFIG_SPECS)
    assert all({"key", "kind", "scope", "default", "title", "minimum", "maximum"} <= set(item) for item in described)


def test_defaults_returns_independent_maps(store: ModuleType) -> None:
    """TEXT_MAP 的默认值是字典；返回副本才不会让调用方改到登记表本身。"""
    first = store.defaults()
    first["auto_settle_students"]["2021001"] = "pw"

    assert store.defaults()["auto_settle_students"] == {}
