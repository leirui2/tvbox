# -*- coding: utf-8 -*-
"""多源合并 / 去重。

- merge_configs    标量字段取首个非空; URL 字段收集候选(供后续挑选); 字符串列表取
                   并集; 字典列表按去重键保序去重(首个出现胜出)
- merge_rules      rules 按 host 集合键并集合并(替代 name|host "首个胜出"),
                   regex/rule/filter/script 各字段取并集, 找回被丢弃的独有规则
- clean_promo_names 站点名推广文字清洗(外科手术式: 只删 URL/联系方式/
                   推广关键词/更新日期章, 不动表情竖线技术标记等命名风格)
- dedup_configs    内容完全相同(忽略条目顺序与 _src 等内部标记)的源配置只保留
                   第一个; 加载顺序为 本地源 + fetched/ 缓存, 故本地源天然优先
"""
import hashlib
import json
import re

# ----------------------------------------------------------------------------
# 2. 合并 / 去重
# ----------------------------------------------------------------------------
STRING_FIRST = ('spider', 'wallpaper', 'logo', 'homeLogo', 'warningText',
                'danmaku', 'adblock')


# 这些字段是 URL, 需要额外做"本地地址/失效地址"过滤与可用性挑选
URL_FIELDS = ('spider', 'wallpaper', 'logo', 'homeLogo', 'danmaku')


LIST_STR_UNION = ('flags', 'ijk')


LIST_DICT_DEDUP = {  # 字段 -> 去重键函数
    'sites': lambda x: json.dumps({
        't': x.get('type'),
        'a': str(x.get('api', '') or '').strip(),
        'e': x.get('ext') or '',
        'j': str(x.get('jar', '') or '').strip(),
        'u': str(x.get('url', '') or '').strip(),
    }, ensure_ascii=False, sort_keys=True),
    'lives': lambda x: str(x.get('url', '')).strip().lower(),
    'parses': lambda x: str(x.get('url', '')).strip().lower(),
    'doh': lambda x: str(x.get('url', '')).strip().lower(),
    'ads': lambda x: json.dumps(x, ensure_ascii=False),
    'proxy': lambda x: json.dumps(x, ensure_ascii=False),
    'hosts': lambda x: json.dumps(x, ensure_ascii=False),
}


def is_source_config(d) -> bool:
    return isinstance(d, dict) and any(
        isinstance(d.get(k), list) for k in ('sites', 'lives', 'parses'))


def merge_configs(configs, verbose=False):
    merged = {}
    # 字符串字段: 取第一个非空
    for fld in STRING_FIRST:
        if fld in URL_FIELDS:
            continue  # URL 字段稍后单独处理
        for d in configs:
            v = d.get(fld)
            if v:
                merged[fld] = v
                break
    # URL 字段: 收集所有候选, 供 pick_static_fields 挑选可用地址
    for fld in URL_FIELDS:
        cands = []
        seen = set()
        for d in configs:
            v = d.get(fld)
            if isinstance(v, str) and v.strip() and v.strip() not in seen:
                seen.add(v.strip())
                cands.append(v.strip())
        if cands:
            merged['_cand_' + fld] = cands
    # 字符串列表: 并集去重, 保序
    for fld in LIST_STR_UNION:
        seen = set()
        acc = []
        for d in configs:
            for v in d.get(fld, []) or []:
                if isinstance(v, str) and v not in seen:
                    seen.add(v)
                    acc.append(v)
        if acc:
            merged[fld] = acc
    # 字典列表: 按去重键去重, 保序 (首个出现胜出)
    for fld, keyfn in LIST_DICT_DEDUP.items():
        seen = set()
        acc = []
        for d in configs:
            for item in d.get(fld, []) or []:
                if not isinstance(item, dict):
                    continue
                k = keyfn(item)
                if not k or k in seen:
                    continue
                seen.add(k)
                acc.append(item)
        if acc:
            merged[fld] = acc
    # rules: 按 host 集合并集合并 (regex/rule/filter/script 取并集, 同 host 折叠为一条;
    #        替代原先 name|host "首个胜出" 去重, 详见 merge_rules)
    merged['rules'], rule_stats = merge_rules(configs)
    if not merged['rules']:
        merged.pop('rules')
    elif verbose:
        rescued = '/'.join('%s+%d' % (f, rule_stats[f])
                           for f in RULE_UNION_FIELDS if rule_stats[f])
        print('[merge] rules: %d -> %d 条 (同 host 折叠 %d 条, 找回 %s)'
              % (rule_stats['in'], rule_stats['out'], rule_stats['merged'],
                 rescued or '0'))
    # headers (dict) 合并
    headers = {}
    for d in configs:
        h = d.get('headers')
        if isinstance(h, dict):
            headers.update(h)
    if headers:
        merged['headers'] = headers
    return merged


RULE_UNION_FIELDS = ('regex', 'rule', 'filter', 'script')


