#!/usr/bin/env bash
# 打包 ai-limit.app 为可分发的 DMG。
#
# 流程：
#   1. 检查 dist/ai-limit.app 存在（不存在则提示先 py2app）
#   2. 在临时目录拼一个挂载结构：ai-limit.app + 软链到 /Applications
#   3. hdiutil create UDZO 压缩，输出 dist/ai-limit-<version>.dmg
#
# 依赖：只用系统自带 hdiutil，无需 brew create-dmg。

set -euo pipefail

cd "$(dirname "$0")"

BUNDLE="dist/ai-limit.app"
if [[ ! -d "$BUNDLE" ]]; then
  echo "error: $BUNDLE 不存在。先跑：/opt/homebrew/bin/python3.13 setup.py py2app" >&2
  exit 1
fi

VERSION=$(defaults read "$PWD/$BUNDLE/Contents/Info.plist" CFBundleShortVersionString)
VOLNAME="ai-limit"
DMG_OUT="dist/ai-limit-${VERSION}.dmg"

STAGE=$(mktemp -d -t ai-limit-dmg)
trap 'rm -rf "$STAGE"' EXIT

# 1. 拼挂载结构
cp -R "$BUNDLE" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

# 2. 输出 DMG（UDZO = 压缩、只读，分发标准）
rm -f "$DMG_OUT"
hdiutil create \
  -volname "$VOLNAME" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  "$DMG_OUT" >/dev/null

# 3. 摘要
SIZE=$(du -h "$DMG_OUT" | awk '{print $1}')
echo "✅ DMG 已生成：$DMG_OUT ($SIZE)"
echo
echo "用户安装步骤："
echo "  1. 双击 $DMG_OUT 挂载"
echo "  2. 把 ai-limit.app 拖到 Applications 文件夹"
echo "  3. 首次启动：右键 → 打开 → 仍要打开（绕过 Gatekeeper，因 DMG 未签名公证）"
