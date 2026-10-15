# -*- coding: utf-8 -*-
"""spider JAR 冲突处理(JAR 投票制的完整链路)。

- log_jar_stats       只观测: type:3 站点结构 / 各源 spider 票数 / 冲突暴露面
- apply_jar_fallback  保守兜底: 全局 spider 保持不变, 给"来源 spider 与全局不一致
                       且无自带 jar"的 type:3 站点补 per-site jar; 相对路径 spider
                       按来源 URL(fetched/url_map.json + 本地源内容指纹继承)解析为
                       绝对 URL; 顺带救治源声明的相对路径站点级 jar 死链
- check_jar_urls      产物引用的 jar 地址并发可达性探测
- rescue_dead_jars    死链 jar 自动救治: 全局 spider 的 jar 中已验证存在所需爬虫类
                       才摘除站点 jar(三种情形均安全, 不瞎猜)
- audit_jar_classes   类名审计(增量): 抓"地址活着但 jar 里没有对应爬虫类"的静默
                       故障; 结论按 jar 全值缓存, 只有新 jar 才下载解包
"""
import hashlib
import io
import json
import os
import re
import time
import urllib.request
import zipfile
import concurrent.futures as cf

from .probe import http_check

def log_jar_stats(merged, src_spiders, spider_cands=None):
    """JAR 投票制落地前的统计日志(只观测, 不改变任何行为)。
       输出 type:3 站点结构、各源 spider 的票数(依赖全局 spider 的站点数)、
       当前全局 spider 选择, 以及维持"单全局"方案受 JAR 冲突影响的站点估计。"""
    sites = merged.get('sites') or []
    t3 = [s for s in sites
          if isinstance(s, dict) and str(s.get('type')) == '3']
    with_jar = [s for s in t3 if str(s.get('jar', '') or '').strip()]
    need_global = [s for s in t3 if not str(s.get('jar', '') or '').strip()]
    global_spider = str(merged.get('spider', '') or '').strip()

    votes = {}   # 源 spider -> 依赖全局 spider 的站点数
    no_src = 0
    for s in need_global:
        sp = src_spiders.get(s.get('_src'), '') or ''
        if sp:
            votes[sp] = votes.get(sp, 0) + 1
        else:
            no_src += 1
    mismatch = sum(1 for s in need_global
                   if (src_spiders.get(s.get('_src'), '') or '')
                   not in ('', global_spider))
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])

    print('[JAR] type:3 站点 %d (自带 jar %d / 依赖全局 spider %d)'
          % (len(t3), len(with_jar), len(need_global)))
    if spider_cands:
        print('[JAR] 全局 spider 候选 %d 个, 当前选用: %s'
              % (len(spider_cands), global_spider[:80] or '(无)'))
    else:
        print('[JAR] 当前全局 spider: %s' % (global_spider[:80] or '(无)'))
    for sp, n in ranked[:5]:
        rel = ' [相对路径!]' if sp.startswith('.') else ''
        mark = '  <- 当前全局' if sp == global_spider else ''
        print('        %5d  %s%s%s' % (n, sp[:76], rel, mark))
    if no_src:
        print('[JAR] 注意: %d 个站点来源未声明 spider (无法归票)' % no_src)
    if mismatch:
        print('[JAR] 不投票的代价: %d 个站点的来源 spider 与全局不一致且无自带 jar '
              '(JAR 投票制落地后由 per-site jar 兜底)' % mismatch)
    if ranked and ranked[0][0].startswith('.'):
        print('[JAR] 警告: 票数最高的 spider 是相对路径, 兜底前需改写为绝对 URL')
    return {'t3': len(t3), 'with_jar': len(with_jar),
            'need_global': len(need_global), 'votes': votes,
            'mismatch': mismatch}


