# MeiCodexHelper Windows 构建脚本 (PyInstaller 多产物打包)
# 用法: powershell -ExecutionPolicy Bypass -File build.ps1 [输出版本]
param([string]$Version = "0.0.0-dev")
$ErrorActionPreference = "Stop"
$env:PYTHONIOENCODING = "utf-8"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

pip install -r requirements-build.txt --quiet
if (-not $?) { throw "依赖安装失败" }

# 托盘/EXE 图标 (Pillow 直接绘制; assets 目录可能不存在于 checkout)
New-Item -ItemType Directory -Force -Path assets | Out-Null
python -c @"
from PIL import Image, ImageDraw
def draw(size):
    img = Image.new('RGBA', (size, size), (0,0,0,0))
    d = ImageDraw.Draw(img)
    r = size*0.22
    d.rounded_rectangle([0,0,size,size], radius=r, fill=(80,51,234))
    # 简洁盾形
    cx, cy = size/2, size/2
    s = size*0.3
    d.polygon([(cx-s,cy-s*0.7),(cx+s,cy-s*0.7),(cx+s,cy+s*0.1),(cx,cy+s),(cx-s,cy+s*0.1)], fill=(255,255,255,235))
    d.polygon([(cx-s*0.28,cy+s*0.05),(cx-s*0.05,cy+s*0.32),(cx+s*0.34,cy-s*0.2)], fill=(80,51,234))
    return img
ico = draw(256)
ico.save('assets/icon.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])
ico.resize((64,64)).save('assets/tray.png')
print('icons ok')
"@
if ($LASTEXITCODE -ne 0) { throw "图标生成失败" }

# 分发包证书 (安装时复制, 免除用户端生成)
python ..\core\certs.py gen
if ($LASTEXITCODE -ne 0) { throw "证书生成失败" }
New-Item -ItemType Directory -Force -Path certs | Out-Null
Copy-Item "..\..\$(Split-Path -Leaf $env:APPDATA)\MeiCodexHelper\certs\*" certs -Force -ErrorAction SilentlyContinue
# certs.py 输出在 AS 目录; 直接从那里取
$asCerts = Join-Path $env:APPDATA "MeiCodexHelper\certs"
if (Test-Path $asCerts) { Copy-Item "$asCerts\*" certs -Force }

# 四个产物: GUI / relay / tlsproxy / watchdog
$common = @('--onefile', '--noconfirm', '--clean')
python -m PyInstaller @common --windowed --name MeiCodexHelper --icon assets/icon.ico gui/app.py
if ($LASTEXITCODE -ne 0) { throw "GUI 打包失败" }
python -m PyInstaller @common --console --name MeiCodexHelper-relay --icon assets/icon.ico ..\core\relay.py
if ($LASTEXITCODE -ne 0) { throw "relay 打包失败" }
python -m PyInstaller @common --console --name MeiCodexHelper-tlsproxy --icon assets/icon.ico ..\core\tlsproxy.py
if ($LASTEXITCODE -ne 0) { throw "tlsproxy 打包失败" }
python -m PyInstaller @common --console --name MeiCodexHelper-watchdog --icon assets/icon.ico ..\core\watchdog.py
if ($LASTEXITCODE -ne 0) { throw "watchdog 打包失败" }

# 汇总分发包
$dist = "dist\MeiCodexHelper-$Version-windows"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
Copy-Item dist\MeiCodexHelper.exe $dist
Copy-Item dist\MeiCodexHelper-relay.exe $dist
Copy-Item dist\MeiCodexHelper-tlsproxy.exe $dist
Copy-Item dist\MeiCodexHelper-watchdog.exe $dist
Copy-Item engine.ps1 $dist
Copy-Item certs $dist\certs -Recurse
Copy-Item ..\README.md $dist\README.md -ErrorAction SilentlyContinue
Compress-Archive -Path $dist -DestinationPath "dist\MeiCodexHelper-$Version-windows.zip" -Force
Write-Host "构建完成: dist\MeiCodexHelper-$Version-windows.zip"
