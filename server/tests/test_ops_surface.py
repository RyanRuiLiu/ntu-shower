"""管理面接口测试：改名（PATCH）、调试执行往返、OTA 发布上传。"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.core import actions


def _recv_until(ws: WebSocketTestSession, msg_type: str) -> dict:
    while True:
        msg = json.loads(ws.receive_text())
        if msg.get("type") == msg_type:
            return msg


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
                "capabilities": [actions.DEVICE_EXEC],
            },
        }
    )


@pytest.fixture
def client(fresh_db: str) -> TestClient:
    from app.main import create_app

    return TestClient(create_app())


def test_rename_device_via_patch(client: TestClient) -> None:
    device_id = "dev-rename-1"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        _ = json.loads(ws.receive_text())
        ws.__exit__(None, None, None)

    r = client.patch(f"/ops/devices/{device_id}", headers={"X-Ops-Token": "test-token"}, json={"name": "二楼浴室"})
    assert r.status_code == 200
    assert r.json()["name"] == "二楼浴室"
    devices = client.get("/ops/devices", headers={"X-Ops-Token": "test-token"}).json()
    assert next(d for d in devices if d["device_id"] == device_id)["name"] == "二楼浴室"


def _recv_until(ws: WebSocketTestSession, msg_type: str) -> dict:
    while True:
        msg = json.loads(ws.receive_text())
        if msg.get("type") == msg_type:
            return msg
