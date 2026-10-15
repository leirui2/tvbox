# -*- coding: utf-8 -*-
"""站点名推广文字清洗(merge.clean_promo_name)。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tvbox_core.merge import clean_promo_name

# (输入, 期望输出, 是否应变化)
CASES = [
    # ---- URL / 域名残留 ----
    ('导航 http://bhapp.top/', '导航', True),
    ('www.abc.com 影视', '影视', True),
    ('影视 xxx.com', '影视', True),
    # ---- 联系方式(须带结构特征) ----
    ('QQ群:123456 影视', '影视', True),
    ('TG群: abc123', 'TG群: abc123', False),     # 清空 -> 保留原名
    ('@abcdef 频道', '频道', True),
    # ---- 推广关键词 ----
    ('腾讯视频 官网地址', '腾讯视频', True),
    ('注册送VIP', 'VIP', True),
    ('最新网址: example.com', '最新网址: example.com', False),  # 清空 -> 保留原名
    # ---- 更新日期章 ----
    ('电影 更新10-05', '电影', True),
    ('❤更新10-05❤', '❤更新10-05❤', False),      # 清空 -> 保留原名
    ('更新至10.05', '更新至10.05', False),
    # ---- 合法命名风格一律不动 ----
    ('❤玩偶｜4K', '❤玩偶｜4K', False),
    ('📺️腾讯 | 官方', '📺️腾讯 | 官方', False),
    ('xxx[js]', 'xxx[js]', False),
    ('🔥豆瓣┃推荐', '🔥豆瓣┃推荐', False),
    ('', '', False),
]


class TestCleanPromoName(unittest.TestCase):
    def test_cases(self):
        for old, expect, changed in CASES:
            new, ch = clean_promo_name(old)
            self.assertEqual(new, expect, '输入: %r' % old)
            self.assertEqual(ch, changed, '变化标记不符: %r' % old)

    def test_idempotent(self):
        for old, expect, changed in CASES:
            once, _ = clean_promo_name(old)
            twice, ch2 = clean_promo_name(once)
            self.assertEqual(twice, once, '重复清洗结果不稳定: %r' % old)
            self.assertFalse(ch2, '重复清洗不应再报变化: %r' % old)


if __name__ == '__main__':
    unittest.main()