def _rule_host_key(rule):
    """规则的合并键: host 字符串, 或 hosts 数组排序去重后拼接。
       无 host/hosts 的匿名规则返回 None (不参与合并, 原样保留)。"""
    host = rule.get('host')
    if isinstance(host, str) and host.strip():
        return host.strip()
    hosts = rule.get('hosts')
    if isinstance(hosts, list):
        vals = sorted({str(h).strip() for h in hosts
                       if isinstance(h, str) and h.strip()})
        if vals:
            return '|'.join(vals)
    return None


def _rule_item_key(x):
    return x if isinstance(x, str) else json.dumps(
        x, ensure_ascii=False, sort_keys=True)


def _merge_rule_arrays(dst, src):
    """把 src 的数组字段并集合并进 dst (保序去重), 返回各字段新增条数。
       始终复制后再追加, 避免污染源配置的共享列表。"""
    added = {}
    for fld in RULE_UNION_FIELDS:
        sv = src.get(fld)
        if not isinstance(sv, list) or not sv:
            continue
        dv = list(dst.get(fld) or [])
        seen = {_rule_item_key(x) for x in dv}
        n = 0
        for x in sv:
            k = _rule_item_key(x)
            if k not in seen:
                seen.add(k)
                dv.append(x)
                n += 1
        if dv:
            dst[fld] = dv
        if n:
            added[fld] = n
    return added


def merge_rules(configs):
    """rules 合并: host 集合相同的规则合并为一条。
       - regex/rule/filter/script 取并集 (保序去重), 找回被"首个胜出"丢弃的独有规则
       - name 等标量字段保留首条非空值
       - 无 host 的匿名规则原样保留, 不参与合并
       - 保序: 输出顺序 = 各规则首次出现的顺序
       返回 (rules 列表, 统计 dict)。"""
    groups = {}   # host_key -> 合并后的规则 (首条为基)
    anon = []     # 匿名规则
    seq = []      # 输出顺序: ('g', key) / ('a', idx)
    stats = {'in': 0, 'out': 0, 'groups': 0, 'merged': 0,
             'regex': 0, 'rule': 0, 'filter': 0, 'script': 0}
    for d in configs:
        for item in d.get('rules') or []:
            if not isinstance(item, dict):
                continue
            stats['in'] += 1
            key = _rule_host_key(item)
            if key is None:
                anon.append(item)
                seq.append(('a', len(anon) - 1))
                continue
            if key not in groups:
                groups[key] = dict(item)
                seq.append(('g', key))
                stats['groups'] += 1
            else:
                stats['merged'] += 1
                for fld, n in _merge_rule_arrays(groups[key], item).items():
                    stats[fld] += n
    out = [groups[ref] if t == 'g' else anon[ref] for t, ref in seq]
    stats['out'] = len(out)
    return out, stats


# ----------------------------------------------------------------------------
# 2.6 站点名推广文字清洗(外科手术式: 只删高置信度残留, 不动命名风格)
# ----------------------------------------------------------------------------
# 设计原则(基于 4727 个真实站点名的统计分析, 2026-10):
#   - 源名 95%+ 是合法命名风格: "❤玩偶｜4K"(表情+竖线)、"xxx[js]/[jar]/[py]"
#     (技术标记)、"📺️腾讯 | 官方"(官方线路描述)、"🔥豆瓣┃推荐" —— 一律不动
#   - 只删四类高置信度残留: URL/域名、联系方式(须带冒号或数字等结构特征,
#     "TG群组搜索"这类真名不会命中)、推广关键词短语、更新日期章
#   - 删完收拾首尾分隔符; 清空则保留原名; 幂等(重复清洗无变化)

_PROMO_PATTERNS = [
    # URL / 域名残留: "导航 http://bhapp.top/" "www.abc.com 影视" "xxx.com"
    re.compile(r'https?://\S+'),
    re.compile(r'\bwww\.[A-Za-z0-9-]+\.[A-Za-z]{2,}\S*'),
    re.compile(r'\b[A-Za-z0-9][A-Za-z0-9-]*\.(?:com|net|org|cc|tv|xyz|top|io|'
               r'me|info|cn|la|pro|site|vip|fun|online|space|website|store|'
               r'tech|art|live|video|kim|pub|buzz|lol|work|shop|club|icu|'
               r'app|dev)(?![A-Za-z0-9-])', re.I),
    # 联系方式(必须带结构特征: 冒号后跟内容 / 5 位以上数字 / @handle)
    re.compile(r'(?:微信|电报|飞机号|telegram|TG群|QQ群|QQ)\s*[:：]\s*\S{1,20}'),
    re.compile(r'[Qq]{2}群?\s*[:：]?\s*\d{5,}'),
    re.compile(r'[@＠][A-Za-z0-9_-]{4,}'),
    # 推广关键词短语
    re.compile(r'永久域名|官网地址|官方网站|最新网址|最新域名|发布地址|发布页|'
               r'备用地址|备用域名|防迷路|防失联|回家地址|回家入口|收藏本站|'
               r'好消息|注册送|招代理|广告招商|招加盟'),
    # 更新日期章: "❤更新10-05❤" / "更新至10.05" / "🔥更新10/05🔥"
    re.compile(r'[❤♥💗💝✨🔥]*\s*更新\s*[至到]?\s*\d{1,2}\s*[-_/月.]\s*\d{1,2}'
               r'\s*[日号]?\s*[❤♥💗💝✨🔥]*'),
]

