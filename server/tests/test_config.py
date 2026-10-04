"""远程配置：默认值与覆盖合并、只存差异、入参校验、推送回执与 spec 渲染契约。"""

import json
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core import config as config_module
from app.modules.config import store
from app.modules.config.spec import CONFIG_SPECS

# ── 存储层：默认值、覆盖、只存差异 ──


def test_defaults_come_from_spec_registry(fresh_db: str) -> None:
    revision, overrides = store.get_global_overrides()

    assert revision == 1
    assert overrides == {}
    effective = store.global_effective()
    assert effective["heartbeat_interval_s"] == 30
    assert effective["overlay_enabled"] is True


def test_put_global_stores_only_differences(fresh_db: str) -> None:
    """提交整个表单也只留下真正的差异：默认值保持可演进。"""
    revision, overrides = store.put_global_overrides({"heartbeat_interval_s": 15, "ota_check_interval_s": 3600})

    assert revision == 2
    assert overrides == {"heartbeat_interval_s": 15}


def test_default_change_reaches_devices_with_overrides(fresh_db: str) -> None:
    """设备覆盖不得冻结全局变更：默认值改了，有覆盖与无覆盖的设备都要跟上。"""
    store.put_device_overrides("dev-A", {"overlay_enabled": False})
    store.put_global_overrides({"heartbeat_interval_s": 20})

    assert store.effective("dev-A")["heartbeat_interval_s"] == 20
    assert store.effective("dev-B")["heartbeat_interval_s"] == 20
    assert store.effective("dev-A")["overlay_enabled"] is False


def test_device_override_wins_over_global(fresh_db: str) -> None:
    store.put_global_overrides({"heartbeat_interval_s": 15})
    store.put_device_overrides("dev-1", {"heartbeat_interval_s": 10})

    assert store.effective("dev-1")["heartbeat_interval_s"] == 10
    assert store.effective("dev-other")["heartbeat_interval_s"] == 15


def test_device_override_leaves_other_keys_following_global(fresh_db: str) -> None:
    store.put_global_overrides({"ota_check_interval_s": 7200})
    store.put_device_overrides("dev-1", {"heartbeat_interval_s": 10})

    effective = store.effective("dev-1")
    assert effective["heartbeat_interval_s"] == 10
    assert effective["ota_check_interval_s"] == 7200


def test_delete_override_falls_back_to_global(fresh_db: str) -> None:
    store.put_device_overrides("dev-1", {"heartbeat_interval_s": 10})

    assert store.delete_device_overrides("dev-1") is True
    assert store.effective("dev-1")["heartbeat_interval_s"] == 30
    assert store.delete_device_overrides("dev-1") is False


def test_device_effective_excludes_server_only_keys(fresh_db: str) -> None:
    """服务端自己的键不下发给设备。"""
    device_view = store.device_effective("dev-1")

    assert "renew_session_threshold_s" not in device_view
    assert "ping_interval_s" not in device_view
    assert "heartbeat_interval_s" in device_view


def test_put_device_override_bumps_revision(fresh_db: str) -> None:
    rev1, _ = store.put_device_overrides("dev-1", {"heartbeat_interval_s": 10})
    rev2, _ = store.put_device_overrides("dev-1", {"heartbeat_interval_s": 11})

    assert rev2 > rev1


# ── 校验 ──


def test_validate_rejects_invalid_values(fresh_db: str) -> None:
    """未知键、越界、类型不符、布尔冒充整数、映射值非文本一律拒绝。"""
    with pytest.raises(store.InvalidConfigError, match="未知配置键"):
        store.validate({"bogus_key": 1})
    with pytest.raises(store.InvalidConfigError, match="不得小于"):
        store.validate({"heartbeat_interval_s": 0})
    with pytest.raises(store.InvalidConfigError, match="不得大于"):
        store.validate({"heartbeat_interval_s": 99999})
    with pytest.raises(store.InvalidConfigError, match="需要整数"):
        store.validate({"heartbeat_interval_s": True})
    with pytest.raises(store.InvalidConfigError, match="需要布尔值"):
        store.validate({"overlay_enabled": "yes"})
    with pytest.raises(store.InvalidConfigError, match="需要文本"):
        store.validate({"overlay_message": 42})
    with pytest.raises(store.InvalidConfigError, match="需要映射"):
        store.validate({"renew_wait_students": ["not", "a", "map"]})
    with pytest.raises(store.InvalidConfigError, match="都必须是文本"):
        store.validate({"renew_wait_students": {"2021001": 12345}})


