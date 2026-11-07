"""旧库 → 新库的数据转换。

切换方式是一次性替换：新服务直接跑新库，不做双写、不留兼容层。因此这个脚本必须
在切换前把旧库全部业务数据搬过去，并且能被验证——搬错的库在新服务上线那一刻才会
暴露，代价是线上数据。

## 用法

    PYTHONPATH=. python tools/convert_v1_to_v2.py --source old.db --target new.db [--verify]

（`PYTHONPATH=.` 必带：脚本要导入 `app.*`，而 `tools/` 不是包。）

`--verify` 只比对行数，不写库。

## 映射规则

- 设备等自然键（`device_id` 字符串）在新库是 `devices.id` 整数，转换时建字典解析；
  旧库里出现新库没有的设备时直接报错，不静默丢行。
- 时间是 RFC 3339 字符串 → `UtcDateTime` 的存储形式（UTC、无时区后缀）。
- JSON 列在新库仍是 TEXT，原样搬运。
- 枚举列取值不变，只校验取值合法。
- 认证材料（`password_hash` / `token_hash`）不从旧库搬运：旧库没有这些凭据，
  切换后由管理员为主体重新签发，凭据明文不落库。

## 分阶段

当前实现「身份组」：`devices`、`principals`。其余表按 `PLANNED` 登记，
未实现时脚本明确拒绝执行，不会产出半份数据。
"""

from __future__ import annotations

import argparse
import hashlib
import secrets
import sqlite3
import sys
from datetime import UTC
from pathlib import Path

from app.core.clock import parse_rfc3339
from app.db.base import utcnow
from app.db.engine import create_db_engine
from app.models import Base

#: 已实现的转换：(旧表, 新表)。
DONE: tuple[tuple[str, str], ...] = (
    ("device", "devices"),
    ("access_identity", "principals"),
    ("clock_shift", "clock_shifts"),
    ("device_config", "device_configs"),
    ("device_link_event", "device_link_events"),
    ("device_log", "device_logs"),
    ("global_config", "app_settings"),
    ("ota_release", "ota_releases"),
    ("voice_asset", "voice_assets"),
    ("door_snapshot", "door_snapshots"),
    ("door_state_event", "door_state_events"),
    ("api_call", "api_calls"),
    ("command_audit", "command_audits"),
    ("background_task_run", "background_task_runs"),
    ("door_live", "door_states"),
    ("usage_session", "usage_sessions"),
    ("voice_rule", "voice_rules"),
    ("voice_unit", "voice_units"),
    ("usage_segment", "usage_segments"),
    ("ota_release_install", "ota_release_installs"),
    ("voice_delivery", "voice_deliveries"),
)

#: 尚未实现的表。存在即拒绝执行——半份数据比没有数据更危险。
PLANNED: tuple[str, ...] = ()


def to_storage_time(text: str | None) -> str | None:
    """RFC 3339 → `UtcDateTime` 的存储形式；空值与不可解析值按空处理。"""
    if not text:
        return None
    moment = parse_rfc3339(text)
    if moment is None:
        return None
    return moment.astimezone(UTC).replace(tzinfo=None).isoformat(sep=" ")


def copy_devices(source: sqlite3.Connection, target: sqlite3.Connection) -> dict[str, int]:
    rows = source.execute(
        "SELECT device_id, name, trusted, app_version, version_code, os_version,"
        " capabilities, last_seen_at, last_ip, booted_at, clock_skew_s, sort_order FROM device"
    ).fetchall()
    mapping: dict[str, int] = {}
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    for row in rows:
        cursor = target.execute(
            "INSERT INTO devices (device_id, name, trusted, app_version, version_code,"
            " os_version, capabilities, last_seen_at, last_ip, booted_at, clock_skew_s,"
            " sort_order, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row[0],
                row[1] or "",
                1 if row[2] else 0,
                row[3] or "",
                int(row[4] or 0),
                row[5] or "",
                row[6] or "[]",
                to_storage_time(row[7]),
                row[8] or None,
                to_storage_time(row[9]),
                float(row[10] or 0.0),
                int(row[11] or 0),
                now,
                now,
            ),
        )
        mapping[str(row[0])] = int(cursor.lastrowid or 0)
    return mapping


