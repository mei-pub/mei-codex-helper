#!/bin/bash
# MeiCodexHelper 引擎 — 所有功能的单一入口
# 用法: engine.sh <install|start|stop|restart|restart-service|status|status-json|log|launch-chatgpt|open-config|open-logs|doctor|uninstall>
set -u

APP_NAME="MeiCodexHelper"
AS="$HOME/Library/Application Support/MeiCodexHelper"
OLD_AS="$HOME/Library/Application Support/ChatGPTChinaHelper"
RES="$(cd "$(dirname "$0")" && pwd)"   # app bundle Resources
CFG="$AS/config.json"
LOGS="$AS/logs"
CERTS="$AS/certs"
LABEL_RELAY="com.income.chatgpt-relay"
LABEL_TLS="com.income.codex-tlsproxy"
LABEL_WATCH="com.income.chatgpt-netwatch"
PY=/usr/bin/python3
UID_N=$(id -u)

# ---------- 配置 ----------
cfg_get() {
  if [ ! -f "$CFG" ]; then echo "$2"; return; fi
  $PY -c "import json;d=json.load(open('$CFG'));print(d.get('$1','$2'))" 2>/dev/null || echo "$2"
}

write_default_config() {
  [ -f "$CFG" ] && return 0
  mkdir -p "$AS"
  cat > "$CFG" <<'EOF'
{
  "clash_socks_host": "127.0.0.1",
  "clash_socks_port": 7890,
  "relay_tls_port": 8943,
  "relay_plain_port": 8945,
  "connect_proxy_port": 8944,
  "impersonate": "safari17_0",
  "upstream_host": "chatgpt.com",
  "remote_control": true,
  "watch_interval_sec": 60
}
EOF
  echo "已生成默认配置 $CFG"
}

# ---------- 旧版迁移 ----------
migrate_old() {
  [ -d "$OLD_AS" ] || return 0
  mkdir -p "$AS"
  [ -f "$OLD_AS/config.json" ] && [ ! -f "$CFG" ] && cp "$OLD_AS/config.json" "$CFG" && echo "已迁移旧配置"
  if [ -d "$OLD_AS/certs" ] && [ ! -f "$AS/certs/ca.pem" ]; then
    mkdir -p "$CERTS"; cp "$OLD_AS/certs/"*.pem "$CERTS/" 2>/dev/null && echo "已迁移证书"
  fi
}

# ---------- 证书 ----------
ensure_certs() {
  mkdir -p "$CERTS"
  if [ ! -f "$CERTS/ca.pem" ]; then
    if [ -f "$HOME/.codex/relay-certs/ca.pem" ] && [ -f "$HOME/.codex/relay-certs/cert.pem" ] && [ -f "$HOME/.codex/relay-certs/key.pem" ]; then
      cp "$HOME/.codex/relay-certs/"{ca.pem,cert.pem,key.pem} "$CERTS/"
      echo "已复用现有证书"
    else
      cd "$CERTS"
      openssl req -x509 -newkey rsa:2048 -nodes -keyout ca.key -out ca.pem -days 3650 \
        -subj "/CN=MeiCodexHelper Local CA" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" -addext "keyUsage=critical,keyCertSign,cRLSign" 2>/dev/null
      openssl req -newkey rsa:2048 -nodes -keyout key.pem -out leaf.csr -subj "/CN=localhost" 2>/dev/null
      openssl x509 -req -in leaf.csr -CA ca.pem -CAkey ca.key -CAcreateserial -out cert.pem -days 3650 \
        -extfile <(printf "subjectAltName=DNS:localhost,IP:127.0.0.1\nbasicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n") 2>/dev/null
      cat ca.pem >> cert.pem; rm -f leaf.csr
      echo "已生成新证书链"
    fi
  fi
  if ! security find-certificate -c "MeiCodexHelper Local CA" "$HOME/Library/Keychains/login.keychain-db" >/dev/null 2>&1 \
     && ! security find-certificate -c "ChatGPT Local Relay CA" "$HOME/Library/Keychains/login.keychain-db" >/dev/null 2>&1 \
     && [ -f "$CERTS/ca.pem" ]; then
    echo ">>> 即将弹出钥匙串授权窗口, 请输入密码点「允许」..."
    security add-trusted-cert -r trustRoot -k "$HOME/Library/Keychains/login.keychain-db" "$CERTS/ca.pem" && echo "证书已信任" || echo "⚠️ 证书信任未完成(HTTPS请求会失败)"
  fi
}