def _resolve_jar_url(spider, base_url):
    """把源声明的 spider(可能是相对路径)解析为绝对 URL。
       base_url 为该源配置的来源地址; 相对路径按 urljoin 规则解析,
       与客户端加载原配置时的解析行为一致。无法解析返回 None。"""
    sp = (spider or '').strip()
    if not sp:
        return None
    if re.match(r'^https?://', sp, re.I):
        return sp
    if not base_url:
        return None
    return urllib.parse.urljoin(base_url, sp)


def apply_jar_fallback(merged, src_spiders, jar_urls=None, verbose=True):
    """JAR 投票制第 2 步(保守方案): 不动全局 spider, 给"来源 spider 与全局
       不一致且无自带 jar"的 type:3 站点补 per-site jar 字段。
       - spider 是绝对 URL -> 直接写入
       - spider 是相对路径 -> 用来源 URL(fetched/url_map.json, 本地源可经
         内容相同的 fetched 副本继承)解析为绝对 URL 后写入;
         解析不了(本地源无来源)则跳过并计数, 保持原样
       纯增量: 现有可用站点不受影响。返回统计 dict。"""
    global_spider = str(merged.get('spider', '') or '').strip()
    stats = {'scanned': 0, 'written': 0, 'resolved': 0, 'unresolvable': 0,
             'same_as_global': 0, 'own_jar': 0, 'own_jar_fixed': 0,
             'own_jar_unresolved': 0}
    unresolvable = {}
    own_unresolved = {}
    for s in merged.get('sites') or []:
        if not isinstance(s, dict):
            continue
        own = str(s.get('jar', '') or '').strip()
        # 顺带救治(所有站点类型): 源声明的 jar 是相对路径时, 它在我们的服务器上
        # 必然 404(死链), 能解析则改写为绝对 URL; 解析不了保持原样并计数
        if own.startswith('.') and not re.match(r'^https?://', own, re.I):
            abs_own = _resolve_jar_url(
                own, (jar_urls or {}).get(s.get('_src'), ''))
            if abs_own and abs_own != own:
                s['jar'] = abs_own
                stats['own_jar_fixed'] += 1
            else:
                stats['own_jar_unresolved'] += 1
                own_unresolved[own] = own_unresolved.get(own, 0) + 1
        if str(s.get('type')) != '3':
            continue
        if own:
            stats['own_jar'] += 1
            continue
        src_spider = (src_spiders.get(s.get('_src'), '') or '').strip()
        if not src_spider or src_spider == global_spider:
            if src_spider:
                stats['same_as_global'] += 1
            continue
        stats['scanned'] += 1
        abs_url = _resolve_jar_url(src_spider, (jar_urls or {}).get(s.get('_src'), ''))
        if abs_url:
            s['jar'] = abs_url
            stats['written'] += 1
            if not re.match(r'^https?://', src_spider, re.I):
                stats['resolved'] += 1
        else:
            stats['unresolvable'] += 1
            unresolvable[src_spider] = unresolvable.get(src_spider, 0) + 1
    if verbose and (stats['written'] or stats['unresolvable']
                    or stats['own_jar_fixed'] or stats['own_jar_unresolved']):
        print('[JAR] per-site jar 兜底: %d 个冲突站点' % stats['scanned'])
        print('        写入 jar %d 个 (相对路径解析 %d 个); '
              '无法解析 %d 个 (本地源无来源 URL, 保持原样)'
              % (stats['written'], stats['resolved'], stats['unresolvable']))
        if stats['own_jar_fixed'] or stats['own_jar_unresolved']:
            print('        源自带 jar(死链救治, 含非 type:3): 相对路径改写 %d 个, '
                  '仍无法解析 %d 个 (需补该源来源 URL 或自行托管 jar)'
                  % (stats['own_jar_fixed'], stats['own_jar_unresolved']))
        for sp, n in sorted(unresolvable.items(), key=lambda kv: -kv[1])[:5]:
            print('        未解析: %-46s x%d' % (sp[:46], n))
        for sp, n in sorted(own_unresolved.items(), key=lambda kv: -kv[1])[:5]:
            print('        未解析(自带): %-40s x%d' % (sp[:40], n))
    return stats


