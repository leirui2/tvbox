# -*- coding: utf-8 -*-
"""企业微信智能机器人通知(构建结果推送 + 异常告警)。

协议: 企业微信智能机器人「长连接 API 模式」(官方文档 path/101463)
  wss://openws.work.weixin.qq.com, 文本帧 JSON:
    aibot_subscribe   带 bot_id/secret 订阅(每次推送独立建链, 用完即断)
    aibot_send_msg    主动推送 markdown 消息到指定会话
    ping              心跳(仅 capture 长等时才需要)
  ⚠ 约束: 机器人只能向"用户已给机器人发过消息"的会话主动推送。
    首次使用必须先绑定:
        python build.py --notify-capture
    然后去企业微信给机器人发任意消息, 自动保存 chatid 并回复确认。

凭证与绑定状态: bot_id/secret 从 notify_config.json 或环境变量读取
                (TVBOX_BOT_ID / TVBOX_BOT_SECRET), 仓库内不保存任何凭证;
                chatid/chat_type 由 --notify-capture 写入 notify_config.json。

接入点(builder.py):
  构建成功        -> notify_build_success
  安全阀触发      -> notify_build_failed
  未捕获异常      -> main() 外层 try/except -> notify_build_crashed
"""
import base64
import datetime
import hashlib
import json
import os
import re
import socket
import ssl
import struct
import time
import uuid

from . import APP_DIR

NOTIFY_CFG_FILE = os.path.join(APP_DIR, 'notify_config.json')

# 机器人凭证: 仓库内不保存凭证(公开仓库会泄露)。
# 配置方式: notify_config.json 里的 bot_id/secret(推荐, 该文件已 gitignore),
# 或环境变量 TVBOX_BOT_ID / TVBOX_BOT_SECRET; 都没配则推送时给出明确提示。
DEFAULT_BOT_ID = os.environ.get('TVBOX_BOT_ID', '')
DEFAULT_SECRET = os.environ.get('TVBOX_BOT_SECRET', '')

WS_URL = 'wss://openws.work.weixin.qq.com'
WS_TIMEOUT = 15          # 单次收发超时(秒)
WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'

# --no-notify 的进程级开关(None=跟配置文件)
_enable_override = None


# ---------------------------------------------------------------- 配置
def load_notify_cfg():
    """读取通知配置: notify_config.json 覆盖默认值。
       返回 {enabled, bot_id, secret, chatid, chat_type}。"""
    cfg = {'enabled': True, 'bot_id': DEFAULT_BOT_ID,
           'secret': DEFAULT_SECRET, 'chatid': '', 'chat_type': 1}
    try:
        with open(NOTIFY_CFG_FILE, encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d, dict):
            for k in ('enabled', 'bot_id', 'secret', 'chatid', 'chat_type'):
                if k in d and d[k] != '':
                    cfg[k] = d[k]
    except Exception:
        pass
    return cfg


def save_notify_cfg(**kw):
    """更新通知配置字段(读-改-写, 原子替换, 保留未提供的字段)。"""
    cfg = load_notify_cfg()
    cfg.update(kw)
    cfg['updated'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    tmp = NOTIFY_CFG_FILE + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, NOTIFY_CFG_FILE)
    return cfg


def set_enabled(on):
    """进程级开关(build.py --no-notify 用)。"""
    global _enable_override
    _enable_override = bool(on)


def _enabled(cfg):
    if _enable_override is False:
        return False
    return bool(cfg.get('enabled', True))


# ---------------------------------------------------------------- 帧编解码(纯函数, 可单测)
def encode_frame(opcode, payload=b''):
    """构造客户端帧: FIN=1 + opcode, 带掩码(客户端->服务端必须掩码)。"""
    payload = payload.encode('utf-8') if isinstance(payload, str) else payload
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack('>H', n)
    else:
        header.append(0x80 | 127)
        header += struct.pack('>Q', n)
    mask = os.urandom(4)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bytes(header) + masked


def decode_frame(buf):
    """从 buf 头部解析一帧服务端帧(服务端->客户端不掩码)。
       返回 (opcode, payload, consumed); 数据不完整返回 None。"""
    if len(buf) < 2:
        return None
    b0, b1 = buf[0], buf[1]
    opcode = b0 & 0x0F
    length = b1 & 0x7F
    pos = 2
    if length == 126:
        if len(buf) < pos + 2:
            return None
        length = struct.unpack('>H', buf[pos:pos + 2])[0]
        pos += 2
    elif length == 127:
        if len(buf) < pos + 8:
            return None
        length = struct.unpack('>Q', buf[pos:pos + 8])[0]
        pos += 8
    if b1 & 0x80:   # 服务端帧理论上不带掩码, 防御性处理
        if len(buf) < pos + 4:
            return None
        mask = buf[pos:pos + 4]
        pos += 4
    else:
        mask = None
    if len(buf) < pos + length:
        return None
    payload = buf[pos:pos + length]
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return opcode, payload, pos + length