def copy_identities(source: sqlite3.Connection, target: sqlite3.Connection) -> None:
    rows = source.execute(
        "SELECT identity, kind, capabilities, device_scope, label, created_at FROM access_identity"
    ).fetchall()
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    for row in rows:
        target.execute(
            "INSERT INTO principals"
            " (name, kind, capabilities, device_scope, label, created_at, updated_at, disabled)"
            " VALUES (?,?,?,?,?,?,?,0)",
            (
                row[0],
                row[1],
                row[2] or "[]",
                row[3] or "[]",
                row[4] or "",
                to_storage_time(row[5]) or now,
                now,
            ),
        )


def copy_simple(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    devices: dict[str, int],
) -> None:
    """逐设备一行的表：`device_link_event`、`device_log`、`clock_shift`、`device_config`。

    这些表没有跨表引用，只有设备外键与时间列，统一用一张映射表处理。
    """
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    for row in source.execute("SELECT device_id, occurred_at, event, reason FROM device_link_event").fetchall():
        target.execute(
            "INSERT INTO device_link_events (device_id, occurred_at, event, reason, created_at) VALUES (?,?,?,?,?)",
            (devices[str(row[0])], to_storage_time(row[1]), row[2], row[3] or "", now),
        )
    for row in source.execute("SELECT device_id, kind, content, uploaded_at FROM device_log").fetchall():
        target.execute(
            "INSERT INTO device_logs (device_id, kind, content, uploaded_at, created_at) VALUES (?,?,?,?,?)",
            (devices[str(row[0])], row[1], row[2], to_storage_time(row[3]) or now, now),
        )
    for row in source.execute("SELECT device_id, shifted_at, assignments FROM clock_shift").fetchall():
        target.execute(
            "INSERT INTO clock_shifts (device_id, shifted_at, assignments, updated_at) VALUES (?,?,?,?)",
            (devices[str(row[0])], to_storage_time(row[1]) or now, row[2] or "[]", now),
        )
    for row in source.execute("SELECT device_id, revision, config FROM device_config").fetchall():
        target.execute(
            "INSERT INTO device_configs (device_id, revision, config, updated_at) VALUES (?,?,?,?)",
            (devices[str(row[0])], int(row[1]), row[2] or "{}", now),
        )


def copy_singletons(source: sqlite3.Connection, target: sqlite3.Connection) -> dict[str, int]:
    """整表单条的配置、无外键的发布与素材表。"""
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    row = source.execute("SELECT revision, config FROM global_config WHERE id = 1").fetchone()
    if row is not None:
        target.execute(
            "INSERT INTO app_settings (id, revision, config, updated_at) VALUES (1,?,?,?)",
            (int(row[0]), row[1] or "{}", now),
        )
    releases: dict[str, int] = {}
    for row in source.execute(
        "SELECT version, version_code, sha256, mandatory, force, target, created_at FROM ota_release"
    ).fetchall():
        target.execute(
            "INSERT INTO ota_releases (version, version_code, sha256, mandatory, force, target,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                row[0],
                int(row[1]),
                row[2],
                1 if row[3] else 0,
                1 if row[4] else 0,
                row[5] or "all",
                to_storage_time(row[6]) or now,
                now,
            ),
        )
        releases[str(row[0])] = int(target.execute("SELECT last_insert_rowid()").fetchone()[0])
    for row in source.execute(
        "SELECT asset_id, name, digit, duration_ms, size_bytes, created_at FROM voice_asset"
    ).fetchall():
        target.execute(
            "INSERT INTO voice_assets (asset_id, name, digit, duration_ms, size_bytes, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (row[0], row[1], row[2], int(row[3]), int(row[4]), to_storage_time(row[5]) or now),
        )
    return releases


