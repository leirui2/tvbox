# -*- coding: utf-8 -*-
"""构建入口(薄包装): 实际逻辑在 tvbox_core.builder。

TVBox 自建源聚合构建。完整文档见 README.md；模块划分见 tvbox_core/__init__.py。

用法:
  python build.py                  # 完整构建(拉取远程源 + 探测存活 + 输出产物)
  python build.py --no-probe       # 只合并去重(离线/调试, 不探测)
  python build.py --no-fetch       # 不拉远程, 仅用 fetched/ 已有缓存
"""
import sys

# Windows 控制台默认 GBK, 站点名含 ✨ 等非 GBK 字符会打印崩溃;
# 强制 stdout/stderr 用 UTF-8 + replace, 不依赖运行环境编码。
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from tvbox_core.builder import main

if __name__ == '__main__':
    # 传退出码: 安全阀触发 return 1 / --notify-capture|test 失败 return 1,
    # 正常构建 main() 返回 None(等价 0)。update.sh 依赖 RC 判断构建是否异常。
    sys.exit(main())
