# MeiCodexHelper Windows 引擎 (平台层)
# 用法: powershell -ExecutionPolicy Bypass -File engine.ps1 <install|status|launch-chatgpt|open-logs|open-config|uninstall --yes>
param(
    [Parameter(Position = 0)][string]$Command = "status",
    [string]$Arg2 = ""
)
$ErrorActionPreference = "Stop"

# ---------- 路径 ----------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$CoreDir   = Join-Path $ScriptDir "..\core"
$AS        = Join-Path $env:APPDATA "MeiCodexHelper"
$Cfg       = Join-Path $AS "config.json"
$Logs      = Join-Path $AS "logs"
$Certs     = Join-Path $AS "certs"
$BinDir    = Join-Path $AS "bin"
$CodexDir  = Join-Path $env:USERPROFILE ".codex"
$StartupLnk = [System.IO.Path]::Combine($env:APPDATA, "Microsoft\Windows\Start Menu\Programs\Startup", "MeiCodexHelper.lnk")

$DefaultConfig = @'
{
  "proxy_socks_host": "127.0.0.1",
  "proxy_socks_port": 7890,
  "relay_tls_port": 8943,
  "relay_plain_port": 8945,
  "connect_proxy_port": 8944,
  "impersonate": "safari17_0",
  "upstream_host": "chatgpt.com",
  "remote_control": true,
  "watch_interval_sec": 60,
  "chatgpt_path": ""
}
'@

function CfgGet([string]$Key, $Default) {
    try {
        $j = Get-Content $Cfg -Raw | ConvertFrom-Json
        $v = $j.$Key
        if ($null -ne $v) { return $v }
        # 历史键兼容
        $legacy = @{ "proxy_socks_host" = "clash_socks_host"; "proxy_socks_port" = "clash_socks_port" }
        if ($legacy.ContainsKey($Key) -and $null -ne $j.($legacy[$Key])) { return $j.($legacy[$Key]) }
    } catch {}
    return $Default
}

function Test-Port([int]$Port) {
    try {
        $c = New-Object System.Net.Sockets.TcpClient
        $r = $c.BeginConnect("127.0.0.1", $Port, $null, $null)
        $ok = $r.AsyncWaitHandle.WaitOne(1000)
        if ($ok -and $c.Connected) { $c.Close(); return $true }
        $c.Close(); return $false
    } catch { return $false }
}

function Find-ChatGPT {
    $cfgPath = CfgGet "chatgpt_path" ""
    if ($cfgPath -and (Test-Path $cfgPath)) { return $cfgPath }
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA "Programs\ChatGPT\ChatGPT.exe"),
        (Join-Path $env:LOCALAPPDATA "ChatGPT\ChatGPT.exe"),
        "C:\Program Files\ChatGPT\ChatGPT.exe",
        "C:\Program Files (x86)\ChatGPT\ChatGPT.exe"
    )
    foreach ($p in $candidates) { if (Test-Path $p) { return $p } }
    return $null
}

