# -*- coding: utf-8 -*-
"""静态字段挑选 + 存活探测 + 运行状态(黑名单 / 禁用清单)。

- pick_static_fields   为 wallpaper/logo/homeLogo/spider 等挑选真正可用的地址
                       (排除本机/内网地址, 探测连通性, 失败置空)
- run_probes           并发探测 sites/lives/parses, 打 _alive/_dead/_dead_reason;
                       同一 URL 连续失败达阈值即拉黑(后续构建跳过并标记失效),
                       探测成功清零; 顺带清理 URL 已不在本轮配置中的僵尸记录;
                       健康分级: 连续失败未达阈值但本次恢复的站点打 _warn(输出加 [⚠]);
                       deep: type 0/1 站点读响应体验证内容(200 但空 list/错误页判失效);
                       熔断: 连接失败占比过高判定网络异常, 放弃本轮黑名单写入;
                       重试探: 已达阈值超过 N 天的条目自动放出重探(活了复活)
                       conn_error 重试: 连接级失败自动以半超时重探 1 次,
                       网络瞬时抖动不直接判失败(配合熔断+重试探,
                       构成完整的探测可靠性体系)
- 黑名单/禁用清单      读写 probe_blacklist.json / disable_list.json
"""
import datetime
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import concurrent.futures as cf

from . import BLACKLIST_FILE, DISABLE_FILE, emit_prog
from .merge import URL_FIELDS
from .output import atomic_write

# ----------------------------------------------------------------------------
# 2.5 静态资源字段清洗 (wallpaper / logo / spider / danmaku)
# ----------------------------------------------------------------------------
LOCAL_HOSTS = {'localhost', '127.0.0.1', '0.0.0.0', '::1', ''}


def _host_of(url):
    m = re.match(r'https?://([^/:?#]+)', url or '', re.I)
    return m.group(1).lower() if m else ''


def _is_local_url(url):
    """判断是否为本机/内网地址 —— 这类地址在电视端必然失败。"""
    h = _host_of(url)
    if h in LOCAL_HOSTS:
        return True
    if h.startswith('127.') or h.startswith('10.') or h.startswith('192.168.'):
        return True
    if re.match(r'^172\.(1[6-9]|2\d|3[01])\.', h):
        return True
    if h.endswith(('.local', '.lan', '.internal', '.localhost')):
        return True
    return False


