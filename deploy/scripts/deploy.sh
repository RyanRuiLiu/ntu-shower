#!/bin/bash
# ============================================================
#  ntu-shower 服务器端部署
#
#  前置（本地完成）：代码包已上传到 /tmp/server_deploy.tar.gz。
#  打包命令在 `docs/RUNBOOK.md` §3——整条发布流程写在那里，此处不复述，避免两处命令分叉。
#
#  用法：sudo NT_TARBALL=/tmp/server_deploy.tar.gz bash /tmp/deploy.sh
#
#  本脚本行为：
#    1. 预检：用**生产解释器**在临时目录导入新代码（本地 3.13 通过不代表生产 3.10 通过）
#    2. 停服 → 备份当前代码与配置到 backups/（保留最近 5 份）
#    3. 保留四项状态目录、其余全删，再把包解开
#    4. requirements.txt 变过才装依赖
#    5. 启动并验证（is-active + /healthz）；失败自动回滚
#
#  ## 为什么是"保留四项、其余全删"，而不是"删掉几个再覆盖"
#
#  后者曾是本项目的做法（`rm -rf app tests`）。它只能清 app/ 与 tests/，`web/`、
#  `tools/` 等目录只解压覆盖——**仓库里删掉的文件会永久留在服务器上**。实际已经攒下
#  两处：`web/core/log.js`（仓库里已删的孤儿）与 `scripts/export_openapi.py`（改名成
#  `tools/` 之前的旧目录）。凡是"枚举要删的"，加一个目录就漏一个。
#  反过来枚举要**保留的**之后，生产就等于"包里的内容 + 这几项状态"，删除天然生效。
#
#  ## 绝不能删的四项（仓库里没有、丢了不可恢复）
#
#    .venv          虚拟环境（重建要联网装依赖）
#    data/          运行时数据库——**唯一副本**
#    ota_releases/  已发布的安装包
#    .env           systemd 的 EnvironmentFile，装着数据库路径等生产配置
# ============================================================

set -euo pipefail

SERVER_DIR="/opt/ntu-shower/server"
VENV_DIR="${SERVER_DIR}/.venv"
PYTHON="${VENV_DIR}/bin/python"
SERVICE="ntu-shower"
HEALTH_URL="http://127.0.0.1:8001/healthz"
BACKUP_DIR="/opt/ntu-shower/backups"
BACKUP_KEEP=5
CHECK_DIR="/tmp/ntu-shower-deploy-check"
NT_TARBALL="${NT_TARBALL:-/tmp/server_deploy.tar.gz}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
log_info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_error() { echo -e "${RED}[ERROR]${NC} $*"; }

# 保留四项状态目录，其余全删。备份文件在 /opt/ntu-shower/backups（本目录之外），安全。
wipe_code() {
    find "$SERVER_DIR" -mindepth 1 -maxdepth 1 \
        ! -name '.venv' ! -name 'data' ! -name 'ota_releases' ! -name '.env' \
        -exec rm -rf {} +
}

BACKUP_FILE=""

rollback() {
    log_error "部署失败，回滚..."
    systemctl stop "$SERVICE" 2>/dev/null || true
    if [[ -n "$BACKUP_FILE" && -f "$BACKUP_FILE" ]]; then
        wipe_code
        tar xzf "$BACKUP_FILE" -C "$SERVER_DIR"
        log_warn "已从 $BACKUP_FILE 恢复代码"
    else
        log_error "没有可用备份，代码目录停在当前状态"
    fi
    systemctl start "$SERVICE" 2>/dev/null || true
    sleep 3
    systemctl is-active "$SERVICE" || true
    log_error "请检查日志：journalctl -u $SERVICE -n 50 --no-pager"
}

# ---- 独立回滚入口（必须先于部署流程判断，否则会"先完整部署一遍再回滚"）----
# 用法：sudo NT_ROLLBACK=/opt/ntu-shower/backups/server-<时间戳>.tar.gz bash deploy.sh --rollback
if [[ "${1:-}" == "--rollback" ]]; then
    if [[ $EUID -ne 0 ]]; then
        log_error "请用 sudo 运行此脚本"
        exit 1
    fi
    if [[ -z "${NT_ROLLBACK:-}" || ! -f "${NT_ROLLBACK}" ]]; then
        log_error "请用 NT_ROLLBACK=<备份包> 指定回滚点"
        log_error "可用的回滚点：ls -1t ${BACKUP_DIR}/server-*.tar.gz | head"
        exit 1
    fi
    BACKUP_FILE="$NT_ROLLBACK"
    rollback
    exit 0
