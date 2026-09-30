"""sessions API 测试：身份要求、预检与错误路径（命令往返由服务层测试+真机 E2E 覆盖）。"""

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

#: 操作类接口要求身份标识：不带即 401，带了但不在名单里即 401（unknown-identity）。
IDENTITY_REQUIRED = "unauthenticated"


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
def _online_device(client: TestClient, device_id: str) -> Iterator[WebSocketTestSession]:
    """建档 → 信任 → 重连，产出一个保持在线会话。

    必须是上下文管理器：手动 __enter__/__exit__ 在断言失败时会漏掉退出，
    连接随之泄漏并让 TestClient 的 portal 挂起。
    """
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        _recv_until(ws, "link.hello_ack")
    client.patch(f"/ops/devices/{device_id}", headers={"X-Ops-Token": "test-token"}, json={"trusted": True})
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        _recv_until(ws, "link.hello_ack")
        yield ws


def test_assign_without_identity_is_rejected(client: TestClient) -> None:
    """操作类接口必须带身份标识：不带即拒绝，且**先于**任何业务校验。"""
    r = client.post(
        "/api/v1/sessions/assign",
        json={"device_id": "dev-unknown", "door_no": 1, "cert_no": "C", "password": "P"},
    )

    assert r.status_code == 401
    assert r.json()["type"].endswith(f"/problems/{IDENTITY_REQUIRED}")


def test_assign_with_unknown_identity_is_rejected(client: TestClient) -> None:
    """标识不在名单里：与"没带标识"区分开，便于排查配置遗漏。"""
    r = client.post(
        "/api/v1/sessions/assign",
        json={"device_id": "dev-unknown", "door_no": 1, "cert_no": "C", "password": "P"},
        headers={"X-Identity": "not-in-any-list"},
    )

    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/unknown-identity")


def test_assign_passes_guard_then_hits_device_guard(
    client: TestClient,
    ops_headers: dict[str, str],
) -> None:
    """过了身份守卫之后才轮到设备存在性检查。"""
    r = client.post(
        "/api/v1/sessions/assign",
        json={"device_id": "dev-unknown", "door_no": 1, "cert_no": "C", "password": "P"},
        headers=ops_headers,
    )

    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/device-not-found")


def test_assign_rejected_when_not_trusted(client: TestClient, ops_headers: dict[str, str]) -> None:
    """设备存在但未信任是 403（不是 404）：客户端要能区分"不存在"与"未授权"。"""
    device_id = "dev-untrusted-1"
    with client.websocket_connect("/gateway/device") as ws:
        ws.send_text(_hello(device_id))
        _recv_until(ws, "link.hello_ack")
        r = client.post(
            "/api/v1/sessions/assign",
            json={"device_id": device_id, "door_no": 1, "cert_no": "C", "password": "P"},
            headers=ops_headers,
        )

    assert r.status_code == 403
    assert r.json()["type"].endswith("/problems/untrusted-device")


def test_assign_blocked_by_precheck_when_door_not_free(client: TestClient, ops_headers: dict[str, str]) -> None:
    device_id = "dev-precheck-1"
    with _online_device(client, device_id):
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
            headers=ops_headers,
        )

    assert r.status_code == 409
    assert r.json()["type"].endswith("/problems/door-not-free")


def test_settle_rejected_without_occupied_door(client: TestClient, ops_headers: dict[str, str]) -> None:
    device_id = "dev-settle-1"
    with _online_device(client, device_id):
        r = client.post(
            "/api/v1/sessions/settle",
            json={"device_id": device_id, "cert_no": "NOBODY", "password": "P"},
            headers=ops_headers,
        )

    assert r.status_code == 409
    assert r.json()["type"].endswith("/problems/no-occupied-door")


#: 管理面的会话操作路径与各自的请求体。
OPS_SESSION_PATHS = {
    "/ops/sessions/assign": {"device_id": "dev-ops-1", "door_no": 1, "cert_no": "C", "password": "P"},
    "/ops/sessions/settle": {"device_id": "dev-ops-1", "cert_no": "C", "password": "P"},
    "/ops/sessions/photo": {"device_id": "dev-ops-1"},
}


def test_ops_session_endpoints_authenticate_by_token(client: TestClient) -> None:
    """管理面的会话操作凭**令牌**授权，不要求 `X-Identity`。

    网页端是运维代操作，不代表某个终端用户。让它去借一个用户身份来通过能力位检查
    是别扭的：那个身份的每一次使用都会被算成"该用户的操作"，审计里就分不清是谁干的。
    """
    for path, payload in OPS_SESSION_PATHS.items():
        # 两个头都不带 → 401。这条只证明它要授权，不区分要哪一种
        missing = client.post(path, json=payload)
        assert missing.status_code == 401, path

        # 只带令牌、不带身份 → 必须越过授权进入业务校验（设备未注册，所以不是 401）
        authed = client.post(path, json=payload, headers={"X-Ops-Token": "test-token"})
        assert authed.status_code != 401, f"{path} 不该再因缺少身份被拒"


