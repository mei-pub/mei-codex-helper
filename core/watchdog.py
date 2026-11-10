#!/usr/bin/env python3
"""MeiCodexHelper 跨平台看门狗 (core 层)

周期体检: relay(_tls/plain)/tlsproxy 端口失活或 relay 功能异常 -> 拉起。
macOS: 由 launchd 托管(KeepAlive) + 本模块双保险
Windows: 由启动项(Startup)托管的常驻进程
"""
import os
import subprocess
import sys
import time
import urllib.request
import ssl

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402
import engine_core as ec  # noqa: E402


def relay_functional(tls_port):
    """HTTPS 功能探活: 拿到任意 HTTP 状态码即健康"""
    try:
        ctx = ssl.create_default_context()
        ctx.load_verify_locations(os.path.join(paths.CERTS_DIR, 'ca.pem'))
        import urllib.request
        req = urllib.request.Request(f'https://127.0.0.1:{tls_port}/api/codex/accounts/check')
        urllib.request.urlopen(req, timeout=5, context=ctx)
        return True
    except urllib.error.HTTPError:
        return True   # 4xx 也是"服务在响应"
    except Exception:
        return False


def once():
    cfg = paths.load_config()
    tls, plain, cp = cfg['relay_tls_port'], cfg['relay_plain_port'], cfg['connect_proxy_port']
    if not ec._port_listening(tls) or not ec._port_listening(plain) or not relay_functional(tls):
        ec._start_one('relay')
    if not ec._port_listening(cp):
        ec._start_one('tlsproxy')


def main():
    while True:
        try:
            once()
        except Exception:
            pass
        time.sleep(max(15, int(paths.cfg_get('watch_interval_sec', 60))))


if __name__ == '__main__':
    main()
