#!/usr/bin/env bash
# 新机初始化：把一台空白 Ubuntu 变成能跑这套栈的机器。幂等，可重复执行。
#
# 用法：sudo ./bootstrap.sh [备份包路径]
# 有备份包就先恢复 /srv，再拉镜像启动；没有就走空的迁移建表。

set -euo pipefail

STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_ROOT=/srv
APP_UID=997
APP_GID=997

log() { printf '[bootstrap] %s\n' "$*" >&2; }
die() { printf '[bootstrap] 失败：%s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "需要 root"

# 1. Docker Engine + compose 插件。装完把当前用户加进 docker 组，
#    但本次脚本仍以 root 执行，不依赖重新登录。
if ! command -v docker >/dev/null 2>&1; then
    log "安装 Docker Engine"
    apt-get update
    apt-get install -y --no-install-recommends docker.io
fi
if ! docker compose version >/dev/null 2>&1; then
    log "安装 compose 插件"
    apt-get update
    apt-get install -y --no-install-recommends docker-compose-v2
fi
systemctl enable --now docker

# 2. 数据目录。命名卷首次挂载会继承镜像内目录属主，绑定目录则必须显式 chown，
#    否则容器内 997 写不进去。
log "准备 /srv 目录"
install -d -o "$APP_UID" -g "$APP_GID" -m 0750 \
    "$DATA_ROOT/ntu-shower" \
    "$DATA_ROOT/status-panel/data" \
    "$DATA_ROOT/status-panel/logs"
install -d -m 0755 "$DATA_ROOT/www" /var/www/xyp.iseekdream.com/public
install -d -m 0755 /run/php 2>/dev/null || true

# php-fpm 仍跑在宿主（xyp 站点），Caddy 容器挂它的 socket
if ! systemctl is-active --quiet php8.5-fpm; then
    log "php8.5-fpm 未运行，请单独安装并启用（xyp 站点依赖它）"
fi

# 3. 配置。.env 必须由外部提供，脚本不生成带真实凭据的文件。
cd "$STACK_DIR"
[ -f .env ] || die "缺少 .env；从 .env.example 复制后填值"
# shellcheck disable=SC1091
set -a; . ./.env; set +a
: "${CADDY_IMAGE:?}" "${NTU_SHOWER_IMAGE:?}" "${STATUS_PANEL_IMAGE:?}"
if [ -z "${SESSION_SECRET:-}" ]; then
    log "生成 SESSION_SECRET"
    secret="$(openssl rand -base64 48 | tr -d '\n')"
    printf 'SESSION_SECRET=%s\n' "$secret" >> .env
    export SESSION_SECRET="$secret"
fi
[ -n "${ALIYUN_ACCESS_KEY_ID:-}" ] || die "缺少 ALIYUN_ACCESS_KEY_ID（通配符证书需要）"
[ -n "${ALIYUN_ACCESS_KEY_SECRET:-}" ] || die "缺少 ALIYUN_ACCESS_KEY_SECRET"

# 4. 恢复数据（可选）。备份包由 backup.sh 产出，自带 srv/ 与 caddy_data/。
#    校验值不对就停在这里，绝不把一份损坏的数据铺到新机上。
backup="${1:-}"
if [ -n "$backup" ]; then
    [ -f "$backup" ] || die "备份包不存在：$backup"
    if [ -f "$backup.sha256" ]; then
        expected="$(cut -d' ' -f1 < "$backup.sha256")"
        actual="$(sha256sum "$backup" | cut -d' ' -f1)"
        [ "$expected" = "$actual" ] || die "备份包校验不通过"
    fi
    log "校验备份包：$backup"
    restore_dir="$(mktemp -d)"
    tar -xzf "$backup" -C "$restore_dir"
    [ -f "$restore_dir/metadata.txt" ] || die "备份包缺少 metadata.txt"
    log "恢复 /srv"
    cp -a "$restore_dir/srv/." "$DATA_ROOT/"
    chown -R "$APP_UID:$APP_GID" "$DATA_ROOT/ntu-shower" "$DATA_ROOT/status-panel"
    log "恢复 Caddy 证书卷"
    docker volume create caddy_data >/dev/null
    docker run --rm -v caddy_data:/data -v "$restore_dir/caddy_data":/src:ro alpine \
        sh -c 'rm -rf /data/* && cp -a /src/. /data/'
    rm -rf "$restore_dir"
fi

# 5. 启动。新机只 pull，不 build——构建缓存留在 CI，不占这台机器的磁盘。
log "拉取镜像"
docker compose pull
log "建表 / 启动"
docker compose up -d

log "完成。检查：docker compose ps；curl -fsS https://ntu-shower.iseekdream.com/healthz"
