"""设备影子测试：reported 聚合、desired 合并、影子文档结构。"""

from types import ModuleType

import pytest

from app.modules.config import store as config_store
from app.modules.devices import repository as devices_repository
from app.modules.doors import service as doors_service
from app.modules.shadow import service as shadow_service


@pytest.fixture
def shadow_db(fresh_db: str) -> ModuleType:
    from app.core import db
    from app.modules.doors import repository as doors_repository

    db.init_db()
    devices_repository.init_tables()
    doors_repository.init_tables()
    config_store.init_tables()
    return shadow_service


def test_get_reported_unknown_device(shadow_db: ModuleType) -> None:
    assert shadow_service.get_reported("dev-none") is None


def test_get_reported_merges_device_and_doors(shadow_db: ModuleType) -> None:

    devices_repository.register("dev-s1", "0.2.3", 5, "4.4.4", [], "127.0.0.1")
    doors_service.process_snapshot(
        "dev-s1",
        {
            "snapshot_id": "11111111-1111-4111-8111-111111111111",
            "source": "db_watch",
            "doors": [
                {
                    "door_no": 1,
                    "card_no": 0,
                    "cert_no": "",
                    "acc_no": "",
                    "open_time": None,
                    "in_time": None,
                    "close_time": None,
                    "bath_time_s": 0,
                    "status_raw": 0,
                }
            ],
        },
    )
    reported = shadow_service.get_reported("dev-s1")
    assert reported["device"]["app_version"] == "0.2.3"
    assert reported["doors"][0]["state"] == "free"


def test_get_shadow_desired_reflects_override(shadow_db: ModuleType) -> None:

    devices_repository.register("dev-s2", "0.2.3", 5, "4.4.4", [], "127.0.0.1")
    config_store.put_device_overrides("dev-s2", {"heartbeat_interval_s": 10})
    shadow = shadow_service.get_shadow("dev-s2")
    assert shadow["desired"]["config"]["heartbeat_interval_s"] == 10
    assert shadow["metadata"]["source"]
