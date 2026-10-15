#!/usr/bin/env bash
# ==========================================================================
# TVBox 源自动更新 — cron 包装脚本
#
# 作用: 加锁防重入 + 记录日志 + 构建后自检。cron 每天调用它即可。
#
# 手动运行:  sudo /opt/tvbox/deploy/update.sh
# cron 配置:  17 4 * * *  /opt/tvbox/deploy/update.sh
# ==========================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(dirname "$SCRIPT_DIR")}"
WEB_DIR="${WEB_DIR:-$APP_DIR/www}"
LOCK_FILE="${LOCK_FILE:-/var/lock/tvbox-build.lock}"
LOG_FILE="${LOG_FILE:-/var/log/tvbox-build.log}"
PYTHON_BIN="$(command -v python3 || command -v python)"
WORKERS="${WORKERS:-128}"
TIMEOUT="${TIMEOUT:-4}"

# 日志文件兜底: 若默认 /var/log 不可写(常见于普通用户), 改用项目目录
if ! touch "$LOG_FILE" 2>/dev/null; then
    mkdir -p "$APP_DIR/logs"
    LOG_FILE="$APP_DIR/logs/tvbox-build.log"
    touch "$LOG_FILE" 2>/dev/null || true
fi

log() { echo "[$(date '+%F %T')] $*" >>"$LOG_FILE"; }

# ---- 1. 防重入: 上一次没跑完就跳过本次 ----
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "SKIP: 上一次构建仍在运行, 本次跳过"
    exit 0
fi

# ---- 2. 日志轮转, 防止无限增长 ----
if [ -f "$LOG_FILE" ]; then
    SIZE=$(wc -c <"$LOG_FILE")
    if [ "$SIZE" -gt 10485760 ]; then
        mv "$LOG_FILE" "${LOG_FILE}.1"
    fi
fi

log "===== 开始构建 ====="
cd "$APP_DIR" || { log "ERROR: 无法进入 $APP_DIR"; exit 1; }

# ---- 2.5 构建前先拉取远端最新(让本地源 / dc.txt 保持最新) ----
# 默认开启; 不想拉取可在环境变量设 ENABLE_PULL=0。
# 令牌不写死在脚本里: 取环境变量 GIT_TOKEN 或 deploy/push.env(已在 .gitignore),
# 通过 -c url...insteadOf 临时注入, 不写进 .git/config。
GIT_REMOTE="${GIT_REMOTE:-origin}"
GIT_BRANCH="${GIT_BRANCH:-main}"
GIT_USER="${GIT_USER:-$(git config user.name 2>/dev/null || echo cnb)}"
GIT_TOKEN="${GIT_TOKEN:-}"
GIT_HOST="${GIT_HOST:-cnb.cool}"
GIT_AUTH=""
if [ -n "$GIT_TOKEN" ]; then
    GIT_AUTH="-c url.https://${GIT_USER}:${GIT_TOKEN}@${GIT_HOST}/.insteadOf=https://${GIT_HOST}/"
fi
if [ "${ENABLE_PULL:-1}" = "1" ] && git rev-parse --git-dir >/dev/null 2>&1; then
    if git $GIT_AUTH pull --rebase --autostash "$GIT_REMOTE" "$GIT_BRANCH" >>"$LOG_FILE" 2>&1; then
        log "已拉取远端最新 ($GIT_BRANCH)"
    else
        log "PULL 未成功(继续构建, 不影响本地源文件)"
    fi
fi

# ---- 3. 构建(存活探测 + 去重合并); 安全阀不通过时返回非 0 ----
"$PYTHON_BIN" build.py --out "$WEB_DIR" --workers "$WORKERS" --timeout "$TIMEOUT" 2>&1 | tee -a "$LOG_FILE"
RC=${PIPESTATUS[0]}
if [ "$RC" -ne 0 ]; then
    log "构建返回非 0 ($RC), 保留原有源文件不动(可能是网络异常触发安全阀)"
    exit "$RC"
fi

# ---- 4. 自检: 产物必须是合法 JSON 且非空 ----
if "$PYTHON_BIN" - "$WEB_DIR/tvbox.json" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding='utf-8'))
except Exception as e:
    print('JSON 校验失败: %s' % e); sys.exit(1)
n = len(d.get('sites', []))
if n < 1:
    print('产物 sites 为空, 视为异常'); sys.exit(1)
print('JSON 校验通过, sites=%d' % n)
PY
then
    log "构建成功并通过自检"
else
    log "自检失败, 已保留原有源文件"
    exit 1
fi

# ---- 5. 构建后推送到远端仓库(cnb.cool/GitHub) ----
# 默认开启: 用户要求每次构建都把最新产物推送上去。
# 推送失败不影响已生成的源文件, 因此这里不让 update.sh 失败。
# 不想推送可在环境变量/ push.env 里设 ENABLE_PUSH=0。
if [ "${ENABLE_PUSH:-1}" = "1" ]; then
    log "开始推送到远端仓库..."
    bash "$APP_DIR/deploy/push.sh" || log "推送未成功(源文件本身不受影响)"
fi

log "===== 结束 ====="
exit 0