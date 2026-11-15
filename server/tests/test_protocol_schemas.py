"""协议 Schema 固定测试：每个消息类型必须有 schema 且样例消息可通过校验。"""

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "protocol" / "schema"


def _registry() -> Registry:
    """按 $id 注册全部 schema，供消息间 $ref 解析。"""
    registry = Registry()
    for path in SCHEMA_DIR.glob("*.json"):
        doc = json.loads(path.read_text(encoding="utf-8"))
        registry = registry.with_resource(doc["$id"], Resource.from_contents(doc))
    return registry


KNOWN_TYPES = [
    "link.hello",
    "link.hello_ack",
    "link.ping",
    "link.pong",
    "link.error",
    "door.snapshot",
    "door.snapshot_ack",
    "device.command",
    "device.command_result",
    "config.update",
    "config.ack",
    "status_line.update",
    "ota.upgrade_available",
    "ota.progress",
    "bridge.open",
    "bridge.close",
    "device.log_upload",
]

SAMPLES: dict[str, dict[str, Any]] = {
    "link.hello": {
        "payload": {
            "device_id": "1c94310a536777d6",
            "app_version": "0.2.4",
            "os_version": "4.4.4",
            "capabilities": ["session.assign"],
            "uptime_s": 45176,
        }
    },
    "link.hello_ack": {
        "payload": {
            "trusted": True,
            "server_time": "2026-09-30T12:00:00.000Z",
            "config": {"heartbeat_interval_s": 30},
            "upgrade": None,
        }
    },
    "link.ping": {"payload": {"sender_time": "2026-09-30T12:00:00.000Z"}},
    "link.pong": {"payload": {"sender_time": "2026-09-30T12:00:00.000Z"}},
    "link.error": {"payload": {"code": "proto.bad_json", "message": "x"}},
    "door.snapshot": {
        "payload": {
            "snapshot_id": "11111111-1111-4111-8111-111111111111",
            "source": "db_watch",
            "captured_at": "2026-09-30T12:00:00.000Z",
            "device_clock": "2026-09-30T12:00:00.000Z",
            "doors": [{"door_no": 1, "card_no": 0, "bath_time_s": 0, "status_raw": 0}],
        }
    },
    "door.snapshot_ack": {"payload": {"snapshot_id": "11111111-1111-4111-8111-111111111111"}},
    "device.command": {"payload": {"action": "session.assign", "params": {}, "timeout_ms": 30000}},
    "device.command_result": {"payload": {"status": "ok", "reason_code": "", "data": {}}},
    "config.update": {"payload": {"revision": 1, "config": {"heartbeat_interval_s": 30}}},
    "config.ack": {"payload": {"revision": 1}},
    "status_line.update": {
        "payload": {
            "floors": [
                {"device_id": "1c94310a536777d6", "name": "1F", "online": True, "free": 3},
                {"device_id": "2c94310a536777d6", "name": "2F", "online": True, "free": 0},
                {"device_id": "3c94310a536777d6", "name": "3F", "online": False, "free": None},
            ]
        }
    },
    "ota.upgrade_available": {
        "payload": {
            "version": "0.2.4",
            "version_code": 6,
            "url": "/api/v1/ota/releases/0.2.4/download",
        }
    },
    "ota.progress": {"payload": {"version": "0.2.4", "stage": "downloading", "detail": ""}},
    "bridge.open": {"payload": {"device_id": "1c94310a536777d6", "nonce": "8f14e45fceea167a"}},
    "device.log_upload": {"payload": {"kind": "device", "content": "line1\nline2\n"}},
    "bridge.close": {"payload": {}},
}


def _envelope(msg_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "v": 1,
        "type": msg_type,
        "id": "11111111-1111-4111-8111-111111111111",
        "ts": "2026-09-30T12:00:00.000Z",
        "payload": payload,
    }


def test_every_known_type_has_schema() -> None:
    for msg_type in KNOWN_TYPES:
        assert (SCHEMA_DIR / f"{msg_type}.json").is_file(), f"missing schema: {msg_type}"


def test_no_extra_schemas() -> None:
    files = {path.stem for path in SCHEMA_DIR.glob("*.json")} - {"envelope"}
    assert files == set(KNOWN_TYPES), f"schema 文件与消息清单不一致: {files ^ set(KNOWN_TYPES)}"


@pytest.mark.parametrize("msg_type", KNOWN_TYPES)
def test_sample_messages_validate(msg_type: str) -> None:
    """每个消息类型的样例帧必须通过对应 schema 校验。"""
    schema = json.loads((SCHEMA_DIR / f"{msg_type}.json").read_text(encoding="utf-8"))
    envelope = _envelope(msg_type, SAMPLES[msg_type]["payload"])

    jsonschema.validate(envelope, schema, registry=_registry())


def test_rejected_unknown_type_pattern() -> None:
    """type 不符合 <层>.<名> 规范的信封必须校验失败。"""
    schema = json.loads((SCHEMA_DIR / "envelope.json").read_text(encoding="utf-8"))
    bad = _envelope("no_dot_type", {})

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
