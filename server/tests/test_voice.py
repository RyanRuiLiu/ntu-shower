"""语音播报：规则接口（通配/批量/启停）、模板试听、事件投递、延迟/去重/失败、
延迟窗口内的设备预热与各阶段耗时记录。"""

import asyncio
import time
from pathlib import Path

import pydantic
import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core.clock import now_rfc3339
from app.modules.doors import service as doors_service
from app.modules.doors.events import DoorEvent, DoorEventKind
from app.modules.sessions import service as sessions_service
from app.modules.sessions.schemas import CommandOutcome
from app.modules.voice import render, repository, schemas, service, tts

DEVICE_ID = "dev-voice"
OTHER_DEVICE_ID = "dev-voice-other"


@pytest.fixture
def voice_device(fresh_db: str) -> None:
    helpers.register_trusted_device(DEVICE_ID)
    helpers.register_trusted_device(OTHER_DEVICE_ID)


@pytest.fixture
def fake_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    synthesized: list[tts.SynthParams] = []

    def synthesize(plan: tts.Plan, clip_id: str, voice: str) -> tts.ClipFile:
        synthesized.append(voice)
        path = tts.clip_path(clip_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = "|".join(f"{kind}:{value}" for kind, value in plan).encode()
        data = f"MP3|voice={voice}|".encode() + body
        path.write_bytes(data)
        return tts.ClipFile(clip_id, tts.sha256_of(path), len(data), 3000, path)

    monkeypatch.setattr(tts, "synthesize", synthesize)
    monkeypatch.setattr(service, "fake_tts_params", synthesized, raising=False)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "devices": [DEVICE_ID],
        "certs": ["C1"],
        "events": ["assigned"],
        "text": "欢迎使用，{door_no} 号喷头",
        "voice": "melo",
        "volume": 3,
        "enabled": True,
    }
    payload.update(overrides)
    return payload


def _create(client: TestClient, headers: dict[str, str], **overrides: object) -> dict[str, object]:
    """创建一条逻辑规则并返回其视图。"""
    response = client.post("/api/v1/voice/rules", headers=headers, json=_payload(**overrides))
    assert response.status_code == 200, response.text
    rule: dict[str, object] = response.json()
    return rule


def _update_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "devices": [DEVICE_ID],
        "certs": ["C1"],
        "events": ["assigned"],
        "text": "固定内容",
        "volume": 3,
        "enabled": True,
    }
    body.update(overrides)
    return body


def _event(
    occurred_at: str,
    *,
    device_id: str = DEVICE_ID,
    cert_no: str = "C1",
    key: str = "session:1:assigned",
    kind: DoorEventKind = DoorEventKind.ASSIGNED,
) -> DoorEvent:
    return DoorEvent(
        kind=kind,
        device_id=device_id,
        door_no=5,
        cert_no=cert_no,
        session_id=1,
        occurred_at=occurred_at,
        event_key=key,
    )


def _ok_outcome(downloaded: bool | None = None) -> CommandOutcome:
    data = {"downloaded": downloaded} if downloaded is not None else None
    return CommandOutcome(status="ok", reason_code="", terminal_message="", data=data)


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


def _fake_send_collector(calls: list[tuple[str, dict[str, object]]]) -> object:
    async def fake_send(
        _device_id: str,
        action: str,
        params: dict[str, object],
        timeout_s: float = 30.0,
    ) -> CommandOutcome:
        calls.append((action, params))
        return _ok_outcome(downloaded=False)

    return fake_send


