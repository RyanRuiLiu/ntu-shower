"""自动结算测试：候选挑选（纯函数）+ 完整结算下发 + 重复分配拦截。"""

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.core import config as config_module
from app.core.clock import now_rfc3339
from app.gateway import manager as manager_module
from app.modules.doors import repository as doors_repository
from app.modules.doors import service as doors_service
from app.modules.sessions import auto_settle

SETTLE_THRESHOLD_S = 1140  # 19 分钟：与 DEFAULT_CONFIG.auto_settle_threshold_s 对齐


def _admin(client: TestClient) -> dict:
    return {"X-Admin-Token": "test-token"}


def _seed_session(device_id: str, door_no: int, cert_no: str, minutes_ago: int) -> None:
    """构造一条已占用 minutes_ago 分钟的会话（经真实快照链路落库）。"""
    started = (
        (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )
    doors_service.process_snapshot(
        device_id,
        {
            "snapshot_id": str(uuid.uuid4()),
            "source": "seed",
            "doors": [
                {
                    "door_no": door_no,
                    "card_no": 900 + door_no,
                    "cert_no": cert_no,
                    "acc_no": "",
                    "open_time": started,
                    "in_time": started,
                    "close_time": None,
                    "bath_time_s": minutes_ago * 60,
                    "status_raw": 0,
                }
            ],
        },
    )
    # process_snapshot 以 now 为会话起点，这里直接回填 started_at 以模拟超时占用
    row = doors_repository.get_open_session(device_id, door_no)
    doors_repository.close_session(row["id"], now_rfc3339(), minutes_ago * 60, "seed")
    doors_repository.open_session(device_id, door_no, 900 + door_no, cert_no, started)


def test_pick_candidate_requires_enabled_and_listed(client: TestClient) -> None:
    """否决条件：开关关闭、学号不在名单——任一不满足都不产生候选。"""
    from app.modules.devices import repository as devices_repository

    devices_repository.register("dev-as-1", "0.2.5", 7, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted("dev-as-1", True)
    _seed_session("dev-as-1", 1, "99990052", minutes_ago=30)

    base = {
        "auto_settle_enabled": True,
        "auto_settle_threshold_s": SETTLE_THRESHOLD_S,
        "auto_settle_students": {"99990052": "000000"},
    }

    # 关闭 → 不结算
    assert auto_settle.pick_auto_settle_candidate({**base, "auto_settle_enabled": False}) is None
    # 名单外 → 不结算
    assert auto_settle.pick_auto_settle_candidate({**base, "auto_settle_students": {"other": "x"}}) is None
    # 名单内 + 超时 → 命中（device_id, cert_no, password, duration_s）
    hit = auto_settle.pick_auto_settle_candidate(base)
    assert hit is not None
    assert hit[0] == "dev-as-1"
    assert hit[1] == "99990052"
    assert hit[2] == "000000"
    assert hit[3] >= SETTLE_THRESHOLD_S


def test_pick_candidate_rejects_session_below_threshold(client: TestClient) -> None:
    """时长不足阈值 → 不产生候选。

    此前该场景被写成了 `pick(...) is None or short is None` 形式的断言，
    而 `short` 恒为 None，断言恒真——等于没有测试。
    """
    from app.modules.devices import repository as devices_repository

    devices_repository.register("dev-as-short", "0.2.5", 7, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted("dev-as-short", True)
    _seed_session("dev-as-short", 1, "99990052", minutes_ago=5)  # 5 分钟 < 19 分钟阈值

    config = {
        "auto_settle_enabled": True,
        "auto_settle_threshold_s": SETTLE_THRESHOLD_S,
        "auto_settle_students": {"99990052": "000000"},
    }

    assert auto_settle.pick_auto_settle_candidate(config) is None


def test_scan_once_settles_listed_timeout_session(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.devices import repository as devices_repository

    device_id = "dev-as-2"
    devices_repository.register(device_id, "0.2.5", 7, "4.4.4", [], "127.0.0.1")
    devices_repository.set_trusted(device_id, True)

    class FakeWS:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send_text(self, data: str) -> bool:
            self.sent.append(data)
            return True

    fake = FakeWS()
    monkeypatch.setattr(manager_module.manager, "_connections", {device_id: fake})

    _seed_session(device_id, 2, "99990052", minutes_ago=25)
    monkeypatch.setattr(config_module.settings, "db_path", config_module.settings.db_path)  # no-op 保持结构

    config = {
        "auto_settle_enabled": True,
        "auto_settle_threshold_s": 600,
        "auto_settle_students": {"99990052": "000000"},
    }

    monkeypatch.setattr(auto_settle.config_store, "effective", lambda _device_id: config)

    import asyncio

    async def flow() -> bool:
        task = asyncio.create_task(auto_settle.scan_once())
        await asyncio.sleep(0.05)
        frame = json.loads(fake.sent[-1])
        assert frame["type"] == "session.command"
        assert frame["payload"]["action"] == "session.settle"
        assert frame["payload"]["params"]["cert_no"] == "99990052"
        from app.modules.sessions import service as sessions_service

        sessions_service.resolve(
            frame["id"], {"status": "ok", "reason_code": "", "terminal_message": "已结算", "data": None}
        )
        return await task

    assert asyncio.run(flow()) is True


def test_duplicate_session_rejected(client: TestClient) -> None:
    from app.modules.devices import repository as devices_repository

    device_id = "dev-dup-1"
    devices_repository.register(device_id, "0.2.5", 7, "4.4.4", [], "127.0.0.1")
    hello = json.dumps(
        {
            "v": 1,
            "type": "link.hello",
            "id": str(uuid.uuid4()),
            "ts": "2026-09-30T12:00:00.000Z",
            "payload": {
                "device_id": device_id,
                "app_version": "0.2.5",
                "version_code": 7,
                "os_version": "4.4.4",
                "capabilities": [],
            },
        }
    )
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(hello)
        while json.loads(ws.receive_text()).get("type") != "link.hello_ack":
            pass
    client.post(f"/admin/devices/{device_id}/trust", headers=_admin(client))
    # 信任后重连（与 _connect_trusted 相同的两次连接模式）
    ws = client.websocket_connect("/gateway/device").__enter__()
    ws.send_text(hello)
    while json.loads(ws.receive_text()).get("type") != "link.hello_ack":
        pass
    try:
        # 门 3 被学号 A 占用；门 1 空闲
        doors_service.process_snapshot(
            device_id,
            {
                "snapshot_id": str(uuid.uuid4()),
                "source": "seed",
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
                    },
                    {
                        "door_no": 3,
                        "card_no": 777,
                        "cert_no": "A",
                        "acc_no": "",
                        "open_time": "2026-09-30T12:00:00.000Z",
                        "in_time": "2026-09-30T12:00:05.000Z",
                        "close_time": None,
                        "bath_time_s": 60,
                        "status_raw": 0,
                    },
                ],
            },
        )

        # 同一学号尝试分配另一空闲门 → 拒绝（对齐旧系统 duplicate_session）
        r = client.post(
            "/api/v1/sessions/assign", json={"device_id": device_id, "door_no": 1, "cert_no": "A", "password": "P"}
        )
        assert r.status_code == 409
        assert r.json()["type"].endswith("/problems/duplicate-session")
    finally:
        ws.__exit__(None, None, None)
