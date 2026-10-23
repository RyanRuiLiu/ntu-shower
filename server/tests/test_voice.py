"""语音播报：规则接口、模板试听、事件投递、延迟/去重/失败与缓存。"""

import asyncio

import pytest
from fastapi.testclient import TestClient
from tests import helpers

from app.core.clock import now_rfc3339, shift_rfc3339
from app.modules.doors import service as doors_service
from app.modules.doors.events import DoorEvent, DoorEventKind
from app.modules.sessions import service as sessions_service
from app.modules.sessions.schemas import CommandOutcome
from app.modules.voice import repository, schemas, service, tts

DEVICE_ID = "dev-voice"


@pytest.fixture
def voice_device(fresh_db: str) -> None:
    helpers.register_trusted_device(DEVICE_ID)


@pytest.fixture
def fake_tts(monkeypatch: pytest.MonkeyPatch) -> None:
    def synthesize(text: str, clip_id: str) -> tts.ClipFile:
        path = tts.clip_path(clip_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = ("MP3:" + text).encode()
        path.write_bytes(data)
        return tts.ClipFile(clip_id, tts.sha256_of(path), len(data), 3000, path)

    monkeypatch.setattr(tts, "synthesize", synthesize)


def _payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "device_id": DEVICE_ID,
        "cert_no": "C1",
        "event": "assigned",
        "text": "欢迎使用，{door_no} 号喷头",
        "delay_ms": 0,
        "volume": 3,
        "enabled": True,
        "priority": 100,
    }
    payload.update(overrides)
    return payload


def _create(client: TestClient, headers: dict[str, str], **overrides: object) -> dict[str, object]:
    response = client.post("/api/v1/voice/rules", headers=headers, json=_payload(**overrides))
    assert response.status_code == 200, response.text
    body: dict[str, object] = response.json()
    return body


def _event(
    occurred_at: str,
    *,
    cert_no: str = "C1",
    key: str = "session:1:assigned",
    kind: DoorEventKind = DoorEventKind.ASSIGNED,
) -> DoorEvent:
    return DoorEvent(
        kind=kind,
        device_id=DEVICE_ID,
        door_no=5,
        cert_no=cert_no,
        session_id=1,
        occurred_at=occurred_at,
        event_key=key,
    )


def _ok_outcome() -> CommandOutcome:
    return CommandOutcome(status="ok", reason_code="", terminal_message="", data=None)


def test_spec_uses_canonical_event_names(client: TestClient, admin_headers: dict[str, str]) -> None:
    response = client.get("/api/v1/voice/spec", headers=admin_headers)
    assert response.status_code == 200
    spec = response.json()
    events = {item["value"]: item for item in spec["events"]}
    assert events["assigned"]["label"] == "等待开阀"
    assert events["shower_started"]["label"] == "淋浴中"
    assert events["paused"]["label"] == "已暂停"
    assert events["resumed"]["label"] == "淋浴中"
    assert events["released"]["label"] == "空闲"
    assert [item["key"] for item in spec["placeholders"]] == [
        "door_no",
        "cert_no",
        "event",
        "assigned_duration",
        "shower_duration",
    ]
    assert spec["delay"] == {"minimum_ms": 0, "maximum_ms": 10_000, "default_ms": 0}
    assert spec["volume"]["maximum"] == 5
    assert spec["priority"]["maximum"] == 999


def test_status_reports_readiness(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    body = client.get("/api/v1/voice/status", headers=admin_headers).json()
    assert body["ready"] is False

    monkeypatch.setattr(tts, "probe", lambda: tts.ClipStatus(True, True, True, True))
    ready = client.get("/api/v1/voice/status", headers=admin_headers).json()
    assert ready["ready"] is True
    assert ready["model_present"] is True
    assert ready["espeak_available"] is True


def test_rule_crud_preview_and_clip(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    created = _create(client, admin_headers)
    rule_id = created["id"]
    assert created["sample_text"] == "欢迎使用，1 号喷头"
    assert created["delay_ms"] == 0
    assert created["event"] == "assigned"

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
        json={
            "cert_no": "C1",
            "event": "assigned",
            "text": "固定内容",
            "delay_ms": 3000,
            "volume": 4,
            "enabled": False,
            "priority": 50,
        },
    )
    assert updated.status_code == 200
    assert updated.json()["sample_text"] == "固定内容"
    assert updated.json()["delay_ms"] == 3000
    assert updated.json()["enabled"] is False

    deleted = client.delete(f"/api/v1/voice/rules/{rule_id}", headers=admin_headers)
    assert deleted.status_code == 204
    assert client.get("/api/v1/voice/rules", headers=admin_headers).json() == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"text": "你好 {name}"},
        {"text": "{door_no"},
        {"text": "   "},
        {"cert_no": "   "},
        {"delay_ms": 10_001},
        {"volume": 6},
        {"priority": 1_000},
        {"event": "bogus"},
    ],
)
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