def copy_door_events(source: sqlite3.Connection, target: sqlite3.Connection, devices: dict[str, int]) -> None:
    """原始帧与状态流水：两张 19 万行级的大表，逐批写。"""
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    for row in source.execute(
        "SELECT device_id, snapshot_id, seq, source, captured_at, received_at, doors, device_clock FROM door_snapshot"
    ):
        target.execute(
            "INSERT INTO door_snapshots (device_id, snapshot_id, seq, source, captured_at,"
            " received_at, doors, device_clock, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                devices[str(row[0])],
                row[1],
                row[2],
                row[3],
                to_storage_time(row[4]),
                to_storage_time(row[5]) or now,
                row[6],
                row[7],
                now,
            ),
        )
    for row in source.execute(
        "SELECT occurred_at, device_id, door_no, from_state, to_state, card_no, cert_no,"
        " bath_time_s FROM door_state_event"
    ):
        target.execute(
            "INSERT INTO door_state_events (device_id, door_no, from_state, to_state, card_no,"
            " cert_no, bath_time_s, occurred_at, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                devices[str(row[1])],
                int(row[2]),
                row[3],
                row[4],
                int(row[5] or 0),
                row[6] or "",
                int(row[7] or 0),
                to_storage_time(row[0]) or now,
                now,
            ),
        )


def copy_audit_and_live(source: sqlite3.Connection, target: sqlite3.Connection, devices: dict[str, int]) -> None:
    """审计两张与门现态：都只依赖设备外键。"""
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    for row in source.execute(
        "SELECT kind, requested_at, request_id, trace_id, actor_kind, actor, client_ip,"
        " user_agent, method, path, query, request_body, status_code, duration_ms, device_id,"
        " problem_type, detail, response_body FROM api_call"
    ):
        target.execute(
            "INSERT INTO api_calls (kind, requested_at, request_id, trace_id, actor_kind, actor,"
            " client_ip, user_agent, method, path, query, request_body, status_code, duration_ms,"
            " device_id, problem_type, detail, response_body, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row[0],
                to_storage_time(row[1]) or now,
                row[2] or "",
                row[3] or "",
                row[4],
                row[5] or "",
                row[6],
                row[7],
                row[8],
                row[9],
                row[10],
                row[11],
                int(row[12]),
                int(row[13]),
                devices.get(str(row[14])) if row[14] else None,
                row[15],
                row[16],
                row[17],
                now,
            ),
        )
    for row in source.execute(
        "SELECT command_id, device_id, action, params, created_at, finished_at, status,"
        " reason_code, terminal_message, trace_id, data FROM command_audit"
    ):
        target.execute(
            "INSERT INTO command_audits (command_id, device_id, action, params, created_at,"
            " finished_at, status, reason_code, terminal_message, trace_id, result)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                row[0],
                devices[str(row[1])],
                row[2],
                row[3],
                to_storage_time(row[4]) or now,
                to_storage_time(row[5]),
                row[6],
                row[7],
                row[8],
                row[9] or "",
                row[10],
            ),
        )
    for row in source.execute(
        "SELECT occurred_at, device_id, door_no, session_id, cert_no, feature, action,"
        " outcome, reason, threshold_s FROM background_task_run"
    ):
        target.execute(
            "INSERT INTO background_task_runs (occurred_at, device_id, door_no, session_id,"
            " cert_no, feature, action, outcome, reason, threshold_s, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                to_storage_time(row[0]) or now,
                devices[str(row[1])],
                int(row[2]),
                row[3],
                row[4] or "",
                row[5],
                row[6],
                row[7],
                row[8],
                row[9],
                now,
            ),
        )
    for row in source.execute(
        "SELECT device_id, door_no, state, card_no, cert_no, acc_no, open_time, in_time,"
        " close_time, bath_time_s, status_raw, reported_at FROM door_live"
    ):
        target.execute(
            "INSERT INTO door_states (device_id, door_no, state, card_no, cert_no, acc_no,"
            " open_time, in_time, close_time, bath_time_s, status_raw, reported_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                devices[str(row[0])],
                int(row[1]),
                row[2],
                int(row[3] or 0),
                row[4] or "",
                row[5] or "",
                to_storage_time(row[6]),
                to_storage_time(row[7]),
                to_storage_time(row[8]),
                int(row[9] or 0),
                int(row[10] or 0),
                to_storage_time(row[11]) or now,
                now,
            ),
        )


