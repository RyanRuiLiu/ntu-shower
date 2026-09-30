"""网关闭环测试：待认领 → 信任 → 上线正常；心跳回路；未知类型报错。"""

import json
import uuid

from fastapi.testclient import TestClient


def _recv_until(ws, msg_type: str) -> dict:
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
            "ts": "2026-09-29T12:00:00.000Z",
            "payload": {
                "device_id": device_id,
                "app_version": "0.1.0",
                "os_version": "4.4.4",
                "capabilities": ["session.assign"],
            },
        }
    )


def _envelope(msg_type: str, payload: dict) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": msg_type,
            "id": str(uuid.uuid4()),
            "ts": "2026-09-29T12:00:00.000Z",
            "payload": payload,
        }
    )


def test_untrusted_device_pends_then_trusted(client: TestClient) -> None:
    device_id = "test-device-0001"

    # 1) 未信任设备：连接成功但 trusted=false
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        ack = json.loads(ws.receive_text())
        assert ack["type"] == "link.hello_ack"
        assert ack["payload"]["trusted"] is False
        assert ack["payload"]["config"]["heartbeat_interval_s"] == 30

        # 2) 心跳回路
        ws.send_text(_envelope("link.ping", {"sender_time": "2026-09-29T12:00:01.000Z"}))
        pong = json.loads(ws.receive_text())
        assert pong["type"] == "link.pong"
        assert pong["reply_to"]

        # 3) 未知类型 → 结构化错误，不断开
        ws.send_text(_envelope("made.up", {}))
        err = json.loads(ws.receive_text())
        assert err["type"] == "link.error"
        assert err["payload"]["code"] == "proto.unknown_type"

    # 4) 后台信任（需令牌）
    r = client.post(f"/admin/devices/{device_id}/trust", headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 200
    assert r.json()["trusted"] is True

    # 5) 错误令牌被拒
    r = client.get("/admin/devices", headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401

    # 6) 重新连接：trusted=true，出现在设备列表
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        ack = json.loads(ws.receive_text())
        assert ack["payload"]["trusted"] is True
        r = client.get("/admin/devices", headers={"X-Admin-Token": "test-token"})
        devices = r.json()
        d = next(x for x in devices if x["device_id"] == device_id)
        assert d["trusted"] is True and d["online"] is True


def test_ota_upgrade_delivered_in_handshake(client: TestClient) -> None:
    """发布新版本 → 设备重连握手直接携带 upgrade 元数据（重连即检查）。"""
    import io

    device_id = "dev-ota-1"
    files = {"file": ("ntu-shower-0.3.0.apk", io.BytesIO(b"PKx34fake"), "application/octet-stream")}
    client.post(
        "/admin/releases",
        headers={"X-Admin-Token": "test-token"},
        data={"version": "0.3.0", "version_code": 6, "mandatory": "false", "force": "false", "target": device_id},
        files=files,
    )

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        ack = _recv_until(ws, "link.hello_ack")
        upgrade = ack["payload"]["upgrade"]
        assert upgrade is not None and upgrade["version"] == "0.3.0"
        assert len(upgrade["sha256"]) == 64
