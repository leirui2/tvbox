# -*- coding: utf-8 -*-
"""容错 JSON 解析与编码处理。

TVBox 的源文件现实得很脏: 带 BOM / // 与 /* */ 注释 / 尾逗号 / 字符串内裸控制字符 /
引号缺失 / GBK 与 UTF-8 混编。本模块提供"尽力解析": 先整体解析+修补, 失败再按条目
抢救; 编码判别以 utf-8 错误率为准, 防止混合编码文件被整文件按 GBK 解码导致全文乱码。
"""
import json
import re
import urllib.parse

# ----------------------------------------------------------------------------
# 1. 容错 JSON 解析
# ----------------------------------------------------------------------------
def strip_comments(text: str) -> str:
    """移除字符串字面量之外的 // 与 /* */ 注释 (JSONC 容错);
       同时把字符串内部的裸控制字符替换成空格 (修复 Invalid control character)。"""
    out = []
    i, n = 0, len(text)
    in_str = False
    esc = False
    while i < n:
        c = text[i]
        if in_str:
            # 字符串内: 裸控制字符 -> 空格; 转义序列保持原样
            if not esc and ord(c) < 0x20:
                out.append(' ')
            else:
                out.append(c)
            if esc:
                esc = False
            elif c == '\\':
                esc = True
            elif c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '/':
            while i < n and text[i] != '\n':
                i += 1
            continue
        if c == '/' and i + 1 < n and text[i + 1] == '*':
            i += 2
            while i + 1 < n and not (text[i] == '*' and text[i + 1] == '/'):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return ''.join(out)