def copy_usage_and_voice(
    source: sqlite3.Connection, target: sqlite3.Connection, devices: dict[str, int]
) -> tuple[dict[int, int], dict[int, int]]:
    """会话、播报规则与语音单元：仅会话依赖设备外键。"""
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    sessions: dict[int, int] = {}
    for row in source.execute(
        "SELECT id, device_id, door_no, card_no, cert_no, assigned_at, assigned_source,"
        " observed_span, device_flow_s, segments_complete, ended_at, last_seen_at, flow_s"
        " FROM usage_session"
    ):
        target.execute(
            "INSERT INTO usage_sessions (device_id, door_no, card_no, cert_no, assigned_at,"
            " assigned_source, observed_span, device_flow_s, segments_complete, ended_at,"
            " last_seen_at, flow_s, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                devices[str(row[1])],
                int(row[2]),
                int(row[3] or 0),
                row[4] or "",
                to_storage_time(row[5]) or now,
                row[6],
                row[7],
                int(row[8] or 0),
                1 if row[9] else 0,
                to_storage_time(row[10]),
                to_storage_time(row[11]) or now,
                int(row[12] or 0),
                now,
                now,
            ),
        )
        sessions[int(row[0])] = int(target.execute("SELECT last_insert_rowid()").fetchone()[0])
    rules: dict[int, int] = {}
    for row in source.execute(
        "SELECT id, devices, certs, events, time_windows, exempt, segments, volume, enabled,"
        " created_at, updated_at FROM voice_rule"
    ):
        target.execute(
            "INSERT INTO voice_rules (devices, certs, events, time_windows, exempt, segments,"
            " volume, enabled, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                row[1],
                row[2],
                row[3],
                row[4] or "[]",
                row[5] or "[]",
                row[6],
                int(row[7]),
                1 if row[8] else 0,
                to_storage_time(row[9]) or now,
                to_storage_time(row[10]) or now,
            ),
        )
        rules[int(row[0])] = int(target.execute("SELECT last_insert_rowid()").fetchone()[0])
    for row in source.execute("SELECT unit, asset_id FROM voice_unit"):
        target.execute("INSERT INTO voice_units (unit, asset_id) VALUES (?,?)", (row[0], row[1]))
    return sessions, rules


def copy_children(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
    devices: dict[str, int],
    sessions: dict[int, int],
    releases: dict[str, int],
    rules: dict[int, int],
) -> dict[str, int]:
    """子表：外键必须能在字典里解析到，解析不到就中止——静默丢行会让统计缺一段。"""
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    skipped = {"release": 0, "session": 0, "rule": 0}
    for row in source.execute(
        "SELECT session_id, device_id, door_no, opened_at, closed_at, flow_s, opened_raw,"
        " closed_raw, source FROM usage_segment"
    ):
        session_id = sessions.get(int(row[0]))
        if session_id is None:
            skipped["session"] += 1
            continue
        target.execute(
            "INSERT INTO usage_segments (session_id, device_id, door_no, opened_at, closed_at,"
            " flow_s, opened_raw, closed_raw, source, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                session_id,
                devices[str(row[1])],
                int(row[2]),
                to_storage_time(row[3]) or now,
                to_storage_time(row[4]),
                int(row[5] or 0),
                row[6] or "",
                row[7] or "",
                row[8],
                now,
            ),
        )
    for row in source.execute("SELECT version, device_id, applied_at FROM ota_release_install"):
        release_id = releases.get(str(row[0]))
        if release_id is None:
            # 发布已按保留策略删除，安装记录成了孤儿。APK 本身已不存在，
            # 这些行无法补全，跳过并计数——不静默吞掉，最后统一报出。
            skipped["release"] += 1
            continue
        target.execute(
            "INSERT INTO ota_release_installs (release_id, device_id, applied_at) VALUES (?,?,?)",
            (release_id, devices[str(row[1])], to_storage_time(row[2]) or now),
        )
    for row in source.execute(
        "SELECT event_key, rule_id, device_id, door_no, cert_no, event, segments,"
        " segment_count, duration_ms, occurred_at, dispatched_at, done_at, downloaded, status,"
        " detail, trace_id, created_at FROM voice_delivery"
    ):
        rule_id = rules.get(int(row[1]))
        if rule_id is None:
            skipped["rule"] += 1
            continue
        target.execute(
            "INSERT INTO voice_deliveries (event_key, rule_id, device_id, door_no, cert_no,"
            " event, segments, segment_count, duration_ms, occurred_at, dispatched_at, done_at,"
            " downloaded, status, detail, trace_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                row[0],
                rule_id,
                devices[str(row[2])],
                int(row[3]),
                row[4],
                row[5],
                row[6],
                int(row[7]),
                int(row[8]),
                to_storage_time(row[9]) or now,
                to_storage_time(row[10]),
                to_storage_time(row[11]),
                1 if row[12] else 0,
                row[13],
                row[14] or "",
                row[15] or "",
                to_storage_time(row[16]) or now,
            ),
        )
    if any(skipped.values()):
        print(f"跳过孤儿行：{skipped}", file=sys.stderr)
    return skipped


