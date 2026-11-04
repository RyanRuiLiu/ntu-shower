#!/usr/bin/env bash
# ntu-shower 数据备份：SQLite 在线备份 + 加密归档 + 滚动保留。
#
# 为什么用 VACUUM INTO：它是 SQLite 官方推荐的在线备份方式，在服务运行中
# 产生一致性备份（直接 cp 会拿到写入中途的库）。
#
# keystore 不在此脚本内：它是静态文件且是"全系统唯一长期绑定物件"，
# 采用人工异地副本 + 指纹校验（见 docs/RUNBOOK.md），不做自动滚动。
#
# 原子写约定：VACUUM INTO 先落 .part，完整性校验通过才 rename；
# gzip 后再验压缩流与解压字节数。避免失败时留下"看起来有效"的残件。
#
# 加密（STANDARDS.md §12.4.2：备份必须加密；密钥与备份分开保管）：
#   gzip 流经 age 以收件人公钥加密，产出 .db.gz.age。私钥**不在服务器上**——
#   公钥放在 /etc/ntu-shower/backup-age.pub，私钥由密钥保管方异地持有；
#   备份文件泄露而服务器未失陷时，内容不可读。密钥对的生成与私钥托管见
#   docs/RUNBOOK.md「备份与恢复」。
set -euo pipefail
umask 077

BACKUP_DIR="${BACKUP_DIR:-/var/backups/ntu-shower}"
SERVER_DIR="${SERVER_DIR:-/opt/ntu-shower/server}"
DB_PATH="/var/lib/ntu-shower/ntu-shower.db"
PYTHON="$SERVER_DIR/.venv/bin/python"
KEEP="${KEEP:-14}"
AGE_PUB="${AGE_PUB:-/etc/ntu-shower/backup-age.pub}"

# 前置条件：age 与收件人公钥缺任一项即停——静默降级成明文备份等于绕过加密要求，
# 失败必须可见（STANDARDS.md §7.8.3）。
command -v age >/dev/null || { echo "backup FAILED: 未安装 age，无法加密备份" >&2; exit 1; }
[ -r "$AGE_PUB" ] || { echo "backup FAILED: 收件人公钥不可读：$AGE_PUB" >&2; exit 1; }

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
target="$BACKUP_DIR/ntu-shower-$stamp.db"
tmpfile="$target.part"

# 磁盘水位：备份与库本身都在这块盘上，写满的那天没有任何功能会替我们说话。
# 阈值留给人工处置（清理或扩容），脚本只负责把话说出来——它每天跑一次，
# journalctl 里看得见。
usage="$(df --output=pcent "$SERVER_DIR" | tail -1 | tr -dc '0-9')"
if [ -n "$usage" ] && [ "$usage" -ge 85 ]; then
    echo "WARNING: 磁盘使用率已达 ${usage}%（阈值 85%），请尽快清理或扩容" >&2
fi

"$PYTHON" - "$DB_PATH" "$tmpfile" <<'PY'
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
chmod 600 "$tmpfile"
mv "$tmpfile" "$target"

# 压缩归档：SQLite 是高度重复的页结构，gzip 通常省 2/3 体积；mtime 随压缩保留
gzip -f "$target"
target="$target.gz"

if ! gzip -t "$target"; then
    rm -f "$target"
    echo "backup FAILED: gzip 流校验失败" >&2
    exit 1
fi
rawsize="$(gzip -dc "$target" | wc -c)"
if [ "$rawsize" -lt 4096 ]; then
    rm -f "$target"
    echo "backup FAILED: 解压后仅 ${rawsize} 字节" >&2
    exit 1
fi

# 加密归档：先落 .part 再 rename，失败不留"看起来有效"的半成品；
# 原始 .gz 在加密成功后删除——盘上只留密文，明文不落地。
encrypted="$BACKUP_DIR/ntu-shower-$stamp.db.gz.age"
age -R "$AGE_PUB" -o "$encrypted.part" "$target"
chmod 600 "$encrypted.part"
mv "$encrypted.part" "$encrypted"
rm -f "$target"
target="$encrypted"

# 校验和（STANDARDS.md §12.4.3）：密文与其 SHA-256 并排登记，恢复前先验。
sha256sum "$target" > "$target.sha256"

# 滚动保留：只留最近 KEEP 份（.db / .db.gz / .db.gz.age 兼容三代历史格式，
# .sha256 边车随各自的密文一起淘汰）。
find "$BACKUP_DIR" -maxdepth 1 -name 'ntu-shower-*.db*' -printf '%T@ %p\n' \
    | sort -rn \
    | awk -v keep="$KEEP" 'NR > keep { sub(/^[^ ]+ /, ""); print }' \
    | while IFS= read -r old; do
        rm -f -- "$old" "$old.sha256"
    done

echo "backup ok: $target ($(du -h "$target" | cut -f1), 明文 ${rawsize} 字节)"