def load_json(path: str):
    """读取并尽力解析一个源文件。
       编码策略(防乱码的关键): gb18030 几乎能解码任意字节序列(替换符恒为 0),
       若无条件让它胜出, "UTF-8 主体混入局部异编码"的混合文件会被整文件按
       GBK 解码, 导致全文乱码。因此: utf-8 替换符极少(<=0.5% 且至多 2 个,
       对应严格 UTF-8 或混合编码)时选 utf-8; 错误率高(整文件就是 GBK)时才
       选 gb18030。整体解析失败则按条目抢救(salvage)。"""
    with open(path, 'rb') as f:
        raw = f.read()
    if raw.startswith(b'\xef\xbb\xbf'):
        raw = raw[3:]

    def _prep(text):
        text = strip_comments(text)
        return re.sub(r',(\s*[}\]])', r'\1', text)   # 尾逗号

    best = None       # (替换字符数, 解析结果)
    salvaged = None
    utf8_cand = None  # (bad, obj, text_len) utf-8 候选, 用于低错误率偏向
    for enc in ('utf-8', 'gb18030'):
        text = _prep(raw.decode(enc, errors='replace'))
        try:
            obj, text2 = _repair_parse(text)
        except Exception:
            # 整体解析失败 -> 尝试按条目抢救
            if salvaged is None:
                salvaged = salvage_config(text)
            continue
        bad = text2.count('�')
        if best is None or bad < best[0]:
            best = (bad, obj)
        if enc == 'utf-8':
            utf8_cand = (bad, obj, len(text))
    # utf-8 低错误率偏向: 严格 UTF-8(bad=0)或混合编码(局部异编码)都保 utf-8,
    # 只有整文件 GBK(错误率远超 0.5%)才让 gb18030 胜出
    if utf8_cand and utf8_cand[0] <= max(2, utf8_cand[2] // 200):
        best = (utf8_cand[0], utf8_cand[1])
    if best is None:
        if salvaged and any(salvaged.get(k) for k in ('sites', 'lives', 'parses')):
            return salvaged
        raise ValueError('无法解析 (整体解析失败且抢救无收获)')
    return best[1]


def extract_objects(text, arr_start):
    """从 text[arr_start]=='[' 开始, 产出每个顶层 {...} 的子串。"""
    objs = []
    i = arr_start + 1
    n = len(text)
    while i < n:
        c = text[i]
        if c == '{':
            depth = 0
            in_str = False
            esc = False
            j = i
            while j < n:
                ch = text[j]
                if in_str:
                    if esc:
                        esc = False
                    elif ch == '\\':
                        esc = True
                    elif ch == '"':
                        in_str = False
                else:
                    if ch == '"':
                        in_str = True
                    elif ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            objs.append(text[i:j+1])
                            i = j
                            break
                j += 1
            i = j + 1
        else:
            i += 1
    return objs


def salvage_config(text):
    """整份解析失败时, 逐条提取 sites/lives/parses 里的对象, 好的留下、坏的跳过。
       同时尽力抓取 spider/wallpaper/logo 等标量字段。"""
    out = {}
    for field in ('sites', 'lives', 'parses'):
        m = re.search(r'"%s"\s*:\s*\[' % field, text)
        if not m:
            continue
        items = []
        for obj in extract_objects(text, m.end() - 1):
            try:
                parsed, _ = _repair_parse(obj)
                if isinstance(parsed, dict):
                    items.append(parsed)
            except Exception:
                continue
        if items:
            out[field] = items
    for fld in ('spider', 'wallpaper', 'logo', 'homeLogo'):
        m = re.search(r'"%s"\s*:\s*"((?:[^"\\]|\\.)*)"' % fld, text)
        if m:
            out[fld] = m.group(1)
    return out


def _repair_parse(text):
    """迭代修补: 依据 json 报错位置做最小字符级修复后重试。
       修复: 缺引号(插"), 字符串内控制字符(改空格), 多余引号(删")。"""
    for _ in range(80):
        try:
            return json.loads(text), text
        except json.JSONDecodeError as e:
            msg = e.msg
            pos = e.pos
            if 'property name' in msg:          # 缺左引号: ext": -> "ext":
                text = text[:pos] + '"' + text[pos:]
            elif 'control character' in msg:     # 字符串内裸控制字符
                if pos < len(text) and ord(text[pos]) < 0x20:
                    text = text[:pos] + ' ' + text[pos + 1:]
                else:
                    raise
            elif "',' delimiter" in msg:         # 多余引号: 0"} -> 0}
                if pos < len(text) and text[pos] == '"':
                    text = text[:pos] + text[pos + 1:]
                else:
                    raise
            else:
                raise
    raise ValueError('修补次数超限')


# ----------------------------------------------------------------------------
# 1.5 远程源自动拉取 (dc.txt 等 URL 列表)
# ----------------------------------------------------------------------------
def extract_remote_urls(text):
    """从容错角度解析 dc.txt 这类"多段 JSON + 夹杂 HTML"的文件,
       提取所有 {name, url} 形式的远程源地址, 按 url 去重。"""
    out = []
    seen = set()
    for m in re.finditer(r'\{[^{}]*\}', text):
        try:
            d = json.loads(m.group(0))
        except Exception:
            continue
        u = d.get('url')
        if not isinstance(u, str):
            continue
        u = u.strip()
        if not re.match(r'^https?://', u, re.I):
            continue
        if u in seen:
            continue
        seen.add(u)
        out.append((str(d.get('name', '') or ''), u))
    return out


def normalize_url(url):
    """把 URL 转成 urllib 可请求的 ASCII 形式:
       中文/IDN 域名 -> punycode; 中文路径/参数 -> 百分号编码。
       否则 urllib 遇到非 ASCII 会抛 ascii codec 错误。"""
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return url
    if not parts.scheme or not parts.netloc:
        return url
    host = parts.hostname or ''
    try:
        host = host.encode('idna').decode('ascii')
    except Exception:
        pass
    port = ''
    if parts.port is not None:
        default = {'http': 80, 'https': 443}.get(parts.scheme.lower())
        if parts.port != default:
            port = ':%d' % parts.port
    path = urllib.parse.quote(parts.path, safe="/%:@!$&'()*+,;=~-._")
    query = urllib.parse.quote(parts.query, safe="=&?:/%!$'()*+,;~-._@")
    return urllib.parse.urlunsplit((parts.scheme, host + port, path, query, ''))
