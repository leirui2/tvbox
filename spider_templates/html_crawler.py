#!/usr/bin/env python3
"""
Python 爬虫模板 —— 抓取影视站片库，输出 MacCMS 兼容的 JSON

适用场景：目标站需要翻页、登录、Cookie、复杂反爬，drpy JS 不好写时，
用 Python 更可控。抓到的数据直接生成标准 JSON 源，或喂给自己的后端。

用法：
    python html_crawler.py          # 抓取并输出 site.json
    python html_crawler.py --search 斗破苍穹   # 测试搜索

依赖：pip install requests
"""
import json
import re
import sys
import requests
from urllib.parse import quote

# ============ 配置区：改成你要抓的站 ============
SITE_HOST = "https://example2.com"
LIST_URL = "https://example2.com/list/dianying/page/{page}.html"   # {page} 是页码占位
SEARCH_URL = "https://example2.com/search/{kw}.html"               # {kw} 是关键词占位

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": SITE_HOST,
}
# ==============================================

session = requests.Session()
session.headers.update(HEADERS)


def fetch(url: str, timeout: int = 10) -> str:
    """抓取页面，返回 HTML 文本"""
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = resp.apparent_encoding  # 自动识别编码，避免乱码
    return resp.text


def parse_list(html: str) -> list:
    """
    从列表页 HTML 抠出视频条目。
    这里用正则示例，实际请对照目标站真实 HTML 结构改写。
    返回：[{vod_id, vod_name, vod_pic, vod_remarks}, ...]
    """
    items = []
    # 示例：匹配 <a class="item" href="/play/(\d+).html"><img src="..."><span>片名</span>
    pattern = re.compile(
        r'<a[^>]*href="[^"]*?/(\d+)\.html"[^>]*>.*?'
        r'<img[^>]*src="([^"]+)"[^>]*>.*?'
        r'<span[^>]*class="title"[^>]*>(.*?)</span>',
        re.S,
    )
    for m in pattern.finditer(html):
        items.append({
            "vod_id": m.group(1),
            "vod_pic": m.group(2),
            "vod_name": re.sub(r"<[^>]+>", "", m.group(3)).strip(),
            "vod_remarks": "",
        })
    return items


def parse_detail(html: str) -> list:
    """从详情页抠出播放地址（m3u8/直链）"""
    # 示例：从 script 里正则抠 m3u8
    m = re.search(r'"url"\s*:\s*"([^"]+\.m3u8[^"]*)"', html)
    if m:
        return [{"name": "高清", "url": m.group(1)}]
    return []


def crawl_site(pages: int = 5) -> dict:
    """抓取整站，生成 MacCMS 兼容的 JSON 源结构"""
    vod_list = []
    for page in range(1, pages + 1):
        url = LIST_URL.format(page=page)
        try:
            html = fetch(url)
            items = parse_list(html)
            vod_list.extend(items)
            print(f"  第 {page} 页抓到 {len(items)} 条")
        except Exception as e:
            print(f"  第 {page} 页失败: {e}")
        if not items:
            break  # 抓到空页说明到底了

    # 组装成 TVBox 认识的格式
    result = {
        "code": 1,
        "msg": "数据列表",
        "page": 1,
        "pagecount": 1,
        "limit": "20",
        "total": len(vod_list),
        "list": vod_list,
    }
    return result


def search(kw: str) -> dict:
    """搜索"""
    url = SEARCH_URL.format(kw=quote(kw))
    html = fetch(url)
    items = parse_list(html)
    return {"code": 1, "list": items, "total": len(items)}


if __name__ == "__main__":
    if "--search" in sys.argv:
        kw = sys.argv[sys.argv.index("--search") + 1]
        print(json.dumps(search(kw), ensure_ascii=False, indent=2))
    else:
        data = crawl_site()
        out = json.dumps(data, ensure_ascii=False, indent=2)
        with open("site.json", "w", encoding="utf-8") as f:
            f.write(out)
        print(f"\n完成，共 {data['total']} 条，已写入 site.json")
