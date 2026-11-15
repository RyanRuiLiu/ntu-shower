"""波次 4 一次性迁移：app_settings 的时区键改名（跑一次验证后即删）。

`extend_hours_zone_offset_minutes` → `server_zone_offset_minutes`：该键实际是
全服务端共用的表盘换算轴（延长服务与自动校时共用），组归属同步迁到 server_clock。
设备域键不下发、不受影响；Device.config_override 里不会有它（写侧一直拒服务端键）。

用法：对生产库的**已停服副本**执行
    python tools/migrate_wave4.py <db_path>
单事务 + 防呆（键不存在即视为已迁移，原样退出）。
"""

from __future__ import annotations

import json
import sqlite3
import sys

OLD_KEY = "extend_hours_zone_offset_minutes"
NEW_KEY = "server_zone_offset_minutes"


def migrate(path: str) -> None:
    con = sqlite3.connect(path)
    con.isolation_level = None
    con.execute("BEGIN")
    try:
        row = con.execute("SELECT revision, config FROM app_settings WHERE id = 1").fetchone()
        if row is None:
            print("app_settings 无行（全新安装）：无需迁移，提交空事务。")
            con.execute("COMMIT")
            return
        revision, raw = int(row[0]), row[1]
        config = json.loads(raw)
        if OLD_KEY not in config:
            print("旧键不存在（已迁移过或从未覆盖）：无需迁移，提交空事务。")
            con.execute("COMMIT")
            return
        if NEW_KEY in config:
            print(f"中止：新旧键同时存在（{NEW_KEY}），需人工确认优先级。", file=sys.stderr)
            sys.exit(1)

        config[NEW_KEY] = config.pop(OLD_KEY)
        con.execute(
            "UPDATE app_settings SET revision = ?, config = ? WHERE id = 1",
            (revision + 1, json.dumps(config, ensure_ascii=False)),
        )
        # 对账
        check = json.loads(con.execute("SELECT config FROM app_settings WHERE id = 1").fetchone()[0])
        assert NEW_KEY in check and OLD_KEY not in check, "对账失败"
        con.execute("COMMIT")
        print(f"迁移完成：{OLD_KEY} → {NEW_KEY}（修订号 {revision} → {revision + 1}），事务已提交。")
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法：python tools/migrate_wave4.py <db_path>", file=sys.stderr)
        sys.exit(2)
    migrate(sys.argv[1])
