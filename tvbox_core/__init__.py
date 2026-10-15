# -*- coding: utf-8 -*-
"""TVBox 自建源核心库(按功能拆分的模块集合)。

模块划分:
  jsonio    容错 JSON 解析 / 编码处理(去 BOM/注释/尾逗号/控制字符, GBK 混编判别)
  fetch     远程源并发拉取 + fetched/ 缓存 + 来源 URL 映射
  merge     多源合并 / rules 按 host 并集合并 / 配置级重复去重
  probe     静态字段挑选 + 存活探测(deep 内容验证 / 健康分级) + 黑名单/禁用清单
  jar       spider JAR 统计 / per-site 兜底 / 可达性 / 死链救治 / 类名审计
  output    产物输出(主源/稳妥源/审计源/报告) + 站点 diff
  notify    企业微信智能机器人通知(WS 长连接客户端 + 结果/告警推送)
  builder   构建流程编排            -> 入口: 根目录 build.py
  webpage   管理后台页面(HTML/JS)
  sources   源文件管理(上传/URL 抓取/校验/列表)
  gitpush   git 提交推送
  server    管理后台 HTTP 服务      -> 入口: 根目录 manage.py

用法:
  python3 build.py       # 构建(等价 python3 -m tvbox_core.builder)
  python3 manage.py      # 管理后台(等价 python3 -m tvbox_core.server)
"""
import os
import sys

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.dirname(PKG_DIR)             # 仓库根目录(build.py/manage.py 所在目录)
SRC_DIR = os.path.join(APP_DIR, 'sources')     # 本地源 json 目录
OUT_DIR = os.path.join(APP_DIR, 'www')         # 产物目录
BACKUP_DIR = os.path.join(APP_DIR, 'sources_backup')
BUILD_LOG = os.path.join(APP_DIR, '.build_output.log')
BLACKLIST_FILE = os.path.join(APP_DIR, 'probe_blacklist.json')
DISABLE_FILE = os.path.join(APP_DIR, 'disable_list.json')
FETCHED_DIR = os.path.join(APP_DIR, 'fetched')
MAX_UPLOAD = 8 * 1024 * 1024                   # 管理页上传上限 8MB


def emit_prog(stage, done, total):
    """输出机器可读的进度标记行: ##PROG <stage> <done> <total>。
       管理页据此渲染进度条; 普通终端里这行可忽略。"""
    print('##PROG %s %d %d' % (stage, int(done), max(1, int(total))),
          flush=True)
