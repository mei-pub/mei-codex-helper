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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from curl_cffi import requests as cr

# ---------- 配置 ----------
AS_DIR = os.path.expanduser('~/Library/Application Support/MeiCodexHelper')
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
SESSION = cr.Session()
SKIP_REQ = {'host', 'connection', 'keep-alive', 'proxy-connection', 'transfer-encoding',
            'content-length', 'accept-encoding', 'upgrade'}
SKIP_RESP = {'content-length', 'content-encoding', 'transfer-encoding', 'connection',
             'keep-alive', 'upgrade', 'content-security-policy', 'strict-transport-security'}


def log(m):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, 'a') as f:
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
        try:
            r = SESSION.request(self.command, UP + up_path, headers=headers,
                                data=body, proxies=PROXIES, impersonate=IMPERSONATE,
                                allow_redirects=False, stream=True, timeout=180)
        except Exception as e:
            log(f'{self.command} {self.path} -> 上游异常 {type(e).__name__}: {str(e)[:80]}')
            self.send_response(502)
            self.send_header('Content-Length', '0')
            self.send_header('Connection', 'close')
            self.end_headers()
            self.close_connection = True
            return
        log(f'{self.command} {self.path} -> {r.status_code} ' + ' '.join(diag))
        ct_full = r.headers.get('content-type') or ''
        if 'json' in ct_full.lower():
            # JSON: 整体缓冲(iter_content拼接,勿用r.content), 把 websocket_url 重写到本地隧道
            try:
                chunks = []
                for ch in r.iter_content(chunk_size=65536):
                    if ch:
                        chunks.append(ch)
                payload = b''.join(chunks)
            except Exception:
                payload = b''
            payload = payload.replace(f'wss://ws.{UP_HOST}'.encode(),
                                      f'wss://127.0.0.1:{PORT}/_ws/ws.{UP_HOST}'.encode())
            snip = b' '.join(payload[:100].split()).decode('utf-8', 'replace')[:80]
            log(f'   resp {ct_full[:30]} | {snip}')
            self.send_response(r.status_code)
            for k, v in r.headers.items():
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
            return
        # 非 JSON: 流式透传(带首块诊断)
        try:
            ct = ct_full[:30]
            first = b''
            it = r.iter_content(chunk_size=256)
            try:
                first = next(it) or b''
            except StopIteration:
                it = None
            except Exception:
                it = None
            snip = b' '.join(first[:100].split()).decode('utf-8', 'replace')[:80]
            log(f'   resp {ct} | {snip}')
        except Exception:
            first, it = b'', None
        self.send_response(r.status_code)
        for k, v in r.headers.items():
            lk = k.lower()
            if lk in SKIP_RESP:
                continue
            if lk == 'set-cookie':
                v = re.sub(rf'[Dd]omain=\.?{re.escape(UP_HOST)};?\s*', '', v)
            elif lk == 'location':
                v = v.replace(f'https://{UP_HOST}', f'http://{LOCAL}')
            self.send_header(k, v)
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            if first:
                self.wfile.write(first)
            if it is not None:
                for chunk in it:
                    if chunk:
                        self.wfile.write(chunk)
        except Exception:
            pass
        finally:
            try:
                r.close()
            except Exception:
                pass
        self.close_connection = True

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = _relay

    def log_message(self, *a):
        pass


def main():
    srv = ThreadingHTTPServer(('127.0.0.1', PORT), Relay)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(os.path.join(CERTS, 'cert.pem'), os.path.join(CERTS, 'key.pem'))

    def wrap_and_track(sock, addr):
        try:
            ts = ctx.wrap_socket(sock, server_side=True)
            return ts
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
    threading.Thread(target=srv2.serve_forever, daemon=True).start()

    log(f'relay TLS :{PORT} + plain :{PLAIN_PORT} -> {UP} via socks5 {SOCKS} ({IMPERSONATE})')
    srv.serve_forever()


if __name__ == '__main__':
    main()
