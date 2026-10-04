"""命令审计表与后台任务表：我们自己做过的事留痕。

## 哪些设备面事件进这张表（口径）

凡不在任何 HTTP 请求生命周期内的设备面事件，必须有自己的一行——桥的开关与
隧道会话在其生命周期没有 HTTP 请求在途，因此自行登记；配置推送、命令下发等
都在某个 HTTP 请求内完成，由 api_call 的请求/响应全文覆盖，不重复记行。判断
依据是"事件发生时有没有一行 HTTP 审计能兜住它"，与动作重要性无关。

## 为什么后台任务另有自己的一行

后台任务（预约续期 / 会话续期 / 自动结算）在**帧**上动手，没有任何 HTTP 请求在途，
按上面那条口径就该自己留一行。而它记的是**决定**——哪个功能、到点时的阈值是多少、
为什么动手或没动手——与 command_audit 记的"发了一条什么命令"不是同一件事：
`no-password` / `not-applicable` / `assign-waiting` / `assign-expired` 那几种根本没有
命令。要对照两边，按 `occurred_at` 与 `command_audit.created_at` 排着看。

**只记终局**：每帧、每个候选都要判一次"到点没有"，那个过程不落库（否则量级等于帧数）。

**永久保留**：它是运营历史（谁被自动续期过），效果本身留在 usage_session 里，但
"为什么、第几次"只有这里说得出来。因此不进 `core.audit.prune` 的清理名单——那张表
只清 HTTP 与命令两条审计流水。
"""

from typing import cast

from app.core.clock import now_rfc3339
from app.core.db import get_conn, write

from .schemas import BackgroundTaskRun, CommandAuditRecord


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS command_audit (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            command_id       TEXT    NOT NULL,
            device_id        TEXT    NOT NULL,
            action           TEXT    NOT NULL,
            params           TEXT,
            created_at       TEXT    NOT NULL,
            finished_at      TEXT,
            status           TEXT,
            reason_code      TEXT,
            terminal_message TEXT,
            trace_id         TEXT,
            data             TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_command_audit_device
            ON command_audit(device_id, created_at);
        CREATE TABLE IF NOT EXISTS background_task_run (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at  TEXT    NOT NULL,
            device_id    TEXT    NOT NULL,
            door_no      INTEGER NOT NULL,
            session_id   INTEGER,
            cert_no      TEXT    NOT NULL,
            feature      TEXT    NOT NULL,
            action       TEXT,
            outcome      TEXT    NOT NULL,
            reason       TEXT,
            threshold_s  INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_background_task_run_device
            ON background_task_run(device_id, occurred_at);
        """
    )


def insert_background_task_run(
    device_id: str,
    door_no: int,
    cert_no: str,
    feature: str,
    outcome: str,
    *,
    action: str | None = None,
    session_id: int | None = None,
    reason: str = "",
    threshold_s: int | None = None,
) -> None:
    """一次终局动作。`outcome` 用的是日志事件名的后缀，两边同一套说法。"""
    write(
        "INSERT INTO background_task_run"
        " (occurred_at, device_id, door_no, session_id, cert_no, feature, action, outcome, reason, threshold_s)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            now_rfc3339(),
            device_id,
            door_no,
            session_id,
            cert_no,
            feature,
            action,
            outcome,
            reason or None,
            threshold_s,
        ),
    )


def insert_command(
    command_id: str,
    device_id: str,
    action: str,
    trace_id: str = "",
    params: str | None = None,
) -> None:
    write(
        "INSERT INTO command_audit (command_id, device_id, action, created_at, trace_id, params) VALUES (?,?,?,?,?,?)",
        (command_id, device_id, action, now_rfc3339(), trace_id or None, params),
    )


def finish(command_id: str, status: str, reason_code: str, terminal_message: str, data: str | None = None) -> None:
    write(
        "UPDATE command_audit SET finished_at = ?, status = ?, reason_code = ?, terminal_message = ?, data = ?"
        " WHERE command_id = ?",
        (now_rfc3339(), status, reason_code, terminal_message, data, command_id),
    )


def list_commands(device_id: str | None = None, limit: int = 50) -> list[CommandAuditRecord]:
    """按时间倒序取命令审计记录（调试与问题追踪）。"""
    sql = (
        "SELECT command_id, device_id, action, params, created_at, finished_at, status,"
        " reason_code, terminal_message, trace_id, data FROM command_audit"
    )
    params: list[object] = []
    if device_id:
        sql += " WHERE device_id = ?"
        params.append(device_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = get_conn().execute(sql, params).fetchall()
    return cast("list[CommandAuditRecord]", [dict(r) for r in rows])


def list_background_task_runs(device_id: str | None = None, limit: int = 50) -> list[BackgroundTaskRun]:
    """按时间倒序取后台任务记录（自动做了什么、为什么）。"""
    sql = (
        "SELECT id, occurred_at, device_id, door_no, session_id, cert_no, feature, action,"
        " outcome, reason, threshold_s FROM background_task_run"
    )
    params: list[object] = []
    if device_id:
        sql += " WHERE device_id = ?"
        params.append(device_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = get_conn().execute(sql, params).fetchall()
    return cast("list[BackgroundTaskRun]", [dict(r) for r in rows])
