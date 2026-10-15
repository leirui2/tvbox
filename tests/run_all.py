#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回归测试一键运行(离线, 不访问网络):

    python tests/run_all.py

等价于 `python -m unittest discover -s tests -v`; 本脚本自动处理好
sys.path(任意目录下都能跑), 并以非零退出码报告失败, 可挂在提交前检查里。
覆盖: deep 判定 / 健康标记 / 熔断 / 重试探 / conn_error 重试 /
      名字清洗 / rules 合并 / notify 帧编解码 / 报告远程源清单。
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

if __name__ == '__main__':
    suite = unittest.TestLoader().discover(HERE, pattern='test_*.py',
                                           top_level_dir=HERE)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
