#!/usr/bin/env bash
# ==========================================================================
# TVBox 源部署后自检
#   用法: bash check.sh https://cnb.cool/仓库名/tvbox/-/git/raw/main/www/tvbox.json
#   检查: HTTP 200 / JSON 合法 / 禁缓存头 / 无残留内部字段 / 站点数
# ==========================================================================
set -uo pipefail

URL="${1:-http://127.0.0.1/tvbox.json}"
PASS=0
FAIL=0

ok()   { echo "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

echo "自检目标: $URL"
echo

# 1. HTTP 状态 + 响应头
HEADERS=$(curl -sS -I --max-time 15 "$URL" 2>/dev/null)
CODE=$(printf '%s' "$HEADERS" | head -n1 | awk '{print $2}')
if [ "$CODE" = "200" ]; then ok "HTTP 状态 200"; else bad "HTTP 状态为 ${CODE:-无响应}, 不是 200"; fi

if printf '%s' "$HEADERS" | grep -qi "cache-control:.*no-store\|cache-control:.*no-cache"; then
    ok "已下发禁缓存头 (电视端能拉到最新配置)"
else
    bad "缺少禁缓存头 Cache-Control: no-store/no-cache"
fi

if printf '%s' "$HEADERS" | grep -qi "access-control-allow-origin"; then
    echo "  [INFO] 有 CORS 头 (TVBox 是原生 App, 不受 CORS 限制, 仅作参考)"
else
    echo "  [INFO] 无 CORS 头 (不影响 TVBox 原生客户端)"
fi

# 2. JSON 合法性与内容
TMP=$(mktemp)
if curl -sS --max-time 30 "$URL" -o "$TMP" 2>/dev/null && [ -s "$TMP" ]; then
    ok "响应体非空 ($(wc -c <"$TMP") 字节)"
else
    bad "响应体为空或下载失败"; rm -f "$TMP"; echo; echo "结果: PASS=$PASS FAIL=$FAIL"; exit 1
fi

if python3 - "$TMP" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding='utf-8'))
sites = d.get('sites', [])
assert isinstance(sites, list), 'sites 不是列表'
# 检查没有残留的内部字段
for it in sites:
    for k in ('_dead', '_alive', '_check'):
        assert k not in it, '残留内部字段 %s' % k
print('sites=%d lives=%d parses=%d' % (
    len(sites), len(d.get('lives', [])), len(d.get('parses', []))))
PY
then
    ok "JSON 严格合法且字段干净(无内部标记残留)"
else
    bad "JSON 解析失败或含残留内部字段"
fi

rm -f "$TMP"
echo
echo "结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
echo "全部通过, 可以把这个地址填进电视/手机的 TVBox 里了。"