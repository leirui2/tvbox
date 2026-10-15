# -*- coding: utf-8 -*-
"""产物输出与构建间观测。

- split_alive           拆出 主源(存活+客户端依赖) / 稳妥源(仅实测存活) / 审计源(全量)
- write_commented_json  失效源以 /* */ 注释保留(人可读可编辑, 客户端读取时忽略注释)
- write_report          探测报告(report.json 机器可读 + report.md 人读; 含不稳定源、
                        源文件贡献度统计)
- site_diff             与上次构建的站点快照对比, 输出新增/移除; 身份标识刻意不含
                        jar/ext(会被清洗与兜底改写, 含了会产生虚假 diff)
- current_counts / read_report_summary / read_report_lists   管理页读取产物统计
"""
import datetime
import json
import os
import re

from . import OUT_DIR

# 健康分级后缀: 近期连续失败但本次探测恢复的站点, name 追加此后缀
WARN_SUFFIX = ' [⚠]'

def _site_identity(s):
    """站点身份标识: 优先 key(csp_xxx / 固定 key), 否则 api+name。
       刻意不含 jar/ext —— 这些字段会被清洗与兜底改写, 会造成虚假 diff。"""
    k = str(s.get('key', '') or '').strip()
    if k:
        return 'key:' + k
    api = str(s.get('api', '') or '').strip()
    nm = str(s.get('name', '') or '').strip()
    return 'api:%s|name:%s' % (api, nm)


