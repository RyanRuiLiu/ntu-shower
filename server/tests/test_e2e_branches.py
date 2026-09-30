"""API 端到端分支补测：网关异常帧、命令往返成功路径、OTA 通知、页面重定向。"""

import asyncio
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.core import actions
from app.core import config as config_module
from app.gateway import manager as manager_module
from app.modules.ota import repository as ota_repository
from app.modules.ota import service as ota_service


def _hello(device_id: str) -> str:
    return json.dumps(
        {
            "v": 1,
            "type": "link.hello",
            "id": str(uuid.uuid4()),
            "ts": "2026-09-30T12:00:00.000Z",
            "payload": {
                "device_id": device_id,
                "app_version": "0.2.4",
                "version_code": 6,
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


def _ops_token(client: TestClient) -> dict:
    return {"X-Ops-Token": "test-token"}


def _connect_trusted(client: TestClient, device_id: str) -> WebSocketTestSession:
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        _recv_until(ws, "link.hello_ack")
    client.patch(f"/ops/devices/{device_id}", headers=_ops_token(client), json={"trusted": True})
    ws = client.websocket_connect("/gateway/device").__enter__()
    ws.send_text(_hello(device_id))
    _recv_until(ws, "link.hello_ack")
    return ws


def test_gateway_rejects_non_hello_first_frame(client: TestClient) -> None:
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(json.dumps({"v": 1, "type": "link.ping", "id": str(uuid.uuid4()), "ts": "t", "payload": {}}))
        err = _recv_until(ws, "link.error")
        assert err["payload"]["code"] == "proto.hello_required"


def test_gateway_bad_json_and_unknown_result(client: TestClient) -> None:
    device_id = "dev-frames-1"
    ws = _connect_trusted(client, device_id)
    try:
        ws.send_text("not-json")
        assert _recv_until(ws, "link.error")["payload"]["code"] == "proto.bad_json"

        ws.send_text(_hello(device_id))  # 再发一次 hello → 未知类型（非首帧校验只做一次）
        assert _recv_until(ws, "link.error")["payload"]["code"] == "proto.unknown_type"

        result = json.dumps(
            {
                "v": 1,
                "type": "session.command_result",
                "id": str(uuid.uuid4()),
                "ts": "t",
                "reply_to": "no-such-command",
                "payload": {},
            }
        )
        ws.send_text(result)
        assert _recv_until(ws, "link.error")["payload"]["code"] == "command.unknown_id"
    finally:
        ws.__exit__(None, None, None)


def test_command_round_trip_success_path(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    ops_headers: dict[str, str],
) -> None:
    """命令完整成功往返（HTTP → 设备 → 回执 → 审计 ok）。"""
    monkeypatch.setattr(config_module.settings, "config_ack_timeout_s", 0.2)
    device_id = "dev-roundtrip"

    ws = _connect_trusted(client, device_id)
    try:
        import threading

        outcome: dict = {}

        def call() -> None:
            outcome["r"] = client.post("/api/v1/sessions/photo", json={"device_id": device_id}, headers=ops_headers)

        thread = threading.Thread(target=call)
        thread.start()

        command = _recv_until(ws, "session.command")
        assert command["payload"]["action"] == actions.CAMERA_CAPTURE
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "session.command_result",
                    "id": str(uuid.uuid4()),
                    "ts": "2026-09-30T12:00:01.000Z",
                    "reply_to": command["id"],
                    "payload": {
                        "status": "ok",
                        "reason_code": "",
                        "terminal_message": "已拍照",
                        "data": {"image_base64": "x"},
                    },
                }
            )
        )
        thread.join(timeout=10)
        body = outcome["r"].json()
        assert outcome["r"].status_code == 200
        assert body["status"] == "ok"
        assert body["terminal_message"] == "已拍照"

        audit = client.get("/ops/commands", headers=_ops_token(client), params={"device_id": device_id}).json()
        assert audit[0]["status"] == "ok"
    finally:
        ws.__exit__(None, None, None)


def test_ota_notify_target_devices_filters_and_handles_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeWS:
        def __init__(self, raise_on_send: bool = False) -> None:
            self.raise_on_send = raise_on_send

        async def send_text(self, data: str) -> bool:
            if self.raise_on_send:
                message = "closed"
                raise RuntimeError(message)
            return True

    connections = {
        "dev-a": FakeWS(),
        "dev-b": FakeWS(raise_on_send=True),
        "dev-c": FakeWS(),
    }
    monkeypatch.setattr(manager_module.manager, "_connections", connections)

    notified = asyncio.run(ota_service.notify_target_devices("0.2.5", 7, False, "dev-a, dev-c"))
    assert notified == ["dev-a", "dev-c"]


def test_ota_repository_force_release_ignores_version(client: TestClient) -> None:
    ota_repository.insert_release("9.9.9", 1, "b" * 64, False, True, "dev-force")
    rel = ota_repository.latest_for("dev-force", 999)
    assert rel is not None
    assert rel["force"] is True


