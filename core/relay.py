#!/usr/bin/env python3
"""ChatGPT China Helper — Relay 服务
本地反向转发器: TLS(默认8943) + 明文(默认8945) -> curl_cffi(Safari指纹) via SOCKS -> chatgpt.com
全部参数来自 ~/Library/Application Support/MeiCodexHelper/config.json
功能: 路径重写(/api剥离、/backend-api挂载) / 请求响应头改写 / wss URL重写+WS隧道 / 访问日志
"""
import time
import sys
import os
import json
import re
import socket
import ssl
import threading
import queue
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from curl_cffi import requests as cr

# ---------- 配置 ----------
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402  (core 层共享路径模块)

AS_DIR = paths.AS_DIR
CFG_PATH = os.path.join(AS_DIR, 'config.json')
try:
    _c = json.load(open(CFG_PATH))
except Exception:
    _c = {}

PORT = int(_c.get('relay_tls_port', 8943))
PLAIN_PORT = int(_c.get('relay_plain_port', 8945))
SOCKS_HOST = _c.get('proxy_socks_host', _c.get('clash_socks_host', '127.0.0.1'))
SOCKS_PORT = int(_c.get('proxy_socks_port', _c.get('clash_socks_port', 7890)))
SOCKS = (SOCKS_HOST, SOCKS_PORT)
UP_HOST = _c.get('upstream_host', 'chatgpt.com')
UP = 'https://' + UP_HOST
PROXIES = {'http': f'socks5h://{SOCKS_HOST}:{SOCKS_PORT}', 'https': f'socks5h://{SOCKS_HOST}:{SOCKS_PORT}'}
IMPERSONATE = _c.get('impersonate', 'safari17_0')
CERTS = os.path.join(AS_DIR, 'certs')
LOG = os.path.join(AS_DIR, 'logs', 'relay-access.log')

LOCAL = f'127.0.0.1:{PORT}'

# curl-impersonate 在多线程下不安全: 会话池(独占借还)仍出现堆损坏
# (SIGABRT curl_easy_reset / OpenSSL RSA_sign 踩到损坏块)。
# 终极方案: 固定 worker 线程, 每个 Session 永久绑定单一线程;
# 处理线程提交任务并等待缓冲结果, 任何 curl 对象不跨线程。
UPSTREAM_Q = queue.Queue()
UPSTREAM_WORKERS = 3

# 熔断器: 防止 OpenAI 客户端无退避重试风暴打爆账号风控
# 同一路径连续 CB_THRESHOLD 次 401/403 -> 冷却 CB_COOLDOWN 秒, 期间本地直接 429
_CB_LOCK = threading.Lock()
_CB = {}          # path -> [连续失败次数, 熔断到期时间戳]
_CB_THRESHOLD = 5
_CB_COOLDOWN = 120

def _cb_blocked(path):
    with _CB_LOCK:
        st = _CB.get(path.split('?')[0])
        return bool(st and st[1] > time.time())

def _cb_record(path, status):
    key = path.split('?')[0]
    with _CB_LOCK:
        st = _CB.setdefault(key, [0, 0.0])
        if status in (401, 403):
            st[0] += 1
            if st[0] >= _CB_THRESHOLD:
                st[1] = time.time() + _CB_COOLDOWN
                log(f'熔断开启: {key} 连续{st[0]}次{status}, 冷却{_CB_COOLDOWN}s')
        elif status < 400:
            st[0] = 0

def _upstream_worker():
    sess = cr.Session()
    while True:
        job = UPSTREAM_Q.get()
        try:
            job['result'] = job['fn'](sess)
        except Exception as e:
            job['error'] = e
        finally:
            job['done'].set()

def upstream_call(fn):
    job = {'fn': fn, 'done': threading.Event()}
    UPSTREAM_Q.put(job)
    if not job['done'].wait(200):
        raise OSError('upstream worker timeout')
    if 'error' in job:
        raise job['error']
    return job['result']

for _ in range(UPSTREAM_WORKERS):
    threading.Thread(target=_upstream_worker, daemon=True).start()
SKIP_REQ = {'host', 'connection', 'keep-alive', 'proxy-connection', 'transfer-encoding',
            'content-length', 'accept-encoding', 'upgrade'}
SKIP_RESP = {'content-length', 'content-encoding', 'transfer-encoding', 'connection',
             'keep-alive', 'upgrade', 'content-security-policy', 'strict-transport-security'}


