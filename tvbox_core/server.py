# -*- coding: utf-8 -*-
"""管理后台 HTTP 服务(入口; 根目录 manage.py 仅是本模块的薄入口)。

纯标准库零依赖: 构建触发(复用 deploy/update.sh 的 flock 锁)/单独推送/黑名单释放/
禁用清单/源管理 API + 内置单页面前端。
"""
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import (APP_DIR, BACKUP_DIR, BLACKLIST_FILE, BUILD_LOG, DISABLE_FILE,
               MAX_UPLOAD, OUT_DIR, SRC_DIR)
from .gitpush import push_git
from .notify import load_notify_cfg, send_markdown
from .output import current_counts, read_report_lists, read_report_summary
from .probe import (load_blacklist_data, load_disable_data,
                    save_blacklist_file, save_disable_list)
from .sources import fetch_url, list_sources, safe_name, validate_and_save
from .webpage import PAGE

# ---------------------------------------------------------------- 构建状态
_build_lock = threading.Lock()


_build_state = {'running': False, 'started': None, 'finished': None,
                'rc': None, 'log': '', 'stage': None, 'done': 0,
                'total': 0, 'percent': 0}


_push_lock = threading.Lock()


_push_state = {'running': False, 'rc': None, 'log': ''}


def have_bash():
    """判断系统是否有 bash(Windows 上可能没有, 需回退到直接跑 build.py)。"""
    try:
        p = subprocess.run(['bash', '--version'], capture_output=True,
                           timeout=10)
        return p.returncode == 0
    except Exception:
        return False


def _update_prog(line):
    """解析 build.py 输出的 ##PROG <stage> <done> <total> 行, 更新构建进度。"""
    try:
        _, stage, done, total = line.split()
        done = int(done)
        total = max(1, int(total))
    except Exception:
        return
    pct = int(done * 100 / total)
    with _build_lock:
        _build_state.update({'stage': stage, 'done': done, 'total': total,
                             'percent': min(100, max(0, pct))})


def start_build(enable_push=False):
    """后台触发一次构建。
       优先走 deploy/update.sh(自带 flock + 自检 + 推送), 与 cron 互斥;
       若无 bash(如 Windows 本地测试)则回退为直接执行 build.py。"""
    with _build_lock:
        if _build_state['running']:
            return False
        _build_state.update({'running': True, 'started': time.time(),
                             'finished': None, 'rc': None, 'log': '',
                             'stage': None, 'done': 0, 'total': 0,
                             'percent': 0})
    script = os.path.join(APP_DIR, 'deploy', 'update.sh')

    def worker():
        env = dict(os.environ)
        env['APP_DIR'] = APP_DIR
        env['WEB_DIR'] = OUT_DIR
        env['PYTHONIOENCODING'] = 'utf-8'
        if enable_push:
            env['ENABLE_PUSH'] = '1'
        rc = -1
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            use_script = os.path.exists(script) and have_bash()
            if use_script:
                cmd = ['bash', script]
            else:
                cmd = [sys.executable, os.path.join(APP_DIR, 'build.py'),
                       '--out', OUT_DIR]
            # 流式读取子进程输出: ##PROG 行用于刷新进度, 其余实时写入日志
            with open(BUILD_LOG, 'w', encoding='utf-8') as logf:
                p = subprocess.Popen(cmd, cwd=APP_DIR,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, text=True,
                                     encoding='utf-8', errors='replace',
                                     env=env, bufsize=1)
                for line in p.stdout:
                    line = line.rstrip('\n')
                    if line.startswith('##PROG '):
                        _update_prog(line)
                    else:
                        logf.write(line + '\n')
                        logf.flush()
                p.wait()
                rc = p.returncode
            if enable_push and not use_script:
                ok, msg = push_git()
                with open(BUILD_LOG, 'a', encoding='utf-8') as f:
                    f.write('\n[推送] %s\n' % msg)
        except Exception as e:
            try:
                with open(BUILD_LOG, 'a', encoding='utf-8') as f:
                    f.write('构建异常: %s\n' % e)
            except Exception:
                pass
            rc = -1
        with _build_lock:
            _build_state.update({'running': False, 'finished': time.time(),
                                 'rc': rc, 'percent': 100})

    threading.Thread(target=worker, daemon=True).start()
    return True