def test_api_routes_win_over_the_web_mount(client: TestClient) -> None:
    """网页端挂在根路径，但 API 路由优先。

    这是"挂载在最后"这个顺序约束的守护：若哪天把 mount("/") 提到路由注册之前，
    所有 API 都会变成静态文件查找并返回 404，而这个测试会立刻失败。
    """
    assert client.get("/healthz").status_code == 200

    # 网页端：根路径返回首页而非 404
    root = client.get("/")
    assert root.status_code == 200
    assert "text/html" in root.headers["content-type"]

    # 各 API 前缀仍由路由处理，未被静态文件抢走
    assert client.get("/api/v1/doors").status_code == 200
    assert client.get("/ops/devices").status_code == 401  # 无令牌 → 401 而非 404
    assert client.get("/ota/releases").status_code == 200


def test_settle_roundtrip_success_path(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    ops_headers: dict[str, str],
) -> None:
    """结算成功往返：预检通过（占用中）→ 命令 → 回执 ok。"""
    import threading

    from app.modules.doors import service as doors_service

    device_id = "dev-settle-ok"
    ws = _connect_trusted(client, device_id)
    try:
        doors_service.process_snapshot(
            device_id,
            {
                "snapshot_id": str(uuid.uuid4()),
                "source": "seed",
                "doors": [
                    {
                        "door_no": 1,
                        "card_no": 501,
                        "cert_no": "S1",
                        "acc_no": "",
                        "open_time": "2026-09-30T12:00:00.000Z",
                        "in_time": "2026-09-30T12:00:05.000Z",
                        "close_time": None,
                        "bath_time_s": 300,
                        "status_raw": 0,
                    }
                ],
            },
        )

        outcome: dict = {}

        def call() -> None:
            outcome["r"] = client.post(
                "/api/v1/sessions/settle",
                json={"device_id": device_id, "cert_no": "S1", "password": "P"},
                headers=ops_headers,
            )

        thread = threading.Thread(target=call)
        thread.start()
        command = _recv_until(ws, "session.command")
        assert command["payload"]["action"] == "session.settle"
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "session.command_result",
                    "id": str(uuid.uuid4()),
                    "ts": "2026-09-30T12:00:01.000Z",
                    "reply_to": command["id"],
                    "payload": {"status": "ok", "reason_code": "", "terminal_message": "已结算", "data": None},
                }
            )
        )
        thread.join(timeout=10)
        assert outcome["r"].status_code == 200
        assert outcome["r"].json()["status"] == "ok"
    finally:
        ws.__exit__(None, None, None)


def test_gateway_invalid_snapshot_rejected(client: TestClient) -> None:
    """已信任设备上报坏快照 → link.error(door.invalid_snapshot)。"""
    device_id = "dev-bad-snap"
    ws = _connect_trusted(client, device_id)
    try:
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "door.snapshot",
                    "id": str(uuid.uuid4()),
                    "ts": "2026-09-30T12:00:00.000Z",
                    "payload": {"snapshot_id": "x", "doors": "not-a-list"},
                }
            )
        )
        err = _recv_until(ws, "link.error")
        assert err["payload"]["code"] == "door.invalid_snapshot"
    finally:
        ws.__exit__(None, None, None)


def test_assign_roundtrip(client: TestClient, ops_headers: dict[str, str]) -> None:
    """分配成功往返（HTTP 面）。同通道的第二条会排队而不是被拒，那条语义另测。"""
    import threading

    from app.modules.doors import service as doors_service

    device_id = "dev-assign-ok"
    ws = _connect_trusted(client, device_id)
    try:
        doors_service.process_snapshot(
            device_id,
            {
                "snapshot_id": str(uuid.uuid4()),
                "source": "seed",
                "doors": [
                    {
                        "door_no": 2,
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

        first: dict = {}

        def call_first() -> None:
            first["r"] = client.post(
                "/api/v1/sessions/assign",
                json={"device_id": device_id, "door_no": 2, "cert_no": "S9", "password": "P"},
                headers=ops_headers,
            )

        thread = threading.Thread(target=call_first)
        thread.start()
        command = _recv_until(ws, "session.command")
        assert command["payload"]["action"] == "session.assign"

        # 在途期间的第二条 assign 会在同通道上排队（不拒绝），排队语义与队列上限
        # 由 test_api_surfaces.py 的两个用例覆盖；这里只验证第一条的完整往返。

        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "session.command_result",
                    "id": str(uuid.uuid4()),
                    "ts": "2026-09-30T12:00:01.000Z",
                    "reply_to": command["id"],
                    "payload": {"status": "ok", "reason_code": "", "terminal_message": "已分配", "data": None},
                }
            )
        )
        thread.join(timeout=10)
        assert first["r"].status_code == 200
        assert first["r"].json()["status"] == "ok"
    finally:
        ws.__exit__(None, None, None)
