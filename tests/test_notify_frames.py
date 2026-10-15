# -*- coding: utf-8 -*-
"""notify WebSocket 帧编解码(纯函数, 离线)。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tvbox_core.notify import decode_frame, encode_frame


class TestFrames(unittest.TestCase):
    def test_small_text_roundtrip(self):
        text = '{"cmd": "ping"}'
        frame = encode_frame(0x1, text)
        self.assertTrue(frame[1] & 0x80)          # 客户端帧必须带掩码
        op, payload, consumed = decode_frame(frame)
        self.assertEqual(op, 0x1)
        self.assertEqual(payload.decode('utf-8'), text)
        self.assertEqual(consumed, len(frame))

    def test_medium_and_large_roundtrip(self):
        for n in (200, 70000):
            payload = ('x' * n).encode('utf-8')
            frame = encode_frame(0x1, payload)
            op, got, consumed = decode_frame(frame)
            self.assertEqual(op, 0x1)
            self.assertEqual(got, payload)
            self.assertEqual(consumed, len(frame))

    def test_incomplete_returns_none(self):
        self.assertIsNone(decode_frame(b''))
        self.assertIsNone(decode_frame(b'\x81'))            # 不足 2 字节
        self.assertIsNone(decode_frame(b'\x81\x05ab'))      # 声明 5 字节只有 2
        self.assertIsNone(decode_frame(b'\x81\x7e\x00'))    # 126 长度头不完整
        self.assertIsNone(decode_frame(b'\x81\x7e\x00\x05ab'))  # 声明 5 只有 2

    def test_control_opcodes(self):
        for op in (0x9, 0xA, 0x8):
            frame = encode_frame(op, b'')
            got_op, payload, _c = decode_frame(frame)
            self.assertEqual(got_op, op)
            self.assertEqual(payload, b'')

    def test_unmasked_server_frame(self):
        payload = b'hello server'
        frame = bytes([0x81, len(payload)]) + payload
        op, got, _c = decode_frame(frame)
        self.assertEqual((op, got), (0x1, payload))

    def test_masked_server_frame_defensive(self):
        payload = b'masked payload'
        mask = b'\x01\x02\x03\x04'
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        frame = bytes([0x81, 0x80 | len(payload)]) + mask + masked
        op, got, _c = decode_frame(frame)
        self.assertEqual((op, got), (0x1, payload))

    def test_two_frames_in_one_buffer(self):
        buf = encode_frame(0x1, 'one') + encode_frame(0x1, 'two')
        _o1, p1, c1 = decode_frame(buf)
        _o2, p2, _c2 = decode_frame(buf[c1:])
        self.assertEqual((p1.decode(), p2.decode()), ('one', 'two'))


if __name__ == '__main__':
    unittest.main()