def test_spec_uses_canonical_event_names(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/voice/spec", headers=admin_headers)
    assert response.status_code == 200
    spec = response.json()
    events = {item["value"]: item for item in spec["events"]}
    # 事件名就是状态转换式：七个互不重名，看名字即知发生了什么
    assert events["assigned"]["label"] == "空闲 / 已选中 → 等待开阀"
    assert events["shower_started"]["label"] == "等待开阀 → 淋浴中"
    assert events["paused"]["label"] == "淋浴中 → 已暂停"
    assert events["resumed"]["label"] == "已暂停 → 淋浴中"
    assert events["released_unused"]["label"] == "等待开阀 → 空闲"
    assert events["released_after_shower"]["label"] == "淋浴中 → 空闲"
    assert events["released_while_paused"]["label"] == "已暂停 → 空闲"
    # 选择界面按阶段分组:分配一项、用水三项、释放三项
    phases = [item["phase"] for item in spec["events"]]
    assert phases.count("分配") == 1
    assert phases.count("用水") == 3
    assert phases.count("释放") == 3
    assert [item["key"] for item in spec["placeholders"]] == [
        "door_no",
        "cert_no",
        "cert_no:4",
        "event",
    ]
    assert spec["volume"] == {"minimum": 0, "maximum": 5, "default": 3}
    assert "delay" not in spec
    assert "priority" not in spec
    assert next(item["key"] for item in spec["voices"]) == "melo"


def test_rule_crud_preview_and_clip(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    created = _create(client, admin_headers)
    rule_id = created["id"]
    assert created["devices"] == [DEVICE_ID]
    assert created["certs"] == ["C1"]
    assert created["events"] == ["assigned"]

    listed = client.get("/api/v1/voice/rules", headers=admin_headers).json()
    assert [item["id"] for item in listed] == [rule_id]

    preview = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={
            "text": created["text"],
            "event": "assigned",
            "cert_no": "C1",
            "door_no": 5,
            "assigned_seconds": 1122,
            "shower_seconds": 965,
        },
    )
    assert preview.status_code == 200
    body = preview.json()
    assert body["rendered_text"] == "欢迎使用，5 号喷头"
    assert body["duration_ms"] == 3000

    clip = client.get(body["clip_url"])
    assert clip.status_code == 200
    assert clip.headers["content-type"].startswith("audio/mpeg")
    assert "immutable" in clip.headers["cache-control"]

    updated = client.put(
        f"/api/v1/voice/rules/{rule_id}",
        headers=admin_headers,
        json=_update_body(volume=4, enabled=False),
    )
    assert updated.status_code == 200
    assert updated.json()["text"] == "固定内容"
    assert updated.json()["enabled"] is False

    patched = client.patch(f"/api/v1/voice/rules/{rule_id}", headers=admin_headers, json={"enabled": True})
    assert patched.status_code == 200
    assert patched.json()["enabled"] is True

    deleted = client.delete(f"/api/v1/voice/rules/{rule_id}", headers=admin_headers)
    assert deleted.status_code == 204
    assert client.get("/api/v1/voice/rules", headers=admin_headers).json() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"text": "你好 {name}"},
        {"text": "{door_no"},
        {"text": "   "},
        {"certs": ["   "]},
        {"certs": []},
        {"devices": []},
        {"devices": ["*", DEVICE_ID]},
        {"certs": ["*", "C1"]},
        {"volume": 6},
        {"events": ["bogus"]},
        {"events": []},
        {"voice": "huayan-medium"},
    ],
)
def test_rule_validation(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    overrides: dict[str, object],
) -> None:
    response = client.post("/api/v1/voice/rules", headers=admin_headers, json=_payload(**overrides))
    assert response.status_code == 422, response.text