# ---------------------------------------------------------------- 最小 WS 客户端
class WSClient:
    """最小 WebSocket 客户端(仅文本帧/控制帧, 满足长连接协议)。"""

    def __init__(self, url, timeout=WS_TIMEOUT):
        m = re.match(r'^wss?://([^/:?#]+)(?::(\d+))?(/.*)?$', url)
        if not m:
            raise ValueError('非法的 ws 地址: %s' % url)
        self.host = m.group(1)
        self.port = int(m.group(2) or (443 if url.startswith('wss') else 80))
        self.path = m.group(3) or '/'
        self.timeout = timeout
        self.sock = None
        self._buf = b''

    def connect(self):
        raw = socket.create_connection((self.host, self.port), self.timeout)
        if self.port == 443:
            ctx = ssl.create_default_context()
            self.sock = ctx.wrap_socket(raw, server_hostname=self.host)
        else:
            self.sock = raw
        self.sock.settimeout(self.timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        req = ('GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\n'
               'Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n'
               'Sec-WebSocket-Version: 13\r\n\r\n'
               % (self.path, self.host, key))
        self.sock.sendall(req.encode())
        head = b''
        while b'\r\n\r\n' not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError('WS 握手失败: 连接被关闭')
            head += chunk
        head, self._buf = head.split(b'\r\n\r\n', 1)
        lines = head.decode('latin1').split('\r\n')
        if ' 101 ' not in lines[0]:
            raise ConnectionError('WS 握手失败: %s' % lines[0])
        accept = base64.b64encode(
            hashlib.sha1((key + WS_GUID).encode()).digest()).decode()
        for ln in lines[1:]:
            if ln.lower().startswith('sec-websocket-accept:'):
                if ln.split(':', 1)[1].strip() != accept:
                    raise ConnectionError('WS 握手失败: accept 校验不通过')
                break

    def send_text(self, text):
        self.sock.sendall(encode_frame(0x1, text))

    def _fill(self, n):
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError('WS 连接已关闭')
            self._buf += chunk

    def recv_text(self, deadline):
        """读取下一条文本帧; 自动回应服务端 ping; 超时抛 socket.timeout。"""
        while True:
            remain = deadline - time.time()
            if remain <= 0:
                raise socket.timeout('等待响应超时')
            self.sock.settimeout(min(self.timeout, remain))
            frame = decode_frame(self._buf)
            if frame is None:
                self._fill(len(self._buf) + 1)
                continue
            opcode, payload, consumed = frame
            self._buf = self._buf[consumed:]
            if opcode == 0x9:                     # ping -> pong
                self.sock.sendall(encode_frame(0xA, payload))
                continue
            if opcode in (0xA, 0x0):              # pong / 续帧(不用)
                continue
            if opcode == 0x8:                     # close
                raise ConnectionError('WS 连接被服务端关闭')
            if opcode != 0x1:
                continue
            return payload.decode('utf-8', 'replace')

    def close(self):
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None


# ---------------------------------------------------------------- 协议命令
def _req_id():
    return uuid.uuid4().hex


def _subscribe(ws, cfg):
    """aibot_subscribe: 凭证校验, 失败抛 RuntimeError。"""
    ws.send_text(json.dumps({
        'cmd': 'aibot_subscribe',
        'headers': {'req_id': _req_id()},
        'body': {'bot_id': cfg['bot_id'], 'secret': cfg['secret']},
    }))
    resp = json.loads(ws.recv_text(time.time() + WS_TIMEOUT))
    if resp.get('errcode') != 0:
        raise RuntimeError('订阅失败(errcode=%s): %s'
                           % (resp.get('errcode'), resp.get('errmsg')))


def _send_msg(ws, cfg, content):
    """aibot_send_msg: 主动推送 markdown, 返回响应 dict。"""
    ws.send_text(json.dumps({
        'cmd': 'aibot_send_msg',
        'headers': {'req_id': _req_id()},
        'body': {
            'chatid': cfg['chatid'],
            'chat_type': int(cfg.get('chat_type') or 1),
            'msgtype': 'markdown',
            'markdown': {'content': content},
        },
    }))
    return json.loads(ws.recv_text(time.time() + WS_TIMEOUT))


# ---------------------------------------------------------------- 对外接口
def send_markdown(content, cfg=None):
    """推送一条 markdown 消息。返回 (ok, detail); 任何失败都不抛异常。"""
    cfg = cfg or load_notify_cfg()
    if not _enabled(cfg):
        return False, '通知已禁用'
    if not cfg.get('chatid'):
        return False, '尚未绑定会话(先跑 python build.py --notify-capture)'
    if not (cfg.get('bot_id') and cfg.get('secret')):
        return False, ('未配置机器人凭证: 在 notify_config.json 填 bot_id/secret, '
                       '或设环境变量 TVBOX_BOT_ID / TVBOX_BOT_SECRET')
    ws = None
    try:
        ws = WSClient(WS_URL)
        ws.connect()
        _subscribe(ws, cfg)
        resp = _send_msg(ws, cfg, content)
        if resp.get('errcode') != 0:
            return False, '发送失败(errcode=%s): %s' % (
                resp.get('errcode'), resp.get('errmsg'))
        return True, 'ok'
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, e)
    finally:
        if ws:
            ws.close()


