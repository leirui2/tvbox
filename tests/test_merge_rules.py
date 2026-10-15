# -*- coding: utf-8 -*-
"""rules 按 host 并集合并 / merge_configs 基础行为。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tvbox_core.merge import merge_configs, merge_rules


class TestMergeRules(unittest.TestCase):
    def test_same_host_union(self):
        cfgs = [
            {'rules': [{'host': 'a.com', 'name': 'A', 'regex': ['r1']}]},
            {'rules': [{'host': 'a.com', 'regex': ['r2'], 'filter': ['f1']}]},
        ]
        out, st = merge_rules(cfgs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['regex'], ['r1', 'r2'])
        self.assertEqual(out[0]['filter'], ['f1'])
        self.assertEqual(out[0]['name'], 'A')       # 标量保留首条非空
        self.assertEqual(st['merged'], 1)
        self.assertEqual(st['regex'], 1)
        self.assertEqual(st['filter'], 1)

    def test_hosts_array_key_order_insensitive(self):
        cfgs = [
            {'rules': [{'hosts': ['a.com', 'b.com'], 'regex': ['r1']}]},
            {'rules': [{'hosts': ['b.com', 'a.com'], 'regex': ['r2']}]},
        ]
        out, _st = merge_rules(cfgs)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['regex'], ['r1', 'r2'])

    def test_anonymous_rules_preserved_and_order(self):
        cfgs = [
            {'rules': [{'regex': ['x']},
                       {'host': 'a.com', 'regex': ['r1']}]},
            {'rules': [{'host': 'b.com', 'regex': ['r2']},
                       {'regex': ['y']}]},
        ]
        out, st = merge_rules(cfgs)
        self.assertEqual([r.get('host') for r in out],
                         [None, 'a.com', 'b.com', None])
        self.assertEqual(st['out'], 4)

    def test_no_mutation_of_source(self):
        src = {'rules': [{'host': 'a.com', 'regex': ['r1']}]}
        cfgs = [src, {'rules': [{'host': 'a.com', 'regex': ['r2']}]}]
        out, _st = merge_rules(cfgs)
        self.assertEqual(src['rules'][0]['regex'], ['r1'])   # 源配置不被污染
        self.assertEqual(out[0]['regex'], ['r1', 'r2'])

    def test_empty(self):
        out, st = merge_rules([])
        self.assertEqual(out, [])
        self.assertEqual(st['out'], 0)


class TestMergeConfigs(unittest.TestCase):
    def test_scalar_union_dedup_headers(self):
        cfgs = [
            {'spider': 'http://s1/jar', 'warningText': '测试提示',
             'flags': ['f1'],
             'sites': [{'name': 'a', 'api': 'http://x/1.php', 'type': 1}],
             'headers': {'User-Agent': 'UA1'}},
            {'spider': '', 'warningText': '', 'wallpaper': 'http://w/2.jpg',
             'flags': ['f1', 'f2'],
             'sites': [{'name': 'a', 'api': 'http://x/1.php', 'type': 1},
                       {'name': 'b', 'api': 'http://x/2.php', 'type': 1}],
             'headers': {'Referer': 'R'}},
        ]
        m = merge_configs(cfgs)
        self.assertEqual(m['warningText'], '测试提示')        # 标量取首个非空
        self.assertEqual(m['_cand_spider'], ['http://s1/jar'])  # URL 字段进候选
        self.assertEqual(m['flags'], ['f1', 'f2'])           # 并集保序
        self.assertEqual(len(m['sites']), 2)                 # 按去重键(首个胜出)
        self.assertEqual(m['headers'], {'User-Agent': 'UA1', 'Referer': 'R'})
        self.assertEqual(m['_cand_wallpaper'], ['http://w/2.jpg'])

    def test_rules_merged_in_configs(self):
        cfgs = [
            {'rules': [{'host': 'a.com', 'regex': ['r1']}]},
            {'rules': [{'host': 'a.com', 'regex': ['r2']}]},
        ]
        m = merge_configs(cfgs)
        self.assertEqual(m['rules'][0]['regex'], ['r1', 'r2'])

    def test_no_rules_key_when_empty(self):
        m = merge_configs([{'sites': [{'name': 'a', 'api': 'u',
                                       'type': 1}]}])
        self.assertNotIn('rules', m)


if __name__ == '__main__':
    unittest.main()
