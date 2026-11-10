"""MeiCodexHelper 跨平台路径与配置加载 (core 层, 无平台依赖)"""
import json
import os
import sys

APP_DIR_NAME = 'MeiCodexHelper'


def app_support() -> str:
    """数据目录: macOS ~/Library/Application Support, Windows %APPDATA%"""
    env = os.environ.get('MEI_CODEX_HOME')
    if env:
        return env
    if sys.platform == 'win32':
        return os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), APP_DIR_NAME)
    return os.path.expanduser(f'~/Library/Application Support/{APP_DIR_NAME}')


def codex_home() -> str:
    return os.environ.get('CODEX_HOME') or os.path.expanduser('~/.codex')


AS_DIR = app_support()
CFG_PATH = os.path.join(AS_DIR, 'config.json')
LOGS_DIR = os.path.join(AS_DIR, 'logs')
CERTS_DIR = os.path.join(AS_DIR, 'certs')
PROFILES_DIR = os.path.join(AS_DIR, 'profiles')
BIN_DIR = os.path.join(AS_DIR, 'bin')

DEFAULT_CONFIG = {
    'proxy_socks_host': '127.0.0.1',
    'proxy_socks_port': 7890,
    'relay_tls_port': 8943,
    'relay_plain_port': 8945,
    'connect_proxy_port': 8944,
    'impersonate': 'safari17_0',
    'upstream_host': 'chatgpt.com',
    'remote_control': True,
    'watch_interval_sec': 60,
    'chatgpt_path': '',
}


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.load(open(CFG_PATH, encoding='utf-8')))
    except Exception:
        pass
    # 历史键兼容
    if 'clash_socks_host' in cfg and 'proxy_socks_host' not in cfg:
        cfg['proxy_socks_host'] = cfg['clash_socks_host']
    if 'clash_socks_port' in cfg and 'proxy_socks_port' not in cfg:
        cfg['proxy_socks_port'] = cfg['clash_socks_port']
    return cfg


def cfg_get(key: str, default=None):
    cfg = load_config()
    return cfg.get(key, DEFAULT_CONFIG.get(key, default))