def capture_chatid(wait_seconds=180):
    """绑定通知会话: 建链订阅后等待用户给机器人发消息, 收到回调即把
       chatid/chat_type 写入 notify_config.json, 并用同一 req_id 回复确认。
       返回 (ok, detail)。"""
    cfg = load_notify_cfg()
    if not (cfg.get('bot_id') and cfg.get('secret')):
        return False, ('未配置机器人凭证: 请先在 notify_config.json 填 '
                       'bot_id/secret, 或设环境变量 TVBOX_BOT_ID / TVBOX_BOT_SECRET')
    ws = None
    try:
        ws = WSClient(WS_URL)
        ws.connect()
        _subscribe(ws, cfg)
        print('[notify] 已连接企业微信机器人, 请在 %d 秒内给机器人发任意消息...'
              % wait_seconds)
        deadline = time.time() + wait_seconds
        last_ping = time.time()
        while time.time() < deadline:
            if time.time() - last_ping > 20:
                ws.send_text(json.dumps({'cmd': 'ping',
                                         'headers': {'req_id': _req_id()}}))
                last_ping = time.time()
            try:
                text = ws.recv_text(min(deadline, time.time() + 5))
            except (socket.timeout, TimeoutError):
                continue
            try:
                msg = json.loads(text)
            except ValueError:
                continue
            if msg.get('cmd') != 'aibot_msg_callback':
                continue
            body = msg.get('body') or {}
            chattype = body.get('chattype') or 'single'
            chatid = body.get('chatid') or (body.get('from') or {}).get('userid') or ''
            if not chatid:
                continue
            chat_type = 2 if chattype == 'group' else 1
            save_notify_cfg(chatid=chatid, chat_type=chat_type)
            # 5 秒内用回调的 req_id 回复确认
            req_id = (msg.get('headers') or {}).get('req_id') or _req_id()
            try:
                ws.send_text(json.dumps({
                    'cmd': 'aibot_respond_msg',
                    'headers': {'req_id': req_id},
                    'body': {'msgtype': 'text',
                             'text': {'content':
                                      '✅ 已绑定 TVBox 构建通知, 后续构建结果会推送到这里'}},
                }))
            except Exception:
                pass
            return True, ('已绑定会话: chatid=%s chat_type=%d(%s)'
                          % (chatid, chat_type, chattype))
        return False, '等待超时: 未收到消息(请先给机器人发条消息再运行本命令)'
    except Exception as e:
        return False, '%s: %s' % (type(e).__name__, e)
    finally:
        if ws:
            ws.close()


# ---------------------------------------------------------------- 消息内容
def _now():
    return datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def notify_build_success(summary, added, removed, elapsed, counts=None):
    """构建成功推送(精简结果)。返回 (ok, detail)。"""
    s = summary or {}
    counts = counts or {}
    lines = ['**TVBox 构建成功** `%s`' % _now(),
             '耗时 %.1fs' % (elapsed or 0),
             '- 点播: 存活 %s / 失效 %s / 客户端依赖 %s%s'
             % (s.get('sites_alive', '-'), s.get('sites_dead', '-'),
                s.get('sites_client_dependent', '-'),
                ' / 不稳定⚠ %s' % s['sites_unstable']
                if s.get('sites_unstable') else ''),
             '- 直播: 存活 %s / 失效 %s' % (s.get('lives_alive', '-'),
                                            s.get('lives_dead', '-')),
             '- 解析: 存活 %s / 失效 %s' % (s.get('parses_alive', '-'),
                                            s.get('parses_dead', '-')),
             '- 相比上次: 恢复 %s / 新失效 %s'
             % (s.get('recovered_vs_last_run', '-'),
                s.get('newly_dead_vs_last_run', '-'))]
    if added or removed:
        lines.append('- 站点变化: 新增 %d / 移除 %d' % (len(added or {}),
                                                       len(removed or {})))
    if counts:
        lines.append('- 产物: 点播 %s / 直播 %s / 解析 %s'
                     % (counts.get('sites', '-'), counts.get('lives', '-'),
                        counts.get('parses', '-')))
    return send_markdown('\n'.join(lines))


def notify_build_failed(reason, detail=''):
    """安全阀触发等失败推送。"""
    lines = ['**TVBox 构建异常** `%s`' % _now(),
             '原因: %s' % reason]
    if detail:
        lines.append('```\n%s\n```' % str(detail)[-1500:])
    lines.append('产物未覆盖, 线上仍是上次的有效源')
    return send_markdown('\n'.join(lines))


def notify_build_crashed(tb_text):
    """未捕获异常推送(traceback 截断到末尾)。"""
    return send_markdown('\n'.join([
        '**TVBox 构建崩溃** `%s`' % _now(),
        '```\n%s\n```' % str(tb_text)[-1500:]]))
