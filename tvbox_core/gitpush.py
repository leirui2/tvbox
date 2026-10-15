# -*- coding: utf-8 -*-
"""管理后台的 git 提交推送(构建产物 + 项目代码)。

只提交产物与项目代码, 临时文件/日志由 .gitignore 排除; 产物与代码均无变化时按
"无内容变化, 跳过推送"处理(与 deploy/push.sh 行为一致, 避免误报失败)。
"""
import datetime
import json
import os
import subprocess

from . import APP_DIR, OUT_DIR

def push_git():
    """Python 版 git 提交+推送。用于无 bash+flock 的环境(如 Windows 本地)
       点「构建并推送」时; 有 bash 的环境仍走 deploy/update.sh。
       优先读 deploy/push.env 的 GIT_TOKEN 注入; 否则直接用 remote 已有凭据。"""
    import subprocess
    remote, branch, user, token = 'origin', 'main', 'cnb', ''
    envp = os.path.join(APP_DIR, 'deploy', 'push.env')
    if os.path.exists(envp):
        try:
            with open(envp, encoding='utf-8') as f:
                for ln in f:
                    ln = ln.strip()
                    if not ln or ln.startswith('#') or '=' not in ln:
                        continue
                    k, _, v = ln.partition('=')
                    v = v.strip().strip('"\'')
                    if k == 'GIT_REMOTE' and v:
                        remote = v
                    elif k == 'GIT_BRANCH' and v:
                        branch = v
                    elif k == 'GIT_USER' and v:
                        user = v
                    elif k == 'GIT_TOKEN' and v:
                        token = v
        except Exception:
            pass

    auth = []
    if token:
        auth = ['-c', 'url.https://%s:%s@cnb.cool/.insteadOf=https://cnb.cool/'
                % (user, token)]

    def git(*args):
        try:
            p = subprocess.run(['git'] + auth + list(args), cwd=APP_DIR,
                               capture_output=True, text=True,
                               encoding='utf-8', errors='replace',
                               timeout=300)
            return p.returncode, (p.stdout or '') + (p.stderr or '')
        except FileNotFoundError:
            return 127, '未找到 git 命令(请安装 Git for Windows 或加入 PATH)'
        except Exception as e:
            return -1, 'git 执行异常: %s' % e

    rc, out = git('rev-parse', '--git-dir')
    if rc != 0:
        return False, '非 git 仓库: %s' % out.strip()

    rc, out = git('add', '-A', 'www/', 'sources/', 'tvbox_core/', 'build.py',
                  'manage.py', 'deploy/', 'README.md', 'DEPLOY.md')
    if rc != 0:
        return False, 'git add 失败: %s' % out.strip()

    # 本次构建产物与上次提交完全一致(例如重跑一次、结果无变化)时, 没有可提交
    # 的内容; 此时 git commit 会以 "no changes added to commit" 失败, 属于正常
    # 情况, 直接按成功处理(与 deploy/push.sh 的"无内容变化, 跳过推送"一致)。
    rc, _out = git('diff', '--cached', '--quiet')
    if rc == 0:
        return True, '无内容变化, 跳过推送(产物与上次提交一致)'

    try:
        with open(os.path.join(APP_DIR, 'www', 'report.json'),
                  encoding='utf-8') as f:
            s = json.load(f)['summary']
        stats = '存活 %d / 失效 %d (点播), 直播 %d/%d, 解析 %d/%d' % (
            s['sites_alive'], s['sites_dead'],
            s['lives_alive'], s['lives_dead'],
            s['parses_alive'], s['parses_dead'])
    except Exception:
        stats = '无统计数据'
    msg = 'chore: 自动更新源 %s | %s' % (
        datetime.datetime.now().strftime('%F %H:%M'), stats)
    rc, out = git('commit', '-q', '-m', msg)
    if rc != 0 and not any(s in out for s in
                           ('nothing to commit', 'no changes added to commit',
                            'nothing added to commit')):
        return False, 'git commit 失败: %s' % out.strip()

    rc, out = git('push', remote, 'HEAD:%s' % branch)
    if rc != 0:
        return False, 'git push 失败: %s' % out.strip()
    return True, '已推送 -> %s/%s' % (remote, branch)
