#!/bin/bash
# ChatGPT China Helper — 看门狗: 每次运行检查三个端口 + relay 功能, 异常自动 kickstart
AS="$HOME/Library/Application Support/ChatGPTChinaHelper"
CFG="$AS/config.json"
PY=/usr/bin/python3
U=$(id -u)
g() { $PY -c "import json;print(json.load(open('$CFG')).get('$1',$2))" 2>/dev/null; }
TLS_PORT=$(g relay_tls_port 8943); PLAIN_PORT=$(g relay_plain_port 8945); CP_PORT=$(g connect_proxy_port 8944)
up() { lsof -nP -iTCP:$1 -sTCP:LISTEN >/dev/null 2>&1; }
[ -n "$TLS_PORT" ] && { up "$TLS_PORT" || launchctl kickstart -k gui/$U/com.income.chatgpt-relay 2>/dev/null; }
[ -n "$CP_PORT" ]   && { up "$CP_PORT"  || launchctl kickstart -k gui/$U/com.income.codex-tlsproxy 2>/dev/null; }
[ -n "$PLAIN_PORT" ] && { up "$PLAIN_PORT" || launchctl kickstart -k gui/$U/com.income.chatgpt-relay 2>/dev/null; }
# relay 功能性检查: 拿到任意 HTTP 状态码即健康; curl 退出码>=22(TLS/HTTP层错误且不是超时)时重启
if [ -n "$TLS_PORT" ]; then
  curl -s -m 5 -o /dev/null --cacert "$AS/certs/ca.pem" "https://127.0.0.1:$TLS_PORT/api/codex/accounts/check" 2>/dev/null
  RC=$?
  [ $RC -ge 22 ] && [ $RC -ne 28 ] && launchctl kickstart -k gui/$U/com.income.chatgpt-relay 2>/dev/null
fi
