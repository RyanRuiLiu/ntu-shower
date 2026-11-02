#!/usr/bin/env bash
# ntu-shower 数据备份：SQLite 在线备份 + 滚动保留。
#
# 为什么用 VACUUM INTO：它是 SQLite 官方推荐的在线备份方式，在服务运行中
# 产生一致性备份（直接 cp 会拿到写入中途的库）。
#
# keystore 不在此脚本内：它是静态文件且是"全系统唯一长期绑定物件"，
# 采用人工异地副本 + 指纹校验（见 docs/RUNBOOK.md §3），不做自动滚动。
set -euo pipefail
umask 077

BACKUP_DIR="${BACKUP_DIR:-/opt/ntu-shower-backup}"
SERVER_DIR="${SERVER_DIR:-/opt/ntu-shower/server}"
DB_PATH="/opt/ntu-shower/data/ntu-shower.db"
PYTHON="$SERVER_DIR/.venv/bin/python"
KEEP="${KEEP:-30}"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
target="$BACKUP_DIR/ntu-shower-$stamp.db"

# 磁盘水位：备份与库本身都在这块盘上，写满的那天没有任何功能会替我们说话。
# 阈值留给人工处置（清理或扩容），脚本只负责把话说出来——它每天跑一次，
# journalctl 里看得见。
usage="$(df --output=pcent "$SERVER_DIR" | tail -1 | tr -dc '0-9')"
if [ -n "$usage" ] && [ "$usage" -ge 85 ]; then
    echo "WARNING: 磁盘使用率已达 ${usage}%（阈值 85%），请尽快清理或扩容" >&2
fi

"$PYTHON" - "$DB_PATH" "$target" <<'PY'
import sqlite3
import sys

source, target = sys.argv[1], sys.argv[2]
connection = sqlite3.connect(source)
connection.execute("VACUUM INTO ?", (target,))
connection.close()

# 校验备份完整：备份的意义在于恢复，恢复不了的备份只是占地方的文件。
# 校验花在刚产出的备份上（几十 MB 秒级），换来的是"每份备份都被证明可恢复"。
check = sqlite3.connect(target)
try:
    result = check.execute("PRAGMA integrity_check").fetchone()[0]
finally:
    check.close()
if result != "ok":
    raise SystemExit(f"备份完整性校验未通过：{result}")
PY
chmod 600 "$target"

# 滚动保留：只留最近 KEEP 份
find "$BACKUP_DIR" -maxdepth 1 -name 'ntu-shower-*.db' -printf '%T@ %p\n' \
    | sort -rn \
    | awk -v keep="$KEEP" 'NR > keep { sub(/^[^ ]+ /, ""); print }' \
    | xargs -r rm --

echo "backup ok: $target ($(du -h "$target" | cut -f1))"