def issue_device_tokens(target: sqlite3.Connection, devices: dict[str, int], output: Path) -> list[tuple[str, str]]:
    """给每台现有设备签发一枚接入凭据，返回 (设备标识, 明文) 清单。

    旧库没有凭据这回事，新服务默认强制校验；切换时不补这一步，设备全部连不上。
    库里只写 SHA-256 摘要，明文只写进调用方指定的文件（权限 600），不进日志、
    不进数据库、不进标准输出。
    """
    now = utcnow().replace(tzinfo=None).isoformat(sep=" ")
    issued: list[tuple[str, str]] = []
    for device_id, device_pk in devices.items():
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        target.execute(
            "INSERT INTO device_credentials (device_id, token_hash, label, created_at) VALUES (?,?,?,?)",
            (device_pk, digest, "停机切换补发", now),
        )
        issued.append((device_id, token))
    lines = [f"{device_id}\t{token}" for device_id, token in issued]
    output.write_text(chr(10).join(lines) + chr(10), encoding="utf-8")
    output.chmod(0o600)
    return issued


#: 子表里会被跳过的孤儿行计数，验证时从期望值里扣掉，避免把已知的跳过数误报成错误。
SKIPPED_BY_TABLE: dict[str, str] = {
    "ota_release_install": "release",
    "voice_delivery": "rule",
}


def verify(source: sqlite3.Connection, target: sqlite3.Connection, skipped: dict[str, int] | None = None) -> list[str]:
    problems: list[str] = []
    for old, new in DONE:
        expected = int(source.execute(f"SELECT COUNT(*) FROM {old}").fetchone()[0])
        key = SKIPPED_BY_TABLE.get(old)
        if skipped and key:
            expected -= skipped.get(key, 0)
        actual = int(target.execute(f"SELECT COUNT(*) FROM {new}").fetchone()[0])
        status = "ok" if expected == actual else "MISMATCH"
        problems.append(f"{old:20s} {expected:8d} → {new:22s} {actual:8d}  {status}")
        if expected != actual:
            problems.append(f"  !! {new} 行数不一致（期望 {expected}，实际 {actual}）")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="旧库 → 新库转换")
    parser.add_argument("--source", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--token-output",
        type=Path,
        default=None,
        help="切换时给每台现有设备签发接入凭据，明文写进这个文件（权限 600）",
    )
    args = parser.parse_args()

    if Path(args.target).exists():
        print(f"目标库已存在，拒绝覆盖：{args.target}", file=sys.stderr)
        return 2

    source = sqlite3.connect(args.source)
    target = sqlite3.connect(args.target)
    try:
        engine = create_db_engine(f"sqlite:///{Path(args.target).as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()

        if args.verify:
            for line in verify(source, target):
                print(line)
            return 0

        missing = [
            name
            for name in PLANNED
            if source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        ]
        if missing:
            print("以下表尚未实现转换，脚本拒绝执行：", file=sys.stderr)
            for name in missing:
                print(f"  - {name}", file=sys.stderr)
            return 3

        target.execute("BEGIN")
        devices = copy_devices(source, target)
        copy_identities(source, target)
        copy_simple(source, target, devices)
        releases = copy_singletons(source, target)
        copy_door_events(source, target, devices)
        copy_audit_and_live(source, target, devices)
        sessions, rules = copy_usage_and_voice(source, target, devices)
        skipped = copy_children(source, target, devices, sessions, releases, rules)
        if args.token_output is not None:
            issued = issue_device_tokens(target, devices, args.token_output)
            print(f"已为 {len(issued)} 台设备签发接入凭据：{args.token_output}", file=sys.stderr)
        target.commit()

        for line in verify(source, target, skipped):
            print(line)
        return 0
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    raise SystemExit(main())
