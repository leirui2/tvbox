#!/usr/bin/env bash
# ==========================================================================
# 构建产物自动 git 推送
#
# 定时任务跑完 build.py 后调用: 把最新的 www/ 产物提交并推送到远端
# (cnb.cool / GitHub / 自建 Gitea 均可), 这样 TVBox 直接读 raw 地址即可。
#
# 必需的环境变量(建议写在 /opt/tvbox/deploy/push.env):
#   GIT_REMOTE   远端名, 默认 origin; 支持多个, 空格分隔(如 "cnb github")
#                多远程会依次推送, 任一失败不影响其它
#   GIT_BRANCH   分支,   默认 main
#   REPO_DIR     git 仓库目录, 默认 /opt/tvbox
#
# 认证: 推荐用 SSH deploy key; 或 HTTPS + PAT。
#   SSH:  ssh-keygen -t ed25519 -C tvbox-bot -f ~/.ssh/tvbox_deploy
#         把公钥加到 cnb.cool 仓库的部署公钥, 再配置 ~/.ssh/config
# ==========================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="${REPO_DIR:-$(dirname "$SCRIPT_DIR")}"
LOG_FILE="${LOG_FILE:-/var/log/tvbox-push.log}"

# 加载可选的环境配置(必须在计算 GIT_* 默认值之前, 否则 push.env 里的远程名/令牌不生效)
[ -f "$REPO_DIR/deploy/push.env" ] && . "$REPO_DIR/deploy/push.env"

GIT_REMOTES="${GIT_REMOTES:-${GIT_REMOTE:-origin}}"   # 可多个: "cnb github"
GIT_BRANCH="${GIT_BRANCH:-main}"

# ---- 认证: HTTPS + Personal Access Token ----
# ⚠ 令牌**不写死在脚本里**(公开仓库会泄露)。按以下优先级取值:
#   1. 环境变量 GIT_TOKEN
#   2. deploy/push.env 里的 GIT_TOKEN=xxx   (该文件已在 .gitignore 中)
#   3. SSH deploy key(此时留空即可)
# 通过 -c url...insteadOf 临时注入, 不写进 .git/config 与 remote 地址。
GIT_USER="${GIT_USER:-$(git config user.name 2>/dev/null || echo cnb)}"
GIT_TOKEN="${GIT_TOKEN:-}"
GIT_HOST="${GIT_HOST:-cnb.cool}"
GIT_AUTH=""
if [ -n "$GIT_TOKEN" ]; then
    GIT_AUTH="-c url.https://${GIT_USER}:${GIT_TOKEN}@${GIT_HOST}/.insteadOf=https://${GIT_HOST}/"
elif [ -f "$REPO_DIR/.git/config" ] && grep -q 'insteadOf' "$REPO_DIR/.git/config" 2>/dev/null; then
    :   # remote 里已配好认证(如 SSH), 无需注入
fi

# 日志文件兜底: 若默认 /var/log 不可写(常见于普通用户), 改用项目目录
if ! touch "$LOG_FILE" 2>/dev/null; then
    mkdir -p "$REPO_DIR/logs"
    LOG_FILE="$REPO_DIR/logs/tvbox-push.log"
    touch "$LOG_FILE" 2>/dev/null || true
fi

log() { echo "[$(date '+%F %T')] $*" >>"$LOG_FILE"; }

cd "$REPO_DIR" || { log "ERROR: 无法进入 $REPO_DIR"; exit 1; }

if ! git rev-parse --git-dir >/dev/null 2>&1; then
    log "SKIP: $REPO_DIR 不是 git 仓库"
    exit 0
fi

# ---- 只提交产物与项目代码, 避免把临时文件/日志带上 ----
# sources/ 与 tvbox_core/ 一并提交: 手工修正过的源配置和拆分后的核心库也要备份到远端
# tests/ 一并提交: 离线回归测试跟着代码走, 服务器上也能直接跑
git add -A www/ sources/ tvbox_core/ tests/ build.py manage.py deploy/ README.md DEPLOY.md 2>/dev/null || true

if git diff --cached --quiet; then
    log "无内容变化, 跳过推送"
    exit 0
fi

# ---- 提交信息带上本次统计, 方便在仓库里看趋势 ----
STATS=$(python3 - <<'PY' 2>/dev/null
import json
try:
    r = json.load(open('www/report.json', encoding='utf-8'))
    s = r['summary']
    print('存活 %d / 失效 %d (点播), 直播 %d/%d, 解析 %d/%d, 恢复 %d' % (
        s['sites_alive'], s['sites_dead'],
        s['lives_alive'], s['lives_dead'],
        s['parses_alive'], s['parses_dead'],
        s['recovered_vs_last_run']))
except Exception:
    print('无统计数据')
PY
)
git commit -q -m "chore: 自动更新源 $(date '+%F %H:%M') | $STATS" \
    || { log "提交失败"; exit 1; }

# ---- 推送(支持多个远程, 空格分隔; 依次推送, 任一失败不影响其它) ----
FAIL=0
for REMOTE in $GIT_REMOTES; do
    if git $GIT_AUTH push "$REMOTE" "HEAD:$GIT_BRANCH" >>"$LOG_FILE" 2>&1; then
        log "推送成功 -> $REMOTE/$GIT_BRANCH | $STATS"
    else
        log "推送失败 -> $REMOTE (不影响已生成的源文件, 详见 $LOG_FILE)"
        FAIL=1
    fi
done
exit "$FAIL"