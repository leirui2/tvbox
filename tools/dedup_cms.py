#!/usr/bin/env python3
"""
CMS 采集接口去重工具 —— 项目长期复用的源管理工具

职责：
  读入 cms_upstreams.json（原始采集清单），分析重复，产出
  cms_upstreams_dedup.json（去重后的干净清单）。

去重策略（实测驱动，不靠猜）：
  1. 按域名分组，找出同域名下的多个接口。
  2. 对同域名接口，实测对比它们的「分类数据 ac=list」是否一致：
     - 分类完全一致  -> 判定为「真重复」，保留响应最快的一个
     - 分类一致但路径含 from/xxx 播放源后缀 -> 进一步对比搜索结果的
       vod_play_url 前缀，若播放源不同则保留（多一个播放源=多一条后路）
  3. 识别异类格式（XML / 非标准路径），单独归类，不打乱。

用法：
  python tools/dedup_cms.py                 # 全量去重
  python tools/dedup_cms.py --probe         # 先去重前先重新探测存活（默认跳过，用现有 resp_s）

输出：
  cms_upstreams_dedup.json
  含 deduped_upstreams（保留的）、removed_duplicates（被去重的）、
     special_formats（异类格式，需引擎特殊处理）
"""
import sys
import os
import json
import ssl
import time
import hashlib
import urllib.request
import urllib.parse
from collections import defaultdict
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INPUT = os.path.join(BASE_DIR, "cms_upstreams.json")
OUTPUT = os.path.join(BASE_DIR, "cms_upstreams_dedup.json")

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def fetch_json(api: str, params: str) -> dict:
    """抓取接口返回 JSON，失败返回 {'_err': ...}"""
    sep = "&" if "?" in api else "?"
    url = api + sep + params
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        resp = urllib.request.urlopen(req, timeout=10, context=ctx)
        return json.loads(resp.read().decode("utf-8", "ignore"))
    except Exception as e:
        return {"_err": str(e)[:60]}


def get_classes(api: str) -> tuple:
    """返回 (分类签名, 原始分类) —— 分类签名用于判断是否一致"""
    d = fetch_json(api, "ac=list")
    if "_err" in d:
        return ("__err__", [])
    classes = d.get("class", []) or d.get("list", [])
    # 用分类的 type_id+type_name 生成稳定签名
    sig_items = []
    for c in classes:
        if isinstance(c, dict):
            sig_items.append(f"{c.get('type_id')}|{c.get('type_name')}")
    return (hashlib.md5("|".join(sorted(sig_items)).encode()).hexdigest(), classes)


def get_play_prefix(api: str) -> str:
    """搜索一部常见剧，取第一条的播放地址「域名+路径特征」，用于判断播放源是否相同。

    关键：不能只看 hostname，因为同域名下可能有不同播放源路径
    （如 /share/xxx 和 /20250215/xxx/index.m3u8 是不同的源）。
    """
    d = fetch_json(api, "ac=videolist&wd=" + urllib.parse.quote("斗破苍穹"))
    lst = d.get("list", [])
    if not lst:
        return ""
    url = lst[0].get("vod_play_url", "") or ""
    parts = url.split("#")
    if parts and "$" in parts[0]:
        link = parts[0].split("$", 1)[1]
        host = urlparse(link).hostname or ""
        path = urlparse(link).path or ""
        # 取路径第一段做特征（/share、/20250215 等），区分不同播放源
        seg = path.strip("/").split("/")[0] if path.strip("/") else ""
        return f"{host}/{seg}"
    return ""


def detect_format(api: str) -> str:
    """判断接口返回格式：json / xml / unknown"""
    sep = "&" if "?" in api else "?"
    url = api + sep + "ac=list"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        raw = urllib.request.urlopen(req, timeout=10, context=ctx).read(2000)
    except Exception:
        return "unknown"
    txt = raw.decode("utf-8", "ignore").lstrip()
    if txt.startswith("<") or txt.startswith("<?xml"):
        return "xml"
    if txt.startswith("{") or txt.startswith("["):
        return "json"
    return "unknown"


def hostname(api: str) -> str:
    return urlparse(api).hostname or urlparse(api).netloc


def main():
    with open(INPUT, encoding="utf-8") as f:
        data = json.load(f)
    upstreams = data["upstreams"]

    # 按域名分组
    by_domain = defaultdict(list)
    for u in upstreams:
        by_domain[hostname(u["api"])].append(u)

    kept = []
    removed = []
    special = []

    print(f"原始接口数: {len(upstreams)}，独立域名: {len(by_domain)}\n")

    for domain, group in sorted(by_domain.items(), key=lambda x: -len(x[1])):
        if len(group) == 1:
            # 单域名，判断是否异类格式
            fmt = detect_format(group[0]["api"])
            if fmt != "json":
                u = dict(group[0]); u["format"] = fmt
                special.append(u)
                print(f"[特殊] {u['name']} ({domain}) 格式={fmt}")
            else:
                u = dict(group[0]); u["format"] = "json"
                kept.append(u)
            continue

        # 同域名多个接口，实测分类是否一致
        print(f"[分析] {domain} 有 {len(group)} 个接口:")
        sig_map = {}
        for u in group:
            sig, _ = get_classes(u["api"])
            sig_map[u["api"]] = sig

        # 按分类签名分组
        by_sig = defaultdict(list)
        for u in group:
            by_sig[sig_map[u["api"]]].append(u)

        for sig, sub in by_sig.items():
            if len(sub) == 1:
                # 该域名下唯一分类签名，保留
                fmt = detect_format(sub[0]["api"])
                u = dict(sub[0]); u["format"] = fmt
                (special if fmt != "json" else kept).append(u)
                print(f"    保留 {sub[0]['name']} (格式={fmt})")
            else:
                # 多个接口分类一致，进一步判断播放源
                play_sig = {}
                for u in sub:
                    play_sig[u["api"]] = get_play_prefix(u["api"])
                by_play = defaultdict(list)
                for u in sub:
                    by_play[play_sig[u["api"]]].append(u)

                for psig, ss in by_play.items():
                    # 同一播放源下的多个接口 = 真重复，留最快
                    ss_sorted = sorted(ss, key=lambda x: x.get("resp_s", 999))
                    best = ss_sorted[0]
                    fmt = detect_format(best["api"])
                    u = dict(best); u["format"] = fmt
                    (special if fmt != "json" else kept).append(u)
                    print(f"    保留 {best['name']} (播放源={psig or '未知'}, {best.get('resp_s')}s)")
                    for dup in ss_sorted[1:]:
                        removed.append({"name": dup["name"], "api": dup["api"],
                                        "reason": f"与 {best['name']} 同域名同播放源",
                                        "dup_of": best["api"]})
                        print(f"      去重 {dup['name']} -> 同 {best['name']}")

    # 排序：按响应时间
    kept.sort(key=lambda x: x.get("resp_s", 999))

    result = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_file": os.path.basename(INPUT),
        "original_count": len(upstreams),
        "kept_count": len(kept),
        "removed_count": len(removed),
        "special_count": len(special),
        "upstreams": kept,
        "special_formats": special,
        "removed_duplicates": removed,
    }
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n===== 去重结果 =====")
    print(f"保留: {len(kept)}  去重移除: {len(removed)}  异类格式: {len(special)}")
    print(f"已写入 {OUTPUT}")


if __name__ == "__main__":
    main()