# ---------- 依赖 ----------
ensure_curl_cffi() {
  $PY -c "from curl_cffi import requests" 2>/dev/null && return 0
  echo "安装 curl_cffi (经代理)..."
  SOCKS=$(cfg_get clash_socks_host 127.0.0.1):$(cfg_get clash_socks_port 7890)
  $PY -m pip install --user --quiet --proxy "http://$SOCKS" curl_cffi 2>&1 | tail -1
  $PY -c "from curl_cffi import requests" 2>/dev/null && echo "curl_cffi OK" || echo "⚠️ curl_cffi 安装失败"
}

# ---------- codex 配置 ----------
patch_codex_config() {
  CT="$HOME/.codex/config.toml"
  TLS_PORT=$(cfg_get relay_tls_port 8943)
  [ -f "$CT" ] && cp "$CT" "$CT.bak-cnhelper"
  if grep -q '^chatgpt_base_url' "$CT" 2>/dev/null; then
    sed -i '' "s#^chatgpt_base_url *= .*#chatgpt_base_url = \"https://127.0.0.1:$TLS_PORT\"#" "$CT"
  else
    printf 'chatgpt_base_url = "https://127.0.0.1:%s"\n\n' "$TLS_PORT" | cat - "$CT" > /tmp/ct.new && mv /tmp/ct.new "$CT"
  fi
  echo "config.toml 已配置 base_url=https://127.0.0.1:$TLS_PORT"
}

patch_codex_env() {
  EV="$HOME/.codex/.env"
  CP="http://127.0.0.1:$(cfg_get connect_proxy_port 8944)"
  [ -f "$EV" ] && cp "$EV" "$EV.bak-cnhelper"
  cat > "$EV" <<EOF
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
SSL_CERT_FILE=$CERTS/ca.pem
CODEX_CA_CERTIFICATE=$CERTS/ca.pem
EOF
  echo ".env 已配置 (代理→$CP, CA→certs)"
}

setup_remote_control() {
  [ "$(cfg_get remote_control true)" = "True" ] || [ "$(cfg_get remote_control true)" = "true" ] || return 0
  MAIN="$HOME/.codex"; ALT="$HOME/.codex-daemon-home"
  PLAIN_PORT=$(cfg_get relay_plain_port 8945)
  mkdir -p "$ALT"
  sed "s#^chatgpt_base_url *= .*#chatgpt_base_url = \"http://127.0.0.1:$PLAIN_PORT\"#" "$MAIN/config.toml" > "$ALT/config.toml"
  ln -sf "$MAIN/auth.json" "$ALT/auth.json"
  [ -e "$ALT/packages" ] || ln -sf "$MAIN/packages" "$ALT/packages"
  if [ -d "$MAIN/app-server-daemon" ] && [ ! -L "$MAIN/app-server-daemon" ]; then
    mv "$MAIN/app-server-daemon" "$ALT/app-server-daemon"
  fi
  [ -L "$MAIN/app-server-daemon" ] || ln -s "$ALT/app-server-daemon" "$MAIN/app-server-daemon"
  [ -f "$ALT/.env" ] || cp "$MAIN/.env" "$ALT/.env"
  echo "远程控制支持已配置 (daemon 明文 $PLAIN_PORT)"
}

# ---------- LaunchAgents ----------
install_agents() {
  mkdir -p "$HOME/Library/LaunchAgents" "$LOGS"
  WATCH=$(cfg_get watch_interval_sec 60)
  cat > "$HOME/Library/LaunchAgents/$LABEL_RELAY.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict>
<key>Label</key><string>$LABEL_RELAY</string>
<key>ProgramArguments</key><array><string>$PY</string><string>$AS/bin/relay.py</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$LOGS/relay.out</string><key>StandardErrorPath</key><string>$LOGS/relay.err</string>
</dict></plist>
EOF
  cat > "$HOME/Library/LaunchAgents/$LABEL_TLS.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict>
<key>Label</key><string>$LABEL_TLS</string>
<key>ProgramArguments</key><array><string>$PY</string><string>$AS/bin/tlsproxy.py</string></array>
<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
<key>StandardOutPath</key><string>$LOGS/tlsproxy.out</string><key>StandardErrorPath</key><string>$LOGS/tlsproxy.err</string>
</dict></plist>
EOF
  cat > "$HOME/Library/LaunchAgents/$LABEL_WATCH.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict>
<key>Label</key><string>$LABEL_WATCH</string>
<key>ProgramArguments</key><array><string>/bin/bash</string><string>$AS/bin/netwatch.sh</string></array>
<key>StartInterval</key><integer>$WATCH</integer><key>RunAtLoad</key><true/>
<key>StandardErrorPath</key><string>$LOGS/netwatch.err</string>
</dict></plist>
EOF
  echo "LaunchAgents 已生成"
}