# ---------- 命令 ----------
function Cmd-Install {
    Write-Host "== MeiCodexHelper 安装 (Windows) =="
    foreach ($d in @($AS, $Logs, $Certs, $BinDir, (Join-Path $AS "profiles"))) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }
    if (-not (Test-Path $Cfg)) { Set-Content $Cfg $DefaultConfig -Encoding UTF8; Write-Host "已生成默认配置" }

    # 内核脚本 (若无打包 exe, 依赖系统 python)
    foreach ($f in @("relay.py", "tlsproxy.py", "watchdog.py", "engine_core.py", "paths.py", "__init__.py", "certs.py")) {
        $src = Join-Path $CoreDir $f
        if (Test-Path $src) { Copy-Item $src (Join-Path $BinDir $f) -Force }
    }
    # 打包 exe (随分发包) 优先
    foreach ($f in @("MeiCodexHelper-relay.exe", "MeiCodexHelper-tlsproxy.exe", "MeiCodexHelper-watchdog.exe")) {
        $src = Join-Path $ScriptDir $f
        if (Test-Path $src) { Copy-Item $src (Join-Path $BinDir $f) -Force }
    }

    # 证书: 分发包自带则复制, 否则尝试 python 生成
    if ((Test-Path (Join-Path $ScriptDir "certs\ca.pem")) -and -not (Test-Path (Join-Path $Certs "ca.pem"))) {
        Copy-Item (Join-Path $ScriptDir "certs\*") $Certs -Force; Write-Host "已复制分发包证书"
    }
    if (-not (Test-Path (Join-Path $Certs "ca.pem"))) {
        try { & python (Join-Path $CoreDir "certs.py") ensure | Out-Host } catch { Write-Host "警告: 证书生成失败(需 python+cryptography)" }
    }
    # 信任 CA 到当前用户根存储 (无需管理员)
    if (Test-Path (Join-Path $Certs "ca.pem")) {
        & certutil -user -addstore Root (Join-Path $Certs "ca.pem") | Out-Null
        Write-Host "CA 已加入当前用户信任存储"
    }

    # ~/.codex 接入
    $tlsPort = CfgGet "relay_tls_port" 8943
    $ct = Join-Path $CodexDir "config.toml"
    if (Test-Path $CodexDir) {
        Copy-Item $ct "$ct.bak-cnhelper" -Force -ErrorAction SilentlyContinue
        $raw = Get-Content $ct -Raw -ErrorAction SilentlyContinue
        if ($raw -match '(?m)^chatgpt_base_url') {
            $raw = $raw -replace '(?m)^chatgpt_base_url\s*=.*$', "chatgpt_base_url = `"https://127.0.0.1:$tlsPort`""
        } else {
            $raw = "chatgpt_base_url = `"https://127.0.0.1:$tlsPort`"`r`n`r`n" + $raw
        }
        Set-Content $ct $raw -NoNewline -Encoding UTF8
        Write-Host "config.toml 已配置 base_url=https://127.0.0.1:$tlsPort"

        $cpPort = CfgGet "connect_proxy_port" 8944
        $envFile = Join-Path $CodexDir ".env"
        Copy-Item $envFile "$envFile.bak-cnhelper" -Force -ErrorAction SilentlyContinue
        $CP = "http://127.0.0.1:$cpPort"
        $caAbs = (Join-Path $Certs "ca.pem") -replace '\\', '/'
        @"
# Managed by MeiCodexHelper
HTTP_PROXY=$CP
HTTPS_PROXY=$CP
ALL_PROXY=$CP
WS_PROXY=$CP
WSS_PROXY=$CP
NO_PROXY=localhost,127.0.0.1,::1
CODEX_HTTP_PROXY=$CP
CODEX_ALL_PROXY=$CP
CODEX_NO_PROXY=localhost,127.0.0.1,::1
SSL_CERT_FILE=$caAbs
CODEX_CA_CERTIFICATE=$caAbs
"@ | Set-Content $envFile -Encoding ASCII
        Write-Host ".env 已配置 (代理 -> $CP)"
    } else {
        Write-Host "警告: 未找到 ~/.codex (请确认已安装 ChatGPT 桌面端)"
    }

    # 启动项: Startup 文件夹快捷方式指向看门狗 (无需管理员)
    $wd = Join-Path $BinDir "MeiCodexHelper-watchdog.exe"
    if (-not (Test-Path $wd)) { $wd = "pythonw.exe" } # 回退系统 python
    try {
        $shell = New-Object -ComObject WScript.Shell
        $lnk = $shell.CreateShortcut($StartupLnk)
        if ($wd -eq "pythonw.exe") { $lnk.Arguments = "`"$BinDir\watchdog.py`"" } else { $lnk.Arguments = "" }
        $lnk.TargetPath = $wd
        $lnk.WorkingDirectory = $AS
        $lnk.WindowStyle = 7  # Minimized
        $lnk.Save()
        Write-Host "已添加启动项 (Startup -> 看门狗)"
    } catch { Write-Host "警告: 启动项创建失败: $_" }

    # 立即拉起服务
    Cmd-Start
    Start-Sleep 3
    Write-Host "== 安装完成 =="
    Cmd-Status
}

function Cmd-Start {
    $wd = Join-Path $BinDir "MeiCodexHelper-watchdog.exe"
    if (Test-Path $wd) { Start-Process $wd -WorkingDirectory $AS -WindowStyle Hidden; Write-Host "看门狗已启动 (将拉起 relay/proxy)"; return }
    $relay = Join-Path $BinDir "relay.py"
    $proxy = Join-Path $BinDir "tlsproxy.py"
    if (Test-Path $relay) { Start-Process pythonw $relay -WindowStyle Hidden -WorkingDirectory $BinDir; Write-Host "relay 启动中" }
    if (Test-Path $proxy) { Start-Process pythonw $proxy -WindowStyle Hidden -WorkingDirectory $BinDir; Write-Host "tlsproxy 启动中" }
}

function Cmd-Stop {
    foreach ($exe in @("MeiCodexHelper-relay.exe", "MeiCodexHelper-tlsproxy.exe", "MeiCodexHelper-watchdog.exe")) {
        Get-Process -Name $exe -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'relay\.py|tlsproxy\.py|watchdog\.py' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if (Test-Path $StartupLnk) { Remove-Item $StartupLnk -Force -ErrorAction SilentlyContinue; Write-Host "启动项已移除" }
    Write-Host "服务已停止"
}

function Cmd-Status {
    $tls = CfgGet "relay_tls_port" 8943; $plain = CfgGet "relay_plain_port" 8945
    $cp = CfgGet "connect_proxy_port" 8944; $socks = CfgGet "proxy_socks_port" 7890
    $rows = @(
        @("Relay 中继 · HTTPS :$tls", (Test-Port $tls)),
        @("Relay 中继 · 明文 :$plain", (Test-Port $plain)),
        @("CONNECT 代理 · :$cp", (Test-Port $cp)),
        @("上游代理 (SOCKS :$socks)", (Test-Port $socks)),
        @("ChatGPT 桌面端", [bool](Get-Process -Name ChatGPT -ErrorAction SilentlyContinue))
    )
    foreach ($r in $rows) {
        if ($r[1]) { Write-Host "$($r[0]): ● 正常" } else { Write-Host "$($r[0]): ○ 停止" }
    }
}

function Cmd-Launch {
    $exe = Find-ChatGPT
    if (-not $exe) { Write-Host "未找到 ChatGPT.exe (可在 config.json 配 chatgpt_path)"; exit 1 }
    if (Get-Process -Name ChatGPT -ErrorAction SilentlyContinue) {
        Write-Host "ChatGPT 已在运行 (需代理参数请先退出再冷启动)"; Start-Process $exe; return
    }
    $cpPort = CfgGet "connect_proxy_port" 8944
    $socksHost = CfgGet "proxy_socks_host" "127.0.0.1"; $socksPort = CfgGet "proxy_socks_port" 7890
    $caAbs = Join-Path $Certs "ca.pem"
    $env:HTTPS_PROXY = "http://127.0.0.1:$cpPort"; $env:https_proxy = $env:HTTPS_PROXY
    $env:ALL_PROXY = "socks5://${socksHost}:${socksPort}"
    $env:NO_PROXY = "localhost,127.0.0.1,::1"
    $env:SSL_CERT_FILE = $caAbs; $env:CODEX_CA_CERTIFICATE = $caAbs; $env:NODE_EXTRA_CA_CERTS = $caAbs
    $env:WS_PROXY = $env:HTTPS_PROXY; $env:WSS_PROXY = $env:HTTPS_PROXY
    Start-Process $exe -ArgumentList "--proxy-server=socks5://${socksHost}:${socksPort}"
    Write-Host "ChatGPT 已带增强参数冷启动"
}

function Cmd-Uninstall {
    if ($Arg2 -ne "--yes") { Write-Host "请使用: engine.ps1 uninstall --yes"; exit 1 }
    Cmd-Stop
    $ct = Join-Path $CodexDir "config.toml"
    if (Test-Path $ct) {
        $raw = Get-Content $ct -Raw
        $raw = ($raw -split "`r?`n" | Where-Object { $_ -notmatch '^chatgpt_base_url' }) -join "`r`n"
        Set-Content $ct $raw -NoNewline -Encoding UTF8
    }
    $envFile = Join-Path $CodexDir ".env"
    if (Test-Path "$envFile.bak-cnhelper") { Copy-Item "$envFile.bak-cnhelper" $envFile -Force; Write-Host ".env 已还原" }
    & certutil -user -delstore Root "MeiCodexHelper Local CA" 2>$null | Out-Null
    Write-Host "已卸载 (证书从信任存储移除; 文件保留在 $AS)"
}

switch ($Command) {
    "install"         { Cmd-Install }
    "start"           { Cmd-Start }
    "stop"            { Cmd-Stop }
    "status"          { Cmd-Status }
    "launch-chatgpt"  { Cmd-Launch }
    "open-logs"       { Start-Process explorer.exe $Logs }
    "open-config"     { if (-not (Test-Path $Cfg)) { New-Item -ItemType Directory -Force -Path $AS | Out-Null; Set-Content $Cfg $DefaultConfig -Encoding UTF8 }; Start-Process notepad.exe $Cfg }
    "uninstall"       { Cmd-Uninstall }
    default           { Write-Host "用法: engine.ps1 {install|start|stop|status|launch-chatgpt|open-logs|open-config|uninstall --yes}" }
}