fi

# ---- 前置检查 ----
if [[ $EUID -ne 0 ]]; then
    log_error "请用 sudo 运行此脚本"
    exit 1
fi
[[ -d "$SERVER_DIR" ]] || { log_error "项目目录不存在: $SERVER_DIR"; exit 1; }
[[ -x "$PYTHON" ]]     || { log_error "虚拟环境不存在: $PYTHON"; exit 1; }
[[ -f "${SERVER_DIR}/.env" ]] || { log_error "生产配置不存在: ${SERVER_DIR}/.env（systemd 的 EnvironmentFile）"; exit 1; }
[[ -f "$NT_TARBALL" ]] || { log_error "代码包不存在: $NT_TARBALL"; exit 1; }

# ---- 1. 预检（在停服之前）----
# 用生产解释器导入。这一步放在停服之前：预检不过时服务一直没停过，不必回滚。
log_info "[1/5] 预检：用生产解释器导入新代码..."
rm -rf "$CHECK_DIR"
mkdir -p "$CHECK_DIR"
tar xzf "$NT_TARBALL" -C "$CHECK_DIR"
if ! ( cd "$CHECK_DIR" && PYTHONPATH="$CHECK_DIR" "$PYTHON" \
        -c "from app.main import create_app; create_app(); print('IMPORT OK')" ); then
    log_error "预检失败，未停服、未改动任何文件"
    rm -rf "$CHECK_DIR"
    exit 1
fi

# requirements.txt 变过才装依赖（RUNBOOK：只改业务代码不需要 pip install）
NEED_PIP=0
if ! diff -q "${SERVER_DIR}/requirements.txt" "${CHECK_DIR}/requirements.txt" >/dev/null 2>&1; then
    NEED_PIP=1
    log_warn "requirements.txt 有变化，稍后安装依赖"
fi

# ---- 2. 停服并备份 ----
log_info "[2/5] 停止服务并备份当前代码..."
systemctl stop "$SERVICE" 2>/dev/null || log_warn "服务本就未在运行"

mkdir -p "$BACKUP_DIR"
BACKUP_FILE="${BACKUP_DIR}/server-$(date +%s).tar.gz"
# 只备代码与配置：四个保留项不会被动，重建它们要联网且体积大（.venv/data/ota_releases）
tar czf "$BACKUP_FILE" -C "$SERVER_DIR" \
    app tests tools web pyproject.toml requirements.txt requirements-dev.txt .env.example
ls -1t "${BACKUP_DIR}"/server-*.tar.gz 2>/dev/null | tail -n +$((BACKUP_KEEP + 1)) | xargs -r rm -f
log_info "备份：$BACKUP_FILE"

# ---- 3. 替换代码 ----
log_info "[3/5] 替换代码（保留 .venv／data／ota_releases／.env）..."
wipe_code
tar xzf "$NT_TARBALL" -C "$SERVER_DIR"

# ---- 4. 依赖 ----
if [[ $NEED_PIP -eq 1 ]]; then
    log_info "[4/5] 安装依赖..."
    "${VENV_DIR}/bin/pip" install -r "${SERVER_DIR}/requirements.txt" -q
else
    log_info "[4/5] requirements.txt 未变，跳过依赖安装"
fi
rm -rf "$CHECK_DIR"

# ---- 5. 启动并验证 ----
log_info "[5/5] 启动并验证..."
systemctl start "$SERVICE"
sleep 3

if ! systemctl is-active --quiet "$SERVICE"; then
    log_error "服务未进入 active"
    journalctl -u "$SERVICE" -n 30 --no-pager || true
    rollback
    exit 1
fi

if ! curl -fsS -o /dev/null "$HEALTH_URL"; then
    log_error "健康检查未通过：$HEALTH_URL"
    journalctl -u "$SERVICE" -n 30 --no-pager || true
    rollback
    exit 1
fi

log_info "============================================"
log_info "  部署完成：$(curl -fsS "$HEALTH_URL")"
log_info "  回滚点：$BACKUP_FILE"
log_info "  回滚：sudo NT_ROLLBACK=\"$BACKUP_FILE\" bash $0 --rollback"
log_info "============================================"
