# -*- coding: utf-8 -*-
"""MeiCodexHelper Windows 单文件构建 (纯 Python, 替代 build.ps1)
产物: dist/MeiCodexHelper-<版本>-windows.zip (内含单个 MeiCodexHelper.exe + README)
"""
import os
import subprocess
import sys
import zipfile

# Windows 控制台默认 cp1252: 进程内强制 UTF-8 (PYTHONIOENCODING 启动后才设无效)
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding='utf-8')
    except Exception:
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
VERSION = sys.argv[1] if len(sys.argv) > 1 else '0.0.0-dev'
os.chdir(ROOT)
os.environ['PYTHONIOENCODING'] = 'utf-8'


def run(cmd):
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise SystemExit(f'失败: {" ".join(cmd)}')


# 依赖
run([sys.executable, '-m', 'pip', 'install', '-q', '-r', 'requirements-build.txt'])

# 图标 (Pillow 绘制, 输出 assets/)
os.makedirs('assets', exist_ok=True)
from PIL import Image, ImageDraw  # noqa: E402


def draw(size):
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size, size], radius=size * 0.22, fill=(82, 51, 234))
    cx, cy = size / 2, size / 2
    s = size * 0.3
    d.polygon([(cx - s, cy - s * 0.7), (cx + s, cy - s * 0.7), (cx + s, cy + s * 0.1),
               (cx, cy + s), (cx - s, cy + s * 0.1)], fill=(255, 255, 255, 235))
    d.polygon([(cx - s * 0.28, cy + s * 0.05), (cx - s * 0.05, cy + s * 0.32),
               (cx + s * 0.34, cy - s * 0.2)], fill=(18, 121, 251))
    return img


ico = draw(256)
ico.save('assets/icon.ico', sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
ico.resize((64, 64)).save('assets/tray.png')
print('icons ok')

# 单文件 exe: GUI + 内核(core 以数据打包, 运行时 _MEIPASS/core 导入)
sep = ';' if os.name == 'nt' else ':'
run([sys.executable, '-m', 'PyInstaller', '--onefile', '--windowed', '--noconfirm', '--clean',
     '--name', 'MeiCodexHelper', '--icon', 'assets/icon.ico',
     f'--add-data', f'..{os.sep}core{sep}core',
     f'--add-data', f'assets{sep}assets',
     '--hidden-import', 'curl_cffi',
     'app.py'])

# 分发包: 单 exe + README
zipname = f'dist/MeiCodexHelper-{VERSION}-windows.zip'
readme = os.path.join(ROOT, '..', 'README.md')
with zipfile.ZipFile(zipname, 'w', zipfile.ZIP_DEFLATED) as z:
    z.write('dist/MeiCodexHelper.exe', 'MeiCodexHelper/MeiCodexHelper.exe')
    if os.path.exists(readme):
        z.write(readme, 'MeiCodexHelper/README.md')
print(f'构建完成: {zipname}')
