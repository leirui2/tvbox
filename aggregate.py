#!/usr/bin/env python3
"""
TVBox 源聚合器 —— 把 38 个 CMS 接口聚合成属于你自己的 TVBox 源

职责：
  1. 调用 engine 读取去重后的 38 个接口
  2. 生成标准 TVBox 配置 JSON（sites 指向各接口，TVBox 自己会聚合展示）
  3. 可选：生成一份「合并去重后的片库索引」（按片名去重，合并多源播放线路）
  4. 健康检测，只保留当前存活的接口

产物：
  www/tvbox_self.json    -> 可直接填进 TVBox 的自建源配置

用法：
  python aggregate.py                 # 生成源配置
  python aggregate.py --build-index   # 额外生成合并去重片库索引
  python aggregate.py --probe         # 生成前先健康检测，剔除失效接口

纯标准库，零第三方依赖。
"""
import sys
import os
import re
import json
import time
import hashlib
from engine import CmsEngine

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "www")
OUT_FILE = os.path.join(OUT_DIR, "tvbox_self.json")
INDEX_FILE = os.path.join(OUT_DIR, "vod_index.json")
# 核心 csp_ 源（网盘 + 精选聚合，26 个代表，已固化，不依赖老系统）
CSP_CORE_FILE = os.path.join(BASE_DIR, "csp_core.json")
# 解析器 + 首页推荐配置（从老系统提取固化，67 个 parses + 99 个 flags）
PARSE_FILE = os.path.join(BASE_DIR, "parses_core.json")


