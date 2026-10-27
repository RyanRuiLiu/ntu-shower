"""语音播报：固定音频素材库、分段规则、事件投递与设备预下载。

不跑真实转码：`audio.normalize_to_mp3` 在用例里换成确定性替身，上传内容直接成为
MP3 字节；`audio.py` 自身的外部命令边界另有单元测试（test_voice_audio.py）。
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core.clock import now_rfc3339
from app.core.config import settings
from app.core.db import get_conn
from app.core.problems import INVALID_VOICE_RULE, ApiProblemError
from app.modules.devices import service as devices_service
from app.modules.doors.events import DoorEvent, DoorEventKind
from app.modules.sessions import service as sessions_service
from app.modules.sessions.schemas import CommandOutcome
from app.modules.voice import audio, repository, schemas, service

DEVICE_ID = "dev-voice"
OTHER_DEVICE_ID = "dev-voice-other"

DIGIT_NAMES = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九"]


@pytest.fixture
def voice_devices(fresh_db: str) -> None:
    helpers.register_trusted_device(DEVICE_ID)
    helpers.register_trusted_device(OTHER_DEVICE_ID)


@pytest.fixture
def fake_normalize(monkeypatch: pytest.MonkeyPatch) -> None:
    """把上传源字节原样包成 MP3，并给出可预测的时长。"""

    def normalize(source: Path, target: Path) -> int:
        data = source.read_bytes()
        target.write_bytes(b"ID3" + data)
        return 1_000 + len(data)

    monkeypatch.setattr(audio, "normalize_to_mp3", normalize)


@pytest.fixture
def assets(
    client: TestClient, admin_headers: dict[str, str], voice_devices: None, fake_normalize: None
) -> dict[str, str]:
    """上传 0–9 数字素材和一段固定音频，返回名称 → 素材 id。"""
    result: dict[str, str] = {}
    for digit, name in enumerate(DIGIT_NAMES):
        result[name] = _upload(client, admin_headers, name, f"digit-{digit}".encode(), str(digit))["asset_id"]
    result["请到"] = _upload(client, admin_headers, "请到", b"please")["asset_id"]
    result["欢迎"] = _upload(client, admin_headers, "欢迎", b"welcome")["asset_id"]
    return result


def _upload(
    client: TestClient,
    headers: dict[str, str],
    name: str,
    payload: bytes = b"x",
    digit: str = "",
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/voice/assets",
        headers=headers,
        data={"name": name, "digit": digit},
        files={"file": ("input.wav", payload, "audio/wav")},
    )
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def _asset_segment(asset_id: str) -> dict[str, Any]:
    return {"kind": "asset", "asset_id": asset_id}


def _payload(asset_id: str, **overrides: object) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "devices": [DEVICE_ID],
        "certs": ["C1"],
        "events": ["assigned"],
        "segments": [_asset_segment(asset_id)],
        "volume": 3,
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def _create(client: TestClient, headers: dict[str, str], asset_id: str, **overrides: object) -> dict[str, Any]:
    response = client.post("/api/v1/voice/rules", headers=headers, json=_payload(asset_id, **overrides))
    assert response.status_code == 200, response.text
    rule: dict[str, Any] = response.json()
    return rule


def _event(
    occurred_at: str,
    *,
    device_id: str = DEVICE_ID,
    cert_no: str = "C1",
    key: str = "session:1:assigned",
    kind: DoorEventKind = DoorEventKind.ASSIGNED,
    door_no: int = 5,
) -> DoorEvent:
    return DoorEvent(
        kind=kind,
        device_id=device_id,
        door_no=door_no,
        cert_no=cert_no,
        session_id=1,
        occurred_at=occurred_at,
        event_key=key,
    )


def _ok_outcome(downloaded: bool | None = None) -> CommandOutcome:
    data = {"downloaded": downloaded} if downloaded is not None else None
    return CommandOutcome(status="ok", reason_code="", terminal_message="", data=data)


def _collector(calls: list[tuple[str, dict[str, object]]], outcome: CommandOutcome | None = None) -> object:
    async def send(
        _device_id: str,
        action: str,
        params: dict[str, object],
        timeout_s: float = 30.0,
    ) -> CommandOutcome:
        calls.append((action, params))
        return outcome or _ok_outcome(downloaded=False)

    return send


# ── 目录与素材 ───────────────────────────────────────────────────────────


def test_spec_and_status(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_devices: None,
    fake_normalize: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service.shutil, "which", lambda name: f"/usr/bin/{name}")
    spec = client.get("/api/v1/voice/spec", headers=admin_headers).json()
    labels = {item["value"]: item["label"] for item in spec["events"]}
    assert labels == {
        "assigned": "空闲 / 已选中 → 等待开阀",
        "shower_started": "等待开阀 → 淋浴中",
        "paused": "淋浴中 → 已暂停",
        "resumed": "已暂停 → 淋浴中",
        "released_unused": "等待开阀 → 空闲",
        "released_after_shower": "淋浴中 → 空闲",
        "released_while_paused": "已暂停 → 空闲",
    }
    assert [item["phase"] for item in spec["events"]].count("用水") == 3
    assert spec["volume"] == {"minimum": 0, "maximum": 5, "default": 3}
    assert spec["digits"] == []
    assert spec["asset_count"] == 0
    assert spec["max_segments"] == schemas.MAX_SEGMENTS_PER_RULE
    assert spec["max_pause_ms"] == schemas.MAX_PAUSE_MS

    status = client.get("/api/v1/voice/status", headers=admin_headers).json()
    assert status == {"asset_count": 0, "digits": [], "ffmpeg_available": True}

    created = _upload(client, admin_headers, "五", b"five", "5")
    assert created["digit"] == 5
    assert created["sha256"] == created["asset_id"]
    assert created["url"] == f"/voice/assets/{created['asset_id']}.mp3"
    spec = client.get("/api/v1/voice/spec", headers=admin_headers).json()
    assert spec["digits"] == [5]
    assert spec["asset_count"] == 1
    status = client.get("/api/v1/voice/status", headers=admin_headers).json()
    assert status["digits"] == [5]


def test_asset_upload_download_and_guards(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_devices: None,
    fake_normalize: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _upload(client, admin_headers, "请到", b"hello")
    asset_id = created["asset_id"]
    assert created["name"] == "请到"
    assert created["digit"] is None
    assert created["duration_ms"] == 1_005
    assert created["size_bytes"] == 8
    assert len(asset_id) == 64

    listing = client.get("/api/v1/voice/assets", headers=admin_headers).json()
    assert [item["asset_id"] for item in listing] == [asset_id]

    download = client.get(created["url"])
    assert download.status_code == 200
    assert download.content == b"ID3hello"
    assert download.headers["content-type"].startswith("audio/mpeg")
    assert "immutable" in download.headers["cache-control"]

    assert client.get("/voice/assets/nothex.mp3").status_code == 404
    assert client.get(f"/voice/assets/{'a' * 64}.mp3").status_code == 404

    # 同名、同内容、空文件、空名称都拒绝；失败不落库
    duplicate_name = client.post(
        "/api/v1/voice/assets",
        headers=admin_headers,
        data={"name": "请到"},
        files={"file": ("x.wav", b"other", "audio/wav")},
    )
    assert duplicate_name.status_code == 422
    duplicate_bytes = client.post(
        "/api/v1/voice/assets",
        headers=admin_headers,
        data={"name": "另一个"},
        files={"file": ("x.wav", b"hello", "audio/wav")},
    )
    assert duplicate_bytes.status_code == 422
    assert "相同音频" in duplicate_bytes.json()["detail"]
    for name, payload in [("   ", b"x"), ("空文件", b""), ("名" * 41, b"x")]:
        response = client.post(
            "/api/v1/voice/assets",
            headers=admin_headers,
            data={"name": name},
            files={"file": ("x.wav", payload, "audio/wav")},
        )
        assert response.status_code == 422
    assert client.get("/api/v1/voice/assets", headers=admin_headers).json() == [created]

    monkeypatch.setattr(service, "MAX_UPLOAD_BYTES", 3)
    oversized = client.post(
        "/api/v1/voice/assets",
        headers=admin_headers,
        data={"name": "超限"},
        files={"file": ("x.wav", b"1234", "audio/wav")},
    )
    assert oversized.status_code == 422


def test_asset_digit_guards_and_conversion_failure(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_devices: None,
    fake_normalize: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _upload(client, admin_headers, "五", b"five", "5")
    duplicate = client.post(
        "/api/v1/voice/assets",
        headers=admin_headers,
        data={"name": "另一个五", "digit": "5"},
        files={"file": ("x.wav", b"other-five", "audio/wav")},
    )
    assert duplicate.status_code == 422
    assert "数字 5 已有素材" in duplicate.json()["detail"]
    for digit in ("10", "x", "-1"):
        response = client.post(
            "/api/v1/voice/assets",
            headers=admin_headers,
            data={"name": f"数字{digit}", "digit": digit},
            files={"file": ("x.wav", b"digit" + digit.encode(), "audio/wav")},
        )
        assert response.status_code == 422

    def boom(_source: Path, _target: Path) -> int:
        message = "坏音频"
        raise audio.AudioError(message)

    monkeypatch.setattr(audio, "normalize_to_mp3", boom)
    failed = client.post(
        "/api/v1/voice/assets",
        headers=admin_headers,
        data={"name": "坏"},
        files={"file": ("x.wav", b"bad", "audio/wav")},
    )
    assert failed.status_code == 422
    assert "坏音频" in failed.json()["detail"]
    assert temp_files() == []


def temp_files() -> list[str]:
    path = audio.tmp_dir()
    return sorted(item.name for item in path.glob("*")) if path.is_dir() else []


def test_asset_rename_and_delete_guards(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
) -> None:
    asset_id = assets["请到"]
    renamed = client.patch(f"/api/v1/voice/assets/{asset_id}", headers=admin_headers, json={"name": "请使用"})
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "请使用"
    conflict = client.patch(f"/api/v1/voice/assets/{asset_id}", headers=admin_headers, json={"name": "欢迎"})
    assert conflict.status_code == 422
    assert (
        client.patch(f"/api/v1/voice/assets/{'a' * 64}", headers=admin_headers, json={"name": "不存在"}).status_code
        == 404
    )
    assert (
        client.patch(f"/api/v1/voice/assets/{asset_id}", headers=admin_headers, json={"name": "   "}).status_code == 422
    )

    rule = _create(client, admin_headers, asset_id)
    in_use = client.delete(f"/api/v1/voice/assets/{asset_id}", headers=admin_headers)
    assert in_use.status_code == 409
    assert in_use.json()["detail"] == "「请使用」正在被规则使用"

    digit_rule = _create(
        client,
        admin_headers,
        assets["一"],
        certs=["C2"],
        events=["shower_started"],
        segments=[{"kind": "digits", "source": "cert_no"}],
    )
    assert digit_rule["summary"] == "学号"
    blocked_digit = client.delete(f"/api/v1/voice/assets/{assets['五']}", headers=admin_headers)
    assert blocked_digit.status_code == 409

    standalone = _upload(client, admin_headers, "独立", b"standalone")
    file_path = audio.asset_path(standalone["asset_id"])
    assert file_path.is_file()
    assert client.delete(f"/api/v1/voice/assets/{standalone['asset_id']}", headers=admin_headers).status_code == 204
    assert not file_path.exists()
    assert client.delete(f"/api/v1/voice/assets/{standalone['asset_id']}", headers=admin_headers).status_code == 404
    assert client.delete(f"/api/v1/voice/assets/{rule['id']}", headers=admin_headers).status_code == 404


def test_rule_crud_and_summary(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
) -> None:
    segments = [
        {"kind": "pause", "ms": 500},
        {"kind": "digits", "source": "door_no"},
        _asset_segment(assets["请到"]),
    ]
    created = _create(client, admin_headers, assets["请到"], segments=segments)
    rule_id = created["id"]
    assert created["devices"] == [DEVICE_ID]
    assert created["certs"] == ["C1"]
    assert created["exempt"] == []
    assert created["events"] == ["assigned"]
    assert created["segments"] == [
        segments[0],
        {"kind": "digits", "source": "door_no", "length": None},
        segments[2],
    ]
    assert created["summary"] == "停顿 0.5 秒 · 喷头号 · 请到"
    assert created["volume"] == 3
    assert created["enabled"] is True

    updated = client.put(
        f"/api/v1/voice/rules/{rule_id}",
        headers=admin_headers,
        json=_payload(assets["欢迎"], certs=["C2"], events=["paused"], volume=4, enabled=False),
    )
    assert updated.status_code == 200
    assert updated.json()["certs"] == ["C2"]
    assert updated.json()["events"] == ["paused"]
    assert updated.json()["summary"] == "欢迎"
    assert updated.json()["volume"] == 4
    assert updated.json()["enabled"] is False

    patched = client.patch(f"/api/v1/voice/rules/{rule_id}", headers=admin_headers, json={"enabled": True})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is True

    listed = client.get("/api/v1/voice/rules", headers=admin_headers).json()
    assert [item["id"] for item in listed] == [rule_id]

    assert (
        client.put("/api/v1/voice/rules/999", headers=admin_headers, json=_payload(assets["请到"])).status_code == 404
    )
    assert client.patch("/api/v1/voice/rules/999", headers=admin_headers, json={"enabled": True}).status_code == 404
    assert client.delete("/api/v1/voice/rules/999", headers=admin_headers).status_code == 404
    assert client.delete(f"/api/v1/voice/rules/{rule_id}", headers=admin_headers).status_code == 204
    assert client.get("/api/v1/voice/rules", headers=admin_headers).json() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"devices": []},
        {"devices": ["   "]},
        {"devices": ["*", DEVICE_ID]},
        {"certs": []},
        {"certs": ["   "]},
        {"certs": ["*", "C1"]},
        {"events": []},
        {"volume": 6},
        {"segments": []},
        {"segments": [{"kind": "pause", "ms": 50}]},
        {"segments": [{"kind": "pause", "ms": 6_000}]},
        {"segments": [{"kind": "digits", "source": "door_no", "length": 2}]},
        {"segments": [_asset_segment("f" * 64)]},
        {"segments": [{"kind": "digits", "source": "door_no"}]},
        {"exempt_cert_nos": ["C9"]},
        {"exempt_cert_nos": ["*"]},
        {"segments": [_asset_segment("f" * 64)] * (schemas.MAX_SEGMENTS_PER_RULE + 1)},
    ],
)
def test_rule_validation(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_devices: None,
    fake_normalize: None,
    overrides: dict[str, Any],
) -> None:
    # 数字素材不全时，任何含 digits 的规则都应拒绝；先不放数字素材
    asset_id = _upload(client, admin_headers, "请到", b"hello")["asset_id"]
    response = client.post("/api/v1/voice/rules", headers=admin_headers, json=_payload(asset_id, **overrides))
    assert response.status_code == 422, response.text
    assert client.get("/api/v1/voice/rules", headers=admin_headers).json() == []


def test_rule_duplicate_and_unknown_device(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_devices: None,
    fake_normalize: None,
) -> None:
    asset_id = _upload(client, admin_headers, "请到", b"hello")["asset_id"]
    created = _create(client, admin_headers, asset_id)
    duplicate = client.post("/api/v1/voice/rules", headers=admin_headers, json=_payload(asset_id))
    assert duplicate.status_code == 422
    assert "同优先级" in duplicate.json()["detail"]

    # 事件不重叠时同一目标允许并存
    other = _create(client, admin_headers, asset_id, events=["shower_started"])
    assert other["id"] != created["id"]

    # 更新自身不算重复；改成与另一条重叠则拒绝
    assert (
        client.put(f"/api/v1/voice/rules/{created['id']}", headers=admin_headers, json=_payload(asset_id)).status_code
        == 200
    )
    overlap = client.put(
        f"/api/v1/voice/rules/{created['id']}",
        headers=admin_headers,
        json=_payload(asset_id, events=["shower_started"]),
    )
    assert overlap.status_code == 422

    unknown = client.post(
        "/api/v1/voice/rules",
        headers=admin_headers,
        json=_payload(asset_id, devices=["missing-device"], events=["paused"]),
    )
    assert unknown.status_code == 404


def test_scope_priority_prefers_narrower_rules(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_devices: None,
    fake_normalize: None,
) -> None:
    asset_id = _upload(client, admin_headers, "请到", b"hello")["asset_id"]

    # 设备维度：范围更小的规则优先级更高
    broad_devices = _create(
        client,
        admin_headers,
        asset_id,
        devices=[DEVICE_ID, OTHER_DEVICE_ID],
        certs=["C1"],
    )
    narrow_device = _create(client, admin_headers, asset_id, certs=["C1"])
    assert int(repository.matching_rules(DEVICE_ID, "C1", "assigned")[0]["id"]) == narrow_device["id"]
    assert int(repository.matching_rules(OTHER_DEVICE_ID, "C1", "assigned")[0]["id"]) == broad_devices["id"]

    # 学号维度：同一设备下，学号集合更小者优先
    broad_certs = _create(
        client,
        admin_headers,
        asset_id,
        certs=["C1", "C2"],
        events=["resumed"],
    )
    narrow_cert = _create(client, admin_headers, asset_id, certs=["C1"], events=["resumed"])
    assert int(repository.matching_rules(DEVICE_ID, "C1", "resumed")[0]["id"]) == narrow_cert["id"]
    assert int(repository.matching_rules(DEVICE_ID, "C2", "resumed")[0]["id"]) == broad_certs["id"]

    # 优先级完全相同且目标相交：没有确定胜者，保存时拒绝
    _create(client, admin_headers, asset_id, certs=["C1", "C2"], events=["shower_started"])
    tie = client.post(
        "/api/v1/voice/rules",
        headers=admin_headers,
        json=_payload(asset_id, certs=["C1", "C3"], events=["shower_started"]),
    )
    assert tie.status_code == 422, tie.text
    assert "同优先级" in tie.json()["detail"]

    # 通配与具体仍然共存：设备维度更具体者胜
    wildcard = _create(
        client,
        admin_headers,
        asset_id,
        devices=["*"],
        certs=["C1"],
        events=["released_unused"],
    )
    specific = _create(client, admin_headers, asset_id, certs=["C1"], events=["released_unused"])
    assert int(repository.matching_rules(DEVICE_ID, "C1", "released_unused")[0]["id"]) == specific["id"]
    assert int(repository.matching_rules(OTHER_DEVICE_ID, "C1", "released_unused")[0]["id"]) == wildcard["id"]


def test_wildcard_rule_and_exemptions(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_devices: None,
    fake_normalize: None,
) -> None:
    asset_id = _upload(client, admin_headers, "请到", b"hello")["asset_id"]
    wildcard = _create(
        client,
        admin_headers,
        asset_id,
        devices=["*"],
        certs=["*"],
        exempt_cert_nos=["C1"],
    )
    assert wildcard["devices"] == ["*"]
    assert wildcard["certs"] == ["*"]
    assert wildcard["exempt"] == ["C1"]
    assert [item["id"] for item in client.get("/api/v1/voice/rules", headers=admin_headers).json()] == [wildcard["id"]]

    # 排除名单只对「全部学号」有效
    specific = client.post(
        "/api/v1/voice/rules",
        headers=admin_headers,
        json=_payload(asset_id, certs=["C2"], events=["paused"], exempt_cert_nos=["C3"]),
    )
    assert specific.status_code == 422
    assert (
        client.post(
            "/api/v1/voice/rules",
            headers=admin_headers,
            json=_payload(asset_id, certs=["*"], events=["paused"], exempt_cert_nos=["*"]),
        ).status_code
        == 422
    )


def test_ambiguous_rules_are_not_picked_by_id(
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[tuple[str, int, int]] = []

    def capture(_level: str, key: str, _message: str, **kwargs: object) -> None:
        if key == "voice.rule-ambiguous":
            ids = kwargs["rule_ids"]
            assert isinstance(ids, list)
            errors.append((key, ids[0], ids[1]))

    monkeypatch.setattr(repository, "log_event", capture)
    now = now_rfc3339()
    first = repository.insert_rule(
        json.dumps([DEVICE_ID]),
        json.dumps(["C1", "C2"]),
        json.dumps(["assigned"]),
        "[]",
        "[]",
        3,
        True,
        now,
    )
    second = repository.insert_rule(
        json.dumps([DEVICE_ID]),
        json.dumps(["C1", "C3"]),
        json.dumps(["assigned"]),
        "[]",
        "[]",
        3,
        True,
        now,
    )
    assert repository.matching_rules(DEVICE_ID, "C1", "assigned") == []
    assert errors == [("voice.rule-ambiguous", first, second)]


def test_preview_resolves_digits_pauses_and_truncation(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
) -> None:
    segments = [
        _asset_segment(assets["请到"]),
        {"kind": "digits", "source": "door_no"},
        {"kind": "pause", "ms": 500},
        {"kind": "digits", "source": "cert_no", "length": 4},
    ]
    response = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={"segments": segments, "event": "assigned", "door_no": 105, "cert_no": "A99990057"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    names = [item["clip"]["name"] if item["kind"] == "clip" else "pause" for item in body["segments"]]
    assert names == ["请到", "一", "零", "五", "pause", "零", "零", "五", "七"]
    assert body["segments"][4] == {"kind": "pause", "clip": None, "ms": 500}
    clip = body["segments"][0]["clip"]
    assert clip["asset_id"] == assets["请到"]
    assert clip["url"] == f"/voice/assets/{assets['请到']}.mp3"
    assert clip["sha256"] == assets["请到"]
    assert body["total_ms"] == sum(
        item["clip"]["duration_ms"] if item["kind"] == "clip" else item["ms"] for item in body["segments"]
    )
    assert body["summary"].startswith("请到 · 一 · 零 · 五 · 停顿 0.5 秒")


def test_preview_guards(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_devices: None,
    fake_normalize: None,
) -> None:
    asset_id = _upload(client, admin_headers, "请到", b"hello")["asset_id"]
    missing_digits = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={"segments": [{"kind": "digits", "source": "door_no"}], "event": "assigned"},
    )
    assert missing_digits.status_code == 422
    assert "数字素材不全" in missing_digits.json()["detail"]
    missing_asset = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={"segments": [_asset_segment("f" * 64)], "event": "assigned"},
    )
    assert missing_asset.status_code == 422
    empty = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={"segments": [], "event": "assigned"},
    )
    assert empty.status_code == 422
    ok = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={"segments": [_asset_segment(asset_id)], "event": "assigned"},
    )
    assert ok.status_code == 200


def test_push_records_manual_delivery(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _create(
        client,
        admin_headers,
        assets["请到"],
        segments=[
            {"kind": "pause", "ms": 500},
            _asset_segment(assets["请到"]),
            {"kind": "digits", "source": "door_no"},
        ],
    )
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _collector(calls))
    response = client.post(
        f"/api/v1/voice/rules/{rule['id']}/push",
        headers=admin_headers,
        json={"device_id": DEVICE_ID},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert body["segment_count"] == 3
    assert body["duration_ms"] > 0
    assert [action for action, _params in calls] == ["voice.play"]
    _action, params = calls[0]
    assert params["volume"] == 3
    assert params["download_budget_ms"] == service.PLAY_DOWNLOAD_BUDGET_MS
    segments = params["segments"]
    assert isinstance(segments, list)
    assert segments[0] == {"kind": "pause", "ms": 500}
    assert segments[1]["kind"] == "clip"
    assert segments[2]["clip_id"] == assets["一"]

    deliveries = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery["source"] == "manual"
    assert delivery["status"] == "ok"
    assert delivery["segments"] == "停顿 0.5 秒 · 请到 · 一"
    assert delivery["detail"] == "手动推送："

    assert (
        client.post(
            f"/api/v1/voice/rules/{rule['id']}/push",
            headers=admin_headers,
            json={"device_id": "missing"},
        ).status_code
        == 404
    )
    assert (
        client.post(
            "/api/v1/voice/rules/999/push",
            headers=admin_headers,
            json={"device_id": DEVICE_ID},
        ).status_code
        == 404
    )


def test_push_failure_is_recorded(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _create(client, admin_headers, assets["请到"])
    failure = CommandOutcome(
        status="failed", reason_code="voice.play_failed", terminal_message="播放失败", data={"downloaded": True}
    )
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _collector([], outcome=failure))
    body = client.post(
        f"/api/v1/voice/rules/{rule['id']}/push",
        headers=admin_headers,
        json={"device_id": DEVICE_ID},
    ).json()
    assert body["status"] == "failed"
    assert body["reason_code"] == "voice.play_failed"
    delivery = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()[0]
    assert delivery["status"] == "failed"
    assert delivery["downloaded"] is True
    assert "voice.play_failed 播放失败" in delivery["detail"]


def test_push_wildcard_rule_reads_sample_cert_digits(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _create(
        client,
        admin_headers,
        assets["请到"],
        devices=["*"],
        certs=["*"],
        segments=[{"kind": "digits", "source": "cert_no"}],
    )
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _collector(calls))
    body = client.post(
        f"/api/v1/voice/rules/{rule['id']}/push",
        headers=admin_headers,
        json={"device_id": DEVICE_ID},
    ).json()
    assert body["status"] == "ok"
    segments = calls[0][1]["segments"]
    assert isinstance(segments, list)
    assert len(segments) == len(service.SAMPLE_CERT_NO)
    assert all(item["kind"] == "clip" for item in segments)
    delivery = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()[0]
    assert delivery["cert_no"] == service.SAMPLE_CERT_NO


def test_schedule_rule_prepare_targets_online_devices(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned: list[tuple[str, list[dict[str, object]]]] = []
    monkeypatch.setattr(service, "_spawn_prepare", lambda device_id, clips: spawned.append((device_id, clips)))
    monkeypatch.setattr(devices_service, "is_online", lambda device_id: device_id == DEVICE_ID)

    _create(client, admin_headers, assets["请到"], devices=[DEVICE_ID, OTHER_DEVICE_ID])
    assert [device_id for device_id, _clips in spawned] == [DEVICE_ID]
    assert [clip["clip_id"] for clip in spawned[0][1]] == [assets["请到"]]

    spawned.clear()
    _create(client, admin_headers, assets["欢迎"], devices=["*"], certs=["C2"], events=["paused"])
    assert [device_id for device_id, _clips in spawned] == [DEVICE_ID]

    spawned.clear()
    _create(
        client,
        admin_headers,
        assets["请到"],
        certs=["C3"],
        events=["resumed"],
        enabled=False,
    )
    assert spawned == []

    spawned.clear()
    _create(
        client,
        admin_headers,
        assets["请到"],
        certs=["C4"],
        events=["shower_started"],
        segments=[{"kind": "digits", "source": "door_no"}],
    )
    clips = {clip["clip_id"] for clip in spawned[0][1]}
    assert clips == {assets[name] for name in DIGIT_NAMES}


def test_prepare_worker_sends_clips_and_swallows_failure(
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _collector(calls))
    clips: list[dict[str, object]] = [{"clip_id": "a" * 64, "sha256": "a" * 64, "size_bytes": 1, "duration_ms": 1}]

    async def run() -> None:
        service._spawn_prepare(DEVICE_ID, clips)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert [action for action, _params in calls] == ["voice.prepare"]
    assert calls[0][1] == {"clips": clips}
    assert service._tasks == set()

    async def boom(*_args: object, **_kwargs: object) -> CommandOutcome:
        message = "offline"
        raise RuntimeError(message)

    monkeypatch.setattr(sessions_service, "send_command_and_wait", boom)

    async def run_failure() -> None:
        service._spawn_prepare(DEVICE_ID, clips)
        await asyncio.sleep(0.05)

    asyncio.run(run_failure())
    assert service._tasks == set()

    # 没有事件循环时调度失败只记日志
    service._spawn_prepare(DEVICE_ID, clips)
    assert service._tasks == set()


def test_task_done_logs_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    errors: list[tuple[str, str]] = []

    def capture(level: str, key: str, _message: str, **_kwargs: object) -> None:
        errors.append((level, key))

    monkeypatch.setattr(service, "log_event", capture)

    async def run() -> None:
        task = asyncio.get_running_loop().create_task(_raise())
        service._tasks.add(task)
        task.add_done_callback(service._task_done)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert ("ERROR", "voice.task-failed") in errors


async def _raise() -> None:
    message = "boom"
    raise RuntimeError(message)


def test_handle_events_delivers_once(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(
        client,
        admin_headers,
        assets["请到"],
        segments=[
            _asset_segment(assets["请到"]),
            {"kind": "pause", "ms": 500},
            {"kind": "digits", "source": "door_no"},
        ],
        volume=4,
    )
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _collector(calls, _ok_outcome(downloaded=True)))

    event = _event(now_rfc3339(), door_no=5)
    asyncio.run(service.handle_events((event,)))
    asyncio.run(service.handle_events((event,)))

    assert [action for action, _params in calls] == ["voice.play"]
    _action, params = calls[0]
    assert params["volume"] == 4
    segments = params["segments"]
    assert isinstance(segments, list)
    assert [item["kind"] for item in segments] == ["clip", "pause", "clip"]
    assert segments[2]["clip_id"] == assets["五"]

    deliveries = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery["source"] == "auto"
    assert delivery["device_id"] == DEVICE_ID
    assert delivery["cert_no"] == "C1"
    assert delivery["event"] == "assigned"
    assert delivery["door_no"] == 5
    assert delivery["segments"] == "请到 · 停顿 0.5 秒 · 五"
    assert delivery["segment_count"] == 3
    assert delivery["status"] == "ok"
    assert delivery["downloaded"] is True
    assert delivery["dispatched_at"]
    assert delivery["done_at"]


def test_handle_events_skips_stale_no_rule_and_empty_cert(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client, admin_headers, assets["请到"])
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _collector(calls))

    asyncio.run(
        service.handle_events(
            (
                _event("2000-01-01T00:00:00.000Z", key="stale"),
                _event(now_rfc3339(), cert_no="C9", key="no-rule"),
                _event(now_rfc3339(), cert_no="", key="empty"),
            )
        )
    )
    assert calls == []
    assert client.get("/api/v1/voice/deliveries", headers=admin_headers).json() == []


def test_handle_events_specific_rule_and_exemption_yields(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(
        client,
        admin_headers,
        assets["请到"],
        devices=["*"],
        certs=["*"],
        exempt_cert_nos=["C1"],
        segments=[_asset_segment(assets["请到"])],
    )
    _create(
        client,
        admin_headers,
        assets["欢迎"],
        devices=[DEVICE_ID],
        certs=["*"],
        segments=[_asset_segment(assets["欢迎"])],
    )
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _collector(calls))

    asyncio.run(service.handle_events((_event(now_rfc3339(), cert_no="C1", key="c1"),)))
    asyncio.run(service.handle_events((_event(now_rfc3339(), cert_no="C2", key="c2"),)))
    asyncio.run(service.handle_events((_event(now_rfc3339(), device_id=OTHER_DEVICE_ID, cert_no="C2", key="other"),)))

    delivered = [params["segments"][0]["clip_id"] for _action, params in calls]  # type: ignore[index]
    assert delivered == [assets["欢迎"], assets["欢迎"], assets["请到"]]


def test_handle_events_resolution_failure_does_not_deliver(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(
        client,
        admin_headers,
        assets["请到"],
        segments=[{"kind": "digits", "source": "door_no"}],
    )
    repository.delete_asset(assets["五"])
    audio.asset_path(assets["五"]).unlink()
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _collector(calls))

    asyncio.run(service.handle_events((_event(now_rfc3339(), door_no=5),)))
    assert calls == []
    assert client.get("/api/v1/voice/deliveries", headers=admin_headers).json() == []


def test_handle_events_swallows_single_event_failure(
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    errors: list[str] = []

    def capture(_level: str, key: str, _message: str, **_kwargs: object) -> None:
        errors.append(key)

    async def boom(_event: DoorEvent, _max_age: int) -> None:
        message = "boom"
        raise RuntimeError(message)

    monkeypatch.setattr(service, "log_event", capture)
    monkeypatch.setattr(service, "_dispatch_event", boom)
    asyncio.run(service.handle_events((_event(now_rfc3339()),)))
    assert errors == ["voice.delivery-error"]


def test_schedule_events_creates_task_and_survives_without_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[DoorEvent, ...]] = []

    async def handle(events: tuple[DoorEvent, ...]) -> None:
        seen.append(events)

    monkeypatch.setattr(service, "handle_events", handle)

    async def run() -> None:
        event = _event(now_rfc3339())
        service.schedule_events((event,))
        await asyncio.sleep(0.05)
        assert seen == [(event,)]
        assert service._tasks == set()

    asyncio.run(run())
    service.schedule_events(())
    service.schedule_events((_event(now_rfc3339()),))
    assert service._tasks == set()


def test_dispatch_event_handles_malformed_rule(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _create(client, admin_headers, assets["请到"])
    row = repository.get_rule(rule["id"])
    assert row is not None
    repository.update_rule(
        rule["id"],
        str(row["devices"]),
        str(row["certs"]),
        str(row["events"]),
        str(row["exempt"]),
        json.dumps([{"kind": "asset", "asset_id": "f" * 64}]),
        int(row["volume"]),
        bool(row["enabled"]),
        now_rfc3339(),
    )
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _collector(calls))
    asyncio.run(service.handle_events((_event(now_rfc3339()),)))
    assert calls == []
    assert client.get("/api/v1/voice/deliveries", headers=admin_headers).json() == []


# ── 一次性重建 ───────────────────────────────────────────────────────────


def test_legacy_rebuild_imports_effects_and_drops_old_tables(
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(settings.voice_dir)
    effects = root / "effects"
    effects.mkdir(parents=True, exist_ok=True)
    effect_id = "e" * 64
    (effects / f"{effect_id}.wav").write_bytes(b"effect")
    duplicate_id = "d" * 64
    (effects / f"{duplicate_id}.wav").write_bytes(b"effect")
    broken_id = "c" * 64
    (effects / f"{broken_id}.wav").write_bytes(b"broken")
    missing_id = "f" * 64
    clips = root / "clips"
    clips.mkdir(parents=True, exist_ok=True)
    (clips / "old.mp3").write_bytes(b"old")
    audio.tmp_dir().mkdir(parents=True, exist_ok=True)
    (audio.tmp_dir() / "orphan.part").write_bytes(b"orphan")

    conn = get_conn()
    conn.executescript(
        """
        DROP TABLE IF EXISTS voice_asset;
        DROP TABLE IF EXISTS voice_rule;
        DROP TABLE IF EXISTS voice_delivery;
        CREATE TABLE voice_rule (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL);
        CREATE TABLE voice_delivery (id INTEGER PRIMARY KEY AUTOINCREMENT, clip_id TEXT NOT NULL);
        CREATE TABLE voice_clip (clip_id TEXT PRIMARY KEY, path TEXT NOT NULL);
        CREATE TABLE voice_effect (
            effect_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute("INSERT INTO voice_rule(text) VALUES ('旧规则')")
    conn.execute("INSERT INTO voice_delivery(clip_id) VALUES ('旧投递')")
    conn.execute("INSERT INTO voice_clip(clip_id,path) VALUES ('x','y')")
    conn.execute(
        "INSERT INTO voice_effect(effect_id,name,created_at) VALUES (?,?,?)",
        (effect_id, "叮", "2026-01-01T00:00:00.000Z"),
    )
    conn.execute(
        "INSERT INTO voice_effect(effect_id,name,created_at) VALUES (?,?,?)",
        (duplicate_id, "重复内容", "2026-01-01T00:00:00.000Z"),
    )
    conn.execute(
        "INSERT INTO voice_effect(effect_id,name,created_at) VALUES (?,?,?)",
        (broken_id, "坏音效", "2026-01-01T00:00:00.000Z"),
    )
    conn.execute(
        "INSERT INTO voice_effect(effect_id,name,created_at) VALUES (?,?,?)",
        (missing_id, "已丢失", "2026-01-01T00:00:00.000Z"),
    )
    conn.commit()

    def normalize(source: Path, target: Path) -> int:
        if source.name.startswith("c"):
            message = "坏音频"
            raise audio.AudioError(message)
        target.write_bytes(b"MP3-EFFECT")
        return 700

    monkeypatch.setattr(audio, "normalize_to_mp3", normalize)
    repository.init_tables()

    names = {str(row["name"]) for row in repository.list_assets()}
    assert names == {"叮"}
    imported = repository.get_asset_by_name("叮")
    assert imported is not None
    assert int(imported["duration_ms"]) == 700
    assert audio.asset_path(str(imported["asset_id"])).read_bytes() == b"MP3-EFFECT"
    assert not _table_exists("voice_effect")
    assert not _table_exists("voice_clip")
    assert not clips.exists()
    assert (effects / f"{effect_id}.wav").is_file()
    assert list(audio.tmp_dir().glob("*")) == []

    repository.init_tables()
    assert len(repository.list_assets()) == 1


def test_legacy_rebuild_name_conflict_keeps_existing(
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(settings.voice_dir)
    existing_id = "b" * 64
    audio.assets_dir().mkdir(parents=True, exist_ok=True)
    audio.asset_path(existing_id).write_bytes(b"existing")
    repository.insert_asset(existing_id, "叮", None, 500, 8, now_rfc3339())

    effects = root / "effects"
    effects.mkdir(parents=True, exist_ok=True)
    effect_id = "a" * 64
    (effects / f"{effect_id}.wav").write_bytes(b"effect")
    monkeypatch.setattr(audio, "normalize_to_mp3", lambda _source, target: _write(target, b"MP3"))
    conn = get_conn()
    conn.execute("CREATE TABLE voice_effect (effect_id TEXT PRIMARY KEY, name TEXT, created_at TEXT)")
    conn.execute(
        "INSERT INTO voice_effect(effect_id,name,created_at) VALUES (?,?,?)",
        (effect_id, "叮", "2026-01-01T00:00:00.000Z"),
    )
    conn.commit()
    repository.init_tables()
    assets = repository.list_assets()
    assert len(assets) == 1
    assert str(assets[0]["asset_id"]) == existing_id


def _write(path: Path, data: bytes) -> int:
    path.write_bytes(data)
    return 500


def _table_exists(name: str) -> bool:
    row = get_conn().execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return row is not None


def test_asset_rows_empty_and_plan_helpers(fresh_db: str) -> None:
    assert repository.asset_rows_by_ids([]) == {}
    assert service._play_timeout_s(0) == 120.0
    assert service._play_timeout_s(10_000) == 130.0
    assert service._play_timeout_s(200_000) == 320.0
    assert service._elapsed_s("not-a-timestamp") == 0.0
    plan = [
        service.ClipPlan("a" * 64, "a" * 64, 1, 1),
        service.PausePlan(250),
    ]
    assert service._plan_summary(plan) == "音频 · 停顿 0.25 秒"
    assert service._plan_duration_ms(plan) == 251
    assert service._plan_json(plan) == [
        {"kind": "clip", "clip_id": "a" * 64, "sha256": "a" * 64, "size_bytes": 1, "duration_ms": 1},
        {"kind": "pause", "ms": 250},
    ]
    assert service._detail(CommandOutcome(status="ok", reason_code="", terminal_message="", data=None)) == ""
    assert service._downloaded(CommandOutcome(status="ok", reason_code="", terminal_message="", data=None)) is False


# ── 服务层边界 ───────────────────────────────────────────────────────────


def test_asset_insert_and_readback_failures_leave_no_file(
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def normalize(source: Path, target: Path) -> int:
        target.write_bytes(b"ID3" + source.read_bytes())
        return 500

    monkeypatch.setattr(audio, "normalize_to_mp3", normalize)
    with pytest.raises(service.InvalidVoiceAssetError, match="0–9"):
        service.create_asset("数字越界", b"x", 10)

    original_insert = repository.insert_asset

    def fail_insert(*_args: object, **_kwargs: object) -> None:
        message = "db"
        raise RuntimeError(message)

    monkeypatch.setattr(repository, "insert_asset", fail_insert)
    with pytest.raises(RuntimeError, match="db"):
        service.create_asset("写库失败", b"fail", None)
    assert list(audio.assets_dir().glob("*.mp3")) == []

    monkeypatch.setattr(repository, "insert_asset", original_insert)
    original_get = repository.get_asset
    monkeypatch.setattr(repository, "get_asset", lambda _asset_id: None)
    with pytest.raises(service.InvalidVoiceAssetError, match="读不到"):
        service.create_asset("读回失败", b"missing", None)
    assert list(audio.assets_dir().glob("*.mp3")) == []
    assert repository.list_assets() == []

    monkeypatch.setattr(repository, "get_asset", original_get)
    created = service.create_asset("正常", b"ok", None)
    monkeypatch.setattr(repository, "get_asset", lambda _asset_id: None)
    with pytest.raises(ApiProblemError) as error:
        service.rename_asset(created.asset_id, "改名")
    assert error.value.problem.code == "voice-asset-not-found"


def test_segment_and_target_normalization_guards(fresh_db: str) -> None:
    for value in ("not-a-dict", {"kind": "bogus"}):
        with pytest.raises(service.InvalidVoiceRuleError):
            service._segment_from_dict(value)
    with pytest.raises(service.InvalidVoiceRuleError, match="至少需要一个分段"):
        service._validate_segments([])

    assert (
        service._segment_summary([schemas.DigitsSegment(kind="digits", source="cert_no", length=4)], {})
        == "学号后 4 位"
    )
    assert service._segment_summary([schemas.AssetSegment(kind="asset", asset_id="f" * 64)], {}) == "缺失素材"

    assert service._normalized_targets([], "设备") == []
    assert service._normalized_targets(["a", "a"], "设备") == ["a"]
    with pytest.raises(service.InvalidVoiceRuleError, match="最多 64"):
        service._normalized_targets(["a" * 65], "设备")
    with pytest.raises(service.InvalidVoiceRuleError, match="不能有空值"):
        service._normalized_targets([" "], "设备")

    with pytest.raises(service.InvalidVoiceRuleError, match="至少选择一个事件"):
        service._normalize_rule([schemas.WILDCARD], ["C1"], [], [schemas.PauseSegment(kind="pause", ms=500)], [], None)


def test_rule_repository_races(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rule = _create(client, admin_headers, assets["请到"])
    rule_id = int(rule["id"])
    original_get = repository.get_rule

    monkeypatch.setattr(repository, "insert_rule", lambda *_args, **_kwargs: 4242)
    monkeypatch.setattr(repository, "get_rule", lambda _rule_id: None)
    request = schemas.VoiceRuleCreate(
        devices=[DEVICE_ID],
        certs=["C9"],
        events=[DoorEventKind.PAUSED],
        segments=[schemas.AssetSegment(kind="asset", asset_id=assets["请到"])],
    )
    with pytest.raises(ApiProblemError) as error:
        service.create_rule(request)
    assert error.value.problem.code == "voice-rule-not-found"

    monkeypatch.setattr(repository, "get_rule", original_get)
    monkeypatch.setattr(repository, "update_rule", lambda *_args, **_kwargs: 0)
    with pytest.raises(ApiProblemError):
        service.update_rule(
            rule_id,
            schemas.VoiceRuleUpdate(
                devices=[DEVICE_ID],
                certs=["C1"],
                events=[DoorEventKind.ASSIGNED],
                segments=[schemas.AssetSegment(kind="asset", asset_id=assets["请到"])],
            ),
        )

    real_row = original_get(rule_id)
    assert real_row is not None
    rows = iter([real_row, None])
    monkeypatch.setattr(repository, "get_rule", lambda _rule_id: next(rows))
    monkeypatch.setattr(repository, "update_rule", lambda *_args, **_kwargs: 1)
    with pytest.raises(ApiProblemError):
        service.update_rule(
            rule_id,
            schemas.VoiceRuleUpdate(
                devices=[DEVICE_ID],
                certs=["C1"],
                events=[DoorEventKind.ASSIGNED],
                segments=[schemas.AssetSegment(kind="asset", asset_id=assets["请到"])],
            ),
        )

    monkeypatch.setattr(repository, "get_rule", lambda _rule_id: None)
    monkeypatch.setattr(repository, "set_enabled", lambda *_args, **_kwargs: 1)
    with pytest.raises(ApiProblemError):
        service.set_enabled(rule_id, True)


def test_schedule_and_preview_edge_cases(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
) -> None:
    service._spawn_prepare(DEVICE_ID, [])
    service.schedule_rule_prepare(
        schemas.VoiceRuleView(
            id=999,
            devices=[DEVICE_ID],
            certs=["C1"],
            exempt=[],
            events=[DoorEventKind.ASSIGNED],
            segments=[schemas.AssetSegment(kind="asset", asset_id=assets["请到"])],
            summary="请到",
            volume=3,
            enabled=True,
            created_at=now_rfc3339(),
            updated_at=now_rfc3339(),
        )
    )
    assert service._preview([service.ClipPlan("f" * 64, "f" * 64, 1, 1)]).segments == []


def test_task_done_ignores_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    errors: list[str] = []
    monkeypatch.setattr(service, "log_event", lambda _level, key, _message, **_kwargs: errors.append(key))

    async def run() -> None:
        task = asyncio.get_running_loop().create_task(asyncio.sleep(10))
        task.cancel()
        await asyncio.sleep(0)
        service._task_done(task)
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert errors == []


def test_push_rejects_malformed_rule(
    client: TestClient,
    admin_headers: dict[str, str],
    assets: dict[str, str],
) -> None:
    rule = _create(client, admin_headers, assets["请到"])
    row = repository.get_rule(rule["id"])
    assert row is not None
    repository.update_rule(
        rule["id"],
        str(row["devices"]),
        str(row["certs"]),
        str(row["events"]),
        str(row["exempt"]),
        json.dumps([{"kind": "asset", "asset_id": "f" * 64}]),
        int(row["volume"]),
        bool(row["enabled"]),
        now_rfc3339(),
    )
    with pytest.raises(ApiProblemError) as error:
        asyncio.run(service.push_to_device(rule["id"], DEVICE_ID))
    assert error.value.problem is INVALID_VOICE_RULE


def test_legacy_rebuild_with_only_clip_table(fresh_db: str) -> None:
    conn = get_conn()
    conn.execute("CREATE TABLE voice_clip (clip_id TEXT PRIMARY KEY, path TEXT NOT NULL)")
    conn.commit()
    repository.init_tables()
    assert not _table_exists("voice_clip")
    assert repository.list_assets() == []


def test_gateway_schedules_voice_events(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[DoorEvent, ...]] = []
    monkeypatch.setattr(service, "schedule_events", captured.append)

    with helpers.trusted_online(client, "dev-gateway-voice", admin_headers) as ws:
        ws.send_text(
            helpers.frame(
                "door.snapshot",
                {
                    "snapshot_id": "voice-gw-1",
                    "source": "db_watch",
                    "captured_at": now_rfc3339(),
                    "doors": [_door_payload()],
                },
            )
        )
        helpers.recv_until(ws, "door.snapshot_ack")
        ws.send_text(
            helpers.frame(
                "door.snapshot",
                {
                    "snapshot_id": "voice-gw-2",
                    "source": "db_watch",
                    "captured_at": now_rfc3339(),
                    "doors": [_door_payload(card_no=1001, cert_no="C1", open_time="2026-10-23T19:00:00.000Z")],
                },
            )
        )
        helpers.recv_until(ws, "door.snapshot_ack")

    assigned = [event for batch in captured for event in batch if event.kind is DoorEventKind.ASSIGNED]
    assert assigned
    assert assigned[0].cert_no == "C1"


def _door_payload(**overrides: object) -> dict[str, object]:
    door: dict[str, object] = {
        "door_no": 1,
        "card_no": 0,
        "cert_no": "",
        "acc_no": "",
        "open_time": None,
        "in_time": None,
        "close_time": None,
        "bath_time_s": 0,
        "status_raw": 0,
    }
    door.update(overrides)
    return door