@pytest.mark.parametrize(
    "overrides",
    [
        {"text": "你好 {name}"},
        {"text": "{door_no"},
        {"text": "   "},
        {"cert_no": "   "},
        {"delay_ms": 10_001},
        {"volume": 6},
        {"priority": 1_000},
        {"event": "bogus"},
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
    assert client.post("/api/v1/voice/rules", headers=admin_headers, json=_payload(device_id="nope")).status_code == 404
    assert (
        client.put(
            "/api/v1/voice/rules/999",
            headers=admin_headers,
            json={
                "cert_no": "C1",
                "event": "assigned",
                "text": "固定内容",
                "delay_ms": 0,
                "volume": 3,
                "enabled": True,
                "priority": 100,
            },
        ).status_code
        == 404
    )
    assert client.delete("/api/v1/voice/rules/999", headers=admin_headers).status_code == 404


def test_priority_order_and_device_filter(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
) -> None:
    first = _create(client, admin_headers, priority=10)
    second = _create(client, admin_headers, priority=20)
    third = _create(client, admin_headers, priority=10)

    matched = repository.match_rule(DEVICE_ID, "C1", "assigned")
    assert matched is not None
    assert int(matched["id"]) == int(first["id"])

    client.put(
        f"/api/v1/voice/rules/{first['id']}",
        headers=admin_headers,
        json={
            "cert_no": "C1",
            "event": "assigned",
            "text": "停用的高优先级",
            "delay_ms": 0,
            "volume": 3,
            "enabled": False,
            "priority": 10,
        },
    )
    matched = repository.match_rule(DEVICE_ID, "C1", "assigned")
    assert matched is not None
    assert int(matched["id"]) == int(third["id"])

    assert client.get(f"/api/v1/voice/rules?device_id={DEVICE_ID}", headers=admin_headers).json()
    assert client.get("/api/v1/voice/rules?device_id=other", headers=admin_headers).json() == []
    assert second["priority"] == 20


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

    def boom(_text: str, _clip_id: str) -> tts.ClipFile:
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


def test_handle_events_delivers_once_and_renders_placeholders(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(
        client,
        admin_headers,
        text="{event}，{door_no} 号，分配 {assigned_duration}，淋浴 {shower_duration}",
        volume=3,
    )
    now = now_rfc3339()
    event = _event(now)
    monkeypatch.setattr(
        doors_service,
        "session_durations",
        lambda _session_id: doors_service.SessionDurations(
            assigned_at=shift_rfc3339(now, -1122),
            ended_at="",
            flow_s=965,
        ),
    )
    calls: list[tuple[str, str, dict[str, object], float]] = []

    async def fake_send(
        device_id: str,
        action: str,
        params: dict[str, object],
        timeout_s: float = 30.0,
    ) -> CommandOutcome:
        calls.append((device_id, action, params, timeout_s))
        return _ok_outcome()

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)

    asyncio.run(service.handle_events((event,)))
    asyncio.run(service.handle_events((event,)))

    assert len(calls) == 1
    device_id, action, params, timeout_s = calls[0]
    assert device_id == DEVICE_ID
    assert action == "voice.play"
    assert params["volume"] == 3
    assert params["duration_ms"] == 3000
    assert timeout_s == 33.0

    deliveries = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert delivery["text"] == "等待开阀，5 号，分配 18分42秒，淋浴 16分5秒"
    assert delivery["status"] == "ok"
    assert delivery["delay_ms"] == 0
    assert delivery["dispatched_at"]


def test_handle_events_skips_stale_and_no_rule(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client, admin_headers)
    calls: list[str] = []

    async def fake_send(
        _device_id: str,
        action: str,
        _params: dict[str, object],
        timeout_s: float = 30.0,
    ) -> CommandOutcome:
        calls.append(action)
        return _ok_outcome()

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    monkeypatch.setattr(doors_service, "session_durations", lambda _id: None)

    asyncio.run(service.handle_events((_event("2000-01-01T00:00:00.000Z"),)))
    asyncio.run(service.handle_events((_event(now_rfc3339(), cert_no="C2"),)))
    asyncio.run(service.handle_events((_event(now_rfc3339()),)))

    assert calls == []
    assert client.get("/api/v1/voice/deliveries", headers=admin_headers).json() == []


def test_handle_events_marks_tts_failure(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client, admin_headers)
    calls: list[str] = []

    async def fake_send(
        _device_id: str,
        action: str,
        _params: dict[str, object],
        timeout_s: float = 30.0,
    ) -> CommandOutcome:
        calls.append(action)
        return _ok_outcome()

    def boom(_text: str, _clip_id: str) -> tts.ClipFile:
        message = "boom"
        raise tts.VoiceSynthesisError(message)

    monkeypatch.setattr(tts, "synthesize", boom)
    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    monkeypatch.setattr(
        doors_service,
        "session_durations",
        lambda _id: doors_service.SessionDurations(assigned_at=now_rfc3339(), ended_at="", flow_s=0),
    )

    asyncio.run(service.handle_events((_event(now_rfc3339()),)))

    assert calls == []
    deliveries = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "failed"
    assert "boom" in deliveries[0]["detail"]


def test_handle_events_honours_delay(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client, admin_headers, delay_ms=50)
    calls: list[float] = []

    async def fake_send(
        _device_id: str,
        _action: str,
        _params: dict[str, object],
        timeout_s: float = 30.0,
    ) -> CommandOutcome:
        calls.append(timeout_s)
        return _ok_outcome()

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    monkeypatch.setattr(
        doors_service,
        "session_durations",
        lambda _id: doors_service.SessionDurations(assigned_at=now_rfc3339(), ended_at="", flow_s=0),
    )

    asyncio.run(service.handle_events((_event(now_rfc3339()),)))
    assert len(calls) == 1


def test_prepare_static_and_schedule(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    static = schemas.VoiceRuleView(**_create(client, admin_headers, text="固定内容", cert_no="C2"))
    dynamic = schemas.VoiceRuleView(**_create(client, admin_headers, text="你好 {door_no}", cert_no="C3"))

    clip = service.prepare_static(static)
    assert clip is not None
    assert service.prepare_static(dynamic) is None
    assert service.schedule_prepare(dynamic, None) is None

    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_send(
        device_id: str,
        action: str,
        params: dict[str, object],
        timeout_s: float = 30.0,
    ) -> CommandOutcome:
        calls.append((action, params))
        return _ok_outcome()

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)

    async def run() -> None:
        service.schedule_prepare(static, clip)
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert calls
    assert calls[0][0] == "voice.prepare"
    assert "volume" not in calls[0][1]


def test_prepare_static_failure_is_not_fatal(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_text: str, _clip_id: str) -> tts.ClipFile:
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
        json={
            "cert_no": "   ",
            "event": "assigned",
            "text": "固定内容",
            "delay_ms": 0,
            "volume": 3,
            "enabled": True,
            "priority": 100,
        },
    )
    assert update.status_code == 422

    preview = client.post(
        "/api/v1/voice/preview",
        headers=admin_headers,
        json={"text": "x" * 110 + "{cert_no}", "event": "assigned", "cert_no": "C" * 60},
    )
    assert preview.status_code == 422


def test_ensure_clip_regenerates_bad_cache(fresh_db: str, fake_tts: None) -> None:
    text = "固定内容"
    clip_id = tts.clip_id_for_text(text)
    repository.upsert_clip(clip_id, "bad", 1, 1, now_rfc3339())

    clip = service._ensure_clip(text)

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
    service.schedule_prepare(view, clip)


def test_prepare_worker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_args: object, **_kwargs: object) -> CommandOutcome:
        message = "boom"
        raise RuntimeError(message)

    monkeypatch.setattr(sessions_service, "send_command_and_wait", boom)
    asyncio.run(service._prepare(DEVICE_ID, {}))


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