def pick_static_fields(merged, timeout=6, verbose=True, do_probe=True,
                       workers=16):
    """为 wallpaper/logo/homeLogo/danmaku/spider 挑选真正可用的地址。
       规则: 排除本机/内网地址 -> 优先选可访问的 -> 都不可用则留空(宁可留空也不报错)。
       spider 例外: 它是 csp 站的基础, 实在挑不到就保留第一个非本机候选。
       do_probe=False 时(离线模式)只做本机地址过滤, 不做连通性探测。
       各字段及其候选并发探测, 但按「字段顺序 + 候选顺序」取首个可用的, 语义不变。"""
    fields = []   # (fld, cands, usable, dropped)
    tasks = []    # (fld, idx, url) 待探测候选(每字段最多前 6 个)
    for fld in URL_FIELDS:
        cands = merged.pop('_cand_' + fld, [])
        usable = [u for u in cands if not _is_local_url(u)]
        dropped = len(cands) - len(usable)
        fields.append((fld, cands, usable, dropped))
        if do_probe and usable:
            for idx, u in enumerate(usable[:6]):
                tasks.append((fld, idx, u))

    probe_ok = set()   # {(fld, idx)} 探测到 2xx/3xx 的候选
    if tasks:
        total = len(tasks)
        emit_prog('static', 0, total)
        done = 0
        step = max(1, total // 50)

        def check(task):
            fld, idx, u = task
            probe = u.split(';')[0]      # spider 的 ;md5;xxx 后缀不能直接请求
            st, _size, _elapsed = http_check(probe, timeout)
            return fld, idx, 200 <= st < 400
        with cf.ThreadPoolExecutor(
                max_workers=max(1, min(workers, total))) as ex:
            for fld, idx, ok in ex.map(check, tasks):
                done += 1
                if ok:
                    probe_ok.add((fld, idx))
                if done % step == 0 or done == total:
                    emit_prog('static', done, total)

    for fld, cands, usable, dropped in fields:
        if not usable:
            merged[fld] = cands[0] if (fld == 'spider' and cands) else ''
            if verbose and dropped:
                print('  [%s] 候选全部为本机/内网地址, 已置空' % fld)
            continue
        chosen = ''
        if do_probe:
            for idx, u in enumerate(usable[:6]):
                if (fld, idx) in probe_ok:
                    chosen = u
                    break
            if not chosen:
                chosen = usable[0] if fld == 'spider' else ''
        else:
            chosen = usable[0]
        merged[fld] = chosen
        if verbose:
            note = '' if chosen else ' (无可用候选, 置空)'
            print('  [%s] 选用 %s%s' % (fld, chosen[:70] or '(空)', note))


# ----------------------------------------------------------------------------
# 3. 存活探测
# ----------------------------------------------------------------------------
def _is_url(s):
    return isinstance(s, str) and re.match(r'^https?://', s.strip(), re.I)


def site_probe_target(site):
    """返回该 site 需要探测的 URL; 返回 None 表示无法/不需探测(客户端依赖)。"""
    t = site.get('type')
    api = site.get('api', '')
    if t in (1, 0, '1', '0') and _is_url(api):
        return api.strip(), 'cms'
    if t == 3 or t == '3':
        if _is_url(api):
            return api.strip(), 'spider-js'
        return None, 'client'  # csp_xxx 等依赖客户端爬虫
    if _is_url(api):
        return api.strip(), 'other'
    return None, 'unknown'


def http_check(url, timeout):
    import urllib.request
    import urllib.error
    req = urllib.request.Request(url, method='GET')
    req.add_header('User-Agent', 'Mozilla/5.0 TVBox-Source-Checker')
    req.add_header('Accept', '*/*')
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(4096)
            return resp.status, len(data), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, 0, time.time() - t0
    except Exception:
        return -1, 0, time.time() - t0


# deep 内容验证: 最多读取的响应体字节(常见 CMS list 响应几 KB~几十 KB)
DEEP_MAX_BYTES = 131072


def http_check_body(url, timeout, max_bytes=DEEP_MAX_BYTES):
    """GET url 并读取最多 max_bytes 字节响应体, 返回 (status, body, elapsed)。
       与 http_check 的区别: 连响应体一起返回, 供 deep 内容验证使用。"""
    req = urllib.request.Request(url, method='GET')
    req.add_header('User-Agent', 'Mozilla/5.0 TVBox-Source-Checker')
    req.add_header('Accept', '*/*')
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_bytes)
            return resp.status, data, time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, b'', time.time() - t0
    except Exception:
        return -1, b'', time.time() - t0


def deep_content_ok(body):
    """CMS 响应体内容验证: 识别"HTTP 200 但内容无效"(空 list/错误页/登录页)。
       宽进严出——只有确定无效才返回 False, 拿不准的一律按有效(保持原判活):
       - JSON 能解析成 dict: list/class 任一为非空数组 -> 有效; 两者皆空/缺失 -> 无效
       - JSON 根节点是非空数组 -> 有效(个别接口根节点即数组)
       - JSON 解析失败(多为响应体超过 max_bytes 被截断): 含 `"list"` 引号标记 -> 有效
       - XML(type 0 CMS): 含 <list/<video/<class 标签 -> 有效
       - 其余(HTML 错误页/纯文本报错/接近空白)-> 无效
       仅用于 kind='cms' 的站点(type 0/1, api 为接口 URL)。"""
    text = (body.decode('utf-8', 'ignore')
            if isinstance(body, (bytes, bytearray)) else str(body or ''))
    if not text.strip():
        return False
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict):
        for k in ('list', 'class'):
            v = data.get(k)
            if isinstance(v, list) and v:
                return True
        return False
    if isinstance(data, list):
        return bool(data)
    # 解析失败(截断的大响应)或非 JSON: 标记兜底, 没有 CMS 结构特征才判无效
    return ('"list"' in text or '<list' in text
            or '<video' in text or '<class' in text)


