#!/usr/bin/env python3
"""
把失效的 GitHub 代理地址批量替换为用户自己的 CF 反代。

替换规则：
  https://<失效代理域名>/<可选https://>raw.githubusercontent.com/xxx/yyy
        ↓
  https://leirui.cc.cd/gh/https://raw.githubusercontent.com/xxx/yyy

覆盖文件：
  - dc.txt（远程源清单）
  - sources/*.json（源文件里的 url/api/ext/jar/spider 等字段）

运行前已由调用方备份到 backup_before_proxy_replace/。
"""
import re
import glob
import os
import json

# 你自己的反代（/gh/ 前缀）
PROXY_HOST = "https://leirui.cc.cd/gh/"

# 已失效的 GitHub 代理域名（按优先级，长的在前避免误匹配）
DEAD_PROXIES = [
    "raw.fastgit.org",
    "cdn.staticaly.com",
    "mirror.ghproxy.com",
    "github.moeyy.xyz",
    "gh.api.99988866.xyz",
    "gh-proxy.com",
    "ghproxy.net",
    "ghproxy.cc",
    "ghproxy.com",
    "ghfast.top",
    "gh.llkk.cc",
    "gitcdn.top",
    "git.yylx.win",
    "fastly.jsdelivr.net",
]

# 目标域：只有目标是 GitHub 系才替换（防止误伤）
GITHUB_HOSTS = [
    "raw.githubusercontent.com",
    "github.com",
    "gist.githubusercontent.com",
    "gist.github.com",
    "raw.github.com",
]


def replace_url(url: str) -> tuple:
    """替换单个 URL，返回 (新url, 是否改动)。"""
    if not isinstance(url, str) or "http" not in url:
        return url, False

    # 匹配: <代理域名>/<可能带 https:// 前缀的 github 目标>
    # 形如 gh-proxy.com/https://raw.githubusercontent.com/...
    # 或  gh-proxy.com/raw.githubusercontent.com/...
    pattern = re.compile(
        r"https?://(" + "|".join(map(re.escape, DEAD_PROXIES)) + r")"
        r"/(?:https?:/+)?((?:raw\.|gist\.)?(?:githubusercontent|github)\.com/.*)$",
        re.IGNORECASE,
    )
    m = pattern.search(url)
    if not m:
        return url, False

    target = m.group(2)
    # 目标必须以 github 系开头
    if not any(target.startswith(h) for h in GITHUB_HOSTS):
        return url, False

    new_url = PROXY_HOST + "https://" + target
    return new_url, True


def process_file(path: str) -> int:
    """处理单个文本文件（dc.txt），返回替换条数。"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    changed = 0

    def repl(m):
        nonlocal changed
        old = m.group(0)
        new, ok = replace_url(old)
        if ok:
            changed += 1
            return new
        return old

    # 匹配 URL（带 http 前缀，直到空白/引号/逗号/右括号）
    new_content = re.sub(r"https?://[^\s\"'>,)]+", repl, content)

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)
    return changed


def process_json(path: str) -> int:
    """处理 JSON 源文件，替换 url/api/ext/jar/spider 等字段值。"""
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    changed = 0

    def repl(m):
        nonlocal changed
        old = m.group(0)
        new, ok = replace_url(old)
        if ok:
            changed += 1
            return new
        return old

    new_text = re.sub(r"https?://[^\s\"'>,)]+", repl, text)

    if changed:
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_text)
    return changed


def main():
    total = 0

    # 1. dc.txt
    if os.path.exists("dc.txt"):
        n = process_file("dc.txt")
        print(f"dc.txt: 替换 {n} 处")
        total += n

    # 2. sources/*.json
    for f in sorted(glob.glob("sources/*.json")):
        n = process_json(f)
        if n:
            print(f"{os.path.basename(f)}: 替换 {n} 处")
        total += n

    print(f"\n合计替换 {total} 处")


if __name__ == "__main__":
    main()
