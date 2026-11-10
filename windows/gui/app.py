# -*- coding: utf-8 -*-
"""MeiCodexHelper Windows GUI (平台层)
托盘 (pystray) + 主窗口 (tkinter): 控制台/设置 双 Tab, 与 macOS 版同构。
内核逻辑直接复用 core/ (engine_core / paths), 平台动作走 engine.ps1。
"""
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'core'))
import paths          # noqa: E402
import engine_core as ec  # noqa: E402

BRAND = ('#5233EA', '#1279FB')   # 与 macOS 图标同源的渐变色
BG_LOG = '#141914'
FG_LOG = '#BFEBC8'
FIELDS = [
    ('proxy_socks_host', '代理主机', 'str'),
    ('proxy_socks_port', '上游 SOCKS 端口', 'int'),
    ('relay_tls_port', '中继 HTTPS 端口', 'int'),
    ('relay_plain_port', '中继明文端口', 'int'),
    ('connect_proxy_port', 'CONNECT 代理端口', 'int'),
    ('upstream_host', '上游域名', 'str'),
]
IMPERSONATES = ['safari17_0', 'safari18_0', 'edge101']


def resource_path(rel):
    base = getattr(sys, '_MEIPASS', ROOT)
    return os.path.join(base, 'windows', rel)


