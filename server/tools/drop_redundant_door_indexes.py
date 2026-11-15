"""波次 2.5 一次性搬迁：删除门域三张表的冗余单列设备外键索引。

用途与生命周期见 CONTRIBUTING 的重写流程：跑一次、验证、删除。只依赖标准库：

    python tools/drop_redundant_door_indexes.py /path/to/ntu-shower.db

冗余依据（PRAGMA 实测）：三个复合/唯一索引的首列都是 device_id，单列索引被
完全覆盖——每个多余的 B 树都在每次帧插入时白付一次维护成本。列表与库不一致
时在缺表/缺索引处报错停止。
"""

from __future__ import annotations

import sqlite3
import sys

#: (表, 待删冗余索引名)。索引名由 db/base.py 的 naming_convention 铸造。
REDUNDANT = (
    ("door_states", "ix_door_states_device_id"),
    ("door_snapshots", "ix_door_snapshots_device_id"),
    ("door_sessions", "ix_door_sessions_device_id"),
)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    conn = sqlite3.connect(sys.argv[1])
    for table, index in REDUNDANT:
        indexes = {r[1] for r in conn.execute(f"PRAGMA index_list({table})")}
        if index not in indexes:
            detail = f"{table}.{index}: 库里没有这个索引，清单与库不一致"
            raise SystemExit(detail)
        conn.execute(f"DROP INDEX {index}")
    conn.commit()
    conn.close()
    print("wave2.5 迁移完成：三个冗余单列设备索引已删除")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
