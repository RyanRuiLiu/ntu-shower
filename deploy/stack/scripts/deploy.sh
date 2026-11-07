#!/usr/bin/env bash
# 发布：换镜像 tag → 拉取 → 重建。回滚就是把 tag 改回旧值再跑一次。
#
# 用法：./deploy.sh <ntu-shower 的新 tag> [status-panel 的新 tag]

set -euo pipefail
STACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$STACK_DIR"
[ -f .env ] || { echo "缺少 .env" >&2; exit 1; }

new_shower="${1:-}"
[ -n "$new_shower" ] || { echo "用法：deploy.sh <tag> [status-panel tag]" >&2; exit 1; }

old_shower="$(grep -E '^NTU_SHOWER_IMAGE=' .env)"
sed -i -E "s#^(NTU_SHOWER_IMAGE=.*:).*#\1${new_shower}#" .env
if [ -n "${2:-}" ]; then
    sed -i -E "s#^(STATUS_PANEL_IMAGE=.*:).*#\1${2}#" .env
fi
printf '[deploy] %s\n  ->  %s\n' "$old_shower" "$(grep -E '^NTU_SHOWER_IMAGE=' .env)" >&2

# 迁移在独立的 migrate 服务里跑；它成功，业务容器才会启动
docker compose pull
docker compose up -d

# 等 ntu-shower 健康，失败就把 tag 改回去
if ! timeout 120 bash -c 'until [ "$(docker inspect -f "{{.State.Health.Status}}" "$(docker compose ps -q ntu-shower)")" = healthy ]; do sleep 2; done'; then
    echo "[deploy] 健康检查未通过，回滚到 $old_shower" >&2
    sed -i -E "s#^(NTU_SHOWER_IMAGE=).*#\1${old_shower#*=}#" .env
    docker compose up -d
    exit 1
fi
echo '[deploy] 完成' >&2
