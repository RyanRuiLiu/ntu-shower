"""远程配置：默认值与覆盖合并、只存差异、入参校验、推送回执与 spec 渲染契约。"""

import json
import threading
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core import config as config_module
from app.core.db import get_conn, write
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


def test_update_global_keeps_the_keys_it_did_not_touch(fresh_db: str) -> None:
    """在名单上做一次增删，别的键一个不动。

    `_as_overrides` 判"哪些偏离默认值"看的是**递进去的那份配置**，所以 `augment` 必须
    回传完整的全局有效配置。只回传要改的那一项，管理员设过的开关会因为"没出现在输入里"
    而被当成等于默认值剔掉——表现是"我加个人进名单，自动结算被关了"。
    """
    store.put_global_overrides({"auto_settle_enabled": True, "auto_settle_students": {"99990052": "000000"}})

    store.update_global(
        lambda config: {**config, "auto_settle_students": {"99990052": "000000", "99990057": "111111"}}
    )

    effective = store.global_effective()
    assert effective["auto_settle_enabled"] is True
    assert effective["auto_settle_students"] == {"99990052": "000000", "99990057": "111111"}


def test_update_global_drops_a_roster_emptied_back_to_default(fresh_db: str) -> None:
    """名单清空到等于默认值时那个键从覆盖里消失，回落默认——语义相同。"""
    store.put_global_overrides({"auto_settle_students": {"99990052": "000000"}})

    store.update_global(lambda config: {**config, "auto_settle_students": {}})

    _revision, overrides = store.get_global_overrides()
    assert "auto_settle_students" not in overrides
    assert store.global_effective()["auto_settle_students"] == {}


#: 生产 `global_config` 里真实存在的那一组覆盖键（2026-10-04 只读查询所得，值取同形状的
#: 假数据）。**四个开关在线上全是开的、四份名单各有一人**，所以下面两条测试用的是真事：
#: `update_global` 少带回任何一个键，那个键就会因为"等于默认值"被剔掉——线上就是关掉一个
#: 功能、删掉一个人的登记。
PRODUCTION_OVERRIDES = {
    "auto_settle_enabled": True,
    "renew_session_enabled": True,
    "renew_wait_enabled": True,
    "extend_hours_enabled": True,
    "automation_trace_enabled": True,
    "boot_time_sync_enabled": True,
    "auto_settle_students": {"99990052": "000000"},
    "renew_session_students": {"99990057": "111111"},
    "renew_wait_students": {"99990053": "000000"},
    "extend_hours_students": {"2217110058": "111111"},
}


def test_update_global_keeps_every_override_that_production_actually_has(fresh_db: str) -> None:
    """改一份名单，其余九个覆盖键逐键不变。

    键组取自线上（不是随手编几个）：少带回一个，那个开关或那份名单就会被写回默认值。
    """
    store.put_global_overrides(dict(PRODUCTION_OVERRIDES))
    before = store.get_global_overrides()[1]
    assert before == PRODUCTION_OVERRIDES, "夹具没能完整写进库里，后面的比对就不成立"

    new_cert = "99990099"
    store.update_global(
        lambda config: {
            **config,
            "auto_settle_students": {**config["auto_settle_students"], new_cert: "000000"},
        }
    )

    after = store.get_global_overrides()[1]
    for key, value in before.items():
        if key == "auto_settle_students":
            continue
        assert after[key] == value, f"{key} 被这次登记改动了"
    assert after["auto_settle_students"] == {**before["auto_settle_students"], new_cert: "000000"}


def test_concurrent_joins_keep_every_entry(fresh_db: str) -> None:
    """并发登记一个不丢。

    `update_global` 是"读整份 → 改一项 → 整份写回"，三步不在同一把锁里的话，两个线程会
    各自读到同一份旧名单，后写的把先写的整个替换掉——名单里只剩最后那个人。**必须用真
    线程**：顺序调用测不出这件事，每一步都能看到上一步的结果。
    """
    cert_nos = [f"2530110{index:03d}" for index in range(16)]
    start = threading.Barrier(len(cert_nos))

    def join(cert_no: str) -> None:
        start.wait()
        store.update_global(
            lambda config: {
                **config,
                "auto_settle_students": {**config["auto_settle_students"], cert_no: "000000"},
            }
        )

    threads = [threading.Thread(target=join, args=(cert_no,)) for cert_no in cert_nos]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert set(store.global_effective()["auto_settle_students"]) == set(cert_nos)


def test_device_overrides_drop_server_keys(fresh_db: str) -> None:
    """设备覆盖里混进服务端键时，读侧把它滤掉。

    服务端的键在服务器上运行、不按设备区分，写入侧（config 域的
    `_validated_device_overrides`）已经拒掉。读侧再滤一遍是为了让 `effective(device)`
    与 `global_effective()` **构造上**给出同一个值：后台任务按前者判、接口按后者报状态，
    一旦分叉，表现是"开关明明开着却没生效"，而库里看不出任何异常。
    """
    write(
        "INSERT OR REPLACE INTO device_config (device_id, revision, config) VALUES (?,?,?)",
        ("dev-A", 7, json.dumps({"overlay_enabled": False, "auto_settle_enabled": False})),
    )

    _revision, overrides = store.get_device_overrides("dev-A")

    assert overrides == {"overlay_enabled": False}
    assert store.effective("dev-A")["auto_settle_enabled"] == store.global_effective()["auto_settle_enabled"]


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


def test_unknown_keys_in_stored_overrides_are_ignored(fresh_db: str) -> None:
    """库里存着登记表已经不认的键时，生效值里不能有它、也不能抛。

    这一条锁的是一条真实的失效路径：**删掉一个键之后，生产上那份覆盖还在**。而
    `device_effective` 与配置路由的 `_device_scope` 都要按键查登记表，查不到就是
    KeyError——两处都在设备上线的路上（`link.hello_ack`），表现是设备一连上就被断开。

    直接写库而不走 `put_*`：那两处入口本来就该拒绝未知键，这里要造的是"表变了、库没变"。
    """
    store.put_global_overrides({"heartbeat_interval_s": 45})
    conn = get_conn()
    conn.execute(
        "UPDATE global_config SET config = ? WHERE id = 1",
        ('{"heartbeat_interval_s": 45, "gone_key": 1}',),
    )
    conn.commit()

    effective = store.global_effective()
    assert effective["heartbeat_interval_s"] == 45
    assert "gone_key" not in effective
    assert store.device_effective("dev-1")["heartbeat_interval_s"] == 45


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


def test_validate_accepts_and_rejects_times(fresh_db: str) -> None:
    """时刻键：先看格式再看范围，两者报的是不同的错。

    分开报是有用的：填错格式（`23:60`）与填得太早（`22:59`）是两种失误，混成一句
    "不在范围内"会让人去范围里找问题，而问题在写法上。
    """
    assert store.validate({"extend_hours_at": "23:49"}) == {"extend_hours_at": "23:49"}

    with pytest.raises(store.InvalidConfigError, match="HH:MM"):
        store.validate({"extend_hours_at": "23:60"})
    with pytest.raises(store.InvalidConfigError, match="HH:MM"):
        store.validate({"extend_hours_at": "2349"})
    with pytest.raises(store.InvalidConfigError, match="需要时刻文本"):
        store.validate({"extend_hours_at": 2349})
    with pytest.raises(store.InvalidConfigError, match="不得早于 23:00"):
        store.validate({"extend_hours_at": "22:59"})


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
