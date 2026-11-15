"""波次 2 一次性搬迁：usage 两表更名 door_sessions/door_segments，列名对齐。

用途与生命周期见 CONTRIBUTING 的重写流程：跑一次、验证、删除。只依赖标准库：

    python tools/migrate_wave2_door_domain.py /path/to/ntu-shower.db

动作（全程 foreign_keys=ON：SQLite 的 RENAME TABLE 会自动改写其他表指向本表的
外键引用，job_runs.session_id 因此同步到 door_sessions）：
1. usage_sessions → door_sessions、usage_segments → door_segments；
2. door_sessions.flow_s → total_flow_s；
3. door_states 补 created_at 并回填 reported_at 首值。
幂等性不提供——跑两次第二次会在 RENAME 处报表不存在，这正是想要的防呆。
"""

from __future__ import annotations

import sqlite3
import sys


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    conn = sqlite3.connect(sys.argv[1])
    existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for required in ("usage_sessions", "usage_segments", "door_states"):
        if required not in existing:
            raise SystemExit(f"{required}: 库里没有这张表，清单与库不一致")
    conn.execute("ALTER TABLE usage_sessions RENAME TO door_sessions")
    conn.execute("ALTER TABLE usage_segments RENAME TO door_segments")
    conn.execute("ALTER TABLE door_sessions RENAME COLUMN flow_s TO total_flow_s")
    conn.execute("ALTER TABLE door_states ADD COLUMN created_at DATETIME")
    conn.execute("UPDATE door_states SET created_at = reported_at")
    conn.commit()
    conn.close()
    print("wave2 迁移完成：door_sessions/door_segments 更名、total_flow_s、created_at 回填")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