load_agents() {
  for L in $LABEL_RELAY $LABEL_TLS $LABEL_WATCH; do
    launchctl unload "$HOME/Library/LaunchAgents/$L.plist" >/dev/null 2>&1
    launchctl load "$HOME/Library/LaunchAgents/$L.plist" >/dev/null 2>&1 && echo "  $L 已加载"
  done
}

# ---------- 状态 ----------
port_up() { lsof -nP -iTCP:$1 -sTCP:LISTEN >/dev/null 2>&1; }
socks_up() { curl -s -m 3 -o /dev/null -x socks5h://127.0.0.1:$1 "https://www.gstatic.com/generate_204" 2>/dev/null; }

collect_status() {  # 每行: id|名称|ok|down
  TLS=$(cfg_get relay_tls_port 8943); PLAIN=$(cfg_get relay_plain_port 8945)
  CP=$(cfg_get connect_proxy_port 8944); SOCKS=$(cfg_get clash_socks_port 7890)
  port_up "$TLS"    && echo "relay|Relay 中继 · HTTPS :$TLS|ok" || echo "relay|Relay 中继 · HTTPS :$TLS|down"
  port_up "$PLAIN"  && echo "relayplain|Relay 中继 · 明文 :$PLAIN|ok" || echo "relayplain|Relay 中继 · 明文 :$PLAIN|down"
  port_up "$CP"     && echo "connect|CONNECT 代理 · :$CP|ok" || echo "connect|CONNECT 代理 · :$CP|down"
  socks_up "$SOCKS" && echo "clash|Clash SOCKS · :$SOCKS|ok" || echo "clash|Clash SOCKS · :$SOCKS|down"
  pgrep -x ChatGPT >/dev/null && echo "chatgpt|ChatGPT 桌面端|ok" || echo "chatgpt|ChatGPT 桌面端|down"
  pgrep -f "app-server --remote-control" >/dev/null && echo "daemon|远程控制 Daemon|ok" || echo "daemon|远程控制 Daemon|down"
}

# ---------- 命令 ----------
cmd_install() {
  echo "== $APP_NAME 安装 =="
  migrate_old
  write_default_config
  mkdir -p "$AS/bin" "$LOGS"
  cp "$RES/relay.py" "$RES/tlsproxy.py" "$RES/netwatch.sh" "$AS/bin/" 2>/dev/null
  chmod +x "$AS/bin/"*.sh
  ensure_certs
  ensure_curl_cffi
  patch_codex_config
  patch_codex_env
  setup_remote_control
  install_agents
  load_agents
  sleep 3
  echo "== 安装完成 =="
  cmd_status
}

cmd_start()   { load_agents; echo "服务已启动"; }
cmd_stop()    {
  for L in $LABEL_RELAY $LABEL_TLS $LABEL_WATCH; do launchctl unload "$HOME/Library/LaunchAgents/$L.plist" >/dev/null 2>&1; done
  echo "服务已停止"
}
cmd_restart() { cmd_stop; sleep 1; cmd_start; }
cmd_restart_service() {
  case "${2:-all}" in
    relay)    launchctl kickstart -k gui/$UID_N/$LABEL_RELAY >/dev/null 2>&1 && echo "Relay 已重启" ;;
    tlsproxy) launchctl kickstart -k gui/$UID_N/$LABEL_TLS >/dev/null 2>&1 && echo "CONNECT 代理已重启" ;;
    watch)    launchctl kickstart -k gui/$UID_N/$LABEL_WATCH >/dev/null 2>&1 && echo "看门狗已重启" ;;
    daemon)   kill $(pgrep -f "app-server --remote-control") 2>/dev/null; sleep 2
              ( CODEX_HOME="$HOME/.codex-daemon-home" "$HOME/.codex/packages/app-server-daemon/current/bin/codex" remote-control start >/dev/null 2>&1 & )
              echo "远程控制 Daemon 已重启" ;;
    *) cmd_restart ;;
  esac
}

cmd_status() {
  collect_status | while IFS='|' read -r id name st; do
    if [ "$st" = "ok" ]; then echo "$name: ● 正常"; else echo "$name: ○ 停止"; fi
  done
}

