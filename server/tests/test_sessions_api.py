"""sessions API 测试：匿名操作、预检与错误路径（命令往返由服务层测试+真机 E2E 覆盖）。"""

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession


def _hello(device_id: str) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": "link.hello",
            "id": str(uuid.uuid4()),
            "ts": "2026-09-30T12:00:00.000Z",
            "payload": {
                "device_id": device_id,
                "app_version": "0.2.3",
                "version_code": 5,
                "os_version": "4.4.4",
                "capabilities": [],
            },
        }
    )


def _recv_until(ws: WebSocketTestSession, msg_type: str) -> dict:
    while True:
        msg = json.loads(ws.receive_text())
        if msg.get("type") == msg_type:
            return msg


@contextmanager
def _connected_device(client: TestClient, device_id: str) -> Iterator[WebSocketTestSession]:
    """建档 → 信任 → 重连，产出一个保持在线会话。

    必须是上下文管理器：手动 __enter__/__exit__ 在断言失败时会漏掉退出，
    连接随之泄漏并让 TestClient 的 portal 挂起（整个测试会话卡死）。
    """
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        _recv_until(ws, "link.hello_ack")
    client.post(f"/admin/devices/{device_id}/trust", headers={"X-Admin-Token": "test-token"})
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        _recv_until(ws, "link.hello_ack")
        yield ws


def test_assign_anonymous_aligned_with_legacy_model(client: TestClient) -> None:
    """操作接口匿名：鉴权由浴室系统凭证完成（docs/decisions/0034）。"""
    r = client.post(
        "/api/v1/sessions/assign",
        json={"device_id": "dev-unknown", "door_no": 1, "cert_no": "C", "password": "P"},
    )

    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/device-not-found")


def test_assign_rejected_when_not_trusted(client: TestClient) -> None:
    """设备存在但未信任是 403（不是 404）：客户端要能区分"不存在"与"未授权"。"""
    device_id = "dev-untrusted-1"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        _recv_until(ws, "link.hello_ack")
        r = client.post(
            "/api/v1/sessions/assign",
            json={"device_id": device_id, "door_no": 1, "cert_no": "C", "password": "P"},
        )

    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/untrusted-device")


def test_assign_blocked_by_precheck_when_door_not_free(client: TestClient) -> None:
    device_id = "dev-precheck-1"
    with _connected_device(client, device_id):
        from app.modules.doors import service as doors_service

        doors_service.process_snapshot(
            device_id,
            {
                "snapshot_id": str(uuid.uuid4()),
                "source": "db_watch",
                "doors": [
                    {
                        "door_no": 1,
                        "card_no": 1001,
                        "cert_no": "X",
                        "acc_no": "",
                        "open_time": "2026-09-30T12:00:00.000Z",
                        "in_time": None,
                        "close_time": None,
                        "bath_time_s": 0,
                        "status_raw": 0,
                    }
                ],
            },
        )
        r = client.post(
            "/api/v1/sessions/assign",
            json={"device_id": device_id, "door_no": 1, "cert_no": "C", "password": "P"},
        )

    assert r.status_code == 409
    assert r.json()["type"].endswith("/problems/door-not-free")


def test_settle_rejected_without_occupied_door(client: TestClient) -> None:
    device_id = "dev-settle-1"
    with _connected_device(client, device_id):
        r = client.post(
            "/api/v1/sessions/settle",
            json={"device_id": device_id, "cert_no": "NOBODY", "password": "P"},
        )

    assert r.status_code == 409
    assert r.json()["type"].endswith("/problems/no-occupied-door")
