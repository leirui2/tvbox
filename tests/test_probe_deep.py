# -*- coding: utf-8 -*-
"""deep 内容验证(probe.deep_content_ok): 200 但内容无效的判定。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tvbox_core.probe import deep_content_ok


class TestDeepContentOk(unittest.TestCase):
    def test_valid_json_lists(self):
        self.assertTrue(deep_content_ok(b'{"list": [{"name": "a"}]}'))
        self.assertTrue(deep_content_ok(b'{"class": [{"type_name": "a"}]}'))
        # list/class 任一为非空数组即有效
        self.assertTrue(deep_content_ok(b'{"list": [], "class": [{"x": 1}]}'))

    def test_empty_or_missing_lists_invalid(self):
        self.assertFalse(deep_content_ok(b'{"list": [], "class": []}'))
        self.assertFalse(deep_content_ok(b'{"code": 0, "msg": "ok"}'))
        self.assertFalse(deep_content_ok(b'{}'))

    def test_root_array(self):
        self.assertTrue(deep_content_ok(b'[{"a": 1}]'))
        self.assertFalse(deep_content_ok(b'[]'))

    def test_truncated_json_with_list_marker(self):
        # 响应体超过 max_bytes 被截断, JSON 解析失败, 但含 "list" 标记 -> 宽进
        body = (b'{"list": [{"vod_id": 1, "vod_name": "'
                + b'x' * 200000)
        self.assertTrue(deep_content_ok(body))

    def test_xml_markers(self):
        self.assertTrue(deep_content_ok(
            b'<rss><list><video><name>a</name></video></list></rss>'))
        self.assertTrue(deep_content_ok(b'<?xml version="1.0"?><video></video>'))
        self.assertTrue(deep_content_ok(b'<class>type</class>'))

    def test_html_error_page_invalid(self):
        self.assertFalse(deep_content_ok(
            b'<html><body>404 Not Found</body></html>'))
        self.assertFalse(deep_content_ok(b'Service Unavailable'))

    def test_blank_invalid(self):
        self.assertFalse(deep_content_ok(b''))
        self.assertFalse(deep_content_ok(b'   \n  '))

    def test_str_input(self):
        self.assertTrue(deep_content_ok('{"list": [1]}'))
        self.assertFalse(deep_content_ok(''))


if __name__ == '__main__':
    unittest.main()