def measure_live_speed(url, timeout):
    """对 m3u8 直播源测播放速度: 下载第一个分片前 64KB 的耗时秒数。
       非 m3u8 或失败返回 0。"""
    import urllib.request
    import urllib.error
    import urllib.parse
    try:
        req = urllib.request.Request(url)
        req.add_header('User-Agent', 'Mozilla/5.0 TVBox-Source-Checker')
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(8192).decode('utf-8', 'ignore')
        if not data.startswith('#EXTM3U'):
            return 0.0
        seg = None
        for line in data.splitlines():
            line = line.strip()
            if line and not line.startswith('#'):
                seg = urllib.parse.urljoin(url, line)
                break
        if not seg:
            return 0.0
        if '.m3u8' in seg.split('?')[0]:
            req2 = urllib.request.Request(seg)
            req2.add_header('User-Agent', 'Mozilla/5.0 TVBox-Source-Checker')
            with urllib.request.urlopen(req2, timeout=timeout) as resp2:
                data2 = resp2.read(8192).decode('utf-8', 'ignore')
            for line in data2.splitlines():
                line = line.strip()
                if line and not line.startswith('#'):
                    seg = urllib.parse.urljoin(seg, line)
                    break
        t0 = time.time()
        req3 = urllib.request.Request(seg)
        req3.add_header('User-Agent', 'Mozilla/5.0 TVBox-Source-Checker')
        with urllib.request.urlopen(req3, timeout=timeout) as resp3:
            resp3.read(65536)
        return round(time.time() - t0, 2)
    except Exception:
        return 0.0


