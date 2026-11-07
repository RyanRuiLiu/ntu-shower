#!/usr/bin/env bash
# 全量备份：产出一个自包含压缩包，换机时 bootstrap.sh 直接吃它。
#
# 包内结构：
#   srv/          整个 /srv（其中 ntu-shower.db 换成 VACUUM INTO 的一致性副本）
#   caddy_data/   Caddy 命名卷内容（ACME 账户与通配符证书）
#   metadata.txt  时间戳、来源、校验值
#
# SQLite 在 WAL 下不能直接 cp 库文件，会拿到写入中途的页；VACUUM INTO 产出
# 一致性副本。备份必须出本机——与库同一块盘的备份，换机时一起丢。

set -euo pipefail
out="${1:-}"
[ -n "$out" ] || { echo "用法：backup.sh <输出目录>" >&2; exit 1; }
mkdir -p "$out"
stamp="$(date +%Y%m%d-%H%M%S)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

db=/srv/ntu-shower/ntu-shower.db
[ -f "$db" ] || { echo "库不存在：$db" >&2; exit 1; }

log() { printf '[backup] %s\n' "$*" >&2; }

log "复制 /srv"
mkdir -p "$tmp/srv"
cp -a /srv/. "$tmp/srv/"

log "SQLite 在线备份"
python3 - "$db" "$tmp/srv/ntu-shower/ntu-shower.db.vacuum" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(src)
conn.execute("VACUUM INTO ?", (dst,))
conn.close()
check = sqlite3.connect(dst)
assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
check.close()
PY
mv "$tmp/srv/ntu-shower/ntu-shower.db.vacuum" "$tmp/srv/ntu-shower/ntu-shower.db"
rm -f "$tmp/srv/ntu-shower/ntu-shower.db-wal" "$tmp/srv/ntu-shower/ntu-shower.db-shm"

log "导出 Caddy 证书卷"
mkdir -p "$tmp/caddy_data"
docker run --rm -v caddy_data:/data:ro -v "$tmp/caddy_data":/out alpine \
    sh -c 'cp -a /data/. /out/' 2>/dev/null || log "caddy_data 卷不存在，跳过"

cat > "$tmp/metadata.txt" <<EOF
created=$stamp
source=$(hostname)
sqlite_vacuum_into=true
EOF

bundle="$out/stack-backup-$stamp.tar.gz"
tar -czf "$bundle" -C "$tmp" srv caddy_data metadata.txt
sha256sum "$bundle" | cut -d' ' -f1 > "$bundle.sha256"

log "完成：$bundle"
ls -lh "$bundle" "$bundle.sha256" >&2