# 清洗后收拾首尾的分隔符/空白
_SEP_EDGE = re.compile(r'^[\s丨|｜┃║\-—_~·•,，、:：]+|[\s丨|｜┃║\-—_~·•,，、:：]+$')


def clean_promo_name(name):
    """清洗单个站点名里的推广文字。返回 (新名, 是否变化)。
       清洗后为空则保留原名; 幂等。"""
    old = str(name or '')
    if not old.strip():
        return old, False
    new = old
    for pat in _PROMO_PATTERNS:
        new = pat.sub('', new)
    if new != old:
        new = _SEP_EDGE.sub('', new)
        new = re.sub(r'\s{2,}', ' ', new).strip()
        if not new:      # 清空了 -> 保留原名(宁可不洗也别没名字)
            return old, False
    return new, new != old


def clean_promo_names(merged, verbose=True):
    """清洗 merged 的 sites/lives/parses 的 name 推广文字。
       必须在站点 diff 之前调用(清洗确定性, 同一源名每次同一结果,
       diff 身份 api+name 才稳定)。返回统计 {in, changed, samples}。"""
    stats = {'in': 0, 'changed': 0, 'samples': []}
    for cat in ('sites', 'lives', 'parses'):
        for it in merged.get(cat, []) or []:
            if not isinstance(it, dict):
                continue
            stats['in'] += 1
            old_nm = str(it.get('name', '') or '')
            new, ch = clean_promo_name(old_nm)
            if ch:
                it['name'] = new
                stats['changed'] += 1
                if len(stats['samples']) < 8:
                    stats['samples'].append((old_nm, new))
    if verbose and stats['changed']:
        print('[names] 推广文字清洗: %d 个条目中清洗 %d 个'
              % (stats['in'], stats['changed']))
        for old_nm, new in stats['samples']:
            print('        %s -> %s' % (old_nm[:36], new[:36]))
    return stats


CONFIG_CONTENT_FIELDS = ('sites', 'lives', 'parses', 'rules', 'doh', 'flags',
                         'ijk', 'hosts', 'ads', 'proxy', 'spider', 'wallpaper',
                         'logo', 'homeLogo', 'danmaku', 'headers')


def _strip_internal(x):
    """递归剥离 _ 开头的内部标记字段 (_src/_alive 等), 用于内容比对。"""
    if isinstance(x, dict):
        return {k: _strip_internal(v) for k, v in x.items()
                if not (isinstance(k, str) and k.startswith('_'))}
    if isinstance(x, list):
        return [_strip_internal(v) for v in x]
    return x


def _canon_json(x):
    return json.dumps(_strip_internal(x), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), default=str)


def config_fingerprint(d):
    """源配置的内容指纹: 对 sites/lives/parses/rules 等内容字段做规范化
       (剥离内部标记 + 列表排序, 忽略条目顺序) 后哈希。
       内容完全相同的配置视为重复源。无内容字段时返回 None。"""
    parts = []
    for fld in sorted(CONFIG_CONTENT_FIELDS):
        v = d.get(fld)
        if not v:
            continue
        if isinstance(v, list):
            parts.append(fld + ':' + ','.join(sorted(_canon_json(x) for x in v)))
        else:
            parts.append(fld + ':' + _canon_json(v))
    if not parts:
        return None
    return hashlib.sha1('|'.join(parts).encode('utf-8')).hexdigest()


def dedup_configs(configs, verbose=False):
    """配置级去重: 内容完全相同(忽略条目顺序与 _src 等内部标记)的源配置
       只保留第一个。加载顺序为 本地源 + fetched/ 缓存, 故本地源天然优先。
       同一线路被多处引用/重复缓存时会产出完全相同的配置, 提前去重可减少
       后续合并与探测开销。返回 (去重后的 configs, 统计 dict)。"""
    kept, dropped = [], []
    seen = {}   # fingerprint -> 保留的源名
    for d in configs:
        fp = config_fingerprint(d)
        name = str(d.get('_src', '') or '')
        if fp is None:
            kept.append(d)
            continue
        if fp in seen:
            dropped.append((name, seen[fp]))
        else:
            seen[fp] = name
            kept.append(d)
    stats = {'in': len(configs), 'out': len(kept), 'dropped': dropped}
    if dropped and verbose:
        print('[dedup] 配置级去重: %d -> %d 个源 (丢弃 %d 个完全相同的配置)'
              % (stats['in'], stats['out'], len(dropped)))
        for name, keep in dropped:
            print('        丢弃 %s (与 %s 内容相同)' % (name or '?', keep or '?'))
    return kept, stats
