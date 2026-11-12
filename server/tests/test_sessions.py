"""会话 API：身份链、设备守卫、喷头预检、重复分配拦截、拍照与完整命令往返。"""

import uuid
from threading import Thread
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests import device_link, door_link, frames
from tests.identity import bearer

from app.models.enums import ASSIGNABLE_STATES, DoorState

TRUSTED = "dev-test-0001"
ASSIGN_PAYLOAD = {"door_no": 1, "cert_no": "C", "password": "P"}


def _seed_door(device_id: str, door_no: int, **fields: object) -> None:
    """推一份只含一个喷头的帧；缺省字段表示空闲。"""
    doors: list[dict[str, Any]] = [
        {
            "door_no": door_no,
            "card_no": fields.get("card_no", 0),
            "cert_no": fields.get("cert_no", ""),
            "acc_no": "",
            "open_time": fields.get("open_time"),
            "in_time": fields.get("in_time"),
            "close_time": fields.get("close_time"),
            "bath_time_s": fields.get("bath_time_s", 0),
            "status_raw": fields.get("status_raw", 0),
        }
    ]
    door_link.feed(device_id, {"snapshot_id": str(uuid.uuid4()), "source": "db_watch", "doors": doors})


def test_assign_without_identity_is_unauthenticated(client: TestClient) -> None:
    """操作类接口先于一切业务校验要求身份。"""
    r = client.post(f"/api/v1/devices/{TRUSTED}/assign", json=ASSIGN_PAYLOAD)

    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/unauthenticated")


def test_assign_with_unknown_identity_is_unauthorized(client: TestClient) -> None:
    r = client.post(f"/api/v1/devices/{TRUSTED}/assign", json=ASSIGN_PAYLOAD, headers=bearer("not-a-valid-token"))

    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/unknown-identity")


def test_assign_hits_device_guard_after_identity(client: TestClient, ops_headers: dict[str, str]) -> None:
    """身份守卫之后才是设备存在性检查：不存在的设备 404。"""
    r = client.post(
        "/api/v1/devices/dev-unknown-01/assign",
        json={"door_no": 1, "cert_no": "C", "password": "P"},
        headers=ops_headers,
    )

    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/device-not-found")


def test_assign_rejects_untrusted_device(client: TestClient, ops_headers: dict[str, str]) -> None:
    """设备存在但未信任是 403：客户端要能区分"不存在"与"未授权"。"""
    device_id = "dev-untrusted-1"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(frames.hello_frame(device_id))
        frames.recv_until(ws, "link.hello_ack")

    r = client.post(
        f"/api/v1/devices/{device_id}/assign",
        json={"door_no": 1, "cert_no": "C", "password": "P"},
        headers=ops_headers,
    )

    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/untrusted-device")


def test_assign_rejects_offline_device(client: TestClient, ops_headers: dict[str, str]) -> None:
    """已注册已信任但不在线是 409（设备离线）。"""
    r = client.post(f"/api/v1/devices/{TRUSTED}/assign", json=ASSIGN_PAYLOAD, headers=ops_headers)

    assert r.status_code == 409
    assert r.json()["type"].endswith("/problems/device-offline")


def test_photo_rejects_offline_device(client: TestClient, ops_headers: dict[str, str]) -> None:
    """拍照走同一条设备守卫，无请求体。"""
    r = client.post(f"/api/v1/devices/{TRUSTED}/photo", headers=ops_headers)

    assert r.status_code == 409
    assert r.json()["type"].endswith("/problems/device-offline")


@pytest.mark.parametrize(
    ("label", "raw"),
    [
        ("等待开阀", {"card_no": 1001}),
        ("出水中", {"card_no": 1001, "in_time": "2026-09-30T12:00:00.000Z"}),
        ("暂停", {"card_no": 1001, "bath_time_s": 60, "close_time": "2026-09-30T12:05:00.000Z"}),
        ("报修", {"status_raw": 1}),
        ("停用", {"status_raw": 3}),
    ],
)
def test_assign_rejects_unassignable_door_states(
    client: TestClient,
    ops_headers: dict[str, str],
    admin_headers: dict[str, str],
    label: str,
    raw: dict[str, object],
) -> None:
    """不可分配的状态一律 409，且在下发命令之前就拒（设备不必在线应答）。"""
    with device_link.trusted_online(client, TRUSTED, admin_headers):
        _seed_door(TRUSTED, 1, **raw)
        r = client.post(f"/api/v1/devices/{TRUSTED}/assign", json=ASSIGN_PAYLOAD, headers=ops_headers)

    assert r.status_code == 409, label
    assert r.json()["type"].endswith("/problems/door-not-free")