def start_push():
    """后台触发一次单独 git 推送(不构建), 把当前 www/ 产物推到远端。
       有 bash+flock 走 deploy/push.sh, 否则走 Python 版 push_git。"""
    with _build_lock:
        building = _build_state['running']
    if building:
        return False
    with _push_lock:
        if _push_state['running']:
            return False
        _push_state.update({'running': True, 'rc': None, 'log': ''})

    def worker():
        script = os.path.join(APP_DIR, 'deploy', 'push.sh')
        try:
            if os.path.exists(script) and have_bash():
                env = dict(os.environ)
                env['REPO_DIR'] = APP_DIR
                env['PYTHONIOENCODING'] = 'utf-8'
                p = subprocess.run(['bash', script], cwd=APP_DIR,
                                   capture_output=True, text=True,
                                   encoding='utf-8', errors='replace',
                                   env=env, timeout=600)
                out = (p.stdout or '') + (p.stderr or '')
                rc = p.returncode
            else:
                ok, msg = push_git()
                out = '[推送] %s\n' % msg
                rc = 0 if ok else 1
        except Exception as e:
            rc, out = -1, '推送异常: %s' % e
        with _push_lock:
            _push_state.update({'running': False, 'rc': rc, 'log': out})

    threading.Thread(target=worker, daemon=True).start()
    return True


