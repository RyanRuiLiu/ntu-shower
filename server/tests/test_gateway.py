"""网关闭环测试：待认领 → 信任 → 上线正常；心跳回路；未知类型报错。"""

import json
import uuid

from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession


def _recv_until(ws: WebSocketTestSession, msg_type: str) -> dict:
    while True:
        msg = json.loads(ws.receive_text())
        if msg.get("type") == msg_type:
            return msg


def _hello(device_id: str, version_code: int = 0) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": "link.hello",
            "id": str(uuid.uuid4()),
            "ts": "2026-09-29T12:00:00.000Z",
            "payload": {
                "device_id": device_id,
                "app_version": "0.1.0",
                "version_code": version_code,
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
        assert d["trusted"] is True
        assert d["online"] is True


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
        assert upgrade is not None
        assert upgrade["version"] == "0.3.0"
        assert len(upgrade["sha256"]) == 64


def test_force_release_not_repushed_when_device_already_on_that_version(client: TestClient) -> None:
    """回归：设备已在目标版本时，force 发布不得再次下发（防无限重装循环）。

    事故场景（2026-09-30 真机复现）：force 发布 + release_install 无记录时，
    每次 hello 都携带 upgrade → 设备下载→安装→重启→重连→再下载，约 7 秒一轮。
    根因是 mark_installed_upto 定义了却从未被调用；本用例锁定调用点存在且
    早于 latest_for 执行。
    """
    import io

    from app.modules.ota import repository as ota_repository

    device_id = "dev-loop-guard"
    files = {"file": ("ntu-shower-0.3.1.apk", io.BytesIO(b"PKx34fake"), "application/octet-stream")}
    client.post(
        "/admin/releases",
        headers={"X-Admin-Token": "test-token"},
        data={"version": "0.3.1", "version_code": 8, "mandatory": "false", "force": "true", "target": device_id},
        files=files,
    )

    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id, version_code=8))
        ack = _recv_until(ws, "link.hello_ack")
        assert ack["payload"]["upgrade"] is None, "设备已在该版本，force 发布不得再推送"

    assert ota_repository.is_installed(device_id, "0.3.1") is True
