# ChatGPT China Helper

ChatGPT macOS 桌面端（Codex 内核）中国网络环境访问增强。

## 功能
- **指纹中继 Relay**（默认 8943 TLS / 8945 明文）：本地反向代理 → curl_cffi 模拟 Safari TLS 指纹 → SOCKS 出海。绕过 Cloudflare 对"机房 IP + 非浏览器指纹"的 403 拦截；内置路径重写（/api 剥离、/backend-api 挂载）、Cookie/Origin/Location 域改写、wss URL 重写与 WebSocket 隧道。
- **TLS 自适应 CONNECT 代理**（默认 8944）：兼容 codex 环境代理机制强改 `HTTPS_PROXY` scheme 的行为（首字节嗅探，http/https 双 scheme）；回环目标直连。
- **一键代理启动 ChatGPT**：Chromium `--proxy-server` + 全套 CA/代理环境变量注入（覆盖内嵌 node 运行时）。
- **远程控制支持**：daemon 专用明文通道（`~/.codex-daemon-home`），绕过其 rustls 不认本地证书的问题。
- **看门狗**：每 60 秒体检，异常自动重启。

## 使用
菜单栏 🛡 图标：
- 🚀 启动 ChatGPT（增强模式）——日常用它启动（冷启动才注入参数）
- 🧰 安装/修复——首次必点（配置 ~/.codex、证书信任、后台服务；已装可随时重跑，幂等）
- 🔧 诊断 / 📝 配置 / 📄 日志

## 可配置变量
`~/Library/Application Support/ChatGPTChinaHelper/config.json`

| 变量 | 默认 | 说明 |
|---|---|---|
| clash_socks_host / clash_socks_port | 127.0.0.1 / 7890 | Clash Verge 混合端口 |
| relay_tls_port / relay_plain_port | 8943 / 8945 | 中继 TLS/明文端口 |
| connect_proxy_port | 8944 | CONNECT 代理端口 |
| impersonate | safari17_0 | curl_cffi 指纹（勿轻易改，chrome 系会被 CF 识别） |
| upstream_host | chatgpt.com | 上游域 |
| remote_control | true | 是否部署远程控制(daemon 明文)支持 |
| watch_interval_sec | 60 | 看门狗周期 |

改完配置后：菜单「🔄 重启增强服务」。**改端口需同步确认 ~/.codex/config.toml 的 base_url 已由安装流程刷新（重跑安装即可）。**

## 文件位置
- 服务/证书/日志：`~/Library/Application Support/ChatGPTChinaHelper/`
- LaunchAgents：`com.income.chatgpt-relay` / `com.income.codex-tlsproxy` / `com.income.chatgpt-netwatch`
- codex 接入点：`~/.codex/config.toml`（chatgpt_base_url）与 `~/.codex/.env`（安装时自动备份 .bak-cnhelper）

## 排障速查
| 症状 | 看 | 常见原因 |
|---|---|---|
| 面板/用量加载失败 | 日志 relay-access.log 是否有 `-> 200 auth` | rustls 信任失效 → 重跑安装；curl_cffi 丢失 → 重跑安装 |
| 启动 ChatGPT 后无法联网 | 是否经「🚀」冷启动（参数仅冷启动生效） | 退出 App 后用菜单项重新启动 |
| 远程控制开启失败 | mfa_requirement 响应 | 账号策略要求开 MFA（服务端 403 会明说） |
| 切换账号后远程控制异常 | 重启远程控制 Daemon（菜单重启服务） | daemon 内存中残留旧账号 token |

## 卸载
菜单「🗑 卸载」或 `engine.sh uninstall --yes`（还原 .env 备份、移除 base_url 与后台服务；证书保留）。