def log(m):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(time.strftime('%H:%M:%S ') + m + '\n')
    except OSError:
        pass


def open_raw_upstream(host=None):
    """原始 TLS 上游(用于 WebSocket 隧道): socks CONNECT -> host"""
    host = host or UP_HOST
    s = socket.create_connection(SOCKS, 10)
    s.sendall(f'CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n'.encode())
    buf = b''
    while b'\r\n\r\n' not in buf:
        d = s.recv(4096)
        if not d:
            raise OSError('proxy no response')
        buf += d
    if b' 200 ' not in buf.split(b'\r\n', 1)[0]:
        raise OSError('CONNECT failed')
    return ssl.create_default_context().wrap_socket(s, server_hostname=host)


def read_head(sock, initial=b''):
    buf = initial
    while b'\r\n\r\n' not in buf:
        d = sock.recv(65536)
        if not d:
            return None, buf
        buf += d
        if len(buf) > (1 << 20):
            return None, buf
    head, rest = buf.split(b'\r\n\r\n', 1)
    return head + b'\r\n\r\n', rest


def read_exact(rfile, n):
    buf = b''
    while len(buf) < n:
        d = rfile.read(n - len(buf))
        if not d:
            break
        buf += d
    return buf


def read_chunked(rfile):
    out = b''
    while True:
        line = rfile.readline(65536).strip()
        if not line:
            break
        try:
            size = int(line.split(b';', 1)[0], 16)
        except ValueError:
            break
        if size == 0:
            rfile.readline(65536)
            break
        out += read_exact(rfile, size)
        rfile.readline(65536)
        if len(out) > (64 << 20):
            break
    return out


def map_path(path):
    if path.startswith('/api/'):
        path = path[4:]
    if not path.startswith('/.well-known') and not path.startswith('/backend-api'):
        path = '/backend-api' + path
    return path


