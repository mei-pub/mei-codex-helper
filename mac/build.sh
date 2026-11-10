#!/bin/bash
# 编译并组装 MeiCodexHelper.app (macOS 平台层; 内核来自 ../core)
set -e
cd "$(dirname "$0")"
VERSION="${VERSION:-$(cat ../VERSION 2>/dev/null || echo 0.0.1)}"
OUT="build/MeiCodexHelper.app"
rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
swiftc -O -parse-as-library -framework SwiftUI -framework AppKit MeiCodexHelper/App.swift -o "$OUT/Contents/MacOS/MeiCodexHelper"
cp MeiCodexHelper/Info.plist "$OUT/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" -c "Set :CFBundleShortVersionString $VERSION" "$OUT/Contents/Info.plist"
cp engine.sh netwatch.sh "$OUT/Contents/Resources/"
cp ../core/relay.py ../core/tlsproxy.py ../core/paths.py ../core/__init__.py "$OUT/Contents/Resources/"
chmod +x "$OUT/Contents/Resources/"*.sh
cp Assets/AppIcon.icns "$OUT/Contents/Resources/AppIcon.icns"
codesign -f -s - --deep "$OUT" 2>/dev/null || true
echo "构建完成($VERSION): $OUT"