def probe_url(url, kind, timeout, retries=0, deep=True):
    """探测一个 URL, 返回 (alive, status, note, elapsed, playback_rt)。
        playback_rt 仅对 live 源测分片下载耗时, 其余为 0。
       deep=True 且 kind='cms'(type 0/1 站点)时, 读取响应体做内容验证:
       200 但空 list/错误页 -> 判失效(note='invalid_content'), 不重试(非瞬时故障)。
       其余 kind(live/parse/spider-js)只做浅层探测。"""
    last = (-1, '')
    rt = float(timeout)
    for attempt in range(retries + 1):
        t = timeout if attempt == 0 else max(2, timeout // 2)
        body = b''
        if kind == 'cms' and deep:
            status, body, elapsed = http_check_body(url, t)
            size = len(body)
        else:
            status, size, elapsed = http_check(url, t)
        rt = elapsed
        last = (status, '')
        if kind == 'live':
            if 200 <= status < 400 and size > 50:
                prt = measure_live_speed(url, t)
                return True, status, '', elapsed, prt
            if 300 <= status < 400:
                return True, status, 'redirect', elapsed, 0.0
        else:
            if 200 <= status < 400 and size > 0:
                if kind == 'cms' and deep and not deep_content_ok(body):
                    return False, status, 'invalid_content', elapsed, 0.0
                return True, status, '', elapsed, 0.0
            if 200 <= status < 400:
                return False, status, 'empty_body', elapsed, 0.0
        if status == -1:
            continue
        break
    note = 'http_%s' % last[0] if last[0] != -1 else 'conn_error'
    return False, last[0], note, rt, 0.0


def run_probes(merged, timeout, workers, blacklist=None, threshold=5,
               warn_threshold=3, deep_check=True, breaker_ratio=0.6,
               breaker_min_probes=30, retry_days=7):
    """对 sites / lives / parses 做并发探测, 给每个条目打 _alive / _dead / _dead_reason。
       blacklist: {url: {fail_count, first_fail, last_fail, kind, name}} 失败计数表(就地修改)。
       规则: 同一 URL 连续失败达到 threshold 次即拉黑 -> 后续构建直接跳过探测(标记 blacklisted);
             探测成功则清零。黑名单由上层负责持久化/手动释放。
       warn_threshold: 健康分级——探测成功但此前已连续失败达到该次数(且未达 threshold)
                       的站点打 _warn 标记(输出时 name 追加 [⚠]), 让用户在配置里直观看到
                       "时好时坏"的源, 而非等到拉黑才发现; 置 0 关闭。只标记不改判定,
                       不影响黑名单计数。
       deep_check: 对 type 0/1 CMS 站点读取响应体做内容验证(识别 200 但空 list/错误页),
                   验证失败判失效(note='invalid_content'); False 时退回纯浅层探测。
       breaker_ratio: 熔断——本轮探测中连接级失败(conn_error)占比达到该值、且探测数
                      >= breaker_min_probes 时, 判定本轮网络异常, 放弃本轮黑名单写入
                      (恢复探测前的计数), 防止一次断网/限速的构建把几百个源永久拉黑;
                      置 0 关闭。产物仍按本轮探测输出(由安全阀兜底)。
       retry_days: 已达阈值的黑名单条目距 last_fail 超过该天数, 本轮自动放出来重新
                   试探(活了清零复活, 死了重新计时); 0 关闭(保持永久跳过)。
       conn_error 重试: 连接级失败(status=-1)自动以半超时重探 1 次再判失败,
                   瞬时网络抖动不会直接把源推进黑名单; 非连接级失败(HTTP 错误/
                   deep 判无效)不重试。半超时 = max(2, timeout//2)。
       返回统计 dict: {probed, conn_error, tripped, retried, retry_recovered,
                      warned, invalid}。"""
    if blacklist is None:
        blacklist = {}
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    now_dt = datetime.datetime.now()

    # 收集所有待探测任务, 按 URL 去重
    tasks = []  # (category, idx, url, kind)
    url_map = {}  # url -> (alive, status, note)

    for idx, s in enumerate(merged.get('sites', [])):
        url, kind = site_probe_target(s)
        if url is None:
            s['_check'] = kind  # client / unknown -> 不探测, 保留
        else:
            tasks.append(('site', idx, url, kind))
    for idx, l in enumerate(merged.get('lives', [])):
        url = l.get('url', '')
        if _is_url(url):
            tasks.append(('live', idx, url.strip(), 'live'))
        else:
            l['_check'] = 'bad-url'
    for idx, p in enumerate(merged.get('parses', [])):
        url = p.get('url', '')
        if _is_url(url):
            base = url.split('?')[0]
            tasks.append(('parse', idx, base, 'parse'))
        else:
            p['_check'] = 'bad-url'

    unique = {}
    for cat, idx, url, kind in tasks:
        unique.setdefault(url, (kind, []))[1].append((cat, idx))

    # 清理僵尸记录: URL 已不在本轮配置中的黑名单条目。
    # 除了让文件停止膨胀, 更重要的是防止"源回归时被陈旧记录直接判死"——
    # 已达阈值的条目会被跳过探测并标记 blacklisted, 若源只是暂时缺席(抓取失败/
    # 被禁用), 不清理的话它恢复后也永远进不了产物。
    # 清理只跟配置有关、跟网络状态无关, 因此熔断恢复快照后要重新应用一次。
    def clean_stale():
        stale = [u for u in blacklist if u not in unique]
        for u in stale:
            blacklist.pop(u, None)
        return stale

    stale = clean_stale()
    if stale:
        print('[blacklist] 清理 %d 条失效记录(URL 不在本轮配置中), 剩 %d 条'
              % (len(stale), len(blacklist)))

    # 已达阈值的条目, 距 last_fail 超过 retry_days 的放出来重新试探
    # (防"一次坏构建永久拉黑"; 老记录没有 last_fail 时间戳的当过期处理)
    def _expired(e):
        if retry_days <= 0:
            return False
        lf = str(e.get('last_fail', '') or '')
        if not lf:
            return True
        try:
            dt = datetime.datetime.strptime(lf, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return True
        return (now_dt - dt).days >= retry_days

    def blocked(u):
        e = blacklist.get(u)
        if not e or int(e.get('fail_count', 0)) < threshold:
            return False
        return not _expired(e)   # 达阈值但已过期 -> 不跳过, 重新试探

    probe_urls = [u for u in unique if not blocked(u)]
    skipped = [u for u in unique if blocked(u)]
    retried = [u for u in probe_urls
               if int((blacklist.get(u) or {}).get('fail_count', 0)) >= threshold]
    for u in skipped:
        url_map[u] = (False, 0, 'blacklisted', 0, 0)
    if skipped:
        print('  跳过 %d 个黑名单源(连续失败>=%d 次)' % (len(skipped), threshold))
    if retried:
        print('  %d 个黑名单源已达阈值超过 %d 天, 本轮重新试探'
              % (len(retried), retry_days))

    # 熔断快照: 本轮探测前的黑名单状态(判定网络异常时整体恢复, 不写坏数据)
    snapshot = json.loads(json.dumps(blacklist))

    prev_fails = {}   # url -> 本次探测前的连续失败次数(供健康分级标记)

    def worker(url):
        kind, refs = unique[url]
        prev = blacklist.get(url) or {}
        try:
            pf = int(prev.get('fail_count', 0) or 0)
        except (TypeError, ValueError):
            pf = 0
        return url, pf, probe_url(url, kind, timeout, retries=1,
                                  deep=deep_check)

    total = len(probe_urls)
    emit_prog('probe', 0, total)
    done = 0
    step = max(1, total // 50)
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for url, prev_fail, res in ex.map(worker, probe_urls):
            url_map[url] = res
            prev_fails[url] = prev_fail
            alive, status, note, rt, prt = res
            if alive:
                blacklist.pop(url, None)          # 恢复 -> 失败计数清零
            else:
                e = blacklist.get(url) or {}
                e['fail_count'] = int(e.get('fail_count', 0)) + 1
                e.setdefault('first_fail', now)
                e['last_fail'] = now
                e['kind'] = unique[url][0]
                cat, idx = unique[url][1][0]
                item = merged[cat + 's'][idx]
                e['name'] = item.get('name') or item.get('key') or url
                e['src'] = item.get('_src') or ''
                blacklist[url] = e
            done += 1
            if done % step == 0 or done == total:
                emit_prog('probe', done, total)

    def apply(cat, idx, url):
        alive, status, note, rt, prt = url_map[url]
        item = merged[cat + 's'][idx]
        if alive:
            item['_alive'] = True
            item['_rt'] = round(rt, 2)
            if prt:
                item['_prt'] = prt
            # 健康分级: 本次恢复了, 但此前已连续失败 warn_threshold~threshold-1 次
            # -> 打 _warn(输出时 name 加 [⚠]), 暴露"时好时坏"的源
            if (cat == 'site' and warn_threshold
                    and warn_threshold <= prev_fails.get(url, 0) < threshold):
                item['_warn'] = prev_fails[url]
        else:
            item['_alive'] = False
            item['_dead'] = True
            item['_dead_reason'] = note
            item['_dead_status'] = status

    for url, (kind, refs) in unique.items():
        for cat, idx in refs:
            apply(cat, idx, url)

    warned = [it for it in merged.get('sites', []) if it.get('_warn')]
    if warned:
        print('  [健康标记] %d 个站点近期连续失败(本次已恢复), 输出 name 加 [⚠]'
              % len(warned))
        for it in sorted(warned, key=lambda x: -x['_warn'])[:5]:
            print('        ⚠ %s (连续失败 %d 次)'
                  % ((it.get('name') or it.get('key') or '?')[:40], it['_warn']))
    n_invalid = 0
    if deep_check:
        n_invalid = sum(1 for s in merged.get('sites', [])
                        if s.get('_dead_reason') == 'invalid_content')
        if n_invalid:
            print('  [deep] %d 个站点返回 200 但内容无效(空 list/错误页), 已判失效'
                  % n_invalid)

    # ---- 熔断: 连接级失败占比过高 = 本轮网络异常, 放弃黑名单写入 ----
    # (一次断网/限速的构建会把几百个源推到阈值永久拉黑; 产物由安全阀兜底,
    #  黑名单没有兜底, 所以这里整体恢复探测前的计数)
    n_probed = len(probe_urls)
    n_conn = sum(1 for u in probe_urls
                 if url_map[u][0] is False and url_map[u][2] == 'conn_error')
    tripped = (breaker_ratio > 0 and n_probed >= breaker_min_probes
               and n_conn >= n_probed * breaker_ratio)
    if tripped:
        blacklist.clear()
        blacklist.update(snapshot)
        clean_stale()   # 僵尸清理与网络无关, 重新应用
        print('[blacklist] 熔断: 本轮 %d/%d 连接失败(%.0f%% >= %.0f%%), '
              '判定网络异常, 已放弃本轮黑名单写入(恢复探测前计数)'
              % (n_conn, n_probed, n_conn * 100.0 / max(1, n_probed),
                 breaker_ratio * 100))

    return {
        'probed': n_probed, 'conn_error': n_conn, 'tripped': tripped,
        'retried': len(retried),
        'retry_recovered': sum(1 for u in retried if url_map[u][0]),
        'warned': len(warned), 'invalid': n_invalid,
    }


# ----------------------------------------------------------------------------
# 3.5 失败黑名单 (连续失败达阈值即拉黑, 后续构建跳过探测; 可手动释放)
# ----------------------------------------------------------------------------
def load_blacklist_file(path):
    """读取完整黑名单文件: {threshold, entries(探测), fetch_entries(拉取)}。
       文件不存在或损坏则返回空 dict。"""
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def load_blacklist(path):
    """读取探测失败计数, 返回 {url: {...}}。"""
    entries = load_blacklist_file(path).get('entries')
    return entries if isinstance(entries, dict) else {}


def save_blacklist_file(path, entries, fetch_entries, threshold,
                        retry_days=7):
    """持久化黑名单(探测 + 拉取两类), 用原子写避免半截文件。
       retry_days 一并落盘: 管理页据此计算"距自动重探 N 天"倒计时。"""
    data = {
        'updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'threshold': threshold,
        'retry_days': int(retry_days or 0),
        'entries': entries,
        'fetch_entries': fetch_entries,
    }
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


# ----------------------------------------------------------------------------
# 3.6 手动禁用清单 (页面搜索后手动禁用某源, 构建时跳过)
# ----------------------------------------------------------------------------
def load_disable_list(path):
    """读取手动禁用清单: {updated, items:[{key,name,api,ext,added}]}。
       文件不存在或损坏返回空 list。"""
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        items = d.get('items') if isinstance(d, dict) else None
        return items if isinstance(items, list) else []
    except Exception:
        return []


def apply_disable_list(merged, disable_items):
    """从 merged 的 sites/lives/parses 中移除命中禁用清单的条目。
       匹配: api / key / name 任一精确命中即移除。返回移除条数。"""
    if not disable_items:
        return 0
    apis, keys, names = set(), set(), set()
    for it in disable_items:
        if not isinstance(it, dict):
            continue
        a = str(it.get('api', '') or '').strip()
        k = str(it.get('key', '') or '').strip()
        n = str(it.get('name', '') or '').strip()
        if a: apis.add(a)
        if k: keys.add(k)
        if n: names.add(n)
    removed = 0
    for cat in ('sites', 'lives', 'parses'):
        lst = merged.get(cat)
        if not isinstance(lst, list):
            continue
        kept = []
        for x in lst:
            if not isinstance(x, dict):
                kept.append(x); continue
            xa = str(x.get('api', '') or '').strip()
            xk = str(x.get('key', '') or '').strip()
            xn = str(x.get('name', '') or '').strip()
            if (xa and xa in apis) or (xk and xk in keys) or (xn and xn in names):
                removed += 1; continue
            kept.append(x)
        merged[cat] = kept
    return removed


def save_disable_list(path, items):
    """持久化手动禁用清单。"""
    data = {
        'updated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'items': items,
    }
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def load_blacklist_data():
    """读取完整黑名单 dict (含 entries / fetch_entries / threshold)。"""
    try:
        with open(BLACKLIST_FILE, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def load_disable_data():
    """读取手动禁用清单 dict。"""
    try:
        with open(DISABLE_FILE, encoding='utf-8') as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}