cmd_status_json() {
  collect_status > /tmp/.mdx_status.$$
  $PY -c "
import json
rows=[l.split('|') for l in open('/tmp/.mdx_status.$$').read().strip().splitlines() if l]
print(json.dumps([{'id':r[0],'name':r[1],'state':('ok' if r[2]=='ok' else 'down')} for r in rows], ensure_ascii=False))
"
  rm -f /tmp/.mdx_status.$$
}

cmd_log() {
  N="${3:-300}"
  case "${2:-relay}" in
    tlsproxy) tail -n "$N" "$LOGS/tlsproxy.log" 2>/dev/null || tail -n "$N" "$LOGS/tlsproxy.out" 2>/dev/null ;;
    *)        tail -n "$N" "$LOGS/relay-access.log" 2>/dev/null || tail -n "$N" "$LOGS/relay.out" 2>/dev/null ;;
  esac
}

cmd_launch_chatgpt() {
  SOCKS="socks5://$(cfg_get clash_socks_host 127.0.0.1):$(cfg_get clash_socks_port 7890)"
  CP="http://127.0.0.1:$(cfg_get connect_proxy_port 8944)"
  if pgrep -x ChatGPT >/dev/null; then echo "ChatGPT 已在运行(需代理参数请先退出再冷启动)"; open -a ChatGPT; exit 0; fi
  open -a ChatGPT \
    --env "HTTPS_PROXY=$CP" --env "https_proxy=$CP" --env "ALL_PROXY=$SOCKS" \
    --env "NO_PROXY=localhost,127.0.0.1,::1" \
    --env "SSL_CERT_FILE=$CERTS/ca.pem" --env "CODEX_CA_CERTIFICATE=$CERTS/ca.pem" \
    --env "NODE_EXTRA_CA_CERTS=$CERTS/ca.pem" \
    --env "WS_PROXY=$CP" --env "WSS_PROXY=$CP" \
    --args --proxy-server="$SOCKS"
  echo "ChatGPT 已带增强参数冷启动"
}

cmd_doctor() {
  echo "== 诊断 =="
  cmd_status
  TLS_PORT=$(cfg_get relay_tls_port 8943)
  echo "-- Relay 自检:"
  curl -s -m 6 --cacert "$CERTS/ca.pem" -o /dev/null -w "  HTTPS 状态码: %{http_code} (401/403=链路通)\n" "https://127.0.0.1:$TLS_PORT/api/codex/accounts/check" || echo "  ✗ 连不上"
  echo "-- rustls 信任:"
  env -i HOME="$HOME" PATH=/usr/bin:/bin SSL_CERT_FILE="$CERTS/ca.pem" "$HOME/.codex/packages/app-server-daemon/current/bin/codex" doctor 2>/dev/null | grep "inference URL" | head -1 | sed 's/^/  /' || echo "  (codex 不可用,跳过)"
  echo "-- 日志目录: $LOGS/"
}

cmd_uninstall() {
  [ "${2:-}" = "--yes" ] || { echo "请使用 engine.sh uninstall --yes 确认"; exit 1; }
  cmd_stop
  CT="$HOME/.codex/config.toml"
  sed -i '' '/^chatgpt_base_url/d' "$CT" 2>/dev/null
  [ -f "$HOME/.codex/.env.bak-cnhelper" ] && cp "$HOME/.codex/.env.bak-cnhelper" "$HOME/.codex/.env" && echo ".env 已还原"
  for L in $LABEL_RELAY $LABEL_TLS $LABEL_WATCH; do rm -f "$HOME/Library/LaunchAgents/$L.plist"; done
  echo "已卸载(证书保留在 $CERTS)"
}

case "${1:-}" in
  install) cmd_install "$@" ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_restart ;;
  restart-service) cmd_restart_service "$@" ;;
  status) cmd_status ;;
  status-json) cmd_status_json ;;
  log) cmd_log "$@" ;;
  launch-chatgpt) cmd_launch_chatgpt ;;
  open-config) mkdir -p "$AS"; [ -f "$CFG" ] || write_default_config; open -t "$CFG" ;;
  open-logs) mkdir -p "$LOGS"; open "$LOGS" ;;
  doctor) cmd_doctor ;;
  uninstall) cmd_uninstall "$@" ;;
  *) echo "用法: engine.sh {install|start|stop|restart|restart-service <relay|tlsproxy|watch|daemon>|status|status-json|log <relay|tlsproxy> [n]|launch-chatgpt|open-config|open-logs|doctor|uninstall --yes}"; exit 1 ;;
esac
