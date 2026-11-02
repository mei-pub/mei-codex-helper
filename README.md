# MeiCodexHelper

ChatGPT macOS 桌面端（Codex 内核）中国网络环境访问增强。

![icon](Assets/AppIcon.iconset/icon_128x128.png)

## 功能

### 核心网络栈
- **指纹中继 Relay**（默认 8943 TLS / 8945 明文）：本地反向代理 → curl_cffi 模拟 Safari TLS 指纹 → SOCKS 出海。绕过 Cloudflare 对"机房 IP + 非浏览器指纹"的 403；内置路径重写（/api 剥离、/backend-api 挂载）、Cookie/Origin/Location 域改写、wss URL 重写与 WebSocket 隧道。
- **TLS 自适应 CONNECT 代理**（默认 8944）：兼容 codex 环境代理机制强改 `HTTPS_PROXY` scheme（首字节嗅探双 scheme）；回环目标直连。
- **一键代理启动 ChatGPT**：Chromium `--proxy-server` + CA/代理环境变量注入（覆盖内嵌 node 运行时）。
- **远程控制支持**：daemon 明文通道（`~/.codex-daemon-home`），绕过其 rustls 不认本地证书的问题。
- **看门狗**：周期体检，异常自动重启。

### GUI（菜单栏 + 窗口）
- 菜单栏盾牌图标：整体状态一目了然，快捷操作（SF Symbols 图标，无 Emoji）
- **控制台**：六项服务状态卡片（异常可单独重启）、启动/重启/停止、诊断报告、实时日志查看（中继/代理双通道、自动刷新）
- **设置**：多配置方案管理（新建/复制/删除/切换启用），可视化编辑全部参数（端口/指纹/上游/远程控制/看门狗周期），"保存并启用"一键应用并重启服务

## 使用
1. 首次：菜单栏 🛡 → 打开设置或直接使用默认配置；点「安装/修复」（或 engine.sh install）
2. 日常：菜单 → **启动 ChatGPT（增强）**（冷启动才注入参数）
3. 观察状态/处理异常：菜单 → **打开控制台**

## 可配置项（设置界面可视化编辑，或直接改 json）
`~/Library/Application Support/MeiCodexHelper/config.json`（多配置存于 `profiles/`，启用者写入 config.json）

| 变量 | 默认 | 说明 |
|---|---|---|
| clash_socks_host / clash_socks_port | 127.0.0.1 / 7890 | Clash Verge 混合端口 |
| relay_tls_port / relay_plain_port | 8943 / 8945 | 中继 TLS/明文端口 |
| connect_proxy_port | 8944 | CONNECT 代理端口 |
| impersonate | safari17_0 | TLS 指纹（chrome 系会被 CF 识别，勿轻易改） |
| upstream_host | chatgpt.com | 上游域 |
| remote_control | true | 远程控制(daemon 明文)支持 |
| watch_interval_sec | 60 | 看门狗周期 |

改端口后建议重跑一次安装（刷新 ~/.codex/config.toml 引用）。

## 文件位置
- 服务/证书/日志/配置：`~/Library/Application Support/MeiCodexHelper/`
- LaunchAgents：`com.income.chatgpt-relay` / `com.income.codex-tlsproxy` / `com.income.chatgpt-netwatch`
- codex 接入点：`~/.codex/config.toml`（chatgpt_base_url）与 `~/.codex/.env`（安装自动备份 `.bak-cnhelper`）

## 构建
```bash
./build.sh          # 需要 Xcode Command Line Tools (swiftc)
```

## 排障速查
| 症状 | 位置 | 常见原因 |
|---|---|---|
| 面板/用量加载失败 | 控制台日志 `-> 200 auth` | rustls 信任失效/curl_cffi 丢失 → 重跑安装 |
| 启动 ChatGPT 后无法联网 | 是否经「启动 ChatGPT」冷启动 | 参数仅冷启动生效 |
| 远程控制开启失败 | 诊断输出 mfa_requirement | 账号策略要求 MFA（服务端 403 会明说） |
| 切换账号后远程控制异常 | 控制台重启 Daemon | daemon 内存残留旧账号 token |