def test_stale_after_delay(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    fake_tts: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _create(client, admin_headers, delay_ms=0)
    calls: list[str] = []

    async def fake_send(
        _device_id: str,
        action: str,
        _params: dict[str, object],
        timeout_s: float = 30.0,
    ) -> CommandOutcome:
        calls.append(action)
        return _ok_outcome()

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    monkeypatch.setattr(
        doors_service,
        "session_durations",
        lambda _id: doors_service.SessionDurations(assigned_at=now_rfc3339(), ended_at="", flow_s=0),
    )
    elapsed = [0.0, 0.0, 100.0]

    def fake_elapsed(_value: str) -> float:
        return elapsed.pop(0) if elapsed else 100.0

    monkeypatch.setattr(service, "_elapsed_s", fake_elapsed)

    asyncio.run(service.handle_events((_event(now_rfc3339()),)))

    assert calls == []
    deliveries = client.get("/api/v1/voice/deliveries", headers=admin_headers).json()
    assert len(deliveries) == 1
    assert deliveries[0]["status"] == "skipped"


def test_event_text_render_failure(
    client: TestClient,
    admin_headers: dict[str, str],
    voice_device: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cert_no = "C" * 60
    _create(client, admin_headers, cert_no=cert_no, text="x" * 110 + "{cert_no}")
    calls: list[str] = []

    async def fake_send(
        _device_id: str,
        action: str,
        _params: dict[str, object],
        timeout_s: float = 30.0,
    ) -> CommandOutcome:
        calls.append(action)
        return _ok_outcome()

    monkeypatch.setattr(sessions_service, "send_command_and_wait", fake_send)
    monkeypatch.setattr(
        doors_service,
        "session_durations",
        lambda _id: doors_service.SessionDurations(assigned_at=now_rfc3339(), ended_at="", flow_s=0),
    )

    asyncio.run(service.handle_events((_event(now_rfc3339(), cert_no=cert_no),)))

    assert calls == []
    assert client.get("/api/v1/voice/deliveries", headers=admin_headers).json() == []


def test_elapsed_parse_failure_and_sample_text() -> None:
    assert service._elapsed_s("not-a-time") == 0.0
    assert service._sample_text("bad {name}", DoorEventKind.ASSIGNED) == "bad {name}"


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


def test_play_timeout_and_delay_helpers() -> None:
    assert service._play_timeout_s(0) == 30.0
    assert service._play_timeout_s(30_000) == 60.0
    assert service._play_timeout_s(120_000) == 120.0
    asyncio.run(service._wait_for_delay(_event(now_rfc3339()), 0))


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
