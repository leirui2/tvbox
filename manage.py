# -*- coding: utf-8 -*-
"""管理后台入口(薄包装): 实际逻辑在 tvbox_core.server。

启动 HTTP 管理面板(构建触发 / 单独推送 / 黑名单释放 / 禁用清单 / 源文件管理)。
模块划分见 tvbox_core/__init__.py；部署说明见 DEPLOY.md。

用法:
  python manage.py                 # 默认 0.0.0.0:8787
  python manage.py --host 127.0.0.1 --port 9000
"""
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from tvbox_core.server import main

if __name__ == '__main__':
    main()