# ---------------------------------------------------------------- HTTP
def _retry_in_days(e, threshold, retry_days):
    """黑名单条目距"自动重试探"还剩几天(与 probe._expired 同规则)。
       未达阈值或关闭重试探 -> None(显示 -); 已到期 -> 0(下次构建重探)。"""
    if retry_days <= 0 or int(e.get('fail_count', 0)) < threshold:
        return None
    lf = str(e.get('last_fail', '') or '')
    if not lf:
        return 0
    try:
        dt = datetime.datetime.strptime(lf, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return 0
    return max(0, retry_days - (datetime.datetime.now() - dt).days)


class Handler(BaseHTTPRequestHandler):
    server_version = 'TVBoxManager/1.0'

    def _send(self, code, body, ctype='application/json; charset=utf-8'):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def log_message(self, fmt, *args):
        sys.stderr.write('[%s] %s\n' % (datetime.datetime.now().strftime('%H:%M:%S'), fmt % args))

    def do_GET(self):
        p = self.path.split('?')[0]
        if p == '/':
            self._send(200, PAGE, 'text/html; charset=utf-8')
            return
        if p == '/api/status':
            with _build_lock:
                st = dict(_build_state)
            with _push_lock:
                pst = dict(_push_state)
            logtxt = ''
            if os.path.exists(BUILD_LOG):
                try:
                    with open(BUILD_LOG, encoding='utf-8') as f:
                        logtxt = f.read()[-4000:]
                except Exception:
                    pass
            if st['running'] and st['started']:
                duration = time.time() - st['started']
            elif st['started'] and st['finished']:
                duration = st['finished'] - st['started']
            else:
                duration = None
            return self._json(200, {
                'ok': True, 'running': st['running'],
                'last_rc': st['rc'],
                'stage': st.get('stage'), 'done': st.get('done', 0),
                'total': st.get('total', 0), 'percent': st.get('percent', 0),
                'duration': duration,
                'started': st['started'], 'finished': st['finished'],
                'counts': current_counts(),
                'summary': read_report_summary(),
                'lists': read_report_lists(),
                'source_count': len([f for f in os.listdir(SRC_DIR)
                                     if f.endswith('.json')]),
                'log': logtxt,
                'push_running': pst['running'], 'push_rc': pst['rc'],
                'push_log': pst['log'],
            })
        if p == '/api/sources':
            return self._json(200, {'ok': True, 'sources': list_sources()})
        if p == '/api/report':
            fp = os.path.join(OUT_DIR, 'report.json')
            if os.path.exists(fp):
                with open(fp, encoding='utf-8') as f:
                    return self._send(200, f.read())
            return self._json(404, {'ok': False, 'message': '暂无报告'})
        if p == '/api/blacklist':
            data = load_blacklist_data()
            threshold = int(data.get('threshold', 5))
            retry_days = int(data.get('retry_days', 7) or 0)

            def rows(d):
                return [{'url': u, 'name': e.get('name') or '',
                         'fail_count': int(e.get('fail_count', 0)),
                         'kind': e.get('kind') or '',
                         'first_fail': e.get('first_fail') or '',
                         'last_fail': e.get('last_fail') or '',
                         'retry_in_days': _retry_in_days(e, threshold,
                                                         retry_days)}
                        for u, e in (d or {}).items()]
            items = sorted(rows(data.get('entries')),
                           key=lambda x: x['fail_count'], reverse=True)
            fetch_items = sorted(rows(data.get('fetch_entries')),
                                 key=lambda x: x['fail_count'], reverse=True)
            return self._json(200, {'ok': True, 'threshold': threshold,
                                    'retry_days': retry_days,
                                    'items': items, 'fetch_items': fetch_items})
        if p == '/api/disable/list':
            data = load_disable_data()
            items = data.get('items') or []
            return self._json(200, {'ok': True, 'items': items})
        if p == '/api/disable/search':
            qs = urllib.parse.urlparse(self.path).query
            q = urllib.parse.parse_qs(qs).get('q', [''])[0].strip().lower()
            fp = os.path.join(OUT_DIR, 'tvbox.json')
            res = []
            if q and os.path.exists(fp):
                try:
                    with open(fp, encoding='utf-8') as f:
                        d = json.load(f)
                    for s in d.get('sites', []) or []:
                        if not isinstance(s, dict): continue
                        nm = str(s.get('name', '') or '')
                        ap = str(s.get('api', '') or '')
                        ex = str(s.get('ext', '') or '')
                        if q in nm.lower() or q in ap.lower() or q in ex.lower():
                            res.append({'key': s.get('key', ''), 'name': nm,
                                        'api': ap, 'ext': ex})
                except Exception:
                    pass
            return self._json(200, {'ok': True, 'items': res, 'q': q})
        if p == '/api/notify/status':
            cfg = load_notify_cfg()
            chatid = str(cfg.get('chatid') or '')
            return self._json(200, {
                'ok': True,
                'enabled': bool(cfg.get('enabled', True)),
                'bound': bool(chatid),
                'chatid': chatid,
                'chat_type': int(cfg.get('chat_type') or 1),
            })
        return self._json(404, {'ok': False, 'message': 'not found'})

    def do_POST(self):
        p = self.path.split('?')[0]

        n = int(self.headers.get('Content-Length') or 0)
        if n <= 0 or n > MAX_UPLOAD + 1024 * 64:
            return self._json(400, {'ok': False,
                                    'message': '请求体为空或过大'})
        raw = self.rfile.read(n)
        try:
            body = json.loads(raw.decode('utf-8'))
        except Exception:
            return self._json(400, {'ok': False, 'message': '请求体不是合法 JSON'})

        if p == '/api/add':
            t = body.get('type')
            fn = body.get('filename') or ''
            try:
                if t == 'url':
                    url = (body.get('url') or '').strip()
                    data = fetch_url(url)
                    if not fn:
                        base = re.sub(r'[?#].*$', '', url).rstrip('/').split('/')[-1] or 'remote'
                        fn = base if base.lower().endswith('.json') else base + '.json'
                elif t == 'content':
                    c = body.get('content') or ''
                    if not c.strip():
                        return self._json(400, {'ok': False, 'message': '内容为空'})
                    data = c.encode('utf-8')
                else:
                    return self._json(400, {'ok': False, 'message': '未知 type'})
                ok, msg, counts = validate_and_save(fn, data)
            except ValueError as e:
                return self._json(400, {'ok': False, 'message': str(e)})
            except Exception as e:
                return self._json(400, {'ok': False,
                                        'message': '抓取/处理失败: %s' % e})
            return self._json(200 if ok else 400,
                               {'ok': ok, 'message': msg, 'counts': counts})

        if p == '/api/rebuild':
            started = start_build(enable_push=bool(body.get('push')))
            if started:
                return self._json(200, {'ok': True, 'started': True})
            return self._json(200, {'ok': False, 'started': False,
                                    'message': '已有构建在进行中'})

        if p == '/api/push':
            started = start_push()
            if started:
                return self._json(200, {'ok': True, 'started': True})
            return self._json(200, {'ok': False, 'started': False,
                                    'message': '构建中或推送已在执行'})

        if p == '/api/blacklist/release':
            url = (body.get('url') or '').strip()
            source = (body.get('source') or 'probe').strip()
            if not url:
                return self._json(400, {'ok': False, 'message': '缺少 url'})
            data = load_blacklist_data()
            entries = data.get('entries') or {}
            fetch_entries = data.get('fetch_entries') or {}
            threshold = int(data.get('threshold', 5))
            target = fetch_entries if source == 'fetch' else entries
            if url in target:
                target.pop(url, None)
                save_blacklist_file(BLACKLIST_FILE, entries, fetch_entries,
                                      threshold)
                return self._json(200, {'ok': True, 'message': '已释放, 下次构建恢复'})
            return self._json(200, {'ok': True, 'message': '该源不在黑名单中'})

        if p == '/api/blacklist/release_file':
            name = os.path.basename((body.get('name') or '').strip())
            if not name:
                return self._json(400, {'ok': False, 'message': '缺少文件名'})
            data = load_blacklist_data()
            entries = data.get('entries') or {}
            fetch_entries = data.get('fetch_entries') or {}
            threshold = int(data.get('threshold', 5))
            hit = [u for u, e in entries.items()
                   if (e.get('src') or '') == name]
            for u in hit:
                entries.pop(u, None)
            if hit:
                save_blacklist_file(BLACKLIST_FILE, entries, fetch_entries,
                                      threshold)
            return self._json(200, {'ok': True, 'released': len(hit),
                                    'message': '已释放 %d 个源' % len(hit)})

        if p == '/api/blacklist/release_all':
            # 一键全部放出: 不用等 7 天自动重探, 下次构建立即重新探测
            source = (body.get('source') or 'all').strip()
            if source not in ('probe', 'fetch', 'all'):
                return self._json(400, {'ok': False, 'message': '未知 source'})
            data = load_blacklist_data()
            entries = data.get('entries') or {}
            fetch_entries = data.get('fetch_entries') or {}
            threshold = int(data.get('threshold', 5))
            retry_days = int(data.get('retry_days', 7) or 0)
            n = 0
            if source in ('probe', 'all'):
                n += len(entries)
                entries = {}
            if source in ('fetch', 'all'):
                n += len(fetch_entries)
                fetch_entries = {}
            if n:
                save_blacklist_file(BLACKLIST_FILE, entries, fetch_entries,
                                    threshold, retry_days=retry_days)
            return self._json(200, {'ok': True, 'released': n,
                                    'message': '已放出 %d 个源, 下次构建重新探测'
                                               % n})

        if p == '/api/notify/test':
            # 给已绑定会话发一条测试消息(绑定状态见 GET /api/notify/status)
            ok, detail = send_markdown(
                '**TVBox 通知测试** `%s`\n如果你看到这条消息, 说明绑定成功。'
                % datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            return self._json(200, {'ok': ok, 'message': detail})

        if p == '/api/disable/add':
            item = {
                'key': (body.get('key') or '').strip(),
                'name': (body.get('name') or '').strip(),
                'api': (body.get('api') or '').strip(),
                'ext': (body.get('ext') or '').strip(),
                'added': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            }
            data = load_disable_data()
            items = data.get('items') or []
            api, key = item['api'], item['key']
            if not any(str(x.get('api', '')) == api and str(x.get('key', '')) == key
                       for x in items if isinstance(x, dict)):
                items.append(item)
                save_disable_list(DISABLE_FILE, items)
                return self._json(200, {'ok': True, 'message': '已禁用, 下次构建生效'})
            return self._json(200, {'ok': True, 'message': '该源已在禁用清单中'})

        if p == '/api/disable/remove':
            api = (body.get('api') or '').strip()
            key = (body.get('key') or '').strip()
            data = load_disable_data()
            items = data.get('items') or []
            before = len(items)
            items = [x for x in items if isinstance(x, dict)
                     and not (str(x.get('api', '')) == api and str(x.get('key', '')) == key)]
            if len(items) < before:
                save_disable_list(DISABLE_FILE, items)
                return self._json(200, {'ok': True, 'message': '已启用, 下次构建恢复'})
            return self._json(200, {'ok': True, 'message': '该源不在禁用清单中'})

        return self._json(404, {'ok': False, 'message': 'not found'})


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=8787)
    args = ap.parse_args()

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print('管理页已启动: http://%s:%d' % (args.host, args.port))
    if args.host in ('127.0.0.1', '::1'):
        print('仅监听本机, 局域网访问请加 --host 0.0.0.0')
    else:
        print('已监听所有网卡, 局域网内设备可直接访问')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')


if __name__ == '__main__':
    main()