def run_ps(args):
    exe_dir = getattr(sys, 'frozen', False) and os.path.dirname(sys.executable) or ROOT
    ps1 = os.path.join(exe_dir if getattr(sys, 'frozen', False) else os.path.join(ROOT, 'windows'), 'engine.ps1')
    subprocess.run(['powershell', '-ExecutionPolicy', 'Bypass', '-File', ps1] + args,
                   capture_output=True, text=True, timeout=300)


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title('MeiCodexHelper')
        self.root.geometry('760x600')
        self.root.minsize(680, 520)
        self.status = []
        self.log_text = tk.StringVar(value='')
        self.log_src = tk.StringVar(value='relay')
        self.profiles = []
        self.active = ''
        self.draft = {}
        self.var = {}
        self._build_ui()
        self._start_refresh()
        self.root.protocol('WM_DELETE_WINDOW', self._hide)

    # ---------- UI ----------
    def _build_ui(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill='both', expand=True)
        self._build_console()
        self._build_settings()

    def _build_console(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text=' 控制台 ')
        head = ttk.Frame(f)
        head.pack(fill='x')
        ttk.Label(head, text='MeiCodexHelper 控制台', font=('Microsoft YaHei UI', 14, 'bold')).pack(side='left')
        self.summary = tk.StringVar(value='加载中…')
        ttk.Label(head, textvariable=self.summary, foreground='#666').pack(side='left', padx=10)
        btns = ttk.Frame(f)
        btns.pack(fill='x', pady=8)
        for text, cmd in [('启动 ChatGPT (增强)', self.launch_chatgpt), ('重启服务', self.restart),
                          ('停止服务', self.stop), ('诊断', self.doctor), ('日志文件夹', self.open_logs)]:
            ttk.Button(btns, text=text, command=cmd).pack(side='left', padx=4)
        box = ttk.Frame(f)
        box.pack(fill='both', expand=True)
        self.svc_box = box
        logf = ttk.LabelFrame(f, text='日志', padding=4)
        logf.pack(fill='both', expand=False, side='bottom', pady=(8, 0))
        row = ttk.Frame(logf)
        row.pack(fill='x')
        ttk.Label(row, text='来源:').pack(side='left')
        for name, val in [('中继', 'relay'), ('代理', 'tlsproxy')]:
            ttk.Radiobutton(row, text=name, value=val, variable=self.log_src,
                            command=self.refresh_log).pack(side='left', padx=4)
        ttk.Button(row, text='刷新', width=6, command=self.refresh_log).pack(side='right')
        self.log_widget = tk.Text(logf, height=9, bg=BG_LOG, fg=FG_LOG, insertbackground=FG_LOG,
                                  font=('Consolas', 10), state='disabled', wrap='none')
        self.log_widget.pack(fill='both', expand=True)

    def _build_settings(self):
        f = ttk.Frame(self.nb, padding=10)
        self.nb.add(f, text=' 设置 ')
        main = ttk.Frame(f)
        main.pack(fill='both', expand=True)
        left = ttk.LabelFrame(main, text=' 配置方案 ', padding=6)
        left.pack(side='left', fill='y', padx=(0, 8))
        self.profile_list = tk.Listbox(left, width=22, exportselection=False)
        self.profile_list.pack(fill='both', expand=True)
        self.profile_list.bind('<<ListboxSelect>>', self._on_profile_select)
        lbtns = ttk.Frame(left)
        lbtns.pack(fill='x', pady=4)
        ttk.Button(lbtns, text='新建', width=5, command=self.profile_new).pack(side='left', padx=1)
        ttk.Button(lbtns, text='复制', width=5, command=self.profile_dup).pack(side='left', padx=1)
        ttk.Button(lbtns, text='删除', width=5, command=self.profile_del).pack(side='left', padx=1)
        form = ttk.Frame(main)
        form.pack(side='left', fill='both', expand=True)
        self.form = form
        self._rebuild_form()
        bottom = ttk.Frame(f)
        bottom.pack(fill='x', pady=8)
        ttk.Button(bottom, text='保存并启用', command=self.profile_apply).pack(side='right', padx=4)
        ttk.Button(bottom, text='保存', command=self.profile_save).pack(side='right', padx=4)

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
        row = len(FIELDS)
        ttk.Label(self.form, text='TLS 指纹', width=16).grid(row=row, column=0, sticky='e', padx=4, pady=4)
        imp = tk.StringVar(value=cfg.get('impersonate', 'safari17_0'))
        ttk.Combobox(self.form, textvariable=imp, values=IMPERSONATES, state='readonly', width=14).grid(row=row, column=1, sticky='w')
        self.var['impersonate'] = (imp, 'str')
        ttk.Label(self.form, text='远程控制支持', width=16).grid(row=row + 1, column=0, sticky='e', padx=4)
        rc = tk.BooleanVar(value=bool(cfg.get('remote_control', True)))
        ttk.Checkbutton(self.form, variable=rc).grid(row=row + 1, column=1, sticky='w')
        self.var['remote_control'] = (rc, 'bool')
        ttk.Label(self.form, text='看门狗周期(秒)', width=16).grid(row=row + 2, column=0, sticky='e', padx=4)
        wi = tk.StringVar(value=str(cfg.get('watch_interval_sec', 60)))
        ttk.Entry(self.form, textvariable=wi, width=8).grid(row=row + 2, column=1, sticky='w')
        self.var['watch_interval_sec'] = (wi, 'int')

    # ---------- 状态 ----------
    def _start_refresh(self):
        def loop():
            while True:
                try:
                    self.status = ec.collect_status()
                    ok = sum(1 for s in self.status if s['state'] == 'ok')
                    self.root.after(0, lambda: self._render_status(f'{ok}/{len(self.status)} 正常'))
                    self.root.after(0, self.refresh_log)
                except Exception:
                    pass
                time.sleep(5)
        threading.Thread(target=loop, daemon=True).start()

    def _render_status(self, summary):
        self.summary.set(summary)
        for w in self.svc_box.winfo_children():
            w.destroy()
        for s in self.status:
            row = ttk.Frame(self.svc_box)
            row.pack(fill='x', pady=2)
            dot = tk.Canvas(row, width=12, height=12, highlightthickness=0)
            dot.create_oval(2, 2, 10, 10, fill='#22A55B' if s['state'] == 'ok' else '#D5453E')
            dot.pack(side='left', padx=(4, 8))
            ttk.Label(row, text=s['name'], width=30).pack(side='left')
            ttk.Label(row, text='正常' if s['state'] == 'ok' else '停止',
                      foreground='#22A55B' if s['state'] == 'ok' else '#D5453E').pack(side='left')
            if s['id'] in ('relay', 'relayplain', 'connect', 'daemon'):
                ttk.Button(row, text='重启', width=4,
                           command=lambda sid=s['id']: self._restart_service(sid)).pack(side='right', padx=4)

    def _restart_service(self, sid):
        if sid == 'daemon':
            subprocess.run(['powershell', '-Command',
                            "Get-CimInstance Win32_Process -Filter \"Name='codex.exe'\" | "
                            "Where-Object CommandLine -match 'remote-control' | "
                            "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"], capture_output=True)
        else:
            target = 'relay' if sid in ('relay', 'relayplain') else 'tlsproxy'
            ec._stop_one(target); time.sleep(0.5); ec._start_one(target)

    # ---------- 动作 ----------
    def launch_chatgpt(self):
        threading.Thread(target=run_ps, args=(['launch-chatgpt'],), daemon=True).start()

    def restart(self):
        threading.Thread(target=lambda: (ec._stop_one('relay'), ec._stop_one('tlsproxy'),
                                         time.sleep(1), ec._start_one('relay'), ec._start_one('tlsproxy')),
                         daemon=True).start()

    def stop(self):
        if messagebox.askyesno('确认', '停止全部增强服务? ChatGPT 将无法经代理访问。'):
            threading.Thread(target=run_ps, args=(['stop'],), daemon=True).start()

    def doctor(self):
        def work():
            lines = ['== 诊断 ==']
            for s in ec.collect_status():
                lines.append(f"{s['name']}: {'正常' if s['state'] == 'ok' else '停止'}")
            cfg = paths.load_config()
            import ssl as _ssl
            import urllib.request as _ur
            try:
                ctx = _ssl.create_default_context()
                ctx.load_verify_locations(os.path.join(paths.CERTS_DIR, 'ca.pem'))
                r = _ur.urlopen(_ur.Request(f"https://127.0.0.1:{cfg['relay_tls_port']}/api/codex/accounts/check"),
                                timeout=6, context=ctx)
                lines.append(f"Relay HTTPS 自检: HTTP {r.status}")
            except _ur.error.HTTPError as e:
                lines.append(f'Relay HTTPS 自检: HTTP {e.code} (401/403=链路通)')
            except Exception as e:
                lines.append(f'Relay HTTPS 自检: 失败 {e}')
            self._append_log('\n'.join(lines) + '\n')
        threading.Thread(target=work, daemon=True).start()

    def open_logs(self):
        subprocess.Popen(['explorer.exe', paths.LOGS_DIR])

    # ---------- 日志 ----------
    def refresh_log(self):
        try:
            text = ec.tail_log(self.log_src.get(), 300)
            self.log_widget.configure(state='normal')
            self.log_widget.delete('1.0', 'end')
            self.log_widget.insert('1.0', text)
            self.log_widget.see('end')
            self.log_widget.configure(state='disabled')
        except Exception:
            pass

    def _append_log(self, text):
        self.log_widget.configure(state='normal')
        self.log_widget.insert('end', text + '\n')
        self.log_widget.see('end')
        self.log_widget.configure(state='disabled')

    # ---------- 配置方案 ----------
    def _profiles_dir(self):
        os.makedirs(paths.PROFILES_DIR, exist_ok=True)
        return paths.PROFILES_DIR

    def reload_profiles(self):
        d = self._profiles_dir()
        items = sorted(f for f in os.listdir(d) if f.endswith('.json'))
        self.profiles = [os.path.splitext(f)[0] for f in items]
        if not self.profiles:
            seed = dict(paths.DEFAULT_CONFIG); seed['name'] = '默认'
            json.dump(seed, open(os.path.join(d, '默认.json'), 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=2)
            self.profiles = ['默认']
        act = os.path.join(paths.AS_DIR, 'active_profile')
        self.active = open(act, encoding='utf-8').read().strip() if os.path.exists(act) else self.profiles[0]
        self.profile_list.delete(0, 'end')
        for p in self.profiles:
            self.profile_list.insert('end', ('● ' if p == self.active else '   ') + p)
        sel = self.profiles.index(self.active) if self.active in self.profiles else 0
        self.profile_list.selection_set(sel)
        self._load_draft(self.profiles[sel])

    def _on_profile_select(self, _=None):
        sel = self.profile_list.curselection()
        if sel:
            self._load_draft(self.profiles[sel[0]])

    def _load_draft(self, name):
        p = os.path.join(self._profiles_dir(), name + '.json')
        self.draft = json.load(open(p, encoding='utf-8')) if os.path.exists(p) else dict(paths.DEFAULT_CONFIG)
        self.draft.setdefault('name', name)
        self._rebuild_form()

    def _collect_form(self):
        out = dict(self.draft)
        for key, (v, kind) in self.var.items():
            raw = v.get()
            if kind == 'int':
                n = int(raw)
                assert 1 <= n <= 65535, f'{key} 端口越界'
                out[key] = n
            elif kind == 'bool':
                out[key] = bool(v.get())
            else:
                out[key] = raw
        return out

    def profile_save(self):
        try:
            data = self._collect_form()
        except Exception as e:
            messagebox.showerror('校验失败', str(e)); return
        name = data.get('name') or (self.profile_list.curselection() and
                                    self.profiles[self.profile_list.curselection()[0]]) or '默认'
        data['name'] = name
        json.dump(data, open(os.path.join(self._profiles_dir(), name + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        self.reload_profiles()

    def profile_apply(self):
        try:
            data = self._collect_form()
        except Exception as e:
            messagebox.showerror('校验失败', str(e)); return
        if not messagebox.askyesno('应用配置', f'启用「{data.get("name")}」并重启服务?'):
            return
        data.pop('name', None)
        json.dump(data, open(paths.CFG_PATH, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
        name = self.draft.get('name', '默认')
        open(os.path.join(paths.AS_DIR, 'active_profile'), 'w', encoding='utf-8').write(name)
        self.restart()

    def profile_new(self):
        name = tk.simpledialog.askstring('新建配置', '配置名称', parent=self.root) if hasattr(tk, 'simpledialog') else None
        if not name:
            import tkinter.simpledialog as sd
            name = sd.askstring('新建配置', '配置名称', parent=self.root)
        if not name:
            return
        data = dict(paths.DEFAULT_CONFIG); data['name'] = name
        json.dump(data, open(os.path.join(self._profiles_dir(), name + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        self.reload_profiles()

    def profile_dup(self):
        sel = self.profile_list.curselection()
        if not sel:
            return
        src = self.profiles[sel[0]]
        data = json.load(open(os.path.join(self._profiles_dir(), src + '.json'), encoding='utf-8'))
        data['name'] = src + ' 副本'
        json.dump(data, open(os.path.join(self._profiles_dir(), data['name'] + '.json'), 'w', encoding='utf-8'),
                  ensure_ascii=False, indent=2)
        self.reload_profiles()

    def profile_del(self):
        sel = self.profile_list.curselection()
        if not sel or len(self.profiles) <= 1:
            return
        name = self.profiles[sel[0]]
        if messagebox.askyesno('删除配置', f'删除「{name}」?'):
            os.remove(os.path.join(self._profiles_dir(), name + '.json'))
            self.reload_profiles()

    # ---------- 生命周期 ----------
    def _hide(self):
        self.root.withdraw()

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def quit(self, icon=None):
        try:
            self.root.after(0, self.root.destroy)
        except Exception:
            pass


def main():
    app = App()
    app.reload_profiles()
    try:
        from pystray import Icon as TrayIcon, Menu as TrayMenu, MenuItem
        from PIL import Image

        def tray_launch():
            threading.Thread(target=run_ps, args=(['launch-chatgpt'],), daemon=True).start()

        def tray_restart():
            app.restart()

        img_path = resource_path(os.path.join('assets', 'tray.png'))
        image = Image.open(img_path) if os.path.exists(img_path) else Image.new('RGB', (64, 64), BRAND[0])
        tray = TrayIcon('MeiCodexHelper', image, 'MeiCodexHelper',
                        TrayMenu(MenuItem('打开主窗口', lambda: app.root.after(0, app.show), default=True),
                                 TrayMenu.SEPARATOR,
                                 MenuItem('启动 ChatGPT (增强)', tray_launch),
                                 MenuItem('重启增强服务', tray_restart),
                                 TrayMenu.SEPARATOR,
                                 MenuItem('退出', app.quit)))
        threading.Thread(target=tray.run, daemon=True).start()
    except ImportError:
        pass  # 无 pystray 时纯窗口模式
    app.root.mainloop()


if __name__ == '__main__':
    main()