def test_validate_passes_normalized_values(fresh_db: str) -> None:
    checked = store.validate(
        {
            "heartbeat_interval_s": 45,
            "overlay_enabled": False,
            "overlay_message": "处理中",
            "renew_wait_students": {"2021001": "pw"},
        }
    )

    assert checked == {
        "heartbeat_interval_s": 45,
        "overlay_enabled": False,
        "overlay_message": "处理中",
        "renew_wait_students": {"2021001": "pw"},
    }


def test_defaults_returns_independent_maps(fresh_db: str) -> None:
    """TEXT_MAP 默认值是可变对象：返回副本才不会污染登记表。"""
    first = store.defaults()
    first["renew_wait_students"]["2021001"] = "pw"

    assert store.defaults()["renew_wait_students"] == {}


def test_describe_covers_every_spec(fresh_db: str) -> None:
    described = store.describe()

    assert len(described) == len(CONFIG_SPECS)
    assert all(
        {"key", "kind", "scope", "default", "title", "description", "minimum", "maximum", "unit", "group"} <= set(item)
        for item in described
    )


# ── API 层：spec 契约、全局与设备配置 ──


def test_config_spec_rendering_contract(client: TestClient, admin_headers: dict[str, str]) -> None:
    """spec 端点渲染登记表：分区、分组与每键的控件元数据。"""
    spec = client.get("/api/v1/config/spec", headers=admin_headers).json()

    # 两个页签按"这个键去哪"分：服务端的键在服务器上跑、不下发；设备的键会推到设备。
    assert [s["key"] for s in spec["sections"]] == ["server", "device"]
    assert [s["title"] for s in spec["sections"]] == ["服务端业务", "设备下发"]
    groups = {g["key"]: g for g in spec["groups"]}
    assert groups["gateway"]["section"] == "device"
    assert groups["renew_wait"]["section"] == "server"
    assert groups["server_bridge"]["section"] == "server"
    heartbeat = next(item for item in spec["specs"] if item["key"] == "heartbeat_interval_s")
    assert heartbeat["kind"] == "int"
    assert heartbeat["scope"] == "device"
    assert heartbeat["minimum"] == 5
    assert heartbeat["unit"] == "秒"
    assert heartbeat["group"] == "gateway"


def test_device_override_rejects_server_only_keys(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    fresh_db: str,
) -> None:
    """服务端的键不按设备区分，写进单台覆盖就该被拒。

    收进来的话：`device_effective` 会把它滤掉、永不下发，而 `effective` 会把它当真——
    于是"同一份阈值、每台设备各不相同"这种既推不出去又说得出口的状态就存在了。
    """
    # 设备在线但不回应 ack：推送要等它，默认 15 秒会把测试拖成超时
    monkeypatch.setattr(config_module.settings, "config_ack_timeout_s", 0.2)
    device_id = "dev-no-server-key"

    with helpers.trusted_online(client, device_id, admin_headers):
        rejected = client.put(
            f"/api/v1/devices/{device_id}/config",
            headers=admin_headers,
            json={"config": {"renew_wait_threshold_s": 200}},
        )
        assert rejected.status_code == 422
        assert "renew_wait_threshold_s" in rejected.json()["detail"]

        # 设备域的键照旧可以覆盖
        accepted = client.put(
            f"/api/v1/devices/{device_id}/config",
            headers=admin_headers,
            json={"config": {"heartbeat_interval_s": 20}},
        )
        assert accepted.status_code == 200
        override = client.get(f"/api/v1/devices/{device_id}/config", headers=admin_headers).json()
        assert override["overrides"] == {"heartbeat_interval_s": 20}


def test_config_surface_requires_admin(client: TestClient, ops_headers: dict[str, str]) -> None:
    """配置域整个是 admin 面：无身份 401，非 admin 403。"""
    assert client.get("/api/v1/config").status_code == 401
    assert client.get("/api/v1/config/spec", headers=ops_headers).status_code == 403
    assert client.put("/api/v1/config", json={"config": {}}).status_code == 401