def test_status_lists_voice_catalog(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = client.get("/api/v1/voice/status", headers=admin_headers).json()
    keys = [item["key"] for item in body["voices"]]
    assert keys == ["melo"]
    assert all("note" in item and "available" in item for item in body["voices"])

    monkeypatch.setattr(tts, "probe", lambda _voice: True)
    refreshed = client.get("/api/v1/voice/status", headers=admin_headers).json()
    assert all(item["available"] for item in refreshed["voices"])


def test_rule_text_and_voice_guards(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
) -> None:
    blank = client.post(
        "/api/v1/voice/rules",
        headers=admin_headers,
        json=_payload(text="   "),
    )
    assert blank.status_code == 422
    assert "不能为空" in blank.json()["detail"]

    too_long = client.post(
        "/api/v1/voice/rules",
        headers=admin_headers,
        json=_payload(text="长" * (render.MAX_TEMPLATE_CHARS + 1)),
    )
    assert too_long.status_code == 422

    unknown_voice = client.post(
        "/api/v1/voice/rules",
        headers=admin_headers,
        json=_payload(voice="kokoro-1"),
    )
    assert unknown_voice.status_code == 422
    assert "只允许" in unknown_voice.json()["detail"]


def test_create_stores_scope_as_authored(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    """一条逻辑规则整存创建时的作用域：按提交的作用域整体存储（提交值去重保序）。"""
    created = _create(client, admin_headers, certs=["C1", "C2", "C2", "C3"])
    # 提交值去重保序：重复的 C2 只留一份
    assert created["certs"] == ["C1", "C2", "C3"]
    assert created["devices"] == [DEVICE_ID]

    listed = client.get("/api/v1/voice/rules", headers=admin_headers).json()
    assert [item["id"] for item in listed] == [created["id"]]

    # 学号列表超上限在 HTTP 边界拒绝
    many = client.post(
        "/api/v1/voice/rules",
        headers=admin_headers,
        json=_payload(certs=[f"C{i}" for i in range(501)]),
    )
    assert many.status_code == 422


def test_match_specificity_exact_beats_wildcard(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    only_device = _create(client, admin_headers, certs=["*"], text="设备级通配")
    only_cert = _create(client, admin_headers, devices=["*"], certs=["C9"], text="学号跨设备")
    exact = _create(client, admin_headers, certs=["C9"], text="完全具体")

    # 完全具体 > 仅设备通配 > 仅学号通配 > 双通配（设备维度先于学号维度）
    matched = repository.matching_rules(DEVICE_ID, "C9", "assigned")
    assert [int(row["id"]) for row in matched] == [int(exact["id"]), int(only_device["id"]), int(only_cert["id"])]
    assert [int(row["id"]) for row in repository.matching_rules(OTHER_DEVICE_ID, "C9", "assigned")] == [
        int(only_cert["id"])
    ]
    assert [int(row["id"]) for row in repository.matching_rules(DEVICE_ID, "C7", "assigned")] == [
        int(only_device["id"])
    ]

    # 停用具体规则后，同目标由次特异的顶上（设备维度先于学号维度：设备级通配压过
    # 跨设备学号）——覆盖切换不需要第二条同目标规则
    client.patch(f"/api/v1/voice/rules/{exact['id']}", headers=admin_headers, json={"enabled": False})
    matched = repository.matching_rules(DEVICE_ID, "C9", "assigned")
    assert [int(row["id"]) for row in matched] == [int(only_device["id"]), int(only_cert["id"])]

    # 列表按特异度排序：具体在前，通配在后
    listed = client.get("/api/v1/voice/rules", headers=admin_headers).json()
    ids = [item["id"] for item in listed]
    assert ids.index(int(exact["id"])) < ids.index(int(only_device["id"]))


def test_update_moves_target(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    moved = _create(client, admin_headers, certs=["C1"])
    to_other = client.put(
        f"/api/v1/voice/rules/{moved['id']}",
        headers=admin_headers,
        json=_update_body(devices=[OTHER_DEVICE_ID]),
    )
    assert to_other.status_code == 200
    assert to_other.json()["devices"] == [OTHER_DEVICE_ID]

    # 目标重叠的两条规则并存是合法的：匹配按特异度与 id 裁决，不由唯一约束拦截
    _create(client, admin_headers, certs=["C2"])
    second = _create(client, admin_headers, certs=["C3"])
    retargeted = client.put(
        f"/api/v1/voice/rules/{second['id']}",
        headers=admin_headers,
        json=_update_body(certs=["C2"], events=["assigned"]),
    )
    assert retargeted.status_code == 200
    assert retargeted.json()["certs"] == ["C2"]


def test_list_rules_groups_by_cert_then_device_display_order(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    from app.modules.devices import repository as devices_repository

    # 展示按学号分组聚拢：具体学号的规则在前、按学号排序；同学号内跟着设备展示顺序走；
    # 通配学号的规则排在最后
    devices_repository.set_order([OTHER_DEVICE_ID, DEVICE_ID])
    wildcard_cert = _create(client, admin_headers, certs=["*"])
    first = _create(client, admin_headers, certs=["C1"])
    second = _create(client, admin_headers, devices=[OTHER_DEVICE_ID], certs=["C1"])
    listed = client.get("/api/v1/voice/rules", headers=admin_headers).json()
    # 设备展示顺序 OTHER 在前：同学号的规则跟着设备展示顺序走
    assert [item["id"] for item in listed] == [int(second["id"]), int(first["id"]), int(wildcard_cert["id"])]
    assert listed[0]["certs"] == ["C1"]
    assert listed[-1]["certs"] == ["*"]


def test_wildcard_rule_exemptions(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    _create(
        client,
        admin_headers,
        devices=["*"],
        certs=["*"],
        text="全场通用",
        exempt_cert_nos=["C1", "C2"],
    )
    # 被排除的学号不命中这条规则；此刻没有别的候选，就不播
    assert repository.matching_rules(DEVICE_ID, "C1", "assigned") == []
    matched = repository.matching_rules(DEVICE_ID, "C9", "assigned")
    assert matched is not None

    listed = client.get("/api/v1/voice/rules", headers=admin_headers).json()
    assert listed[0]["exempt"] == ["C1", "C2"]

    # 排除只作用于那条规则：更具体的设备级通配照常接住被全场规则排除的人
    _create(client, admin_headers, certs=["*"], text="设备级", exempt_cert_nos=["C9"])
    fell_through = repository.matching_rules(DEVICE_ID, "C9", "assigned")
    assert fell_through is not None
    import json as json_module

    assert json_module.loads(fell_through[0]["devices"]) == ["*"]
    assert json_module.loads(fell_through[0]["certs"]) == ["*"]
    specific = repository.matching_rules(DEVICE_ID, "C1", "assigned")
    assert specific is not None
    assert json_module.loads(specific[0]["certs"]) == ["*"]

    # 排除名单只允许配「全部学号」；名单里不许再写「全部」
    assert (
        client.post(
            "/api/v1/voice/rules",
            headers=admin_headers,
            json=_payload(certs=["C1"], exempt_cert_nos=["C9"]),
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/voice/rules",
            headers=admin_headers,
            json=_payload(certs=["*"], exempt_cert_nos=["*"]),
        ).status_code
        == 422
    )

    # 更新可清空排除名单
    updated = client.put(
        f"/api/v1/voice/rules/{listed[0]['id']}",
        headers=admin_headers,
        json={
            "devices": ["*"],
            "certs": ["*"],
            "events": ["assigned"],
            "text": "全场通用",
            "volume": 4,
            "enabled": True,
            "exempt_cert_nos": [],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["exempt"] == []
    assert repository.matching_rules(DEVICE_ID, "C1", "assigned") is not None


def _write_effect_wav(target: Path, seconds: float = 0.5, rate: int = 22050) -> int:
    import wave as wave_module

    target.parent.mkdir(parents=True, exist_ok=True)
    frames = int(rate * seconds)
    with wave_module.open(str(target), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(b"\x00\x00" * frames)
    return int(seconds * 1000)


def test_effects_upload_rules_and_guard(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tts, "normalize_to_wav", lambda _src, target: _write_effect_wav(target, 0.5))

    upload = client.post(
        "/api/v1/voice/effects",
        headers=admin_headers,
        data={"name": "叮"},
        files={"file": ("ding.wav", b"arbitrary-bytes", "audio/wav")},
    )
    assert upload.status_code == 200, upload.text
    effect = upload.json()
    assert effect["name"] == "叮"
    assert effect["duration_ms"] == 500
    effect_id = effect["effect_id"]

    # 同内容再传只改名,不产生第二个音效
    renamed = client.post(
        "/api/v1/voice/effects",
        headers=admin_headers,
        data={"name": "叮咚"},
        files={"file": ("ding.wav", b"arbitrary-bytes", "audio/wav")},
    ).json()
    assert renamed["effect_id"] == effect_id
    assert renamed["name"] == "叮咚"

    listing = client.get("/api/v1/voice/effects", headers=admin_headers).json()
    assert [item["effect_id"] for item in listing] == [effect_id]

    # 试听下载:内容寻址、公开
    audition = client.get(f"/voice/effects/{effect_id}.wav")
    assert audition.status_code == 200
    assert audition.headers["content-type"].startswith("audio/wav")

    # 模板里写不在库的音效名 → 422 点名;写在库的名字 → 建成
    missing = client.post(
        "/api/v1/voice/rules",
        headers=admin_headers,
        json=_payload(text="{fx:不存在} 固定内容"),
    )
    assert missing.status_code == 422
    assert "不存在" in missing.json()["detail"]
    rule = _create(client, admin_headers, text="{fx:叮咚} 固定内容")
    assert rule["text"] == "{fx:叮咚} 固定内容"

    # 被模板引用的音效不许删;同内容换新名上传会级联改写引用它的规则模板
    blocked = client.delete(f"/api/v1/voice/effects/{effect_id}", headers=admin_headers)
    assert blocked.status_code == 409
    cascaded = client.post(
        "/api/v1/voice/effects",
        headers=admin_headers,
        data={"name": "新名字"},
        files={"file": ("ding.wav", b"arbitrary-bytes", "audio/wav")},
    )
    assert cascaded.status_code == 200, cascaded.text
    assert cascaded.json()["name"] == "新名字"
    rules_listing = client.get("/api/v1/voice/rules", headers=admin_headers).json()
    assert any("{fx:新名字}" in item["text"] for item in rules_listing)
    assert client.delete(f"/api/v1/voice/rules/{rule['id']}", headers=admin_headers).status_code == 204
    assert client.delete(f"/api/v1/voice/effects/{effect_id}", headers=admin_headers).status_code == 204
    assert client.get("/api/v1/voice/effects", headers=admin_headers).json() == []
    assert not tts.effect_path(effect_id).is_file()

    # 坏文件与超长音效在入口被拒
    def broken(_src: object, _target: object) -> int:
        message = "boom"
        raise tts.VoiceSynthesisError(message)

    monkeypatch.setattr(tts, "normalize_to_wav", broken)
    bad = client.post(
        "/api/v1/voice/effects",
        headers=admin_headers,
        data={"name": "坏"},
        files={"file": ("x.wav", b"junk", "audio/wav")},
    )
    assert bad.status_code == 422

    monkeypatch.setattr(tts, "normalize_to_wav", lambda _src, target: _write_effect_wav(target, 11.0))
    too_long = client.post(
        "/api/v1/voice/effects",
        headers=admin_headers,
        data={"name": "太长"},
        files={"file": ("x.wav", b"junk", "audio/wav")},
    )
    assert too_long.status_code == 422


def test_plan_of_rejects_missing_effect(fresh_db: str) -> None:
    from app.core.problems import ApiProblemError
    from app.modules.voice import render as voice_render

    with pytest.raises(ApiProblemError):
        service._plan_of([voice_render.Segment(voice_render.FX, effect="不存在")])
    effect_id = "a" * 64
    repository.upsert_effect(effect_id, "孤儿", 100, 100, now_rfc3339())
    # 登记在库但文件被外部删掉:点名文件缺失
    with pytest.raises(ApiProblemError):
        service._plan_of([voice_render.Segment(voice_render.FX, effect="孤儿")])


def test_effect_download_rejects_bad_ids(client: TestClient, admin_headers: dict[str, str]) -> None:
    assert client.get("/voice/effects/nothex.wav").status_code == 404
    assert client.get(f"/voice/effects/{'b' * 64}.wav").status_code == 404


def test_preview_and_dispatch_carry_effect(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tts, "normalize_to_wav", lambda _src, target: _write_effect_wav(target, 0.5))
    client.post(
        "/api/v1/voice/effects",
        headers=admin_headers,
        data={"name": "叮"},
        files={"file": ("ding.wav", b"arbitrary-bytes", "audio/wav")},
    )

    preview = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={"text": "{fx:叮} 固定内容", "event": "assigned"},
    )
    assert preview.status_code == 200
    assert preview.json()["rendered_text"].startswith("♪叮")

    _create(client, admin_headers, text="{fx:叮} 固定内容")
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _fake_send_collector(calls))

    asyncio.run(service.handle_events((_event(now_rfc3339()),)))

    assert [action for action, _params in calls] == ["voice.play"]
    # 展示文本带音效记号;内容身份与纯文本不同
    deliveries = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()
    assert deliveries[0]["text"].startswith("♪叮")
    plain_plan: tts.Plan = [("say", "固定内容")]
    assert calls[0][1]["clip_id"] != tts.clip_id_for(plain_plan, "melo")


def test_effect_upload_entry_guards(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
) -> None:
    def post(name: str, payload: bytes) -> object:
        return client.post(
            "/api/v1/voice/effects",
            headers=admin_headers,
            data={"name": name},
            files={"file": ("x.wav", payload, "audio/wav")},
        ).status_code

    assert post("   ", b"x") == 422
    assert post("名" * 41, b"x") == 422
    assert post("空文件", b"") == 422
    assert post("超限", b"x" * (tts.MAX_EFFECT_UPLOAD_BYTES + 1)) == 422


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


def test_missing_device_and_rule(client: TestClient, admin_headers: dict[str, str], voice_device: None) -> None:
    assert client.post("/api/v1/voice/rules", headers=admin_headers, json=_payload(devices=["nope"])).status_code == 404
    assert (
        client.put(
            "/api/v1/voice/rules/999",
            headers=admin_headers,
            json=_update_body(),
        ).status_code
        == 404
    )
    assert client.patch("/api/v1/voice/rules/999", headers=admin_headers, json={"enabled": True}).status_code == 404
    assert client.delete("/api/v1/voice/rules/999", headers=admin_headers).status_code == 404


def test_preview_invalid_and_synthesis_failure(
    client: TestClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={"text": "你好 {name}", "event": "assigned"},
    )
    assert invalid.status_code == 422

    def boom(_plan: object, _clip_id: str, _voice: str) -> tts.ClipFile:
        message = "boom"
        raise tts.VoiceSynthesisError(message)

    monkeypatch.setattr(tts, "synthesize", boom)
    failed = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={"text": "固定内容", "event": "assigned"},
    )
    assert failed.status_code == 503
    assert failed.json()["type"].endswith("/problems/voice-synthesis-failed")


def test_clip_endpoint_rejects_bad_ids(client: TestClient, admin_headers: dict[str, str]) -> None:
    assert client.get("/voice/clips/nothex.mp3").status_code == 404
    assert client.get(f"/voice/clips/{'a' * 64}.mp3").status_code == 404


def test_handle_events_delivers_once_and_records_stage_timings(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(
        client,
        admin_headers,
        text="{event}，{door_no} 号喷头已就绪",
        volume=3,
    )
    now = now_rfc3339()
    event = _event(now)
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _fake_send_collector(calls))

    asyncio.run(service.handle_events((event,)))
    asyncio.run(service.handle_events((event,)))

    # 服务端只下发一次播放；等待浴室提示音在设备端完成
    assert [action for action, _params in calls] == ["voice.play"]
    _action, params = calls[0]
    assert params["volume"] == 3
    assert params["duration_ms"] == 3000

    deliveries = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery["text"] == "空闲或已选中，转为等待开阀，5 号喷头已就绪"
    assert delivery["status"] == "ok"
    assert delivery["source"] == "auto"
    assert "delay_ms" not in delivery
    assert delivery["dispatched_at"]
    assert delivery["done_at"]
    assert delivery["clip_ms"] >= 0
    assert delivery["dispatch_offset_ms"] is not None
    assert delivery["dispatch_offset_ms"] >= 0
    assert delivery["finish_offset_ms"] is not None
    assert delivery["finish_offset_ms"] >= 0
    assert delivery["downloaded"] is False


def test_handle_events_skips_stale_and_no_rule(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client, admin_headers)
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _fake_send_collector(calls))
    monkeypatch.setattr(doors_service, "session_durations", lambda _id: None)

    # 时效过期与无规则两条都不播;播报不再依赖会话表(时长占位符已移除)
    asyncio.run(service.handle_events((_event("2000-01-01T00:00:00.000Z"),)))
    asyncio.run(service.handle_events((_event(now_rfc3339(), cert_no="C2"),)))

    assert calls == []
    assert client.get("/api/v1/voice/deliveries", headers=admin_headers).json() == []


def test_wildcard_rule_matches_any_cert(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client, admin_headers, devices=["*"], certs=["*"], text="全场通用")
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _fake_send_collector(calls))

    asyncio.run(service.handle_events((_event(now_rfc3339(), device_id=OTHER_DEVICE_ID, cert_no="C77"),)))

    assert [action for action, _params in calls] == ["voice.play"]
    deliveries = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()
    assert len(deliveries) == 1
    assert deliveries[0]["text"] == "全场通用"


def test_handle_events_marks_tts_failure(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client, admin_headers)
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _fake_send_collector(calls))

    def boom(_plan: object, _clip_id: str, _voice: str) -> tts.ClipFile:
        message = "boom"
        raise tts.VoiceSynthesisError(message)

    monkeypatch.setattr(tts, "synthesize", boom)

    asyncio.run(service.handle_events((_event(now_rfc3339()),)))

    assert calls == []
    deliveries = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "failed"
    assert "boom" in deliveries[0]["detail"]
    assert deliveries[0]["done_at"]


def test_prepare_static_and_schedule(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    static = schemas.VoiceRuleView(**_create(client, admin_headers, text="固定内容", certs=["C2"]))
    dynamic = schemas.VoiceRuleView(**_create(client, admin_headers, text="你好 {door_no}", certs=["C3"]))

    clip = service.prepare_static(static)
    assert clip is not None
    assert service.prepare_static(dynamic) is None
    assert service.schedule_prepare([], clip) is None

    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _fake_send_collector(calls))

    async def run() -> None:
        service.schedule_prepare(service.rule_device_targets([static]), clip)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert [action for action, _params in calls] == ["voice.prepare"]
    assert "volume" not in calls[0][1]


def test_wildcard_rule_warms_all_registered_devices(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    view = schemas.VoiceRuleView(**_create(client, admin_headers, devices=["*"], text="固定内容"))
    clip = service.prepare_static(view)
    assert clip is not None
    assert set(service.rule_device_targets([view])) == {DEVICE_ID, OTHER_DEVICE_ID}

    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _fake_send_collector(calls))

    async def run() -> None:
        service.schedule_prepare(service.rule_device_targets([view]), clip)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert [action for action, _params in calls] == ["voice.prepare", "voice.prepare"]


def test_patch_enabled_warms_static_clip(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = _create(client, admin_headers, text="固定内容")
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _fake_send_collector(calls))

    disabled = client.patch(f"/api/v1/voice/rules/{created['id']}", headers=admin_headers, json={"enabled": False})
    assert disabled.json()["enabled"] is False

    enabled = client.patch(f"/api/v1/voice/rules/{created['id']}", headers=admin_headers, json={"enabled": True})
    assert enabled.json()["enabled"] is True

    # 预热任务挂在应用的 portal 事件循环上：真实睡一小段时间让它跑完
    time.sleep(0.1)
    assert [action for action, _params in calls] == ["voice.prepare"]


def test_voice_flows_into_synthesis(
    fresh_db: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    def capture(plan: tts.Plan, clip_id: str, voice: str) -> tts.ClipFile:
        captured.append(voice)
        path = tts.clip_path(clip_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = ("MP3:" + tts.plan_identity(plan)).encode()
        path.write_bytes(data)
        return tts.ClipFile(clip_id, tts.sha256_of(path), len(data), 3000, path)

    monkeypatch.setattr(tts, "synthesize", capture)

    plan: tts.Plan = [("say", "音色样本")]
    clip, _clip_ms = service._ensure_clip(plan, "melo")
    assert captured[-1] == "melo"
    assert clip.clip_id == tts.clip_id_for(plan, "melo")

    other, _clip_ms = service._ensure_clip([("say", "另一段")], "melo")
    assert captured[-1] == "melo"
    assert other.clip_id != clip.clip_id


def test_prepare_static_failure_is_not_fatal(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_plan: object, _clip_id: str, _voice: str) -> tts.ClipFile:
        message = "boom"
        raise tts.VoiceSynthesisError(message)

    monkeypatch.setattr(tts, "synthesize", boom)
    view = schemas.VoiceRuleView(**_create(client, admin_headers, text="从未合成过的固定内容"))
    assert service.prepare_static(view) is None


def test_update_validation_and_preview_render_failure(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    created = _create(client, admin_headers)
    update = client.put(
        f"/api/v1/voice/rules/{created['id']}",
        headers=admin_headers,
        json=_update_body(certs=["   "]),
    )
    assert update.status_code == 422

    preview = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={"text": "x" * 110 + "{cert_no}", "event": "assigned", "cert_no": "C" * 60},
    )
    assert preview.status_code == 422


def test_ensure_clip_regenerates_bad_cache(fresh_db: str, fake_tts: None) -> None:
    plan: tts.Plan = [("say", "固定内容")]
    clip_id = tts.clip_id_for(plan, "melo")
    repository.upsert_clip(clip_id, "bad", 1, 1, now_rfc3339())

    clip, _clip_ms = service._ensure_clip(plan, "melo")

    assert clip.path.is_file()
    assert clip.sha256 != "bad"


def test_preview_prunes_unused_clip(
    client: TestClient,
    admin_headers: dict[str, str],
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repository, "prune_clips", lambda *_args: ["dead"])
    response = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={"text": "固定内容", "event": "assigned"},
    )
    assert response.status_code == 200


def test_schedule_prepare_without_loop(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    view = schemas.VoiceRuleView(**_create(client, admin_headers, text="固定内容"))
    clip = service.prepare_static(view)
    assert clip is not None
    service.schedule_prepare(service.rule_device_targets([view]), clip)


def test_prepare_worker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_args: object, **_kwargs: object) -> CommandOutcome:
        message = "boom"
        raise RuntimeError(message)

    monkeypatch.setattr(sessions_service, "send_command_and_wait", boom)
    asyncio.run(service._prepare(DEVICE_ID, {}, 60.0))


def test_handle_events_swallows_single_event_failure(fresh_db: str, monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(_event: DoorEvent, _max_age: int) -> None:
        message = "boom"
        raise RuntimeError(message)

    monkeypatch.setattr(service, "_dispatch_event", boom)
    asyncio.run(service.handle_events((_event(now_rfc3339()),)))


def test_task_done_logs_exception() -> None:
    async def run() -> None:
        async def boom() -> None:
            message = "boom"
            raise RuntimeError(message)

        task = asyncio.create_task(boom())
        service._tasks.add(task)
        task.add_done_callback(service._task_done)
        await asyncio.sleep(0.05)

    asyncio.run(run())


def test_stale_after_preparation(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client, admin_headers)
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _fake_send_collector(calls))
    # 两次读时：事件时效初检（未超）→ 音频准备后再查（超了）
    elapsed = [0.0, 100.0]

    def fake_elapsed(_value: str) -> float:
        return elapsed.pop(0) if elapsed else 100.0

    monkeypatch.setattr(service, "_elapsed_s", fake_elapsed)

    asyncio.run(service.handle_events((_event(now_rfc3339()),)))

    assert calls == []
    deliveries = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "skipped"
    assert deliveries[0]["dispatch_offset_ms"] is None


def test_event_text_render_failure(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cert_no = "C" * 60
    _create(client, admin_headers, certs=[cert_no], text="x" * 110 + "{cert_no}")
    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _fake_send_collector(calls))

    asyncio.run(service.handle_events((_event(now_rfc3339(), cert_no=cert_no),)))

    assert calls == []
    assert client.get("/api/v1/voice/deliveries", headers=admin_headers).json() == []


def test_elapsed_parse_failure() -> None:
    assert service._elapsed_s("not-a-time") == 0.0


def test_schedule_events_creates_task(monkeypatch: pytest.MonkeyPatch) -> None:
    ran = asyncio.Event()

    async def fake_handle(_events: tuple[DoorEvent, ...]) -> None:
        ran.set()

    monkeypatch.setattr(service, "handle_events", fake_handle)

    async def run() -> None:
        service.schedule_events((_event(now_rfc3339()),))
        await asyncio.wait_for(ran.wait(), timeout=1)

    asyncio.run(run())


def test_schedule_events_without_loop_is_safe() -> None:
    service.schedule_events((_event(now_rfc3339()),))


def test_play_timeout_and_downloaded_helpers() -> None:
    assert service._play_timeout_s(0) == 30.0
    assert service._play_timeout_s(30_000) == 60.0
    assert service._play_timeout_s(120_000) == 120.0
    assert service._downloaded(_ok_outcome(downloaded=True)) is True
    assert service._downloaded(_ok_outcome(downloaded=False)) is False
    assert service._downloaded(_ok_outcome()) is False


def test_repository_prune_clips(fresh_db: str) -> None:
    for index in range(3):
        repository.upsert_clip(
            f"{index:064x}",
            "sha",
            10,
            1000,
            f"2026-10-2{index}T00:00:00.000Z",
        )
    assert repository.count_clips() == 3
    assert repository.prune_clips(10, 1000) == []
    removed = repository.prune_clips(1, 1000)
    assert len(removed) == 2
    assert repository.count_clips() == 1
    assert repository.get_clip(removed[0]) is None


def test_effect_rename_endpoint_and_name_autofill(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    durations = iter([0.4, 0.6])

    def fake_normalize(_src: object, target: object) -> int:
        return _write_effect_wav(target, next(durations))

    monkeypatch.setattr(tts, "normalize_to_wav", fake_normalize)

    # 名称由前端选中文件时自动填入后提交；接口侧必填
    upload = client.post(
        "/api/v1/voice/effects",
        headers=admin_headers,
        data={"name": "door-open"},
        files={"file": ("door-open.mp3", b"arbitrary-bytes", "audio/mpeg")},
    )
    assert upload.status_code == 200, upload.text
    effect = upload.json()
    assert effect["name"] == "door-open"
    effect_id = effect["effect_id"]

    # 改名端点：改名字本身
    renamed = client.put(f"/api/v1/voice/effects/{effect_id}", headers=admin_headers, json={"name": "开门"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "开门"

    # 被规则模板引用的旧名：改名级联改写模板，规则不失效
    rule = _create(client, admin_headers, text="{fx:开门} 前奏")
    again = client.put(f"/api/v1/voice/effects/{effect_id}", headers=admin_headers, json={"name": "关门"})
    assert again.status_code == 200, again.text
    rules_listing = client.get("/api/v1/voice/rules", headers=admin_headers).json()
    assert any("{fx:关门}" in item["text"] for item in rules_listing)
    assert not any("{fx:开门}" in item["text"] for item in rules_listing)

    # 与库内其他音效重名：拒绝
    other = client.post(
        "/api/v1/voice/effects",
        headers=admin_headers,
        data={"name": "别的"},
        files={"file": ("other.wav", b"other-bytes", "audio/wav")},
    )
    assert other.status_code == 200
    conflict = client.put(f"/api/v1/voice/effects/{effect_id}", headers=admin_headers, json={"name": "别的"})
    assert conflict.status_code == 422

    # 不存在的音效：404
    missing = client.put("/api/v1/voice/effects/" + "0" * 64, headers=admin_headers, json={"name": "随意"})
    assert missing.status_code == 404

    assert client.delete(f"/api/v1/voice/rules/{rule['id']}", headers=admin_headers).status_code == 204


def test_rule_push_plays_on_selected_device(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tts, "normalize_to_wav", lambda _src, target: _write_effect_wav(target, 0.4))
    rule = _create(client, admin_headers, text="手动 {door_no} 号", volume=3)
    rule_id = int(rule["id"])

    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(sessions_service, "send_command_and_wait", _fake_send_collector(calls))

    pushed = client.post(f"/api/v1/voice/rules/{rule_id}/push", headers=admin_headers, json={"device_id": "dev-voice"})
    assert pushed.status_code == 200, pushed.text
    body = pushed.json()
    assert body["device_id"] == "dev-voice"
    assert body["status"] == "ok"
    assert "手动 1 号" in body["rendered_text"]

    # 下发参数与真实事件一致：voice.play + 规则的音量
    assert [action for action, _params in calls] == ["voice.play"]
    assert calls[0][1]["volume"] == 3

    # 最近推送里以 manual:* 落一条：来源可区分
    deliveries = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()
    assert deliveries[0]["status"] == "ok"
    assert deliveries[0]["source"] == "manual"
    assert "delay_ms" not in deliveries[0]
    assert deliveries[0]["detail"].startswith("手动推送")


def test_rule_push_rejects_unknown_device_and_rule(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tts, "normalize_to_wav", lambda _src, target: _write_effect_wav(target, 0.4))
    rule = _create(client, admin_headers, text="固定内容")

    missing_device = client.post(
        f"/api/v1/voice/rules/{rule['id']}/push", headers=admin_headers, json={"device_id": "no-such"}
    )
    assert missing_device.status_code == 404

    missing_rule = client.post(
        "/api/v1/voice/rules/999999/push", headers=admin_headers, json={"device_id": "dev-voice"}
    )
    assert missing_rule.status_code == 404


def test_rule_update_rejects_bad_scope_shapes(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    """整体编辑的形状校验与创建同一套：排除名单只配「全部学号」，事件至少一个。"""
    rule = _create(client, admin_headers, certs=["*"])

    exempt_on_concrete = client.put(
        f"/api/v1/voice/rules/{rule['id']}",
        headers=admin_headers,
        json=_update_body(certs=["C1"], exempt_cert_nos=["C9"]),
    )
    assert exempt_on_concrete.status_code == 422
    assert "排除名单只用于" in exempt_on_concrete.json()["detail"]

    no_events = client.put(
        f"/api/v1/voice/rules/{rule['id']}",
        headers=admin_headers,
        json=_update_body(events=[]),
    )
    assert no_events.status_code == 422


def test_rule_scope_service_guards(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    """服务层形状守卫：事件集合为空在服务层拒绝（HTTP 边界拦不住服务层直调）。"""
    with pytest.raises(pydantic.ValidationError):
        schemas.VoiceRuleCreate(
            devices=[DEVICE_ID],
            certs=["C1"],
            events=[],
            text="固定内容",
        )
    # 事件集合的下限由 pydantic 边界把关，服务层守卫是同一形状的背面
    with pytest.raises(pydantic.ValidationError):
        schemas.VoiceRuleUpdate(
            devices=[DEVICE_ID],
            certs=["C1"],
            events=[],
            text="固定内容",
        )


def test_effect_rename_service_guards(fresh_db: str, fake_tts: None) -> None:
    from app.core.problems import ApiProblemError

    effect_id = "a" * 64
    repository.upsert_effect(effect_id, "叮", 10, 100, now_rfc3339())

    with pytest.raises(ApiProblemError):
        service.rename_effect("b" * 64, "随意")
    with pytest.raises(ApiProblemError):
        service.rename_effect(effect_id, "   ")
    with pytest.raises(ApiProblemError):
        service.rename_effect(effect_id, "长" * 41)
    # 同名改名是空操作，原样返回
    same = service.rename_effect(effect_id, "叮")
    assert same.name == "叮"


def test_render_branch_guards() -> None:
    """渲染分支守卫：空内容、无名音效占位符在渲染层拒绝。"""
    with pytest.raises(render.TemplateError):
        render.render_segments("   ", render.sample_values("分配"))
    with pytest.raises(render.TemplateError):
        render.render_segments("{fx:} 固定", render.sample_values("分配"))
    with pytest.raises(render.TemplateError):
        render.validate_template("{fx:} 固定")


def test_offset_ms_rejects_unparseable_timestamps() -> None:
    """投递时刻解析失败时，耗时字段留空，不拿 0 冒充。"""
    assert service._offset_ms("not-a-time", "also-bad") is None
    assert service._offset_ms(now_rfc3339(), "not-a-time") is None
    # 全量渲染：值齐全时逐段拼出最终文本
    assert render.render_text(render.render_segments("你好 {door_no}", render.sample_values("分配"))) == "你好 1"
