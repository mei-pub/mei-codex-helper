# -*- coding: utf-8 -*-
"""MeiCodexHelper Windows 单文件主程序 (GUI + 全部内核服务同进程)

形态: 一个 exe 携带一切 ——
  托盘/窗口 GUI (tkinter+pystray) + Relay/tlsproxy 以线程方式内嵌运行
  + 内置安装器(证书信任/自启动注册表/~.codex 接入) + 看门狗定时器。
首次运行自动安装; 无需任何脚本与外部 Python。
"""
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
import tkinter.simpledialog as sd

IS_FROZEN = getattr(sys, 'frozen', False)


def _core_dir():
    if IS_FROZEN:
        return os.path.join(sys._MEIPASS, 'core')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'core')


def _asset(rel):
    if IS_FROZEN:
        return os.path.join(sys._MEIPASS, 'assets', rel)
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets', rel)


sys.path.insert(0, _core_dir())
import paths          # noqa: E402
import certs as certs_mod  # noqa: E402
import engine_core as ec  # noqa: E402

BG_LOG, FG_LOG = '#141914', '#BFEBC8'
FIELDS = [
    ('proxy_socks_host', '代理主机', 'str'),
    ('proxy_socks_port', '上游 SOCKS 端口', 'int'),
    ('relay_tls_port', '中继 HTTPS 端口', 'int'),
    ('relay_plain_port', '中继明文端口', 'int'),
    ('connect_proxy_port', 'CONNECT 代理端口', 'int'),
    ('upstream_host', '上游域名', 'str'),
]
IMPERSONATES = ['safari17_0', 'safari18_0', 'edge101']
CA_CN = 'MeiCodexHelper Local CA'
CREATE_NO_WINDOW = 0x08000000


# ============================================================ 服务宿主 (线程)

class ServiceHost:
    """同进程托管 relay + tlsproxy; 支持启停与线程死亡自愈。"""

    def __init__(self):
        import relay  # noqa: F401 预热
        import tlsproxy  # noqa: F401
        self.relay_mod = None
        self.proxy_mod = None
        self.relay_srvs = None
        self.proxy_sock = None
        self._lock = threading.Lock()

    def _load(self):
        import importlib
        self.relay_mod = importlib.import_module('relay')
        self.proxy_mod = importlib.import_module('tlsproxy')

    def start(self):
        with self._lock:
            self._load()
            self.relay_srvs = self.relay_mod.start_servers()
            threading.Thread(target=self.relay_srvs[0].serve_forever, daemon=True).start()
            threading.Thread(target=self.relay_srvs[1].serve_forever, daemon=True).start()
            self.proxy_sock = self.proxy_mod.start_listener()
            threading.Thread(target=self.proxy_mod.accept_loop, args=(self.proxy_sock,), daemon=True).start()

    def stop(self):
        with self._lock:
            if self.relay_srvs:
                for s in self.relay_srvs:
                    try:
                        s.shutdown(); s.server_close()
                    except Exception:
                        pass
                self.relay_srvs = None
            if self.proxy_sock:
                try:
                    self.proxy_sock.close()
                except Exception:
                    pass
                self.proxy_sock = None

    def heal_if_dead(self):
        """看门狗: 端口失活则重启线程(配置未变时)。"""
        try:
            cfg = paths.load_config()
            need = not ec._port_listening(cfg['relay_tls_port']) or not ec._port_listening(cfg['connect_proxy_port'])
            if need:
                self.stop(); time.sleep(0.5); self.start()
        except Exception:
            pass

    def restart_with_new_config(self):
        """配置已写入 config.json 后重载模块并重启(模块级常量需重读)。"""
        self.stop()
        sys.modules.pop('relay', None)
        sys.modules.pop('tlsproxy', None)
        sys.modules.pop('paths', None)
        sys.path.insert(0, _core_dir())  # paths 弹出后确保下次仍可导入
        import paths as _p  # 重新装载 (读取新 config)
        globals()['paths'] = _p
        ec.paths = _p
        time.sleep(0.5)
        self.start()


# ============================================================ 安装器 (纯 Python)

