#!/usr/bin/env python3
"""
从老系统 www/tvbox.json 里智能筛选「网盘类 + 精选聚合类」csp_ 爬虫源，
去重后合并进自建源 www/tvbox_self.json。

筛选规则（按 api 名匹配）：
  A. 网盘类（保留）—— 走自己 token / 网盘搜索，稳定
  B. 精选聚合类（保留）—— 资源全，半可控
  C. 明确排除（砍掉）—— Bili/App*/Guard 等易失效、冗余的

保留完整字段（api/ext/jar/key/name/type），确保源能跑。

用法：
  python tools/filter_csp.py            # 默认：筛选 + 合并进自建源
  python tools/filter_csp.py --dry-run  # 只看会筛出什么，不写入
"""
import json
import os
import sys

# 脚本位于 tools/ 子目录，项目根目录是其上一级
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OLD_FILE = os.path.join(BASE_DIR, "www", "tvbox.json")
SELF_FILE = os.path.join(BASE_DIR, "www", "tvbox_self.json")

# ---- 网盘类 api（保留）----
PAN_APIS = {
    # 直连网盘（走自己 token）
    "csp_PanAli", "csp_PanQuark", "csp_PanUC", "csp_PanBaidu",
    "csp_PanXunlei", "csp_PanGuangya", "csp_PanTianyi", "csp_Pan123",
    # 网盘搜索聚合
    "csp_PanSou", "csp_PanSearch", "csp_PanWebShare", "csp_PanWebShareKF",
    "csp_PanWebShareHJ", "csp_PanWebShare123", "csp_PanCms", "csp_PanTa",
    "csp_Panta", "csp_Panta91", "csp_HunHePan", "csp_DaPanSo", "csp_QuarkPanso",
    "csp_QuPanSo", "csp_PanSou139", "csp_PanSouFish", "csp_MIPanSo", "csp_AiPan",
    # 挂「我的网盘」分享清单
    "csp_MyAli", "csp_MyQuark", "csp_MyBaiDu", "csp_MyXunlei", "csp_MyUC",
    # 网盘直连/转存
    "csp_Duopan", "csp_Dovx", "csp_Zhaozy", "csp_AliShare", "csp_AliPS",
    "csp_QuarkShare", "csp_UCShare", "csp_ThunderShare", "csp_PikPakShare",
    "csp_P115Share", "csp_Alist", "csp_AList", "csp_AlistShare", "csp_YunPan",
    "csp_TGYunPan", "csp_AliYunPan", "csp_Xunlei8",
}

# ---- 精选聚合类 api（保留，资源全）----
AGGREGATE_APIS = {
    "csp_Wogg", "csp_Wobg", "csp_XYQHiker", "csp_XYQHikerAL",
    "csp_MacCMS", "csp_Libvio", "csp_RenRen", "csp_FourKZn",
    "csp_XBPQ",  # 小爆脾气聚合（大量，但质量高，保留但去重）
}

# ---- 每个 api 最多保留实例数（防止 XBPQ 这种单 api 700+ 实例重新变成大杂烩）----
# 聚合类 api（XBPQ/XYQHiker 等）本质是「1 个 jar 爬虫 + N 个站点参数」，
# 全量保留会让自建源失去「精简可控」的意义，这里限制每个 api 的保留数量。
MAX_PER_API = {
    "csp_XBPQ": 20,        # 小爆脾气聚合，只留 20 个优质代表
    "csp_XYQHiker": 20,    # 香雅情聚合，留 20 个
    "csp_XYQHikerAL": 10,
    "csp_Wogg": 10,
    "csp_Wobg": 10,
    "csp_PanSou": 10,
    "csp_PanWebShare": 10,
}
DEFAULT_MAX_PER_API = 5   # 其余 api 默认最多 5 个实例

# ---- 明确排除（砍掉）----
EXCLUDE_KEYWORDS = (
    "Bili",        # B站爬虫，规则频繁变
    "App",         # 各种 APP 专用爬虫
    "Guard",       # 防封变体，冗余
    "Amns",        # 变体包装
)

EXCLUDE_APIS = {
    "csp_Bili", "csp_BiliGuard", "csp_BiliAmns",
    "csp_Config", "csp_Push", "csp_Market", "csp_Local", "csp_LocalFile",
}


def should_keep(site) -> bool:
    """判断一个 csp_ 站点是否保留"""
    api = str(site.get("api", ""))

    # 只看 csp_ 源
    if not api.startswith("csp_"):
        return False

    # 明确排除
    if api in EXCLUDE_APIS:
        return False
    if any(k in api for k in EXCLUDE_KEYWORDS):
        return False

    # 网盘类 或 聚合类
    if api in PAN_APIS or api in AGGREGATE_APIS:
        return True

    return False


def dedup_key(site) -> str:
    """去重键：api + ext（同 api 不同 ext 是不同站点）"""
    api = str(site.get("api", ""))
    ext = json.dumps(site.get("ext", ""), ensure_ascii=False, sort_keys=True)
    return api + "|" + ext


def filter_csp_sites(old_sites: list) -> list:
    """从老系统站点列表里筛出「网盘类 + 精选聚合类」csp_ 源（去重 + 每 api 限额）。

    纯函数，不读写文件，供 aggregate.py 复用。
    """
    kept = []
    seen = set()
    per_api_count = {}
    for s in old_sites:
        if not should_keep(s):
            continue
        k = dedup_key(s)
        if k in seen:
            continue
        api = str(s.get("api", ""))
        limit = MAX_PER_API.get(api, DEFAULT_MAX_PER_API)
        if per_api_count.get(api, 0) >= limit:
            continue
        seen.add(k)
        per_api_count[api] = per_api_count.get(api, 0) + 1
        kept.append(s)
    return kept


def main():
    dry_run = "--dry-run" in sys.argv

    with open(OLD_FILE, "r", encoding="utf-8") as f:
        old = json.load(f)

    old_sites = old.get("sites", [])

    # 筛选（每个 api 限额）
    kept = filter_csp_sites(old_sites)

    # 按 api 分组统计
    from collections import Counter
    api_count = Counter(str(s.get("api")) for s in kept)

    print(f"从老系统 {len(old_sites)} 站点中筛出 csp_ 源: {len(kept)} 个（去重后）")
    print(f"涉及 api 种类: {len(api_count)}")
    print()
    print("=== 筛出的 api 分布 ===")
    for api, c in sorted(api_count.items(), key=lambda x: -x[1]):
        print(f"  {api}: {c}")

    if dry_run:
        print("\n[--dry-run] 仅预览，未写入。")
        return

    # 合并进自建源
    with open(SELF_FILE, "r", encoding="utf-8") as f:
        self_cfg = json.load(f)

    existing_keys = {s.get("key") for s in self_cfg.get("sites", [])}
    existing_apis = {s.get("api") for s in self_cfg.get("sites", [])}

    added = 0
    for s in kept:
        # key 冲突则跳过（同名 key 已存在）
        if s.get("key") in existing_keys:
            continue
        self_cfg["sites"].append(s)
        existing_keys.add(s.get("key"))
        added += 1

    with open(SELF_FILE, "w", encoding="utf-8") as f:
        json.dump(self_cfg, f, ensure_ascii=False, indent=2)

    print(f"\n已合并 {added} 个 csp_ 源到 {SELF_FILE}")
    print(f"  自建源当前总站点数: {len(self_cfg['sites'])}")


if __name__ == "__main__":
    main()