def test_two_planes_share_the_same_precheck(
    client: TestClient,
    ops_headers: dict[str, str],
) -> None:
    """两面对同一台设备的同一请求给出**同一个**错误。

    校验逻辑只有一份（`perform_assign` 等），两面只是授权方式不同。若哪天有人往
    其中一面单独加了校验，这条会立刻发现——那正是"逻辑被复制成两份"的开始。
    """
    device_id = "dev-shared-1"
    payload = {"device_id": device_id, "door_no": 1, "cert_no": "C", "password": "P"}
    with _online_device(client, device_id):
        user_plane = client.post("/api/v1/sessions/assign", json=payload, headers=ops_headers)
        ops_plane = client.post("/ops/sessions/assign", json=payload, headers={"X-Ops-Token": "test-token"})

    assert user_plane.status_code == ops_plane.status_code
    assert user_plane.json()["type"] == ops_plane.json()["type"]


# ── 分配前的喷头状态预检 ──


def _seed_door(device_id: str, door_no: int, **fields: object) -> None:
    """推一份只含一扇门的快照；缺省字段表示空闲。"""
    from app.modules.doors import service as doors_service

    doors_service.process_snapshot(
        device_id,
        {
            "snapshot_id": str(uuid.uuid4()),
            "source": "db_watch",
            "doors": [
                {
                    "door_no": door_no,
                    "card_no": fields.get("card_no", 0),
                    "cert_no": "",
                    "acc_no": "",
                    "open_time": None,
                    "in_time": fields.get("in_time"),
                    "close_time": fields.get("close_time"),
                    "bath_time_s": fields.get("bath_time_s", 0),
                    "status_raw": fields.get("status_raw", 0),
                }
            ],
        },
    )


def test_assignable_states_are_free_and_selected() -> None:
    """可分配的状态集合本身。

    单独锁这个集合而不是只看接口返回：接口那边为了不真的下发命令，只能覆盖被拒的分支。
    `selected` 必须在内——它表示"有人在浴室 App 上选中了这扇门"，而 `cardno` 仍是 0，
    没有任何人占用，且这个状态会自行消失（真机实测两次，门自己回到了 free）。
    要求必须 free 会把学生点选中的那几秒挡在门外。

    `repair` / `disabled` 必须不在：旧系统把它们也放行了，那是它的疏漏。
    """
    from app.modules.doors.schemas import ASSIGNABLE_STATES, DoorState

    assert {DoorState.FREE, DoorState.SELECTED} == ASSIGNABLE_STATES


@pytest.mark.parametrize(
    ("label", "raw"),
    [
        ("已进门但未出水", {"card_no": 1001}),
        ("出水中", {"card_no": 1001, "in_time": "2026-09-30T12:00:00.000Z"}),
        ("暂停", {"card_no": 1001, "bath_time_s": 60, "close_time": "2026-09-30T12:05:00.000Z"}),
        ("报修", {"status_raw": 1}),
        ("停用", {"status_raw": 3}),
    ],
)
def test_assign_rejects_unassignable_states(
    client: TestClient,
    ops_headers: dict[str, str],
    label: str,
    raw: dict[str, object],
) -> None:
    """不可分配的状态一律 409，且**在下发命令之前**就拒。

    这条也顺带证明预检没有退化成"先发给设备再说"：拒绝发生在 `send_command_and_wait`
    之前，所以这里不会等设备回执（设备根本没接上）。
    """
    device_id = f"dev-reject-{label.encode('utf-8').hex()[:8]}"
    with _online_device(client, device_id):
        _seed_door(device_id, 1, **raw)
        r = client.post(
            "/api/v1/sessions/assign",
            json={"device_id": device_id, "door_no": 1, "cert_no": "C", "password": "P"},
            headers=ops_headers,
        )

    assert r.status_code == 409, label
    assert r.json()["type"].endswith("/problems/door-not-free")


def test_assign_reports_unreported_door_as_not_found(
    client: TestClient,
    ops_headers: dict[str, str],
) -> None:
    """门从没被上报过是 404，不是 409。

    "这个门号不存在"与"门在、但现在不能分配"是两回事——前者说明设备没上报那扇门
    （门号打错，或设备的采集出了问题），后者说明里面有人。合成一句话会让排查走错方向。

    门号范围不硬编码：存在哪些门是设备上报的事实，不是服务端的常量。
    """
    device_id = "dev-no-such-door"
    with _online_device(client, device_id):
        _seed_door(device_id, 1)  # 只上报 1 号门
        r = client.post(
            "/api/v1/sessions/assign",
            json={"device_id": device_id, "door_no": 9, "cert_no": "C", "password": "P"},
            headers=ops_headers,
        )

    assert r.status_code == 404
    assert r.json()["type"].endswith("/problems/door-not-found")