def site_diff(merged, snapshot_path, verbose=True):
    """站点增删 diff: 与上次构建的站点快照对比, 输出新增/移除数量与样本,
       随后更新快照。快照损坏或缺失时按首次建立处理。
       返回 (added, removed) 两个 {identity: name} 字典。"""
    cur = {}
    total = 0
    for s in merged.get('sites') or []:
        if isinstance(s, dict):
            total += 1
            cur.setdefault(_site_identity(s), str(s.get('name', '') or ''))
    prev = {}
    if os.path.exists(snapshot_path):
        try:
            with open(snapshot_path, encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                prev = {str(k): str(v or '') for k, v in loaded.items()}
        except Exception:
            prev = {}
    added = {k: cur[k] for k in cur if k not in prev} if prev else {}
    removed = {k: prev[k] for k in prev if k not in cur} if prev else {}
    try:
        with open(snapshot_path, 'w', encoding='utf-8') as f:
            json.dump(cur, f, ensure_ascii=False, indent=1, sort_keys=True)
    except Exception:
        pass   # 快照写失败不影响构建
    if verbose:
        if not prev:
            print('[diff] 首次建立站点快照: 唯一标识 %d / 站点条目 %d -> %s'
                  % (len(cur), total, os.path.basename(snapshot_path)))
        else:
            print('[diff] 站点变化: 新增 %d, 移除 %d  '
                  '(唯一标识 %d -> %d; 站点条目 %d, 其中 %d 个 key 重复)'
                  % (len(added), len(removed), len(prev), len(cur),
                     total, total - len(cur)))
            for _k, nm in list(added.items())[:8]:
                print('        + %s' % (nm or _k)[:60])
            for _k, nm in list(removed.items())[:8]:
                print('        - %s' % (nm or _k)[:60])
            if len(added) > 8 or len(removed) > 8:
                print('        (仅显示前 8 条; 完整差集可对比 %s)'
                      % os.path.basename(snapshot_path))
    return added, removed


# ----------------------------------------------------------------------------
# 4. 输出
# ----------------------------------------------------------------------------
def _dup_cfg(cfg):
    """对配置做"一层拷贝": 顶层 dict、各列表、以及每个列表项 dict 都是新对象,
       但列表项内部的嵌套结构仍共享引用。
       用于 split_alive 生成三份互不影响的副本 —— 比整份 json 深拷贝快得多,
       且 clean() 只 pop 列表项的顶层字段, 不会串改其它两份。"""
    out = {}
    for k, v in cfg.items():
        if isinstance(v, list):
            out[k] = [dict(x) if isinstance(x, dict) else x for x in v]
        elif isinstance(v, dict):
            out[k] = dict(v)
        else:
            out[k] = v
    return out


def split_alive(merged):
    """拆成三份:
       alive  -> 实测存活 + 客户端依赖(csp_) 都在, 失效已剔除  [主源]
       lite   -> 只保留实测存活, 零客户端爬虫依赖               [稳妥源]
       full   -> 全量, 含 _dead 标记                             [审计用]
    """
    alive = _dup_cfg(merged)
    lite = _dup_cfg(merged)
    full = _dup_cfg(merged)

    def clean(items, mode):
        """mode: 'drop_dead' | 'probed_only' | 'keep_all'"""
        out = []
        for it in items:
            is_dead = it.get('_dead') is True
            chk = it.get('_check')
            probed_ok = it.get('_alive') is True
            if mode == 'drop_dead' and is_dead:
                continue
            if mode == 'probed_only' and (is_dead or chk in ('client', 'unknown')):
                continue
            it.pop('_alive', None)
            it.pop('_check', None)
            it.pop('_src', None)
            if mode != 'keep_all':
                it.pop('_dead', None)
                it.pop('_dead_reason', None)
                it.pop('_dead_status', None)
            # 健康分级: 本次存活但近期连续失败过 -> name 加 [⚠](幂等, 防重复追加)
            if it.pop('_warn', None):
                nm = str(it.get('name', '') or '')
                if nm and not nm.endswith(WARN_SUFFIX):
                    it['name'] = nm + WARN_SUFFIX
            out.append(it)
        return out

    for cat in ('sites', 'lives', 'parses'):
        if cat in alive:
            alive[cat] = clean(alive[cat], 'drop_dead')
            lite[cat] = clean(lite[cat], 'probed_only')
            full[cat] = clean(full[cat], 'keep_all')
    if 'sites' in alive:
        alive['sites'].sort(key=lambda s: s.get('_rt', 999))
    if 'sites' in lite:
        lite['sites'].sort(key=lambda s: s.get('_rt', 999))
    if 'parses' in alive:
        alive['parses'].sort(key=lambda p: p.get('_rt', 999))
    if 'parses' in lite:
        lite['parses'].sort(key=lambda p: p.get('_rt', 999))
    if 'lives' in alive:
        alive['lives'].sort(key=lambda l: l.get('_prt', 0) or 999)
    if 'lives' in lite:
        lite['lives'].sort(key=lambda l: l.get('_prt', 0) or 999)
    return alive, lite, full


def _item_id(cat, it):
    """条目的稳定标识(用于跨次运行对比状态)。"""
    if cat == 'sites':
        return str(it.get('key') or it.get('name') or '')
    return str(it.get('url') or it.get('name') or '')


def write_report(merged, path_json, path_md, out_dir, fetch_dead=None):
    """fetch_dead: 远程源(dc.txt)里连续拉取失败已达阈值的条目
                   [{url, name, fail_count, last_fail}], 报告里给出"建议移除"清单。
                   后续构建不再抓取它们(仍用本地缓存), 但缓存可能也越来越旧,
                   因此建议尽快从 dc.txt 清理。"""
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    dead = {'sites': [], 'lives': [], 'parses': []}
    alive_count = {'sites': 0, 'lives': 0, 'parses': 0}
    client_count = {'sites': 0}
    unstable = []   # 本次存活但近期连续失败过的站点(输出已加 [⚠])
    state_now = {}   # id -> 'alive' | 'dead' | 'client'
    name_of = {}     # id -> 展示名
    for cat in ('sites', 'lives', 'parses'):
        for it in merged.get(cat, []):
            name = it.get('name') or it.get('key') or it.get('url') or '?'
            iid = '%s::%s' % (cat, _item_id(cat, it))
            name_of[iid] = name
            if it.get('_dead'):
                state_now[iid] = 'dead'
                dead[cat].append({
                    'name': name, 'type': it.get('type'),
                    'url': it.get('api') or it.get('url'),
                    'reason': it.get('_dead_reason'), 'status': it.get('_dead_status'),
                })
            elif it.get('_check') in ('client', 'unknown'):
                state_now[iid] = 'client'
                if cat == 'sites':
                    client_count['sites'] += 1
            else:
                state_now[iid] = 'alive'
                alive_count[cat] += 1
                if cat == 'sites' and it.get('_warn'):
                    unstable.append({
                        'name': name, 'key': it.get('key', ''),
                        'url': it.get('api') or it.get('url'),
                        'fail_count': int(it.get('_warn', 0) or 0),
                    })

    # ---- 与上次运行对比, 标出"恢复"与"新失效" ----
    prev_state = {}
    prev_path = os.path.join(out_dir, 'report.json')
    if os.path.exists(prev_path):
        try:
            with open(prev_path, encoding='utf-8') as f:
                prev_state = json.load(f).get('state') or {}
        except Exception:
            prev_state = {}

    recovered, newly_dead = [], []
    if prev_state:
        for iid, st in state_now.items():
            old = prev_state.get(iid)
            if old == 'dead' and st == 'alive':
                recovered.append(iid)
            elif old == 'alive' and st == 'dead':
                newly_dead.append(iid)

    # ---- 按源文件统计贡献度(每个源文件贡献多少存活/失效/拉黑, 供清理决策) ----
    src_stats = {}
    for cat in ('sites', 'lives', 'parses'):
        for it in merged.get(cat, []) or []:
            if not isinstance(it, dict):
                continue
            src = str(it.get('_src', '') or '(未知)')
            st = src_stats.setdefault(src, {
                'src': src, 'sites': 0, 'sites_alive': 0, 'sites_dead': 0,
                'sites_blacklisted': 0, 'sites_client': 0,
                'lives': 0, 'lives_dead': 0, 'parses': 0, 'parses_dead': 0})
            if cat == 'sites':
                st['sites'] += 1
                if it.get('_dead'):
                    st['sites_dead'] += 1
                    if it.get('_dead_reason') == 'blacklisted':
                        st['sites_blacklisted'] += 1
                elif it.get('_check') in ('client', 'unknown'):
                    st['sites_client'] += 1
                else:
                    st['sites_alive'] += 1
            elif cat == 'lives':
                st['lives'] += 1
                if it.get('_dead'):
                    st['lives_dead'] += 1
            else:
                st['parses'] += 1
                if it.get('_dead'):
                    st['parses_dead'] += 1
    src_rows = sorted(src_stats.values(),
                      key=lambda x: (-x['sites_dead'], x['src']))

    report = {
        'generated_at': now,
        'summary': {
            'sites_alive': alive_count['sites'],
            'sites_dead': len(dead['sites']),
            'sites_client_dependent': client_count['sites'],
            'lives_alive': alive_count['lives'],
            'lives_dead': len(dead['lives']),
            'parses_alive': alive_count['parses'],
            'parses_dead': len(dead['parses']),
            'recovered_vs_last_run': len(recovered),
            'newly_dead_vs_last_run': len(newly_dead),
            'sites_unstable': len(unstable),
            'fetch_dead': len(fetch_dead or []),
        },
        'dead': dead,
        'unstable': unstable,
        'sources': src_rows,
        'recovered': [{'id': i, 'name': name_of.get(i, i)} for i in recovered],
        'newly_dead': [{'id': i, 'name': name_of.get(i, i)} for i in newly_dead],
        'fetch_dead': fetch_dead or [],
        'state': state_now,
    }
    with open(path_json, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    lines = [f'# TVBox 源探测报告  ({now})', '']
    s = report['summary']
    lines.append(f"- 点播 sites: 存活 **{s['sites_alive']}** / 失效 **{s['sites_dead']}** / 客户端依赖 **{s['sites_client_dependent']}**"
                 + (f" / 不稳定 ⚠ **{s['sites_unstable']}**" if s['sites_unstable'] else ""))
    lines.append(f"- 直播 lives: 存活 **{s['lives_alive']}** / 失效 **{s['lives_dead']}**")
    lines.append(f"- 解析 parses: 存活 **{s['parses_alive']}** / 失效 **{s['parses_dead']}**")
    if prev_state:
        lines.append(f"- 相比上次: 恢复 **{s['recovered_vs_last_run']}** 个 / 新增失效 **{s['newly_dead_vs_last_run']}** 个")
    lines.append('')
    if recovered:
        lines.append('## 本次恢复的源 (上次失效, 这次活了)')
        for i in recovered:
            lines.append("- `%s`" % name_of.get(i, i))
        lines.append('')
    if newly_dead:
        lines.append('## 本次新失效的源 (上次还活着)')
        for i in newly_dead:
            lines.append("- `%s`" % name_of.get(i, i))
        lines.append('')
    if unstable:
        lines.append('## 不稳定源 (近期连续失败后恢复, 配置中已加 [⚠] 标记)')
        for u in unstable:
            lines.append("- `%s` — 连续失败 %d 次后恢复" % (u['name'], u['fail_count']))
        lines.append('')
    if fetch_dead:
        lines.append('## 远程源健康 (dc.txt 连续拉取失败已达阈值, 建议移除)')
        lines.append('以下条目后续构建不再抓取(仍用本地缓存), 缓存会越来越旧, '
                     '建议尽快从 dc.txt 删除:')
        for f in fetch_dead:
            lines.append("- `%s` — 连续失败 %d 次, 最后失败 %s  url: %s"
                         % (f['name'], f['fail_count'],
                            f.get('last_fail') or '?', f['url']))
        lines.append('')
    for cat, label in (('sites', '点播失效源'), ('lives', '直播失效源'), ('parses', '解析失效源')):
        lines.append(f'## {label}')
        if not dead[cat]:
            lines.append('- 无')
        else:
            for d in dead[cat]:
                lines.append(f"- `{d['name']}` — {d['reason']} (status={d['status']}) url: {d['url']}")
        lines.append('')
    if src_rows:
        lines.append('## 源文件贡献度 (失效最多在前, 供清理决策)')
        lines.append('| 源文件 | 点播 | 存活 | 失效 | 其中拉黑 | 客户端依赖 | 直播 | 解析 |')
        lines.append('|---|---|---|---|---|---|---|---|')
        for r in src_rows[:20]:
            lines.append('| `%s` | %d | %d | %d | %d | %d | %d | %d |'
                         % (r['src'], r['sites'], r['sites_alive'],
                            r['sites_dead'], r['sites_blacklisted'],
                            r['sites_client'], r['lives'], r['parses']))
        lines.append('')
    with open(path_md, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def atomic_write(path, data_str):
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write(data_str)
    os.replace(tmp, path)


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def write_commented_json(merged, path, ts):
    """生成 JSONC 文本: 失效源以 /* ... */ 注释块保留在数组里。
       人可直接查看/编辑; 客户端读取时会忽略注释, 达到"注释掉但保留"的效果。"""
    top = json.loads(json.dumps(merged, ensure_ascii=False))
    top['updated'] = ts

    lines = []
    lines.append('{')
    # 逐个顶层字段输出, 对 sites/lives/parses 做"注释掉失效项"的渲染
    keys = [k for k in top.keys()]
    for ki, k in enumerate(keys):
        v = top[k]
        last = (ki == len(keys) - 1)
        comma = '' if last else ','
        if k in ('sites', 'lives', 'parses') and isinstance(v, list):
            lines.append('  %s: [' % json.dumps(k, ensure_ascii=False))
            alive_chunks, dead_blocks = [], []
            for it in v:
                if it.get('_dead') is True:
                    reason = it.get('_dead_reason')
                    status = it.get('_dead_status')
                    body = json.dumps(it, ensure_ascii=False, indent=2)
                    block = ['    /* ---- 失效, 已注释掉 (reason=%s, status=%s) ---- */'
                             % (reason, status)]
                    for bl in body.splitlines():
                        block.append('    // ' + bl)
                    block.append('    // (下次构建若源恢复, 会自动重新启用)')
                    dead_blocks.append('\n'.join(block))
                else:
                    clean = dict(it)
                    for kk in ('_alive', '_check', '_src', '_dead',
                               '_dead_reason', '_dead_status'):
                        clean.pop(kk, None)
                    # 健康分级: 与 split_alive 保持一致, name 加 [⚠]
                    if clean.pop('_warn', None):
                        nm = str(clean.get('name', '') or '')
                        if nm and not nm.endswith(WARN_SUFFIX):
                            clean['name'] = nm + WARN_SUFFIX
                    alive_chunks.append('    ' + json.dumps(clean, ensure_ascii=False))
            # 存活项之间用逗号连接(不会产生尾逗号), 注释块追加在后面且不带逗号
            parts = ([',\n'.join(alive_chunks)] if alive_chunks else []) + dead_blocks
            lines.append('\n'.join(parts))
            lines.append('  ]%s' % comma)
        else:
            if isinstance(v, (dict, list)):
                body = json.dumps(v, ensure_ascii=False, indent=2)
                body = '\n'.join('  ' + ln for ln in body.splitlines())
                lines.append('  %s: %s%s' % (json.dumps(k, ensure_ascii=False), body, comma))
            else:
                lines.append('  %s: %s%s' % (json.dumps(k, ensure_ascii=False),
                                             json.dumps(v, ensure_ascii=False), comma))
    lines.append('}')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')


def read_report_summary():
    p = os.path.join(OUT_DIR, 'report.json')
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding='utf-8') as f:
            return json.load(f).get('summary')
    except Exception:
        return None


def read_report_lists(limit=10):
    """读取报告里的不稳定源(⚠)、deep 失效源清单、远程拉取失效清单,
       供管理页状态展示。"""
    out = {'unstable': [], 'invalid_content': [], 'fetch_dead': [],
           'fetch_dead_count': 0}
    p = os.path.join(OUT_DIR, 'report.json')
    try:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        out['unstable'] = (d.get('unstable') or [])[:limit]
        out['invalid_content'] = [
            {'name': x.get('name'), 'url': x.get('url')}
            for x in (d.get('dead', {}).get('sites') or [])
            if x.get('reason') == 'invalid_content'][:limit]
        fd = d.get('fetch_dead') or []
        out['fetch_dead_count'] = len(fd)
        out['fetch_dead'] = fd[:limit]
    except Exception:
        pass
    return out


def current_counts():
    """从产物里读当前源数量。"""
    p = os.path.join(OUT_DIR, 'tvbox.json')
    counts = {}
    for name, fn in (('sites', 'tvbox.json'), ('lives', 'tvbox.json'),
                     ('parses', 'tvbox.json')):
        fp = os.path.join(OUT_DIR, fn)
        try:
            with open(fp, encoding='utf-8') as f:
                d = json.load(f)
            counts[name] = len(d.get(name, []))
        except Exception:
            counts[name] = 0
    return counts
