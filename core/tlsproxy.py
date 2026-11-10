#!/usr/bin/env python3
"""ChatGPT China Helper — TLS 自适应 CONNECT 代理 (默认 8944)
解决 codex 环境代理机制强制改写 HTTPS_PROXY scheme (https://) 的兼容问题:
首字节嗅探 — 0x16(TLS) 则先用本地受信证书做 TLS 剥离再按 HTTP CONNECT 处理; 否则纯 HTTP 代理。
CONNECT 上游经 SOCKS5 隧道到目标; 回环目标直连。参数见 config.json。"""
import socket, ssl, threading, json, os, time

import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402  (core 层共享路径模块)

AS_DIR = paths.AS_DIR
try:
    _c = json.load(open(os.path.join(AS_DIR, 'config.json')))
except Exception:
    _c = {}

LISTEN_PORT = int(_c.get('connect_proxy_port', 8944))
SOCKS_HOST = _c.get('proxy_socks_host', _c.get('clash_socks_host', '127.0.0.1'))
SOCKS_PORT = int(_c.get('proxy_socks_port', _c.get('clash_socks_port', 7890)))
SOCKS = (SOCKS_HOST, SOCKS_PORT)
CERTS = os.path.join(AS_DIR, 'certs')
CERT = os.path.join(CERTS, 'cert.pem')
KEY = os.path.join(CERTS, 'key.pem')
LOG = os.path.join(AS_DIR, 'logs', 'tlsproxy.log')
LISTEN = ('127.0.0.1', LISTEN_PORT)


def log(m):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, 'a') as f:
            f.write(time.strftime('%H:%M:%S ') + m + '\n')
    except OSError:
        pass


def socks5_connect(host, port, timeout=15):
    if host in ('127.0.0.1', 'localhost', '::1'):   # 回环直连(如 8943 relay 自身)
        s = socket.create_connection((host, port), timeout)
        s.settimeout(None)
        return s
    s = socket.create_connection(SOCKS, timeout)
    s.settimeout(timeout)
    s.sendall(b'\x05\x01\x00')
    r = s.recv(2)
    if r != b'\x05\x00':
        raise OSError('socks greeting failed: ' + repr(r))
    hb = host.encode()
    s.sendall(b'\x05\x01\x00\x03' + bytes([len(hb)]) + hb + port.to_bytes(2, 'big'))
    hdr = s.recv(4)
    if len(hdr) < 4 or hdr[1] != 0:
        raise OSError('socks connect failed: ' + repr(hdr))
    atyp = hdr[3]
    skip = {1: 4, 4: 16}.get(atyp, 0)
    if atyp == 3:
        skip = 1 + s.recv(1)[0]
    while skip > 0:
        d = s.recv(skip)
        if not d:
            break
        skip -= len(d)
    s.recv(2)
    s.settimeout(None)
    return s


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
        for x in (a, b):
            try:
                x.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def handle(conn):
    try:
        conn.settimeout(20)
        first = conn.recv(1, socket.MSG_PEEK)
        if first == b'\x16':
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(CERT, KEY)
            try:
                conn = ctx.wrap_socket(conn, server_side=True)
            except (ssl.SSLError, OSError) as e:
                log(f'TLS握手失败: {str(e)[:60]}')
                return
        elif first == b'':
            return
        head = b''
        while b'\r\n\r\n' not in head:
            d = conn.recv(65536)
            if not d:
                return
            head += d
            if len(head) > 16384:
                return
        line = head.split(b'\r\n', 1)[0].decode('latin1')
        parts = line.split()
        if len(parts) < 2 or parts[0].upper() != 'CONNECT':
            conn.sendall(b'HTTP/1.1 405 Only CONNECT\r\nContent-Length: 0\r\n\r\n')
            return
        host, _, port = parts[1].rpartition(':')
        port = int(port or 443)
        try:
            up = socks5_connect(host, port)
        except OSError as e:
            log(f'CONNECT {host}:{port} 失败 {e}')
            conn.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n')
            return
        log(f'CONNECT {host}:{port} ({"TLS" if first == bytes([0x16]) else "plain"})')
        conn.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        t1 = threading.Thread(target=pump, args=(conn, up), daemon=True)
        t2 = threading.Thread(target=pump, args=(up, conn), daemon=True)
        t1.start(); t2.start(); t1.join(); t2.join()
    except OSError:
        pass
    finally:
        try:
            conn.close()
        except OSError:
            pass


def start_listener():
    """创建监听 socket (CLI main 与宿主 GUI 线程共用); 关闭 socket 即停止服务。"""
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(LISTEN)
    srv.listen(128)
    log(f'tls-adaptive connect proxy on {LISTEN} via socks5 {SOCKS}')
    return srv


def accept_loop(srv):
    while True:
        try:
            c, _ = srv.accept()
        except OSError:
            return  # listener 关闭
        threading.Thread(target=handle, args=(c,), daemon=True).start()


def main():
    accept_loop(start_listener())


if __name__ == '__main__':
    main()
