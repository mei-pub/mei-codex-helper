#!/bin/bash
# 编译并组装 MeiCodexHelper.app
set -e
cd "$(dirname "$0")"
OUT="build/MeiCodexHelper.app"
# 版本号: 取环境变量 VERSION (CI 由 tag 注入), 否则读 VERSION 文件
VERSION="${VERSION:-$(cat VERSION 2>/dev/null || echo 0.0.1)}"
rm -rf "$OUT"
mkdir -p "$OUT/Contents/MacOS" "$OUT/Contents/Resources"
swiftc -O -parse-as-library -framework SwiftUI -framework AppKit MeiCodexHelper/App.swift -o "$OUT/Contents/MacOS/MeiCodexHelper"
cp MeiCodexHelper/Info.plist "$OUT/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" -c "Set :CFBundleShortVersionString $VERSION" "$OUT/Contents/Info.plist"
echo "版本号: $VERSION"
cp Resources/* "$OUT/Contents/Resources/"
cp Assets/AppIcon.icns "$OUT/Contents/Resources/AppIcon.icns"
chmod +x "$OUT/Contents/Resources/"*.sh
codesign -f -s - --deep "$OUT" 2>/dev/null || true
echo "构建完成: $OUT"
echo "安装: cp -R \"$OUT\" /Applications/ && open /Applications/MeiCodexHelper.app"
