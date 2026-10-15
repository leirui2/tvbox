# -*- coding: utf-8 -*-
"""报告输出: 远程源健康清单(dc.txt 已达拉取阈值的条目)。"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tvbox_core.output import write_report


class TestWriteReportFetchDead(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='tvbox_test_')

    def _paths(self):
        return (os.path.join(self.tmp, 'report.json'),
                os.path.join(self.tmp, 'report.md'))

    def test_fetch_dead_in_json_and_md(self):
        merged = {'sites': [{'name': 's', 'api': 'http://a/b.php', 'type': 1,
                             '_dead': True, '_dead_reason': 'conn_error',
                             '_dead_status': -1}],
                  'lives': [], 'parses': []}
        fetch_dead = [{'url': 'http://dead/1.json', 'name': '死源1',
                       'fail_count': 7,
                       'last_fail': '2026-10-13 10:00:00'}]
        pj, pm = self._paths()
        write_report(merged, pj, pm, self.tmp, fetch_dead=fetch_dead)
        with open(pj, encoding='utf-8') as f:
            d = json.load(f)
        self.assertEqual(d['summary']['fetch_dead'], 1)
        self.assertEqual(d['fetch_dead'], fetch_dead)
        md = open(pm, encoding='utf-8').read()
        self.assertIn('远程源健康', md)
        self.assertIn('http://dead/1.json', md)
        self.assertIn('死源1', md)

    def test_no_fetch_dead(self):
        merged = {'sites': [], 'lives': [], 'parses': []}
        pj, pm = self._paths()
        write_report(merged, pj, pm, self.tmp)
        with open(pj, encoding='utf-8') as f:
            d = json.load(f)
        self.assertEqual(d['fetch_dead'], [])
        self.assertEqual(d['summary']['fetch_dead'], 0)
        md = open(pm, encoding='utf-8').read()
        self.assertNotIn('远程源健康', md)


if __name__ == '__main__':
    unittest.main()
