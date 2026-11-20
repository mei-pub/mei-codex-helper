#!/usr/bin/env python3
"""ChatGPT China Helper — TLS 自适应 CONNECT 代理 (默认 8944)
解决 codex 环境代理机制强制改写 HTTPS_PROXY scheme (https://) 的兼容问题:
首字节嗅探 — 0x16(TLS) 则先用本地受信证书做 TLS 剥离再按 HTTP CONNECT 处理; 否则纯 HTTP 代理。
CONNECT 上游经 SOCKS5 隧道到目标; 回环目标直连。参数见 config.json。"""
import socket, ssl, threading, json, os, time, re as _re

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
MITM_HOSTS = set()
MITM_UP_HOST = _c.get('upstream_host', 'chatgpt.com')
MITM_IMPERSONATE = _c.get('impersonate', 'safari17_0')
MITM_ENABLED = bool(_c.get('mitm_upstream', True))
RELAY_TLS_PORT = int(_c.get('relay_tls_port', 8943))
if MITM_ENABLED:
    MITM_HOSTS.add(MITM_UP_HOST)
CERTS = os.path.join(AS_DIR, 'certs')
CERT = os.path.join(CERTS, 'cert.pem')
KEY = os.path.join(CERTS, 'key.pem')
LOG = os.path.join(AS_DIR, 'logs', 'tlsproxy.log')
LISTEN = ('127.0.0.1', LISTEN_PORT)


# ---------- MITM (chatgpt.com 专用) ----------
# 目的: 让 codex base_url 直接使用 https://chatgpt.com (真实 origin, 满足
# 远程控制注册挑战的 origin 绑定), 流量经本代理解密后以 Safari 指纹转发,
# 绕过 CF 对机房 IP 非浏览器指纹的拦截。证书由本地 CA 现场签发
# (客户端经 SSL_CERT_FILE/CODEX_CA_CERTIFICATE 信任该 CA)。
_mitm_lock = threading.Lock()
_mitm_cache = {}            # host -> (certfile, keyfile)


def _mitm_cert_paths(host):
    with _mitm_lock:
        if host in _mitm_cache:
            return _mitm_cache[host]
        cdir = os.path.join(AS_DIR, 'certs')
        cert_p = os.path.join(cdir, f'mitm-{host}.pem')
        key_p = os.path.join(cdir, f'mitm-{host}.key')
        if not os.path.exists(cert_p):
            import datetime
            from cryptography import x509 as _x
            from cryptography.hazmat.primitives import hashes as _h, serialization as _s
            from cryptography.hazmat.primitives.asymmetric import rsa as _rsa
            from cryptography.x509.oid import NameOID as _n
            import ipaddress as _ip
            ca_cert = _x.load_pem_x509_certificate(open(os.path.join(cdir, 'ca.pem'), 'rb').read())
            from cryptography.hazmat.primitives.serialization import load_pem_private_key
            ca_priv = load_pem_private_key(open(os.path.join(cdir, 'ca.key'), 'rb').read(), password=None)
            key = _rsa.generate_private_key(public_exponent=65537, key_size=2048)
            now = datetime.datetime.now(datetime.timezone.utc)
            cert = (_x.CertificateBuilder()
                    .subject_name(_x.Name([_x.NameAttribute(_n.COMMON_NAME, host)]))
                    .issuer_name(ca_cert.subject)
                    .public_key(key.public_key())
                    .serial_number(_x.random_serial_number())
                    .not_valid_before(now - datetime.timedelta(days=1))
                    .not_valid_after(now + datetime.timedelta(days=3650))
                    .add_extension(_x.BasicConstraints(ca=False, path_length=None), critical=True)
                    .add_extension(_x.KeyUsage(digital_signature=True, key_cert_sign=False, crl_sign=False,
                                               content_commitment=False, key_encipherment=True,
                                               data_encipherment=False, key_agreement=False,
                                               encipher_only=False, decipher_only=False), critical=True)
                    .add_extension(_x.SubjectAlternativeName([_x.DNSName(host)]), critical=False)
                    .sign(ca_priv, _h.SHA256()))
            open(cert_p, 'wb').write(cert.public_bytes(_s.Encoding.PEM))
            open(key_p, 'wb').write(key.private_bytes(_s.Encoding.PEM, _s.PrivateFormat.TraditionalOpenSSL,
                                                       _s.NoEncryption()))
        _mitm_cache[host] = (cert_p, key_p)
        return _mitm_cache[host]


_MITM_SESS = None
_MITM_SESS_LOCK = threading.Lock()


def _mitm_session():
    """长生命周期会话: 经本地 relay 转发(复用其上游连接池, 无需新握手)"""
    global _MITM_SESS
    with _MITM_SESS_LOCK:
        if _MITM_SESS is None:
            from curl_cffi import requests as _cr
            _MITM_SESS = _cr.Session()
        return _MITM_SESS


