#!/usr/bin/env python3
"""
TVBox CMS 采集通用引擎 —— 适配多个苹果 CMS 采集接口的统一爬虫核心

设计目标：
  38 个接口虽然都是 MacCMS 标准，但细节有差异。本引擎自动适配这些差异，
  让上层（聚合器 aggregate.py）只需调用统一函数，不用关心每个站的参数名。

自动适配的差异：
  1. 地址拼接：接口地址带 ?ac=list 还是不带 -> 自动用 ? 或 & 拼接
  2. 分类参数名：t 还是 tid -> 自动探测（先试 t，失败试 tid）
  3. 字段差异：total vs recordcount -> 统一解析
  4. 播放地址：$$$ 多播放源分隔符 -> 统一解析成干净列表
  5. 容错：超时、重试、失败标记 -> 单站失败不影响整体

纯标准库，零第三方依赖。

用法示例：
    from engine import CmsEngine
    eng = CmsEngine('cms_upstreams_dedup.json')
    eng.fetch_classes('量子')              # 拉某站分类
    eng.fetch_list('量子', 1, 1)           # 拉某站分类1第1页
    eng.search('量子', '斗破苍穹')         # 搜索
    eng.fetch_detail('量子', 111482)       # 详情
    eng.health_check()                     # 全站健康检测
"""
import json
import ssl
import time
import urllib.request
import urllib.parse
from urllib.parse import urlparse


