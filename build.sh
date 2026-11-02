#!/bin/bash
# 编译并组装 ChatGPT China Helper.app
set -e
cd "$(dirname "$0")"
OUT="build/ChatGPT China Helper.app"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
swiftc -O -framework AppKit ChatGPTChinaHelper/main.swift -o "$OUT/Contents/MacOS/ChatGPTChinaHelper"
cp ChatGPTChinaHelper/Info.plist "$OUT/Contents/Info.plist"
cp Resources/* "$OUT/Contents/Resources/"
chmod +x "$OUT/Contents/Resources/"*.sh
codesign -f -s - --deep "$OUT" 2>/dev/null || true
echo "构建完成: $OUT"
echo "安装: cp -R \"$OUT\" /Applications/"
