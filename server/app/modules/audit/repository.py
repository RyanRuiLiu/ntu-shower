from app.core.db import get_conn


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
