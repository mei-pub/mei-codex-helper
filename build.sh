#!/bin/bash
# 编译并组装 MeiCodexHelper.app
set -e
cd "$(dirname "$0")"
OUT="build/MeiCodexHelper.app"
rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
swiftc -O -parse-as-library -framework SwiftUI -framework AppKit MeiCodexHelper/App.swift -o "$OUT/Contents/MacOS/MeiCodexHelper"
cp MeiCodexHelper/Info.plist "$OUT/Contents/Info.plist"
cp Resources/* "$OUT/Contents/Resources/"
cp Assets/AppIcon.icns "$OUT/Contents/Resources/AppIcon.icns"
chmod +x "$OUT/Contents/Resources/"*.sh
codesign -f -s - --deep "$OUT" 2>/dev/null || true
echo "构建完成: $OUT"
echo "安装: cp -R \"$OUT\" /Applications/ && open /Applications/MeiCodexHelper.app"
