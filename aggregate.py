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

# 复用 filter_csp 的筛选纯函数，把「网盘类 + 精选聚合类」csp_ 源并入自建源
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "tools"))
try:
    from filter_csp import filter_csp_sites
except ImportError:
    filter_csp_sites = None

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "www")
OUT_FILE = os.path.join(OUT_DIR, "tvbox_self.json")
INDEX_FILE = os.path.join(OUT_DIR, "vod_index.json")
# 输出一份「标准源文件」到 sources/，供老系统 build.py 自动合并进主源 tvbox.json。
# 这样新引擎的 38 个接口就并入了老系统的 4493 源，电视仍填原地址，无需换地址。
SRC_FILE = os.path.join(BASE_DIR, "sources", "self_cms.json")


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
        sites.append({
            "key": key,
            "name": u["name"],
            "type": 1,
            "api": u["api"],
            "searchable": 1,
            "quickSearch": 1,
            "filterable": 1,
            "changeable": 1,
        })
    return sites


def build_config(engine, probe: bool = False) -> dict:
    """生成完整的 TVBox 配置 JSON"""
    sites = build_sites(engine, probe=probe)

    config = {
        "sites": sites,
        # 解析器留空——这些 CMS 接口的 vod_play_url 已含直链，无需解析
        "parses": [],
        # 直播源留空，以后可单独加
        "lives": [],
        # 可选：首页推荐（留空，由 App 端按分类展示）
        "flags": [],
    }
    return config


def build_source_file(engine, probe: bool = False) -> dict:
    """生成一份「标准源文件」用于并入老系统 build.py。

    与 build_config 的区别：只含 sites（老系统合并器会忽略 flags 等），
    字段与 sources/ 里 type=1 站点完全一致，老系统 merge 时能正确去重合并。
    """
    sites = build_sites(engine, probe=probe)
    return {"sites": sites}


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
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"\n已生成源配置: {OUT_FILE}")
    print(f"  站点数: {len(config['sites'])}")

    # 1.5 生成标准源文件，喂给老系统 build.py 合并进主源
    src_cfg = build_source_file(engine, probe=probe)
    os.makedirs(os.path.dirname(SRC_FILE), exist_ok=True)
    with open(SRC_FILE, "w", encoding="utf-8") as f:
        json.dump(src_cfg, f, ensure_ascii=False, indent=2)
    print(f"已生成源文件: {SRC_FILE}")
    print(f"  （老系统 build.py 会自动合并它，站点并入主源 tvbox.json）")

    # 1.6 从老系统 tvbox.json 筛选「网盘类 + 精选聚合类」csp_ 源，并入自建源
    #     这样自建源 = 38 CMS + 网盘 + 精选 csp_，每次构建幂等。
    if filter_csp_sites is not None:
        old_tvbox = os.path.join(OUT_DIR, "tvbox.json")
        if os.path.exists(old_tvbox):
            with open(old_tvbox, "r", encoding="utf-8") as f:
                old_cfg = json.load(f)
            csp_sites = filter_csp_sites(old_cfg.get("sites", []))
            existing_keys = {s.get("key") for s in config["sites"]}
            added = 0
            for s in csp_sites:
                if s.get("key") in existing_keys:
                    continue
                config["sites"].append(s)
                existing_keys.add(s.get("key"))
                added += 1
            if added:
                # 重写 tvbox_self.json（因为上面已经写入过，这里重新落盘）
                with open(OUT_FILE, "w", encoding="utf-8") as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
            print(f"已并入 {added} 个 csp_ 源到自建源，总站点 {len(config['sites'])}")
        else:
            print("提示: 未找到 www/tvbox.json，跳过 csp_ 源合并（可先跑 build.py）")

    # 2. 可选：生成片库索引
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