def _mitm_upstream_request(method, path, headers_lines, body):
    """解密后的明文请求 -> 本地 relay(连接池+Safari) -> 真实上游 -> (status, headers, body)
    直接经 socks 的路径保留为回退(出口新握手可用时)。"""
    from curl_cffi import requests as _cr
    hs = {'Host': MITM_UP_HOST}
    for l in headers_lines:
        if ':' not in l:
            continue
        k, v = l.split(':', 1)
        k = k.strip()
        if k.lower() in ('host', 'connection', 'proxy-connection', 'content-length', 'transfer-encoding',
                         'accept-encoding'):
            continue
        hs[k] = v.strip()
    ca = os.path.join(AS_DIR, 'certs', 'ca.pem')
    sess = _mitm_session()
    try:
        r = sess.request(method, f'https://127.0.0.1:{RELAY_TLS_PORT}{path}', headers=hs, data=body,
                         verify=ca, allow_redirects=False, timeout=300)
        return r.status_code, list(r.headers.items()), r.content or b''
    except Exception:
        # 回退: 出口直连(新握手可用时)
        sess2 = _cr.Session()
        r = sess2.request(method, f'https://{MITM_UP_HOST}{path}', headers=hs, data=body,
                          proxies={'http': f'socks5h://{SOCKS[0]}:{SOCKS[1]}',
                                   'https': f'socks5h://{SOCKS[0]}:{SOCKS[1]}'},
                          impersonate=MITM_IMPERSONATE, allow_redirects=False, timeout=300)
        return r.status_code, list(r.headers.items()), r.content or b''


def _mitm_serve(conn, host):
    """MITM 主循环: TLS 解密 -> HTTP 请求转发; WebSocket 升级则转原始隧道"""
    cert_p, key_p = _mitm_cert_paths(host)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert_p, key_p)
    try:
        tls = ctx.wrap_socket(conn, server_side=True)
    except (ssl.SSLError, OSError):
        return
    # 升级为 WS 的 CONNECT 命中 MITM 时: 转原始隧道 (Safari 无法承载 WS)
    def _maybe_ws(first_req):
        return _re.search(rb'(?i)^upgrade:\s*websocket', first_req, _re.M) is not None

    buf = b''
    while True:
        # 读一个完整请求头
        while b'\r\n\r\n' not in buf:
            d = tls.recv(65536)
            if not d:
                return
            buf += d
            if len(buf) > (1 << 20):
                return
        head, rest = buf.split(b'\r\n\r\n', 1)
        lines = head.decode('latin1').split('\r\n')
        parts = lines[0].split()
        if len(parts) < 3:
            return
        method, path = parts[0], parts[1]
        # body (Content-Length)
        clen = 0
        for l in lines[1:]:
            if l.lower().startswith('content-length:'):
                clen = int(l.split(':', 1)[1])
        body = rest
        while len(body) < clen:
            d = tls.recv(65536)
            if not d:
                break
            body += d
        buf = body[clen:]
        if _maybe_ws(head):
            # WS: 桥接到真实 host 的 TLS, 原样转发握手, 101 及后续帧双向透传
            up = socks5_connect(host, 443)
            upctx = ssl.create_default_context()
            uptls = upctx.wrap_socket(up, server_hostname=host)
            uptls.sendall(head.encode('latin1') + b'\r\n\r\n' + body[:clen])
            def _p(a, b):
                try:
                    while True:
                        d = a.recv(65536)
                        if not d:
                            break
                        b.sendall(d)
                except OSError:
                    pass
            t1 = threading.Thread(target=_p, args=(tls, uptls), daemon=True)
            t2 = threading.Thread(target=_p, args=(uptls, tls), daemon=True)
            t1.start(); t2.start(); t1.join(); t2.join()
            return
        # 普通请求: Safari 指纹转发
        try:
            status, rheaders, rbody = _mitm_upstream_request(method, path, lines[1:], body[:clen])
        except Exception as e:
            tls.sendall(b'HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n')
            log(f'MITM {method} {path} 失败: {e}')
            return
        reason = {200: 'OK', 204: 'No Content', 301: 'Moved Permanently', 302: 'Found',
                  304: 'Not Modified', 400: 'Bad Request', 401: 'Unauthorized', 403: 'Forbidden',
                  404: 'Not Found', 405: 'Method Not Allowed', 409: 'Conflict', 429: 'Too Many Requests',
                  500: 'Internal Server Error', 502: 'Bad Gateway', 503: 'Service Unavailable'}.get(status, 'X')
        out = [f'HTTP/1.1 {status} {reason}']
        skip = {'connection', 'transfer-encoding', 'content-length', 'content-encoding', 'keep-alive', 'upgrade'}
        for k, v in rheaders:
            if k.lower() in skip:
                continue
            out.append(f'{k}: {v}')
        resp = ('\r\n'.join(out) + f'\r\nContent-Length: {len(rbody)}\r\nConnection: close\r\n\r\n').encode('latin1') + rbody
        tls.sendall(resp)
        log(f'MITM {method} {path} -> {status}')
        return  # Connection: close 语义, 单请求单连接 (客户端会重连)


def log(m):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, 'a', encoding='utf-8') as f:
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
        conn.sendall(b'HTTP/1.1 200 Connection Established\r\n\r\n')
        if MITM_ENABLED and host in MITM_HOSTS and port == 443:
            try:
                nxt = conn.recv(1, socket.MSG_PEEK)
            except OSError:
                nxt = b''
            if nxt == bytes([0x16]):   # 隧道内 TLS: 走 MITM
                log(f'CONNECT {host}:{port} [MITM]')
                _mitm_serve(conn, host)
                return
        log(f'CONNECT {host}:{port} ({"TLS" if first == bytes([0x16]) else "plain"})')
        conn.settimeout(None)  # 隧道为长连接: 静默期(上传/生图/长轮询)不因 20s 读超时被掐断
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
