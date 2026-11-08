"""设备自注册：claim 令牌换接入凭据、轮换语义、fail closed 与信任位边界。"""

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tests import device_link

from app.core import config as config_module

CLAIM = "enroll-claim-token-0001"

ENROLL_BODY = {
    "device_id": "dev-new-enroll",
    "app_version": "0.5.0",
    "version_code": 57,
    "os_version": "4.4.4",
    "capabilities": ["session.assign"],
}


@pytest.fixture
def enroll_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_module.settings, "device_enroll_token", CLAIM)


def _enroll(client: TestClient, body: dict, claim: str = CLAIM) -> object:
    return client.post(
        "/api/v1/devices/enroll",
        json=body,
        headers={"Authorization": f"Bearer {claim}"},
    )


def test_enroll_creates_device_and_returns_working_credential(
    client: TestClient, enroll_settings: None, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """新设备注册：档案行建立（未信任）、凭据可完成 WS 握手。"""
    monkeypatch.setattr(config_module.settings, "require_device_token", True)
    r = _enroll(client, ENROLL_BODY)
    assert r.status_code == 200
    body = r.json()
    assert body["device_id"] == ENROLL_BODY["device_id"]
    assert body["token"]

    records = client.get("/api/v1/devices/runtime", headers=admin_headers).json()
    record = next(item for item in records if item["device_id"] == ENROLL_BODY["device_id"])
    assert record["trusted"] is False  # 注册不等于信任

    # 凭据真的能用：带它完成设备 WS 握手
    with client.websocket_connect("/gateway/device", headers={"Authorization": f"Bearer {body['token']}"}) as ws:
        hello = {
            "v": 1,
            "type": "link.hello",
            "id": "h1",
            "ts": "t",
            "payload": {
                "device_id": ENROLL_BODY["device_id"],
                "app_version": "0.5.0",
                "version_code": 57,
                "os_version": "4.4.4",
                "capabilities": ["session.assign"],
            },
        }
        ws.send_text(json.dumps(hello))
        ack = json.loads(ws.receive_text())
    assert ack["type"] == "link.hello_ack"
    assert ack["payload"]["trusted"] is False


def test_enroll_rotates_the_credential(
    client: TestClient, enroll_settings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """再次注册 = 轮换：旧凭据失效、新凭据可用。重装 APK 自助恢复靠它。"""
    monkeypatch.setattr(config_module.settings, "require_device_token", True)
    first = _enroll(client, ENROLL_BODY).json()["token"]
    second = _enroll(client, ENROLL_BODY).json()["token"]
    assert first != second

    rejected = False
    try:
        with client.websocket_connect("/gateway/device", headers={"Authorization": f"Bearer {first}"}) as ws:
            ws.receive_text()
    except WebSocketDisconnect:
        rejected = True  # 旧凭据已在握手层被拒（close 1008）
    assert rejected
    with client.websocket_connect("/gateway/device", headers={"Authorization": f"Bearer {second}"}) as ws:
        ws.send_text(
            json.dumps(
                {
                    "v": 1,
                    "type": "link.hello",
                    "id": "h2",
                    "ts": "t",
                    "payload": {
                        "device_id": ENROLL_BODY["device_id"],
                        "app_version": "0.5.0",
                        "version_code": 57,
                        "os_version": "4.4.4",
                        "capabilities": [],
                    },
                }
            )
        )
        ack = json.loads(ws.receive_text())
    assert ack["type"] == "link.hello_ack"


def test_enroll_rejects_a_wrong_claim(client: TestClient, enroll_settings: None) -> None:
    r = _enroll(client, ENROLL_BODY, claim="not-the-claim")
    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/invalid-device-claim")


def test_enroll_fails_closed_when_not_configured(client: TestClient) -> None:
    """claim 未配置（留空）时注册通道整体关闭：带什么都 401。"""
    r = _enroll(client, ENROLL_BODY)
    assert r.status_code == 401
    assert r.json()["type"].endswith("/problems/invalid-device-claim")


def test_enroll_preserves_the_trusted_bit(
    client: TestClient, enroll_settings: None, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """已在册且已信任的设备（转换搬来的 6 台同型）：注册刷新档案、信任位不动。"""
    device_link.register_trusted_device("dev-trusted-enroll")
    r = _enroll(client, {**ENROLL_BODY, "device_id": "dev-trusted-enroll"})
    assert r.status_code == 200

    records = client.get("/api/v1/devices/runtime", headers=admin_headers).json()
    record = next(item for item in records if item["device_id"] == "dev-trusted-enroll")
    assert record["trusted"] is True


def test_enroll_validates_the_payload(client: TestClient, enroll_settings: None) -> None:
    """device_id 越界 / version_code 负数在入口 422。"""
    r = _enroll(client, {**ENROLL_BODY, "device_id": ""})
    assert r.status_code == 422
    r = _enroll(client, {**ENROLL_BODY, "version_code": -1})
    assert r.status_code == 422
