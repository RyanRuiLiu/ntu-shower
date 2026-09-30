#!/usr/bin/env bash
# ntu-shower 数据备份：SQLite 在线备份 + 滚动保留。
#
# 为什么用 VACUUM INTO：它是 SQLite 官方推荐的在线备份方式，在服务运行中
# 产生一致性快照（直接 cp 会拿到写入中途的库）。
#
# keystore 不在此脚本内：它是静态文件且是"全系统唯一长期绑定物件"，
# 采用人工异地副本 + 指纹校验（见 docs/RUNBOOK.md §3），不做自动滚动。
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/opt/ntu-shower-backup}"
SERVER_DIR="${SERVER_DIR:-/opt/ntu-shower/server}"
DB_PATH="$SERVER_DIR/data/ntu-shower.db"
PYTHON="$SERVER_DIR/.venv/bin/python"
KEEP="${KEEP:-30}"

mkdir -p "$BACKUP_DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
target="$BACKUP_DIR/ntu-shower-$stamp.db"

"$PYTHON" - "$DB_PATH" "$target" <<'PY'
import sqlite3
import sys

source, target = sys.argv[1], sys.argv[2]
connection = sqlite3.connect(source)
connection.execute("VACUUM INTO ?", (target,))
connection.close()
PY

# 滚动保留：只留最近 KEEP 份
find "$BACKUP_DIR" -maxdepth 1 -name 'ntu-shower-*.db' -printf '%T@ %p\n' \
    | sort -rn \
    | awk -v keep="$KEEP" 'NR > keep { sub(/^[^ ]+ /, ""); print }' \
    | xargs -r rm --

echo "backup ok: $target ($(du -h "$target" | cut -f1))"