def test_global_config_roundtrip(client: TestClient, admin_headers: dict[str, str]) -> None:
    r = client.get("/api/v1/config", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["revision"] == 1

    updated = client.put("/api/v1/config", headers=admin_headers, json={"config": {"heartbeat_interval_s": 45}})
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2

    global_cfg = client.get("/api/v1/config", headers=admin_headers).json()
    assert global_cfg["config"]["heartbeat_interval_s"] == 45
    assert global_cfg["config"]["ota_check_interval_s"] == 3600  # 未覆盖的键仍是默认值


def test_global_update_rejects_invalid_values(client: TestClient, admin_headers: dict[str, str]) -> None:
    unknown = client.put("/api/v1/config", headers=admin_headers, json={"config": {"nonexistent_key": 1}})
    assert unknown.status_code == 422
    assert unknown.json()["type"].endswith("/problems/invalid-config")

    out_of_range = client.put("/api/v1/config", headers=admin_headers, json={"config": {"heartbeat_interval_s": 0}})
    assert out_of_range.status_code == 422


def test_global_update_returns_noticed_devices(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PUT /config 返回 notified 数组：每个在线设备一条推送回执，没有在线设备则为空。"""
    monkeypatch.setattr(config_module.settings, "config_ack_timeout_s", 0.2)

    empty = client.put("/api/v1/config", headers=admin_headers, json={"config": {"heartbeat_interval_s": 44}})
    assert empty.status_code == 200
    assert empty.json()["notified"] == []

    from app.core.connections import manager

    monkeypatch.setattr(manager, "_connections", {"dev-cfg-anon": helpers.FakeGatewayWS()})
    notified = client.put(
        "/api/v1/config", headers=admin_headers, json={"config": {"heartbeat_interval_s": 45}}
    ).json()["notified"]
    assert notified == [{"device_id": "dev-cfg-anon", "notified": True, "applied": False, "reason": "ack timeout"}]


def test_device_config_ack_round_trip(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """设备在线并回 ack：推送已送达且已生效。"""
    monkeypatch.setattr(config_module.settings, "config_ack_timeout_s", 2.0)
    outcome: dict[str, Any] = {}

    with helpers.trusted_online(client, "dev-cfg-ack", admin_headers) as ws:

        def call() -> None:
            outcome["r"] = client.put(
                "/api/v1/devices/dev-cfg-ack/config",
                headers=admin_headers,
                json={"config": {"heartbeat_interval_s": 20}},
            )

        thread = threading.Thread(target=call)
        thread.start()
        while True:
            frame = json.loads(ws.receive_text())
            if frame["type"] == "config.update":
                break
        ws.send_text(json.dumps({"v": 1, "type": "config.ack", "id": "ack-1", "ts": "t", "payload": {"revision": 1}}))
        thread.join(timeout=10)

    body = outcome["r"].json()
    assert outcome["r"].status_code == 200
    assert body["notified"] is True
    assert body["applied"] is True
    assert body["config"]["heartbeat_interval_s"] == 20


def test_device_config_lifecycle_without_ack(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """设备在线但不应答：notified 为真、applied 为假并带原因；覆盖与删除照常落库。"""
    monkeypatch.setattr(config_module.settings, "config_ack_timeout_s", 0.2)

    with helpers.trusted_online(client, "dev-cfg-1", admin_headers):
        r = client.put(
            "/api/v1/devices/dev-cfg-1/config",
            headers=admin_headers,
            json={"config": {"heartbeat_interval_s": 20}},
        )
        body = r.json()
        assert {k: body[k] for k in ("device_id", "revision", "notified", "applied", "reason")} == {
            "device_id": "dev-cfg-1",
            "revision": 1,
            "notified": True,
            "applied": False,
            "reason": "ack timeout",
        }

        override = client.get("/api/v1/devices/dev-cfg-1/config", headers=admin_headers).json()
        assert override["overrides"] == {"heartbeat_interval_s": 20}
        assert override["config"]["heartbeat_interval_s"] == 20

        removed = client.delete("/api/v1/devices/dev-cfg-1/config", headers=admin_headers)
        assert removed.json()["override_removed"] is True

    after = client.get("/api/v1/devices/dev-cfg-1/config", headers=admin_headers).json()
    assert after["overrides"] is None
    assert after["config"]["heartbeat_interval_s"] == 30


def test_device_config_rejected_when_offline(client: TestClient, admin_headers: dict[str, str], fresh_db: str) -> None:
    helpers.register_trusted_device("dev-cfg-off")

    r = client.put("/api/v1/devices/dev-cfg-off/config", headers=admin_headers, json={"config": {}})

    assert r.status_code == 409