class Relay(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def _read_body(self):
        te = self.headers.get('Transfer-Encoding', '')
        if 'chunked' in te.lower():
            return read_chunked(self.rfile)
        cl = self.headers.get('Content-Length')
        if cl:
            return read_exact(self.rfile, int(cl))
        return None

    def _ws_tunnel(self):
        """WebSocket 升级: 重建请求头后与上游做原始双向隧道; /_ws/<host>/... 路由到指定上游"""
        up_path = self.path
        host = UP_HOST
        if up_path.startswith('/_ws/'):
            rest = up_path[5:]
            host, up_path = rest.split('/', 1)
            up_path = '/' + up_path
        else:
            up_path = map_path(up_path)
        lines = [f'{self.command} {up_path} HTTP/1.1']
        for k, v in self.headers.items():
            lk = k.lower()
            if lk in ('host', 'proxy-connection'):
                continue
            v = v.replace(LOCAL, UP_HOST).replace('http://127.0.0.1', 'https://chatgpt.com')
            lines.append(f'{k}: {v}')
        lines.append(f'Host: {host}')
        head = ('\r\n'.join(lines) + '\r\n\r\n').encode('latin1')
        try:
            tls = open_raw_upstream(host)
            tls.sendall(head)
            buf = b''
            while b'\r\n\r\n' not in buf:
                d = tls.recv(8192)
                if not d:
                    raise OSError('upstream closed before response')
                buf += d
        except OSError as e:
            log(f'WS {self.path} -> 上游失败 {e}')
            self.send_response(502)
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            return
        rhead, rest = buf.split(b'\r\n\r\n', 1)
        status_line = rhead.split(b'\r\n', 1)[0].decode('latin1', 'replace')[:40]
        log(f'WS {self.path} -> {status_line}')
        self.connection.sendall(rhead + b'\r\n\r\n')
        if rest:
            self.connection.sendall(rest)

        def pump(a, b):
            try:
                while True:
                    d = a.recv(65536)
                    if not d:
                        break
                    b.sendall(d)
            except OSError:
                pass
            finally:
                try:
                    b.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
        t1 = threading.Thread(target=pump, args=(self.connection, tls), daemon=True)
        t2 = threading.Thread(target=pump, args=(tls, self.connection), daemon=True)
        t1.start(); t2.start(); t1.join(); t2.join()
        self.close_connection = True

    def _relay(self):
        if 'websocket' in (self.headers.get('Upgrade') or '').lower():
            return self._ws_tunnel()
        if _cb_blocked(self.path):
            self.send_response(429)
            self.send_header('Retry-After', '60')
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            return
        body = self._read_body()
        up_path = map_path(self.path)
        headers = {}
        diag = []
        for k, v in self.headers.items():
            if k.lower() in SKIP_REQ:
                continue
            v = v.replace(LOCAL, UP_HOST).replace('http://127.0.0.1', 'https://chatgpt.com')
            headers[k] = v
            lk = k.lower()
            if lk == 'authorization':
                diag.append('auth=' + v[:12])
            elif lk == 'cookie':
                diag.append('cookie=' + str(len(v)) + 'B')
        def _fetch(sess):
            r = sess.request(self.command, UP + up_path, headers=headers,
                             data=body, proxies=PROXIES, impersonate=IMPERSONATE,
                             allow_redirects=False, timeout=180)
            return r.status_code, list(r.headers.items()), r.content or b''
        try:
            status, up_headers, payload = upstream_call(_fetch)
        except Exception as e:
            log(f'{self.command} {self.path} -> 上游异常 {type(e).__name__}: {str(e)[:80]}')
            self.send_response(502)
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            return
        log(f'{self.command} {self.path} -> {status} ' + ' '.join(diag))
        _cb_record(self.path, status)
        ct_full = next((v for k, v in up_headers if k.lower() == 'content-type'), '')
        # 统一缓冲响应; JSON 额外重写 websocket_url 到本地隧道
        if 'json' in ct_full.lower():
            payload = payload.replace(f'wss://ws.{UP_HOST}'.encode(),
                                      f'wss://127.0.0.1:{PORT}/_ws/ws.{UP_HOST}'.encode())
        snip = b' '.join(payload[:100].split()).decode('utf-8', 'replace')[:80]
        log(f'   resp {ct_full[:30]} | {snip}')
        self.send_response(status)
        for k, v in up_headers:
            lk = k.lower()
            if lk in SKIP_RESP or lk == 'content-length':
                continue
            if lk == 'set-cookie':
                v = re.sub(rf'[Dd]omain=\.?{re.escape(UP_HOST)};?\s*', '', v)
            elif lk == 'location':
                v = v.replace(f'https://{UP_HOST}', f'http://{LOCAL}')
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            if self.command != 'HEAD':
                self.wfile.write(payload)
        except Exception:
            pass
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _relay

    def log_message(self, *a):
        pass


def start_servers():
    """构建 TLS + 明文双监听服务 (CLI main 与宿主进程 GUI 线程共用)。
    返回 (tls_srv, plain_srv); 由调用方决定 serve_forever 的宿主线程与 shutdown 时机。"""
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), Relay)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(CERTS, 'cert.pem'), os.path.join(CERTS, 'key.pem'))

    def wrap_and_track(sock, addr):
        try:
            ts = ctx.wrap_socket(sock, server_side=True)
            return ts
        except (ssl.SSLEOFError, ConnectionAbortedError, ConnectionResetError):
            # 客户端单方面断开(握手即断/abort/reset): 探活探测、扫描器或客户端放弃,
            # 非服务端问题, 不记录
            try:
                sock.close()
            except OSError:
                pass
            return None
        except (ssl.SSLError, OSError) as e:
            log(f'TLS握手失败 from {addr[0]}:{addr[1]}: {type(e).__name__} {str(e)[:80]}')
            try:
                sock.close()
            except OSError:
                pass
            return None

    orig_get = srv.get_request

    def get_request():
        sock, addr = orig_get()
        ts = wrap_and_track(sock, addr)
        if ts is None:
            return None, addr
        return ts, addr

    srv.get_request = get_request

    old_handle = srv.process_request

    def process(request, client_address):
        if request is None:
            return
        return old_handle(request, client_address)

    srv.process_request = process

    # 明文监听(供不支持本地CA的 rust transport 用 ws://)
    srv2 = ThreadingHTTPServer(('127.0.0.1', PLAIN_PORT), Relay)
    log(f'relay TLS :{PORT} + plain :{PLAIN_PORT} -> {UP} via socks5 {SOCKS} ({IMPERSONATE})')
    return srv, srv2


def main():
    srv, srv2 = start_servers()
    threading.Thread(target=srv2.serve_forever, daemon=True).start()
    srv.serve_forever()


if __name__ == '__main__':
    main()