class CmsEngine:
    def __init__(self, config_path="cms_upstreams_dedup.json", timeout=8, retries=1):
        self.timeout = timeout
        self.retries = retries
        self.ctx = ssl.create_default_context()
        self.ctx.check_hostname = False
        self.ctx.verify_mode = ssl.CERT_NONE

        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        self.upstreams = data.get("upstreams", [])

        # 建名称 -> 接口 的索引，支持用中文名或地址查找
        self._by_name = {}
        self._by_api = {}
        for u in self.upstreams:
            self._by_name[u["name"]] = u
            self._by_api[u["api"]] = u

        # 参数名探测缓存：api -> 't'/'tid'，避免每次调用都重复探测
        self._tid_param_cache = {}
        # 搜索参数缓存：api -> 'wd'/'search'
        self._search_param_cache = {}

    # ---------- 内部工具 ----------

    def _lookup(self, ref) -> dict:
        """按名称或地址找到接口配置"""
        if ref in self._by_name:
            return self._by_name[ref]
        if ref in self._by_api:
            return self._by_api[ref]
        # 也支持模糊匹配名称（含关键字即可）
        for name, u in self._by_name.items():
            if ref in name:
                return u
        raise KeyError(f"未找到接口: {ref}")

    def _build_url(self, api: str, **params) -> str:
        """拼接请求 URL，自动处理 ? 和 & """
        # 接口地址里可能已经带了 ?ac=list 之类，需区分
        base = api
        if "?" in base:
            # 已有查询串，去掉尾部可能残留的空 ac 参数，用 & 续接
            base = base.rstrip("&")
            sep = "&"
        else:
            sep = "?"
        qs = urllib.parse.urlencode(params)
        return base + sep + qs if qs else base

    def _http_get(self, url: str) -> bytes:
        """带重试的 HTTP GET，返回原始字节"""
        last_err = None
        for _ in range(self.retries + 1):
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                resp = urllib.request.urlopen(req, timeout=self.timeout, context=self.ctx)
                return resp.read()
            except Exception as e:
                last_err = e
                time.sleep(0.3)
        raise last_err

    def _get_json(self, api: str, **params) -> dict:
        """抓取并解析 JSON，失败返回 {'_err': ...}"""
        url = self._build_url(api, **params)
        try:
            raw = self._http_get(url)
            return json.loads(raw.decode("utf-8", "ignore"))
        except Exception as e:
            return {"_err": str(e)[:80]}

    # ---------- 分类参数名自动探测 ----------

    def _detect_tid_param(self, api: str) -> str:
        """
        探测分类参数名是 t 还是 tid。
        标准 MacCMS 用 t，但部分接口（如量子）用 tid。
        返回 't' 或 'tid'。

        判断标准：返回条目数更多的那个参数才是对的。
        （部分接口用错误参数时也会返回「最新1条」而非空，所以不能用 >0 判断）
        结果会缓存，避免重复探测。
        """
        if api in self._tid_param_cache:
            return self._tid_param_cache[api]

        d_t = self._get_json(api, ac="videolist", t=1, pg=1)
        d_tid = self._get_json(api, ac="videolist", tid=1, pg=1)
        n_t = len(d_t.get("list", [])) if "_err" not in d_t else 0
        n_tid = len(d_tid.get("list", [])) if "_err" not in d_tid else 0
        # 条目多的参数胜出；一样多则默认标准 t
        result = "tid" if n_tid > n_t else "t"
        self._tid_param_cache[api] = result
        return result

    def _detect_search_param(self, api: str) -> str:
        """
        探测搜索参数名是 wd 还是 search。
        标准 MacCMS 用 wd，但部分接口用 search。
        返回 'wd' 或 'search'。结果缓存。
        """
        if api in self._search_param_cache:
            return self._search_param_cache[api]

        kw = "斗破苍穹"
        d_wd = self._get_json(api, ac="videolist", wd=kw)
        d_search = self._get_json(api, ac="videolist", search=kw)
        n_wd = len(d_wd.get("list", [])) if "_err" not in d_wd else 0
        n_search = len(d_search.get("list", [])) if "_err" not in d_search else 0
        # 条目多的参数胜出；一样多则默认标准 wd
        result = "search" if n_search > n_wd else "wd"
        self._search_param_cache[api] = result
        return result

    # ---------- 对外统一接口 ----------

    def fetch_classes(self, ref) -> list:
        """拉分类列表，返回 [{'type_id':.., 'type_name':..}, ...]"""
        u = self._lookup(ref)
        d = self._get_json(u["api"], ac="list")
        if "_err" in d:
            return []
        classes = d.get("class", []) or d.get("list", [])
        out = []
        for c in classes:
            if isinstance(c, dict):
                out.append({
                    "type_id": c.get("type_id") or c.get("type"),
                    "type_name": c.get("type_name") or c.get("name", ""),
                })
        return out

    def fetch_list(self, ref, tid, page=1) -> list:
        """
        拉某个分类下的视频列表。
        自动探测参数名（t/tid），返回标准化条目列表。
        """
        u = self._lookup(ref)
        param = self._detect_tid_param(u["api"])
        d = self._get_json(u["api"], ac="videolist", **{param: tid, "pg": page})
        if "_err" in d:
            return []
        return self._normalize_vod_list(d.get("list", []))

    def search(self, ref, keyword) -> list:
        """搜索，返回标准化条目列表"""
        u = self._lookup(ref)
        param = self._detect_search_param(u["api"])
        d = self._get_json(u["api"], ac="videolist", **{param: keyword})
        if "_err" in d:
            return []
        return self._normalize_vod_list(d.get("list", []))

    def fetch_detail(self, ref, vod_id) -> dict:
        """拉单部详情（含播放地址），返回标准化 dict"""
        u = self._lookup(ref)
        d = self._get_json(u["api"], ac="detail", ids=vod_id)
        if "_err" in d:
            return {}
        lst = d.get("list", [])
        if not lst:
            return {}
        vod = lst[0]
        return {
            "vod_id": vod.get("vod_id"),
            "vod_name": vod.get("vod_name"),
            "vod_pic": vod.get("vod_pic"),
            "vod_blurb": vod.get("vod_blurb") or vod.get("vod_content", ""),
            "vod_year": vod.get("vod_year"),
            "vod_area": vod.get("vod_area"),
            "vod_remarks": vod.get("vod_remarks"),
            "play_urls": self.parse_play_url(vod),
        }

    # ---------- 标准化 ----------

    @staticmethod
    def _normalize_vod_list(raw_list: list) -> list:
        """把各接口的字段差异统一成标准条目"""
        out = []
        for v in raw_list:
            if not isinstance(v, dict):
                continue
            out.append({
                "vod_id": v.get("vod_id"),
                "vod_name": v.get("vod_name"),
                "vod_pic": v.get("vod_pic"),
                "vod_remarks": v.get("vod_remarks"),
                "vod_year": v.get("vod_year"),
                "vod_area": v.get("vod_area"),
            })
        return out

    @staticmethod
    def parse_play_url(vod: dict) -> list:
        """
        解析 vod_play_url 字段，处理 $$$ 多播放源分组。
        返回 [{'name': '第01集', 'url': 'https://...'}, ...]
        """
        raw = vod.get("vod_play_url", "") or ""
        result = []
        for group in raw.split("$$$"):
            for block in group.split("#"):
                if "$" in block:
                    name, url = block.split("$", 1)
                    if url.strip().startswith("http"):
                        result.append({"name": name.strip(), "url": url.strip()})
        return result

    # ---------- 健康检测 ----------

    def health_check(self) -> dict:
        """全站健康检测，返回各接口的存活状态"""
        result = {"alive": [], "dead": [], "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        for u in self.upstreams:
            t0 = time.time()
            d = self._get_json(u["api"], ac="list")
            dt = round(time.time() - t0, 2)
            ok = "_err" not in d and (d.get("class") or d.get("list"))
            item = {"name": u["name"], "api": u["api"], "resp_s": dt if ok else None}
            if ok:
                result["alive"].append(item)
            else:
                item["error"] = d.get("_err", "无数据")
                result["dead"].append(item)
        return result


if __name__ == "__main__":
    eng = CmsEngine()

    print("=== 健康检测 ===")
    hc = eng.health_check()
    print(f"存活 {len(hc['alive'])} / 失效 {len(hc['dead'])}\n")

    # 以量子为例演示完整链路
    print("=== 量子接口演示 ===")
    classes = eng.fetch_classes("量子")
    print(f"分类数: {len(classes)}, 前3个: {[c['type_name'] for c in classes[:3]]}")

    if classes:
        tid = classes[0]["type_id"]
        lst = eng.fetch_list("量子", tid, 1)
        print(f"分类[{classes[0]['type_name']}] 第1页: {len(lst)} 部")

        if lst:
            detail = eng.fetch_detail("量子", lst[0]["vod_id"])
            print(f"详情: {detail.get('vod_name')}, 播放地址 {len(detail.get('play_urls', []))} 条")

    res = eng.search("量子", "斗破苍穹")
    print(f"\n搜索「斗破苍穹」: {len(res)} 条")
