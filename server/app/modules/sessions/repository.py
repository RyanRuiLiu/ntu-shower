"""命令审计表：每次 session.command 的下发与结果留痕。"""

from app.core.clock import now_rfc3339
from app.core.db import get_conn
from app.core.protocol import JsonObject


def init_tables() -> None:
    get_conn().executescript(
        """
        CREATE TABLE IF NOT EXISTS command_audit (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            command_id       TEXT    NOT NULL,
            device_id        TEXT    NOT NULL,
            action           TEXT    NOT NULL,
            created_at       TEXT    NOT NULL,
            finished_at      TEXT,
            status           TEXT,
            reason_code      TEXT,
            terminal_message TEXT,
            trace_id         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_command_audit_device
            ON command_audit(device_id, created_at);
        """
    )


def insert_command(command_id: str, device_id: str, action: str, trace_id: str = "") -> None:
    get_conn().execute(
        "INSERT INTO command_audit (command_id, device_id, action, created_at, trace_id) VALUES (?,?,?,?,?)",
        (command_id, device_id, action, now_rfc3339(), trace_id or None),
    )
    get_conn().commit()


def finish(command_id: str, status: str, reason_code: str, terminal_message: str) -> None:
    get_conn().execute(
        "UPDATE command_audit SET finished_at = ?, status = ?, reason_code = ?, terminal_message = ?"
        " WHERE command_id = ?",
        (now_rfc3339(), status, reason_code, terminal_message, command_id),
    )
    get_conn().commit()


def list_commands(device_id: str | None = None, limit: int = 50) -> list[JsonObject]:
    """按时间倒序取命令审计记录（调试与问题追踪）。"""
    sql = (
        "SELECT command_id, device_id, action, created_at, finished_at, status,"
        " reason_code, terminal_message, trace_id FROM command_audit"
    )
    params: list[object] = []
    if device_id:
        sql += " WHERE device_id = ?"
        params.append(device_id)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    rows = get_conn().execute(sql, params).fetchall()
    return [dict(r) for r in rows]
