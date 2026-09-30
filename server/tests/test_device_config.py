"""配置存储测试：全局默认、设备覆盖、有效配置合并。"""

import pytest

from app.modules.config import store as device_config


@pytest.fixture()
def store(fresh_db):
    device_config.init_tables()
    return device_config


def test_defaults(store) -> None:
    revision, config = store.get_global()
    assert revision == 1
    assert config["heartbeat_interval_s"] == 30
    assert config["overlay_enabled"] is True


def test_put_global_bumps_revision_and_merges(store) -> None:
    rev1, _ = store.get_global()
    rev2, config = store.put_global({"heartbeat_interval_s": 15, "bogus_key": 1})
    assert rev2 > rev1
    assert config["heartbeat_interval_s"] == 15
    assert "bogus_key" not in config  # 未知键不进入配置
    assert config["ota_check_interval_s"] == 3600  # 未覆盖键保持默认


def test_device_override_merges_over_global(store) -> None:
    store.put_global({"heartbeat_interval_s": 15})
    store.put_device_override("dev-1", {"heartbeat_interval_s": 10})
    effective = store.effective("dev-1")
    assert effective["heartbeat_interval_s"] == 10  # 设备覆盖生效
    assert effective["ota_check_interval_s"] == 3600  # 其余键来自全局
    other = store.effective("dev-other")
    assert other["heartbeat_interval_s"] == 15  # 其他设备不受影响


def test_delete_override_falls_back_to_global(store) -> None:
    store.put_device_override("dev-1", {"heartbeat_interval_s": 10})
    assert store.delete_device_override("dev-1") is True
    assert store.effective("dev-1")["heartbeat_interval_s"] == 30
    assert store.delete_device_override("dev-1") is False