class Installer:
    @staticmethod
    def ensure_dirs():
        for d in (paths.AS_DIR, paths.LOGS_DIR, paths.CERTS_DIR, paths.PROFILES_DIR):
            os.makedirs(d, exist_ok=True)

    @staticmethod
    def ensure_config():
        if not os.path.exists(paths.CFG_PATH):
            json.dump(paths.DEFAULT_CONFIG, open(paths.CFG_PATH, 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=2)
            return True
        return False

    @staticmethod
    def ensure_certs():
        if not os.path.exists(os.path.join(paths.CERTS_DIR, 'ca.pem')):
            certs_mod.gen()

    @staticmethod
    def trust_ca():
        ca = os.path.join(paths.CERTS_DIR, 'ca.pem')
        if not os.path.exists(ca):
            return False
        r = subprocess.run(['certutil', '-user', '-addstore', 'Root', ca],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        return r.returncode == 0

    @staticmethod
    def patch_codex():
        ct = os.path.join(paths.codex_home(), 'config.toml')
        if not os.path.isdir(paths.codex_home()):
            return '未找到 ~/.codex (请先安装 ChatGPT 桌面端)'
        cfg = paths.load_config()
        tls_port = cfg['relay_tls_port']
        raw = open(ct, encoding='utf-8', errors='replace').read() if os.path.exists(ct) else ''
        if os.path.exists(ct):
            import shutil
            shutil.copy2(ct, ct + '.bak-cnhelper')
        if re.search(r'(?m)^chatgpt_base_url', raw):
            raw = re.sub(r'(?m)^chatgpt_base_url\s*=.*$',
                         f'chatgpt_base_url = "https://127.0.0.1:{tls_port}"', raw)
        else:
            raw = f'chatgpt_base_url = "https://127.0.0.1:{tls_port}"\n\n' + raw
        open(ct, 'w', encoding='utf-8', newline='').write(raw)
        # .env
        cp = f"http://127.0.0.1:{cfg['connect_proxy_port']}"
        socks = f"socks5://{cfg['proxy_socks_host']}:{cfg['proxy_socks_port']}"
        ca_abs = os.path.join(paths.CERTS_DIR, 'ca.pem').replace('\\', '/')
        envf = os.path.join(paths.codex_home(), '.env')
        if os.path.exists(envf):
            import shutil
            shutil.copy2(envf, envf + '.bak-cnhelper')
        open(envf, 'w', encoding='ascii', newline='').write(
            f'# Managed by MeiCodexHelper\n'
            f'HTTP_PROXY={cp}\nHTTPS_PROXY={cp}\nALL_PROXY={socks}\n'
            f'WS_PROXY={cp}\nWSS_PROXY={cp}\nNO_PROXY=localhost,127.0.0.1,::1\n'
            f'CODEX_HTTP_PROXY={cp}\nCODEX_ALL_PROXY={cp}\n'
            f'CODEX_NO_PROXY=localhost,127.0.0.1,::1\n'
            f'SSL_CERT_FILE={ca_abs}\nCODEX_CA_CERTIFICATE={ca_abs}\n')
        return 'codex 已接入'

    @staticmethod
    def set_autostart():
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r'Software\Microsoft\Windows\CurrentVersion\Run',
                                 0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(key, 'MeiCodexHelper', 0, winreg.REG_SZ, f'"{sys.executable}"')
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    @staticmethod
    def remove_autostart():
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                 r'Software\Microsoft\Windows\CurrentVersion\Run',
                                 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(key, 'MeiCodexHelper')
            winreg.CloseKey(key)
        except Exception:
            pass

    @classmethod
    def install(cls):
        cls.ensure_dirs()
        cls.ensure_config()
        cls.ensure_certs()
        trusted = cls.trust_ca()
        codex_msg = cls.patch_codex()
        auto = cls.set_autostart()
        return f'证书信任: {"OK" if trusted else "失败"}\n{codex_msg}\n开机自启: {"OK" if auto else "失败"}'

    @classmethod
    def uninstall(cls):
        cls.remove_autostart()
        subprocess.run(['certutil', '-user', '-delstore', 'Root', CA_CN],
                       capture_output=True, creationflags=CREATE_NO_WINDOW)
        ct = os.path.join(paths.codex_home(), 'config.toml')
        if os.path.exists(ct):
            raw = open(ct, encoding='utf-8', errors='replace').read()
            raw = '\n'.join(l for l in raw.splitlines() if not l.startswith('chatgpt_base_url'))
            open(ct, 'w', encoding='utf-8', newline='').write(raw)
        envf = os.path.join(paths.codex_home(), '.env')
        if os.path.exists(envf + '.bak-cnhelper'):
            import shutil
            shutil.copy2(envf + '.bak-cnhelper', envf)
        return '已卸载 (还原 .codex 配置/移除自启/移除证书信任; 数据目录保留)'


# ============================================================ ChatGPT 启动注入

def find_chatgpt():
    cfg = paths.load_config()
    p = cfg.get('chatgpt_path', '')
    if p and os.path.exists(p):
        return p
    cands = [
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Programs', 'ChatGPT', 'ChatGPT.exe'),
        os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ChatGPT', 'ChatGPT.exe'),
        r'C:\Program Files\ChatGPT\ChatGPT.exe',
        r'C:\Program Files (x86)\ChatGPT\ChatGPT.exe',
    ]
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def launch_chatgpt():
    exe = find_chatgpt()
    if not exe:
        return '未找到 ChatGPT.exe (可在 config.json 配 chatgpt_path)'
    if ec._proc_running('ChatGPT.exe'):
        subprocess.Popen([exe])
        return 'ChatGPT 已在运行 (需代理参数请先退出再冷启动)'
    cfg = paths.load_config()
    cp = f"http://127.0.0.1:{cfg['connect_proxy_port']}"
    socks = f"socks5://{cfg['proxy_socks_host']}:{cfg['proxy_socks_port']}"
    ca = os.path.join(paths.CERTS_DIR, 'ca.pem')
    env = dict(os.environ)
    env.update({'HTTPS_PROXY': cp, 'https_proxy': cp, 'ALL_PROXY': socks,
                'NO_PROXY': 'localhost,127.0.0.1,::1',
                'SSL_CERT_FILE': ca, 'CODEX_CA_CERTIFICATE': ca, 'NODE_EXTRA_CA_CERTS': ca,
                'WS_PROXY': cp, 'WSS_PROXY': cp})
    subprocess.Popen([exe, f'--proxy-server={socks}'], env=env)
    return 'ChatGPT 已带增强参数冷启动'


# ============================================================ GUI

class App:
    def __init__(self, host: ServiceHost):
        self.host = host
        self.root = tk.Tk()
        self.root.title('MeiCodexHelper')
        self.root.geometry('760x620')
        self.root.minsize(680, 540)
        self.status = []
        self.log_src = tk.StringVar(value='relay')
        self.profiles, self.active, self.draft, self.var = [], '', {}, {}
        self._build()
        self._loop()
        self.root.protocol('WM_DELETE_WINDOW', self.root.withdraw)

    def _build(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill='both', expand=True)
        # ---- 控制台 ----
        f = ttk.Frame(nb, padding=10); nb.add(f, text=' 控制台 ')
        head = ttk.Frame(f); head.pack(fill='x')
        ttk.Label(head, text='MeiCodexHelper 控制台',
                  font=('Microsoft YaHei UI', 14, 'bold')).pack(side='left')
        self.summary = tk.StringVar(value='加载中…')
        ttk.Label(head, textvariable=self.summary, foreground='#666').pack(side='left', padx=10)
        row = ttk.Frame(f); row.pack(fill='x', pady=8)
        for text, cmd in [('启动 ChatGPT (增强)', self.a_launch), ('重启服务', self.a_restart),
                          ('安装 / 修复', self.a_install), ('诊断', self.a_doctor),
                          ('卸载…', self.a_uninstall), ('日志文件夹', self.a_logs)]:
            ttk.Button(row, text=text, command=cmd).pack(side='left', padx=3)
        self.svc_box = ttk.Frame(f); self.svc_box.pack(fill='both', expand=True)
        lf = ttk.LabelFrame(f, text=' 日志 ', padding=4)
        lf.pack(fill='both', side='bottom', pady=(8, 0))
        lr = ttk.Frame(lf); lr.pack(fill='x')
        ttk.Label(lr, text='来源:').pack(side='left')
        for name, val in [('中继', 'relay'), ('代理', 'tlsproxy')]:
            ttk.Radiobutton(lr, text=name, value=val, variable=self.log_src,
                            command=self.refresh_log).pack(side='left', padx=4)
        ttk.Button(lr, text='刷新', width=6, command=self.refresh_log).pack(side='right')
        self.logw = tk.Text(lf, height=9, bg=BG_LOG, fg=FG_LOG, insertbackground=FG_LOG,
                            font=('Consolas', 10), state='disabled', wrap='none')
        self.logw.pack(fill='both', expand=True)
        # ---- 设置 ----
        s = ttk.Frame(nb, padding=10); nb.add(s, text=' 设置 ')
        main = ttk.Frame(s); main.pack(fill='both', expand=True)
        left = ttk.LabelFrame(main, text=' 配置方案 ', padding=6)
        left.pack(side='left', fill='y', padx=(0, 8))
        self.plist = tk.Listbox(left, width=22, exportselection=False)
        self.plist.pack(fill='both', expand=True)
        self.plist.bind('<<ListboxSelect>>', lambda e: self._on_select())
        lb = ttk.Frame(left); lb.pack(fill='x', pady=4)
        for t, c in [('新建', self.p_new), ('复制', self.p_dup), ('删除', self.p_del)]:
            ttk.Button(lb, text=t, width=5, command=c).pack(side='left', padx=1)
        self.form = ttk.Frame(main); self.form.pack(side='left', fill='both', expand=True)
        bot = ttk.Frame(s); bot.pack(fill='x', pady=8)
        ttk.Button(bot, text='保存并启用', command=self.p_apply).pack(side='right', padx=4)
        ttk.Button(bot, text='保存', command=self.p_save).pack(side='right', padx=4)

    def _rebuild_form(self):
        for w in self.form.winfo_children():
            w.destroy()
        cfg = self.draft if self.draft else dict(paths.DEFAULT_CONFIG)
        self.var.clear()
        for i, (key, label, kind) in enumerate(FIELDS):
            ttk.Label(self.form, text=label, width=16).grid(row=i, column=0, sticky='e', padx=4, pady=4)
            v = tk.StringVar(value=str(cfg.get(key, '')))
            ttk.Entry(self.form, textvariable=v, width=14 if kind == 'int' else 24).grid(row=i, column=1, sticky='w')
            self.var[key] = (v, kind)
        r = len(FIELDS)
        ttk.Label(self.form, text='TLS 指纹', width=16).grid(row=r, column=0, sticky='e', padx=4, pady=4)
        imp = tk.StringVar(value=cfg.get('impersonate', 'safari17_0'))
        ttk.Combobox(self.form, textvariable=imp, values=IMPERSONATES, state='readonly', width=14).grid(row=r, column=1, sticky='w')
        self.var['impersonate'] = (imp, 'str')
        ttk.Label(self.form, text='远程控制支持', width=16).grid(row=r + 1, column=0, sticky='e', padx=4)
        rc = tk.BooleanVar(value=bool(cfg.get('remote_control', True)))
        ttk.Checkbutton(self.form, variable=rc).grid(row=r + 1, column=1, sticky='w')
        self.var['remote_control'] = (rc, 'bool')
        ttk.Label(self.form, text='看门狗周期(秒)', width=16).grid(row=r + 2, column=0, sticky='e', padx=4)
        wi = tk.StringVar(value=str(cfg.get('watch_interval_sec', 60)))
        ttk.Entry(self.form, textvariable=wi, width=8).grid(row=r + 2, column=1, sticky='w')
        self.var['watch_interval_sec'] = (wi, 'int')

    # ---- 后台循环: 状态 + 看门狗 + 日志 ----
    def _loop(self):
        def work():
            while True:
                try:
                    self.host.heal_if_dead()
                    self.status = ec.collect_status()
                    ok = sum(1 for x in self.status if x['state'] == 'ok')
                    self.root.after(0, lambda: self._render(f'{ok}/{len(self.status)} 正常'))
                    self.root.after(0, self.refresh_log)
                except Exception:
                    pass
                time.sleep(5)
        threading.Thread(target=work, daemon=True).start()

    def _render(self, summary):
        try:
            self.summary.set(summary)
            for w in self.svc_box.winfo_children():
                w.destroy()
            for st in self.status:
                row = ttk.Frame(self.svc_box); row.pack(fill='x', pady=2)
                dot = tk.Canvas(row, width=12, height=12, highlightthickness=0)
                dot.create_oval(2, 2, 10, 10, fill='#22A55B' if st['state'] == 'ok' else '#D5453E')
                dot.pack(side='left', padx=(4, 8))
                ttk.Label(row, text=st['name'], width=30).pack(side='left')
                ttk.Label(row, text='正常' if st['state'] == 'ok' else '停止',
                          foreground='#22A55B' if st['state'] == 'ok' else '#D5453E').pack(side='left')
        except Exception:
            pass

    # ---- 动作 ----
    def a_launch(self):
        threading.Thread(target=lambda: launch_chatgpt(), daemon=True).start()

    def a_restart(self):
        threading.Thread(target=self.host.restart_with_new_config, daemon=True).start()

    def a_install(self):
        def work():
            msg = Installer.install()
            self._append('[安装/修复]\n' + msg + '\n')
        threading.Thread(target=work, daemon=True).start()

    def a_doctor(self):
        def work():
            lines = ['== 诊断 ==']
            for st in ec.collect_status():
                lines.append(f"{st['name']}: {'正常' if st['state'] == 'ok' else '停止'}")
            cfg = paths.load_config()
            import ssl as ssl_mod
            import urllib.request as ur
            try:
                ctx = ssl_mod.create_default_context()
                ctx.load_verify_locations(os.path.join(paths.CERTS_DIR, 'ca.pem'))
                r = ur.urlopen(ur.Request(
                    f"https://127.0.0.1:{cfg['relay_tls_port']}/api/codex/accounts/check"),
                    timeout=6, context=ctx)
                lines.append(f'Relay HTTPS 自检: HTTP {r.status}')
            except ur.error.HTTPError as e:
                lines.append(f'Relay HTTPS 自检: HTTP {e.code} (401/403=链路通)')
            except Exception as e:
                lines.append(f'Relay HTTPS 自检: 失败 {e}')
            self._append('\n'.join(lines) + '\n')
        threading.Thread(target=work, daemon=True).start()

    def a_uninstall(self):
        if messagebox.askyesno('卸载', '卸载将还原 .codex 配置、移除自启与证书信任。继续?'):
            threading.Thread(target=lambda: self._append('[卸载]\n' + Installer.uninstall() + '\n'),
                             daemon=True).start()

    def a_logs(self):
        os.makedirs(paths.LOGS_DIR, exist_ok=True)
        subprocess.Popen(['explorer.exe', paths.LOGS_DIR])

    # ---- 日志 ----
    def refresh_log(self):
        try:
            text = ec.tail_log(self.log_src.get(), 300)
            self.logw.configure(state='normal')
            self.logw.delete('1.0', 'end')
            self.logw.insert('1.0', text)
            self.logw.see('end')
            self.logw.configure(state='disabled')
        except Exception:
            pass

    def _append(self, text):
        self.logw.configure(state='normal')
        self.logw.insert('end', text + '\n')
        self.logw.see('end')
        self.logw.configure(state='disabled')

    # ---- 配置方案 ----
    def reload_profiles(self):
        os.makedirs(paths.PROFILES_DIR, exist_ok=True)
        self.profiles = sorted(os.path.splitext(f)[0] for f in os.listdir(paths.PROFILES_DIR)
                               if f.endswith('.json'))
        if not self.profiles:
            seed = dict(paths.DEFAULT_CONFIG); seed['name'] = '默认'
            json.dump(seed, open(os.path.join(paths.PROFILES_DIR, '默认.json'), 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=2)
            self.profiles = ['默认']
        act = os.path.join(paths.AS_DIR, 'active_profile')
        self.active = open(act, encoding='utf-8').read().strip() if os.path.exists(act) else self.profiles[0]
        self.plist.delete(0, 'end')
        for p in self.profiles:
            self.plist.insert('end', ('● ' if p == self.active else '   ') + p)
        sel = self.profiles.index(self.active) if self.active in self.profiles else 0
        self.plist.selection_set(sel)
        self._load_draft(self.profiles[sel])

    def _on_select(self):
        sel = self.plist.curselection()
        if sel:
            self._load_draft(self.profiles[sel[0]])

    def _load_draft(self, name):
        p = os.path.join(paths.PROFILES_DIR, name + '.json')
        self.draft = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else dict(paths.DEFAULT_CONFIG)
        self.draft.setdefault('name', name)
        self._rebuild_form()

    def _collect(self):
        out = dict(self.draft)
        for key, (v, kind) in self.var.items():
            raw = v.get()
            if kind == 'int':
                n = int(raw); assert 1 <= n <= 65535, f'{key} 端口越界'
                out[key] = n
            elif kind == 'bool':
                out[key] = bool(v.get())
            else:
                out[key] = raw
        return out

    def p_save(self):
        try:
            data = self._collect()
        except Exception as e:
            messagebox.showerror('校验失败', str(e)); return
        name = data.get('name') or '默认'
        data['name'] = name
        json.dump(data, open(os.path.join(paths.PROFILES_DIR, name + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        self.reload_profiles()

    def p_apply(self):
        try:
            data = self._collect()
        except Exception as e:
            messagebox.showerror('校验失败', str(e)); return
        if not messagebox.askyesno('应用配置', f'启用「{data.get("name")}」并重启服务?'):
            return
        data.pop('name', None)
        json.dump(data, open(paths.CFG_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        open(os.path.join(paths.AS_DIR, 'active_profile'), 'w', encoding='utf-8').write(
            self.draft.get('name', '默认'))
        self.a_restart()

    def p_new(self):
        name = sd.askstring('新建配置', '配置名称', parent=self.root)
        if not name:
            return
        data = dict(paths.DEFAULT_CONFIG); data['name'] = name
        json.dump(data, open(os.path.join(paths.PROFILES_DIR, name + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        self.reload_profiles()

    def p_dup(self):
        sel = self.plist.curselection()
        if not sel:
            return
        src = self.profiles[sel[0]]
        data = json.load(open(os.path.join(paths.PROFILES_DIR, src + '.json'), encoding='utf-8'))
        data['name'] = src + ' 副本'
        json.dump(data, open(os.path.join(paths.PROFILES_DIR, data['name'] + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        self.reload_profiles()

    def p_del(self):
        sel = self.plist.curselection()
        if not sel or len(self.profiles) <= 1:
            return
        name = self.profiles[sel[0]]
        if messagebox.askyesno('删除配置', f'删除「{name}」?'):
            os.remove(os.path.join(paths.PROFILES_DIR, name + '.json'))
            self.reload_profiles()


# ============================================================ 入口

def single_instance_lock():
    s = socket.socket()
    try:
        s.bind(('127.0.0.1', 8951))
        return s
    except OSError:
        return None


def main():
    lock = single_instance_lock()
    if lock is None:
        root = tk.Tk(); root.withdraw()
        messagebox.showinfo('MeiCodexHelper', '已在运行 (见系统托盘)')
        return
    Installer.ensure_dirs()
    first_run = Installer.ensure_config()
    Installer.ensure_certs()
    host = ServiceHost()
    try:
        host.start()
    except Exception:
        pass  # 端口被旧实例占用等: GUI 仍可用, 看门狗会自愈
    app = App(host)
    app.reload_profiles()
    if first_run:
        threading.Thread(target=lambda: app._append('[首次运行]\n' + Installer.install() + '\n'),
                         daemon=True).start()
    try:
        from pystray import Icon as TrayIcon, Menu as TrayMenu, MenuItem
        from PIL import Image
        img_path = _asset('tray.png')
        image = Image.open(img_path) if os.path.exists(img_path) else Image.new('RGB', (64, 64), '#5233EA')

        def tray_launch():
            threading.Thread(target=launch_chatgpt, daemon=True).start()

        tray = TrayIcon('MeiCodexHelper', image, 'MeiCodexHelper',
                        TrayMenu(MenuItem('打开主窗口', lambda: app.root.after(0, app.root.deiconify), default=True),
                                 TrayMenu.SEPARATOR,
                                 MenuItem('启动 ChatGPT (增强)', tray_launch),
                                 MenuItem('重启增强服务', lambda: threading.Thread(
                                     target=host.restart_with_new_config, daemon=True).start()),
                                 TrayMenu.SEPARATOR,
                                 MenuItem('退出', lambda: app.root.after(0, app.root.destroy))))
        threading.Thread(target=tray.run, daemon=True).start()
    except ImportError:
        pass
    app.root.mainloop()


if __name__ == '__main__':
    main()