def _jar_probe_url(v):
    """jar/spider 字段值 -> 可请求的 URL(去 ;md5; 后缀); 非 http(s) 返回 None。"""
    s = str(v or '').strip()
    if s and re.match(r'^https?://', s, re.I):
        return s.split(';')[0]
    return None


def check_jar_urls(merged, timeout=6, workers=16, verbose=True):
    """对产物中引用的 JAR 地址做可达性探测。
       per-site jar 兜底会写入大量 jar 地址(含由相对路径解析出来的), 其中死链
       会让 type:3 站点依然跑不起来 —— 本函数集中探测去重后的 jar URL, 输出
       可达/失效计数与失效样本, 让"兜底是否真的生效"可观测。
       返回 (alive_urls, dead_urls)。"""
    urls = set()
    for s in merged.get('sites') or []:
        if isinstance(s, dict):
            u = _jar_probe_url(s.get('jar'))
            if u:
                urls.add(u)
    u = _jar_probe_url(merged.get('spider'))
    if u:
        urls.add(u)
    if not urls:
        return set(), set()

    def check(u):
        try:
            st, _sz, _el = http_check(u, timeout)
            return u, 200 <= st < 400
        except Exception:
            return u, False

    alive, dead = set(), set()
    with cf.ThreadPoolExecutor(max_workers=max(1, min(workers, len(urls)))) as ex:
        for u, ok in ex.map(check, sorted(urls)):
            (alive if ok else dead).add(u)
    n_dead_sites = sum(1 for s in merged.get('sites') or []
                       if isinstance(s, dict)
                       and _jar_probe_url(s.get('jar')) in dead)
    if verbose:
        print('[JAR] jar 可达性: 产物引用 %d 个地址, 可达 %d, 失效 %d (影响 %d 个站点)'
              % (len(urls), len(alive), len(dead), n_dead_sites))
        for u in sorted(dead)[:5]:
            print('        失效: %s' % u[:80])
    return alive, dead


JAR_CACHE_TTL_DAYS = 7   # 全局 spider jar 的缓存有效期(类名会随 jar 更新而变)


def _load_jar_bytes(url, cache_path, ttl_days=JAR_CACHE_TTL_DAYS,
                    timeout=30, max_mb=60):
    """下载 jar 并按 TTL 缓存到 cache_path; 缓存新鲜则直接读盘。
       下载失败时回退到旧缓存。返回字节或 None。"""
    if os.path.exists(cache_path):
        try:
            if time.time() - os.path.getmtime(cache_path) < ttl_days * 86400:
                with open(cache_path, 'rb') as f:
                    return f.read()
        except Exception:
            pass
    data = None
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(max_mb * 1024 * 1024)
    except Exception:
        data = None
    if data is None:              # 下载失败: 用旧缓存兜底
        if os.path.exists(cache_path):
            try:
                with open(cache_path, 'rb') as f:
                    return f.read()
            except Exception:
                pass
        return None
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp = cache_path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        os.replace(tmp, cache_path)
    except Exception:
        pass   # 缓存写失败不影响本次使用
    return data