def test_assign_reports_unreported_door_as_not_found(
    client: TestClient,
    ops_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """喷头从未被上报是 404：编号打错与"喷头被占用"是两回事。"""
    with device_link.trusted_online(client, TRUSTED, admin_headers):
        _seed_door(TRUSTED, 1)
        r = client.post(
            f"/api/v1/devices/{TRUSTED}/assign",
            json={"door_no": 9, "cert_no": "C", "password": "P"},
            headers=ops_headers,
        )

    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/door-not-found")


def test_settle_rejected_without_occupied_door(
    client: TestClient,
    ops_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    with device_link.trusted_online(client, TRUSTED, admin_headers):
        r = client.post(
            f"/api/v1/devices/{TRUSTED}/settle", json={"cert_no": "NOBODY", "password": "P"}, headers=ops_headers
        )

    assert r.status_code == 409
    assert r.json()["type"].endswith("/problems/no-occupied-door")


def test_assign_rejects_duplicate_session(
    client: TestClient,
    ops_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """同一学号已有活跃会话时再分配被拒（409 duplicate-session）。"""
    with device_link.trusted_online(client, TRUSTED, admin_headers):
        _seed_door(
            TRUSTED,
            3,
            card_no=777,
            cert_no="A",
            open_time="2026-09-30T12:00:00.000Z",
            in_time="2026-09-30T12:00:05.000Z",
            bath_time_s=60,
        )
        _seed_door(TRUSTED, 1)
        r = client.post(
            f"/api/v1/devices/{TRUSTED}/assign",
            json={"door_no": 1, "cert_no": "A", "password": "P"},
            headers=ops_headers,
        )

    assert r.status_code == 409
    assert r.json()["type"].endswith("/problems/duplicate-session")


def test_admin_identity_shares_user_endpoints(
    client: TestClient,
    ops_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """管理台凭 session/photo 能力位调用同一路径：与用户面同源同错（perform_* 只有一份）。"""
    with device_link.trusted_online(client, TRUSTED, admin_headers):
        _seed_door(TRUSTED, 1, card_no=1001)
        user_plane = client.post(f"/api/v1/devices/{TRUSTED}/assign", json=ASSIGN_PAYLOAD, headers=ops_headers)
        admin_plane = client.post(f"/api/v1/devices/{TRUSTED}/assign", json=ASSIGN_PAYLOAD, headers=admin_headers)

    assert user_plane.status_code == admin_plane.status_code == 409
    assert user_plane.json()["type"] == admin_plane.json()["type"]


def test_assign_round_trip_returns_device_outcome(
    client: TestClient,
    ops_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    """完整往返：空闲喷头预检通过 → session.assign 下发 → 设备回执透传给调用方。"""
    with device_link.trusted_online(client, TRUSTED, admin_headers) as ws:
        _seed_door(TRUSTED, 1)
        outcome: dict[str, Any] = {}

        def call() -> None:
            outcome["r"] = client.post(f"/api/v1/devices/{TRUSTED}/assign", json=ASSIGN_PAYLOAD, headers=ops_headers)

        thread = Thread(target=call)
        thread.start()
        command = frames.recv_until(ws, "session.command")
        assert command["payload"]["action"] == "session.assign"
        assert command["payload"]["params"] == ASSIGN_PAYLOAD
        ws.send_text(
            frames.frame(
                "session.command_result",
                {"status": "ok", "reason_code": "", "terminal_message": "已分配", "data": None},
                reply_to=command["id"],
            )
        )
        thread.join(timeout=10)

    assert outcome["r"].status_code == 200
    body = outcome["r"].json()
    assert body["status"] == "ok"
    assert body["terminal_message"] == "已分配"


def test_validation_error_is_problem_json(client: TestClient, ops_headers: dict[str, str]) -> None:
    """422 校验错误同样走 RFC 9457 problem+json。"""
    r = client.post(f"/api/v1/devices/{TRUSTED}/assign", json={"door_no": 0}, headers=ops_headers)

    assert r.status_code == 422
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert {"type", "title", "status", "detail"} <= set(body)


def test_assignable_states_are_free_and_selected() -> None:
    """可分配状态集合：free 与 selected；repair/disabled 必须被拒。"""
    assert {DoorState.FREE, DoorState.SELECTED} == ASSIGNABLE_STATES
