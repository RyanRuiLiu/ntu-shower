"""旧库 → 新库转换：凭据签发这一段。

转换工具其余 21 张表的核对靠手工在生产副本上跑 `--verify`；这里锁住
「切换时给现有设备补凭据」这一段，因为它一旦漏掉，设备全部连不上网关。
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from tools.convert_v1_to_v2 import issue_device_tokens

from app.db.base import Base
from app.db.engine import create_db_engine


def test_issue_writes_only_digests_and_plaintext_goes_to_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        db_path = root / "target.db"
        token_file = root / "tokens.txt"
        engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
        Base.metadata.create_all(engine)
        engine.dispose()

        target = sqlite3.connect(db_path)
        try:
            devices = {"dev-a": 1, "dev-b": 2}
            issued = issue_device_tokens(target, devices, token_file)
            target.commit()

            rows = target.execute("SELECT device_id, token_hash FROM device_credentials").fetchall()
            assert len(rows) == 2
            assert all(len(str(digest)) == 64 for _, digest in rows)
            assert token_file.exists()
            # chmod 在 POSIX 上收紧到 600；Windows 没有同一套权限位，不在此断言
            if os.name == "posix":
                assert token_file.stat().st_mode & 0o777 == 0o600
            content = token_file.read_text(encoding="utf-8")
            assert "dev-a" in content
            assert "dev-b" in content
            assert len(issued) == 2
            for device_id, token in issued:
                assert token not in str(rows)
                assert device_id in content
        finally:
            target.close()