def _load_parses_flags() -> tuple:
    """加载解析器和首页推荐配置，返回 (parses, flags)。文件缺失时返回空。"""
    if os.path.exists(PARSE_FILE):
        with open(PARSE_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d.get("parses", []), d.get("flags", [])
    return [], []


def build_sites(engine, probe: bool = False) -> list:
    """把 38 个接口转成 TVBox sites 配置"""
    upstreams = engine.upstreams

    if probe:
        print("健康检测中...")
        hc = engine.health_check()
        alive_apis = {u["api"] for u in hc["alive"]}
        upstreams = [u for u in upstreams if u["api"] in alive_apis]
        print(f"  存活 {len(upstreams)}/{len(engine.upstreams)}，剔除 {len(engine.upstreams)-len(upstreams)} 个")

    sites = []
    for u in upstreams:
        # key 用干净的标识（去掉 emoji、| 符号、多余空格）
        key = (u.get("name", "")
               .replace("🎉", "").replace("🍀", "").replace("🥑", "")
               .replace("🐉", "").replace("🐯", "").replace("🦊", "")
               .replace("🎏", "").replace("🔱", "").replace("🕹️", "")
               .replace("|", "").replace("┃", "").replace("｜", "").replace("～", "")
               .replace("【", "").replace("】", "")
               .strip())
        key = re.sub(r"\s+", "", key)  # 去掉所有空白
        if not key:
            key = u["api"].split("/")[2]

        site = {
            "key": key,
            "name": u["name"],
            "type": 1,
            "api": u["api"],
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 1,
            "changeable": 1,
        }

        # 关键：部分 CMS 站只认 tid 参数（不认 t），TVBox 默认用 t 会导致列表为空。
        # 这类站点必须补 categories（写死分类名），App 才会改用 tid 请求。
        try:
            param = engine._detect_tid_param(u["api"])
        except Exception:
            param = "t"
        if param == "tid":
            classes = engine.fetch_classes(u["name"])
            cat_names = [c["type_name"] for c in classes if c.get("type_name")]
            if cat_names:
                site["categories"] = cat_names
                site["playerType"] = 2  # 标记为 tid 型（与老系统一致）

        sites.append(site)
    return sites


def build_config(engine, probe: bool = False) -> dict:
    """生成完整的 TVBox 配置 JSON"""
    sites = build_sites(engine, probe=probe)
    parses, flags = _load_parses_flags()

    config = {
        "sites": sites,
        # 解析器：很多 CMS 站的 m3u8 是加密/鉴权地址，必须靠 parses 解析才能播
        "parses": parses,
        # 直播源留空，以后可单独加
        "lives": [],
        # 首页推荐：识别 vod_name 里的站点标签（youku/腾讯/爱奇艺 等）走对应线路
        "flags": flags,
    }
    return config


def build_index(engine, max_per_site: int = 50) -> dict:
    """
    生成合并去重片库索引。
    从每个接口拉前 N 部（按最新），按 vod_name 去重，合并多源播放线路。
    注意：这是「索引」不是「完整片库」，完整片库太大（单站就几十万部），
    索引只存最近更新的热门，供首页/推荐用。
    """
    print("构建片库索引（每站拉最新 50 部）...")
    merged = {}  # vod_name -> 条目

    for u in engine.upstreams:
        name = u["name"]
        try:
            classes = engine.fetch_classes(name)
            if not classes:
                continue
            tid = classes[0]["type_id"]
            lst = engine.fetch_list(name, tid, 1)[:max_per_site]
            for v in lst:
                vname = v.get("vod_name", "")
                if not vname:
                    continue
                if vname not in merged:
                    merged[vname] = {
                        "vod_name": vname,
                        "vod_pic": v.get("vod_pic"),
                        "vod_year": v.get("vod_year"),
                        "vod_remarks": v.get("vod_remarks"),
                        "sources": [],  # 多源
                    }
                merged[vname]["sources"].append({
                    "site": name,
                    "vod_id": v.get("vod_id"),
                })
        except Exception as e:
            print(f"  {name} 拉取失败: {str(e)[:40]}")

    vod_list = list(merged.values())
    # 按来源数量排序（多源的在前面，播放更有保障）
    vod_list.sort(key=lambda x: -len(x["sources"]))

    return {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(vod_list),
        "list": vod_list,
    }


def main():
    probe = "--probe" in sys.argv
    build_idx = "--build-index" in sys.argv

    os.makedirs(OUT_DIR, exist_ok=True)

    print("=== TVBox 自建源聚合器 ===\n")
    engine = CmsEngine(os.path.join(BASE_DIR, "cms_upstreams_dedup.json"))

    # 1. 生成源配置
    config = build_config(engine, probe=probe)
    print(f"\n已生成源配置（CMS）: {len(config['sites'])} 站点")

    # 1.5 从 csp_core.json 读取核心 csp_ 源（网盘 + 精选聚合，26 个代表），并入自建源
    if os.path.exists(CSP_CORE_FILE):
        with open(CSP_CORE_FILE, "r", encoding="utf-8") as f:
            csp_core = json.load(f)
        csp_sites = csp_core.get("sites", [])
        existing_keys = {s.get("key") for s in config["sites"]}
        added = 0
        for s in csp_sites:
            if s.get("key") in existing_keys:
                continue
            config["sites"].append(s)
            existing_keys.add(s.get("key"))
            added += 1
        print(f"已并入 {added} 个核心 csp_ 源，总站点 {len(config['sites'])}")
    else:
        print(f"提示: 未找到 {CSP_CORE_FILE}，跳过 csp_ 源合并")

    # 2. 落盘最终自建源
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"已生成源配置: {OUT_FILE}")
    print(f"  站点数: {len(config['sites'])}")

    # 3. 可选：生成片库索引
    if build_idx:
        index = build_index(engine)
        with open(INDEX_FILE, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        print(f"已生成片库索引: {INDEX_FILE}")
        print(f"  合并去重后 {index['total']} 部（多源自动合并）")

    print("\n完成。TVBox 填这个源地址即可使用。")
    print("提示：把 www/tvbox_self.json 推送到仓库，TVBox 填其 raw 地址。")


if __name__ == "__main__":
    main()
