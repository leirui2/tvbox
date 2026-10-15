# -*- coding: utf-8 -*-
"""管理后台的源文件管理: 上传 / URL 抓取 / 直接粘贴 / 校验落盘 / 列表。

落盘前用 jsonio.load_json 做容错解析校验(与构建端同一套标准), 同名文件自动备份
到 sources_backup/。
"""
import datetime
import os
import re
import shutil
import tempfile
import urllib.request

from . import BACKUP_DIR, MAX_UPLOAD, SRC_DIR
from .jsonio import load_json, normalize_url
from .merge import is_source_config
from .probe import load_blacklist_data

# ---------------------------------------------------------------- 源管理
def safe_name(name):
    """文件名白名单, 防目录穿越。"""
    name = os.path.basename((name or '').strip().replace('\\', '/'))
    name = re.sub(r'[^A-Za-z0-9._-]', '_', name).strip('._-')
    if not name:
        name = 'source'
    if not name.lower().endswith('.json'):
        name += '.json'
    return name[:100]


def validate_and_save(filename, raw_bytes):
    """校验 JSON 并落盘, 返回 (ok, message, counts)。
       校验复用 build.load_json(容错解析), 落盘前自动备份同名旧文件。"""
    # 通过临时文件走 build 的容错解析
    import tempfile
    obj = None
    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.json')
        tmp_path = tmp.name
        tmp.write(raw_bytes)
        tmp.close()
        obj = load_json(tmp_path)
    except Exception as e:
        return False, '不是可解析的 JSON: %s' % e, ''
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    is_src = is_source_config(obj)
    counts = ''
    if isinstance(obj, dict):
        n_s = len(obj.get('sites', []) or [])
        n_l = len(obj.get('lives', []) or [])
        n_p = len(obj.get('parses', []) or [])
        counts = 'sites=%d lives=%d parses=%d' % (n_s, n_l, n_p)
        if not is_src:
            return False, ('能解析, 但不含 sites/lives/parses, 不是 TVBox 源配置'), counts

    fname = safe_name(filename)
    dest = os.path.join(SRC_DIR, fname)
    if os.path.abspath(dest) != os.path.abspath(
            os.path.join(SRC_DIR, os.path.basename(dest))):
        return False, '非法路径', ''
    # 备份同名旧文件
    if os.path.exists(dest):
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
        shutil.copy2(dest, os.path.join(
            BACKUP_DIR, '%s.%s.bak' % (fname, stamp)))
    with open(dest, 'wb') as f:
        f.write(raw_bytes)
    return True, '已保存为 %s' % fname, counts


def fetch_url(url):
    """抓取远程源。仅允许 http/https, 限制体积。"""
    if not re.match(r'^https?://', url or '', re.I):
        raise ValueError('只支持 http/https 地址')
    ctx = None
    try:
        import ssl
        ctx = ssl.create_default_context()
    except Exception:
        pass
    req = urllib.request.Request(normalize_url(url), headers={
        'User-Agent': 'Mozilla/5.0 TVBox-Source-Fetcher'})
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        data = r.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise ValueError('远程文件过大(>8MB)')
    if not data.strip():
        raise ValueError('远程内容为空')
    return data


def list_sources():
    data = load_blacklist_data()
    threshold = int(data.get('threshold', 5))
    by_src = {}   # 源文件 -> {'blocked': 已达阈值, 'pending': 累计中}
    for e in (data.get('entries') or {}).values():
        src = e.get('src') or ''
        if not src:
            continue
        d = by_src.setdefault(src, {'blocked': 0, 'pending': 0})
        if int(e.get('fail_count', 0)) >= threshold:
            d['blocked'] += 1
        else:
            d['pending'] += 1
    out = []
    for fn in sorted(os.listdir(SRC_DIR)):
        if not fn.endswith('.json'):
            continue
        fp = os.path.join(SRC_DIR, fn)
        if not os.path.isfile(fp):
            continue
        try:
            sz = os.path.getsize(fp)
            mt = datetime.datetime.fromtimestamp(
                os.path.getmtime(fp)).strftime('%m-%d %H:%M')
        except Exception:
            sz, mt = 0, '?'
        st = by_src.get(fn, {'blocked': 0, 'pending': 0})
        out.append({'name': fn, 'size': sz, 'mtime': mt,
                    'blocked': st['blocked'], 'pending': st['pending']})
    return out
