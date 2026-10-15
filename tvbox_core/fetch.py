# -*- coding: utf-8 -*-
"""远程源并发拉取。

从 dc.txt 这类"多段 JSON + 夹杂 HTML"的列表里提取 {name, url}, 并发抓取并缓存到
fetched/(断网时回退上次缓存); 抓取成功才更新缓存, 失败保留旧文件(源不丢失)。
同时持久化 缓存文件 -> 来源 URL 映射(url_map.json), 供 JAR 相对路径解析;
并清理 dc.txt 里已不存在的僵尸拉取黑名单记录。
"""
import datetime
import hashlib
import json
import os
import tempfile
import urllib.error
import urllib.request
import concurrent.futures as cf

from . import emit_prog
from .jsonio import extract_remote_urls, load_json, normalize_url

def _fetch_one(task):
    name, url, cache_path, timeout = task
    try:
        req = urllib.request.Request(
            normalize_url(url),
            headers={'User-Agent': 'Mozilla/5.0 TVBox-Source-Fetcher'})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(4 * 1024 * 1024)        # 单文件上限 4MB
        if not data.strip():
            return (url, False, 'empty_body')
        # 容错解析验证, 避免把网页/错误页当成源缓存下来
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.json')
        tmp.write(data)
        tmp.close()
        try:
            load_json(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
        with open(cache_path, 'wb') as f:
            f.write(data)
        return (url, True, 'ok')
    except Exception as e:
        return (url, False, str(e)[:80])


def fetch_remotes(list_path, cache_dir, timeout, workers, verbose=True,
                  blacklist=None, threshold=5):
    """读取 URL 列表, 并发抓取并缓存到 cache_dir。
       原则: 抓取成功才更新缓存; 抓取失败但本地已有缓存 -> 保留上一份(源不丢失)。
       blacklist: 拉取失败计数(就地修改)。同一远程源连续失败达 threshold 次后,
                  后续构建不再抓取它(仍照常使用其本地缓存); 抓取成功则清零。
       返回缓存文件路径列表。"""
    if blacklist is None:
        blacklist = {}
    if not os.path.exists(list_path):
        if verbose:
            print('[fetch] 列表 %s 不存在, 跳过远程拉取' % list_path)
        return []
    os.makedirs(cache_dir, exist_ok=True)
    text = open(list_path, encoding='utf-8', errors='replace').read()
    entries = extract_remote_urls(text)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    name_map = {url: name for name, url in entries}
    # 清理僵尸拉取记录: dc.txt 里已不存在的 URL 对应的黑名单条目删除,
    # 避免文件无限膨胀, 也避免源被加回 dc.txt 时因陈旧记录被跳过
    stale = [u for u in blacklist if u not in {u2 for _n, u2 in entries}]
    for u in stale:
        blacklist.pop(u, None)
    if stale:
        print('[blacklist] 清理 %d 条远程拉取记录(URL 不在 %s 中), 剩 %d 条'
              % (len(stale), os.path.basename(list_path), len(blacklist)))

    tasks = []
    skipped = []
    for name, url in entries:
        fn = hashlib.sha1(url.encode('utf-8')).hexdigest()[:16] + '.json'
        cp = os.path.join(cache_dir, fn)
        e = blacklist.get(url)
        if e and int(e.get('fail_count', 0)) >= threshold:
            skipped.append((name, url, cp))
        else:
            tasks.append((name, url, cp, timeout))
    if verbose:
        print('[fetch] 从 %s 解析出 %d 个远程源 (跳过黑名单 %d 个), 并发抓取中...'
              % (os.path.basename(list_path), len(entries), len(skipped)))

    ok = 0
    fails = []
    total = len(tasks)
    emit_prog('fetch', 0, total)
    done = 0
    step = max(1, total // 50)
    with cf.ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        for url, success, why in ex.map(_fetch_one, tasks):
            done += 1
            if success:
                ok += 1
                blacklist.pop(url, None)          # 恢复 -> 失败计数清零
            else:
                fails.append((url, why))
                e = blacklist.get(url) or {}
                e['fail_count'] = int(e.get('fail_count', 0)) + 1
                e.setdefault('first_fail', now)
                e['last_fail'] = now
                e['kind'] = 'fetch'
                e['name'] = name_map.get(url, url)
                blacklist[url] = e
            if done % step == 0 or done == total:
                emit_prog('fetch', done, total)
    all_cps = [cp for _, _, cp, _ in tasks] + [cp for _, _, cp in skipped]
    cached = sum(1 for cp in all_cps if os.path.exists(cp))
    if verbose:
        print('[fetch] 本次新抓取成功 %d, 仍可用缓存 %d, 失败 %d'
              % (ok, max(0, cached - ok), len(fails)))
        for url, why in fails[:12]:
            print('        x %s  (%s)' % (url[:72], why))
    # 持久化 缓存文件 -> 来源 URL 映射(合并写入, 不清旧条目):
    # 供 JAR 投票制把源声明的相对路径 spider 解析为绝对 URL
    umap_path = os.path.join(cache_dir, 'url_map.json')
    try:
        old = {}
        if os.path.exists(umap_path):
            loaded = json.load(open(umap_path, encoding='utf-8'))
            if isinstance(loaded, dict):
                old = loaded
        for _name, url, cp in skipped:
            old[os.path.basename(cp)] = url
        for _name, url, cp, _t in tasks:
            old[os.path.basename(cp)] = url
        with open(umap_path, 'w', encoding='utf-8') as f:
            json.dump(old, f, ensure_ascii=False, indent=1, sort_keys=True)
    except Exception:
        pass   # 映射写失败不影响主流程
    return [cp for cp in all_cps if os.path.exists(cp)]
