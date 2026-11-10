#!/usr/bin/env python3
"""MeiCodexHelper core 引擎 — 跨平台状态/启停/日志/配置 (core 层)

平台层(mac engine.sh / windows engine.ps1 / GUI)统一调用本模块:
  python engine_core.py {status|status-json|start|stop|restart|log <name> [n]|version}
进程管理: 优先复用平台托管(launchd/Startup), 否则由本模块直接拉起(detached)。
"""
import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import paths  # noqa: E402

IS_WIN = sys.platform == 'win32'
PY_EXEC = sys.executable
_NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW: GUI 内调命令行工具不闪控制台


# ---------- 进程托管 (无平台托管时的兜底; Windows 主路径) ----------

def _spawn_detached(args):
    if IS_WIN:
        DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(args, creationflags=DETACHED, close_fds=True,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                stdin=subprocess.DEVNULL, cwd=paths.BIN_DIR)
    return subprocess.Popen(args, start_new_session=True, close_fds=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL, cwd=paths.BIN_DIR)


def _service_cmd(name):
    """relay / tlsproxy 的可执行命令。
    打包形态(GUI exe 同目录的 MeiCodexHelper-<name>.exe)优先, 其次 AS/bin 脚本。"""
    exe_name = f'MeiCodexHelper-{name}.exe' if IS_WIN else f'mei-{name}'
    if getattr(sys, 'frozen', False):
        sibling = os.path.join(os.path.dirname(sys.executable), exe_name)
        if os.path.exists(sibling):
            return [sibling]
    script = os.path.join(paths.BIN_DIR, f'{name}.py')
    return [PY_EXEC, script]


def _pidfile(name):
    return os.path.join(paths.LOGS_DIR, f'{name}.pid')


def _start_one(name):
    os.makedirs(paths.LOGS_DIR, exist_ok=True)
    cmd = _service_cmd(name)
    if not os.path.exists(cmd[-1]) and not os.path.exists(cmd[0]):
        return f'{name}: 脚本不存在, 请先安装'
    p = _spawn_detached(cmd)
    with open(_pidfile(name), 'w') as f:
        f.write(str(p.pid))
    return f'{name}: 已启动 (pid {p.pid})'


def _stop_one(name):
    pf = _pidfile(name)
    try:
        pid = int(open(pf).read().strip())
        os.kill(pid, 9 if IS_WIN else 15)
        try:
            os.remove(pf)
        except OSError:
            pass
        return f'{name}: 已停止'
    except FileNotFoundError:
        return f'{name}: 未在运行'
    except (ProcessLookupError, PermissionError, OSError):
        try:
            os.remove(pf)
        except OSError:
            pass
        return f'{name}: 进程已不存在'


def _port_listening(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex(('127.0.0.1', int(port))) == 0


def _socks_functional(host, port):
    """经 SOCKS 出网探活(与状态机的'上游代理'判定一致)"""
    try:
        from curl_cffi import requests as cr
        r = cr.get('https://www.gstatic.com/generate_204',
                   proxies={'https': f'socks5h://{host}:{port}',
                            'http': f'socks5h://{host}:{port}'},
                   timeout=4)
        return r.status_code == 204
    except Exception:
        return False


def _win_cmdline_grep(pattern):
    """命令行子串查进程 (wmic 不可用时经 PowerShell CIM)。pattern 含空格=命令行特征。"""
    try:
        out = subprocess.run(['wmic', 'process', 'get', 'commandline'],
                             capture_output=True, text=True, timeout=10, errors='replace',
                             creationflags=_NO_WINDOW).stdout or ''
        return pattern.lower() in out.lower()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass  # Win11 24H2+ 已移除 wmic
    word = pattern.strip().split()[0].replace("'", "").replace('"', '')
    cmd = (f"Get-CimInstance Win32_Process -Filter \"CommandLine LIKE '%{word}%'\" "
           '| Select-Object -ExpandProperty CommandLine')
    try:
        out = subprocess.run(['powershell.exe', '-NoProfile', '-Command', cmd],
                             capture_output=True, text=True, timeout=15, errors='replace',
                             creationflags=_NO_WINDOW).stdout or ''
        return pattern.lower() in out.lower()
    except Exception:
        return False


def _proc_running(pattern):
    """按命令行/进程名查进程(跨平台)"""
    try:
        if IS_WIN:
            if ' ' in pattern.strip():
                return _win_cmdline_grep(pattern)
            name = pattern.strip()
            if not name.lower().endswith('.exe'):
                name += '.exe'
            out = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {name}', '/FO', 'CSV'],
                                 capture_output=True, text=True, timeout=10, errors='replace',
                                 creationflags=_NO_WINDOW).stdout or ''
            return name.lower() in out.lower()
        out = subprocess.run(['ps', '-axo', 'command'], capture_output=True, text=True, timeout=10).stdout or ''
        return pattern in out
    except Exception:
        return False


# ---------- 状态 ----------

def collect_status():
    cfg = paths.load_config()
    tls = cfg['relay_tls_port']
    plain = cfg['relay_plain_port']
    cp = cfg['connect_proxy_port']
    sh, sp = cfg['proxy_socks_host'], cfg['proxy_socks_port']
    rows = [
        ('relay', f'Relay 中继 · HTTPS :{tls}', _port_listening(tls)),
        ('relayplain', f'Relay 中继 · 明文 :{plain}', _port_listening(plain)),
        ('connect', f'CONNECT 代理 · :{cp}', _port_listening(cp)),
        ('proxy', f'上游代理 (SOCKS :{sp})', _socks_functional(sh, sp)),
        ('chatgpt', 'ChatGPT 桌面端', _proc_running('ChatGPT')),
    ]
    rc = cfg.get('remote_control')
    if rc:
        rows.append(('daemon', '远程控制 Daemon', _proc_running('app-server --remote-control')))
    return [{'id': i, 'name': n, 'state': 'ok' if ok else 'down'} for i, n, ok in rows]


# ---------- 日志 ----------

def tail_log(name='relay', n=300):
    cfg_names = {'relay': ['relay-access.log', 'relay.out'],
                 'tlsproxy': ['tlsproxy.log', 'tlsproxy.out']}
    for fn in cfg_names.get(name, ['relay-access.log']):
        p = os.path.join(paths.LOGS_DIR, fn)
        if os.path.exists(p):
            try:
                lines = open(p, encoding='utf-8', errors='replace').read().splitlines()
                return '\n'.join(lines[-int(n):])
            except OSError:
                pass
    return '(暂无日志)'


# ---------- 命令入口 ----------

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'status'
    if cmd == 'status':
        for r in collect_status():
            print(f"{r['name']}: {'● 正常' if r['state'] == 'ok' else '○ 停止'}")
    elif cmd == 'status-json':
        print(json.dumps(collect_status(), ensure_ascii=False))
    elif cmd == 'start':
        print(_start_one('relay'))
        print(_start_one('tlsproxy'))
    elif cmd == 'stop':
        print(_stop_one('relay'))
        print(_stop_one('tlsproxy'))
    elif cmd == 'restart':
        _stop_one('relay'); _stop_one('tlsproxy')
        time.sleep(1)
        print(_start_one('relay'))
        print(_start_one('tlsproxy'))
    elif cmd == 'log':
        print(tail_log(sys.argv[2] if len(sys.argv) > 2 else 'relay',
                       sys.argv[3] if len(sys.argv) > 3 else 300))
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == '__main__':
    main()
