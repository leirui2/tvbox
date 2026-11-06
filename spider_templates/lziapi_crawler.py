#!/usr/bin/env python3
"""
针对「量子采集」接口的爬虫实现 —— 演示「爬取 → 解析 → 落盘」完整流程

这个脚本展示了一个 CMS 采集接口爬虫的真实写法：
  1. 爬取分类 (ac=list)
  2. 爬取某个分类下的视频列表 (ac=videolist&t=xxx&pg=n)
  3. 爬取视频详情 + 播放地址 (ac=detail)
  4. 把结果整理成自己可控的片库 JSON

纯标准库，零第三方依赖。

用法：
  python lziapi_crawler.py list            # 拉分类
  python lziapi_crawler.py crawl 1 3       # 爬分类1，翻3页
  python lziapi_crawler.py detail 111482   # 爬单部详情
  python lziapi_crawler.py search 斗破苍穹  # 搜索
"""
import sys
import json
import ssl
import time
import urllib.request
import urllib.parse

API = "https://cj.lziapi.com/api.php/provide/vod/"

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE


def get(url: str) -> dict:
    """抓取接口并解析 JSON —— 这就是爬虫的「抓取 + 解析」两步"""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    resp = urllib.request.urlopen(req, timeout=10, context=ctx)
    return json.loads(resp.read().decode("utf-8", "ignore"))


def add_qs(base: str, **params) -> str:
    sep = "&" if "?" in base else "?"
    return base + sep + urllib.parse.urlencode(params)


def list_classes() -> list:
    """爬分类列表"""
    d = get(add_qs(API, ac="list"))
    return d.get("class", [])


def crawl_class(tid: int, pages: int = 3) -> list:
    """爬某个分类下的视频（翻页）

    注意：量子接口用 tid（不是 t）作为分类参数，这是实测发现的。
    """
    vods = []
    for pg in range(1, pages + 1):
        d = get(add_qs(API, ac="videolist", tid=tid, pg=pg))
        lst = d.get("list", [])
        vods.extend(lst)
        if not lst:
            break  # 空页 = 到底
    return vods


def detail(vod_id) -> dict:
    """爬单部视频详情（含播放地址）"""
    d = get(add_qs(API, ac="detail", ids=vod_id))
    return d.get("list", [{}])[0]


def search(kw: str) -> list:
    """搜索"""
    d = get(add_qs(API, ac="videolist", wd=kw))
    return d.get("list", [])


def extract_play_url(vod: dict) -> list:
    """
    从 vod_play_url 字段解析出可播放的地址列表。
    原始格式（注意含多播放源分组，用 $$$ 分隔）:
      '国语中字$https://...m3u8#粤语中字$https://...m3u8$$$国语中字$https://...'
    解析成:   [{'name': '国语中字', 'url': 'https://...m3u8'}, ...]
    这就是「字符串匹配提取结构化数据」的核心，要处理 $$$ 分组脏数据。
    """
    raw = vod.get("vod_play_url", "") or ""
    result = []
    # 先按 $$$ 拆多播放源，再按 # 拆集数，再按 $ 拆 名称/链接
    for group in raw.split("$$$"):
        for block in group.split("#"):
            if "$" in block:
                name, url = block.split("$", 1)
                if url.startswith("http"):
                    result.append({"name": name.strip(), "url": url.strip()})
    return result


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"

    if cmd == "list":
        classes = list_classes()
        print(f"共 {len(classes)} 个分类:\n")
        for c in classes:
            print(f"  {c['type_id']:>3}  {c['type_name']}")

    elif cmd == "crawl":
        tid = int(sys.argv[2]) if len(sys.argv) > 2 else 1
        pages = int(sys.argv[3]) if len(sys.argv) > 3 else 3
        print(f"爬取分类 {tid}，翻 {pages} 页...")
        vods = crawl_class(tid, pages)
        print(f"共 {len(vods)} 部:\n")
        for v in vods[:20]:
            print(f"  [{v['vod_id']}] {v['vod_name']}  ({v.get('vod_remarks','')})")

    elif cmd == "detail":
        vid = sys.argv[2]
        v = detail(vid)
        plays = extract_play_url(v)
        print(f"片名: {v.get('vod_name')}")
        print(f"封面: {v.get('vod_pic')}")
        print(f"简介: {(v.get('vod_blurb') or v.get('vod_content') or '')[:80]}")
        print(f"\n播放地址 {len(plays)} 条:")
        for p in plays[:10]:
            print(f"  {p['name']}: {p['url'][:80]}")

    elif cmd == "search":
        kw = sys.argv[2]
        vods = search(kw)
        print(f"搜索「{kw}」共 {len(vods)} 条:")
        for v in vods[:10]:
            print(f"  [{v['vod_id']}] {v['vod_name']}  ({v.get('vod_remarks','')})")

    else:
        print(__doc__)