def _jar_class_tokens(data):
    """从 jar 字节提取全部标识符 token(DEX 字符串池 / .class 常量池均适用)。
       TVBox 的 spider jar 多为 DEX 打包(classes*.dex), 类名以 MUTF-8 存在
       字符串池里; 正则提取成集合后即可 O(1) 判断某爬虫类是否存在。"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            blob = b''
            for n in z.namelist():
                if n.endswith('.dex') or n.endswith('.class'):
                    blob += z.read(n)
    except Exception:
        return set()
    if not blob:
        return set()
    return {m.group(0) for m in re.finditer(rb'[A-Za-z_][A-Za-z0-9_$]{2,60}', blob)}


def rescue_dead_jars(merged, dead_urls, cache_dir, timeout=30,
                     ttl_days=JAR_CACHE_TTL_DAYS, verbose=True):
    """死链 jar 自动救治(全自动, 无需人工判断)。
       对 jar 地址已失效的站点: 若全局 spider 的 jar 中**已验证存在**该站点
       api(csp_XXX)对应的爬虫类, 则摘除站点 jar 字段, 使其改走全局 spider。
       三种情况都安全:
       - jar 真死        -> 站点原本就坏, 摘除后修复(严格变好)
       - 探测误判(其实活) -> 全局已验证含此类, 走全局同样可用(无损)
       - 全局没有该类     -> 不动, 保持现状(不瞎猜)
       返回 (rescued, kept)。"""
    if not dead_urls:
        return 0, 0
    gs = _jar_probe_url(merged.get('spider'))
    if not gs:
        return 0, 0
    cache_path = os.path.join(
        cache_dir, hashlib.sha1(gs.encode('utf-8')).hexdigest()[:16] + '.jar')
    data = _load_jar_bytes(gs, cache_path, ttl_days=ttl_days, timeout=timeout)
    if not data:
        if verbose:
            print('[JAR] 死链救治: 全局 spider jar 下载失败(%s), 本轮跳过'
                  % gs[:60])
        return 0, 0
    tokens = _jar_class_tokens(data)
    if not tokens:
        return 0, 0
    rescued = kept = 0
    for s in merged.get('sites') or []:
        if not isinstance(s, dict):
            continue
        u = _jar_probe_url(s.get('jar'))
        if not u or u not in dead_urls:
            continue
        api = str(s.get('api', '') or '')
        cls = api[4:] if api.startswith('csp_') else ''
        if cls and cls.encode('utf-8') in tokens:
            s.pop('jar', None)      # 改走全局 spider(已验证含此类)
            rescued += 1
        else:
            kept += 1               # 无法验证, 保持原样
    if verbose and (rescued or kept):
        print('[JAR] 死链救治: %d 个站点的 jar 已摘除并改走全局 spider'
              '(已验证全局含所需爬虫类); %d 个无法验证, 保持原样'
              % (rescued, kept))
    return rescued, kept


JAR_AUDIT_TTL_DAYS = 30   # 不带 ;md5; 的 jar 审计结论缓存有效期


def audit_jar_classes(merged, cache_dir, alive_urls=None, timeout=20,
                      ttl_days=JAR_AUDIT_TTL_DAYS, workers=8, verbose=True):
    """jar 类名审计(增量, 按需触发): 验证每个站点的自带 jar 里是否真的包含
       其 api(csp_XXX)对应的爬虫类 —— 地址活着但类缺失是静默故障, 可达性探测
       发现不了。处置: 全局 spider 已验证含该类 -> 摘除 jar 改走全局(修复);
       否则保持原样(坏是一样的坏, 不瞎猜)。
       增量: 审计结论按 jar 全值(含 ;md5;)缓存, 内容寻址的永不过期, 其余按
       TTL; 只有缓存里没有的 jar 才下载解包, 日常构建零成本。
       alive_urls: 可达性探测判定为可达的 jar 地址集合; 已探明的死链直接跳过
       (下载必然失败, 且死链处置已由 rescue_dead_jars 负责), 避免每轮白等超时。
       返回 (n_jars, n_downloaded, n_mismatched, n_rescued)。"""
    sites = [s for s in merged.get('sites') or []
             if isinstance(s, dict) and str(s.get('type')) == '3']

    def class_of(s):
        api = str(s.get('api', '') or '')
        return api[4:] if api.startswith('csp_') else ''

    # 1. 汇总: jar 全值 -> {probe_url, 需要的类集合}; 跳过已知死链
    need = {}
    for s in sites:
        jf = str(s.get('jar', '') or '').strip()
        cls = class_of(s)
        if not jf or not cls or not re.match(r'^https?://', jf, re.I):
            continue
        probe = jf.split(';')[0]
        if alive_urls is not None and probe not in alive_urls:
            continue          # 已死或未探明: 交给 rescue_dead_jars, 不重复下载
        e = need.setdefault(jf, {'probe': probe, 'classes': set()})
        e['classes'].add(cls)
    if not need:
        return 0, 0, 0, 0

    def cache_path(jf):
        return os.path.join(
            cache_dir, 'audit_' +
            hashlib.sha1(jf.encode('utf-8')).hexdigest()[:16] + '.json')

    def fresh(entry, jf):
        if ';md5;' in jf:
            return True                      # 内容寻址: 结论永久有效
        try:
            return time.time() - float(entry.get('audited_at') or 0) \
                < ttl_days * 86400
        except Exception:
            return False

    # 2. 读缓存, 分出"已结论"与"待下载审计"
    cached, todo, cache_hits = {}, [], 0
    for jf, e in need.items():
        entry = {}
        try:
            with open(cache_path(jf), encoding='utf-8') as f:
                entry = json.load(f) or {}
        except Exception:
            entry = {}
        checked = entry.get('checked') or {}
        missing = e['classes'] - set(checked)
        if not missing and fresh(entry, jf):
            cached[jf] = checked
            cache_hits += 1
        else:
            todo.append((jf, e['probe'], missing or e['classes']))

    # 3. 并发下载 + 解包审计新 jar, 结论合并写回缓存
    downloaded = 0

    def audit_one(item):
        jf, url, classes = item
        try:
            req = urllib.request.Request(url,
                                         headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read(60 * 1024 * 1024)
        except Exception:
            return jf, None
        tokens = _jar_class_tokens(data)
        return jf, {c: (c.encode('utf-8') in tokens) for c in classes}

    if todo:
        with cf.ThreadPoolExecutor(
                max_workers=max(1, min(workers, len(todo)))) as ex:
            for jf, res in ex.map(audit_one, todo):
                downloaded += 1
                if res is None:
                    continue                 # 下载失败: 不写缓存, 下轮重试
                old = {}
                try:
                    with open(cache_path(jf), encoding='utf-8') as f:
                        old = json.load(f) or {}
                except Exception:
                    pass
                checked = old.get('checked') or {}
                checked.update(res)
                try:
                    os.makedirs(cache_dir, exist_ok=True)
                    tmp = cache_path(jf) + '.tmp'
                    with open(tmp, 'w', encoding='utf-8') as f:
                        json.dump({'url': jf, 'audited_at': time.time(),
                                   'checked': checked}, f, ensure_ascii=False)
                    os.replace(tmp, cache_path(jf))
                except Exception:
                    pass
                cached[jf] = checked

    # 4. 判定: jar 有所需类 -> 正常; 没有 -> 全局含类则摘 jar, 否则不动
    gs_tokens = None                          # 延迟加载全局 jar 类名

    def global_has(cls):
        nonlocal gs_tokens
        if gs_tokens is None:
            gs_url = _jar_probe_url(merged.get('spider'))
            data = None
            if gs_url:
                cp = os.path.join(
                    cache_dir,
                    hashlib.sha1(gs_url.encode('utf-8')).hexdigest()[:16]
                    + '.jar')
                data = _load_jar_bytes(gs_url, cp)
            gs_tokens = _jar_class_tokens(data) if data else set()
        return cls.encode('utf-8') in gs_tokens

    mismatched = rescued = 0
    for s in sites:
        jf = str(s.get('jar', '') or '').strip()
        cls = class_of(s)
        if not jf or not cls:
            continue
        checked = cached.get(jf)
        if not checked or cls not in checked:
            continue                          # 未审计/下载失败 -> 不动
        if checked[cls]:
            continue                          # jar 里确实有该类 -> 正常
        mismatched += 1
        if global_has(cls):
            s.pop('jar', None)                # 改走全局 spider(已验证含此类)
            rescued += 1

    if verbose:
        print('[JAR] 类名审计: %d 个 jar(缓存命中 %d, 新审计 %d), '
              '类不匹配 %d 个站点, 改走全局 %d 个'
              % (len(need), cache_hits, downloaded, mismatched, rescued))
    return len(need), downloaded, mismatched, rescued
