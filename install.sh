#!/usr/bin/env bash
# ai-limit menubar app — 一键安装脚本
#
# 用法：
#   curl -fsSL https://raw.githubusercontent.com/zhuchenxi113/ai-limit/main/install.sh | bash
#
# 流程：从 GitHub Releases 获取最新 DMG → 挂载 → 复制到 /Applications → 卸载挂载 → 清理

set -euo pipefail

REPO="zhuchenxi113/ai-limit"
APP_NAME="ai-limit.app"
INSTALL_DIR="/Applications"

# ── 颜色输出 ────────────────────────────────────────────────────────────────
info()  { printf "  \033[34m•\033[0m %s\n" "$*"; }
ok()    { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn()  { printf "  \033[33m!\033[0m %s\n" "$*"; }
die()   { printf "\n\033[31merror:\033[0m %s\n" "$*" >&2; exit 1; }

# ── 环境检查 ─────────────────────────────────────────────────────────────────
[[ "$(uname)" == "Darwin" ]] || die "仅支持 macOS"

# ── 获取最新 Release 下载链接 ────────────────────────────────────────────────
info "查询最新版本…"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"

if command -v curl &>/dev/null; then
  RELEASE_JSON=$(curl -fsSL "$API_URL")
else
  die "未找到 curl，无法继续"
fi

VERSION=$(printf '%s' "$RELEASE_JSON" | grep '"tag_name"' | head -1 | sed 's/.*"tag_name": *"\([^"]*\)".*/\1/')
[[ -n "$VERSION" ]] || die "无法获取版本号，请检查网络或稍后重试"

DMG_URL=$(printf '%s' "$RELEASE_JSON" | grep '"browser_download_url"' | grep '\.dmg"' | head -1 | sed 's/.*"browser_download_url": *"\([^"]*\)".*/\1/')
[[ -n "$DMG_URL" ]] || die "Release ${VERSION} 中未找到 DMG 文件"

ok "最新版本：${VERSION}"

# ── 下载 DMG ─────────────────────────────────────────────────────────────────
TMP_DIR=$(mktemp -d -t ai-limit-install)
DMG_PATH="${TMP_DIR}/ai-limit.dmg"
trap 'rm -rf "$TMP_DIR"' EXIT

info "下载 DMG（${DMG_URL##*/}）…"
curl -fsSL --progress-bar -o "$DMG_PATH" "$DMG_URL"
ok "下载完成"

# ── 挂载 DMG ─────────────────────────────────────────────────────────────────
info "挂载磁盘映像…"
MOUNT_OUT=$(hdiutil attach "$DMG_PATH" -nobrowse -noverify -noautoopen 2>&1)
MOUNT_POINT=$(printf '%s' "$MOUNT_OUT" | grep '/Volumes/' | awk '{print $NF}')
[[ -n "$MOUNT_POINT" ]] || die "挂载失败：\n${MOUNT_OUT}"

# ── 复制 App ─────────────────────────────────────────────────────────────────
SRC="${MOUNT_POINT}/${APP_NAME}"
[[ -d "$SRC" ]] || die "DMG 中未找到 ${APP_NAME}"

if [[ -d "${INSTALL_DIR}/${APP_NAME}" ]]; then
  info "检测到旧版本，先停止并移除…"
  pkill -x "ai-limit" 2>/dev/null || true
  rm -rf "${INSTALL_DIR}/${APP_NAME}"
fi

info "安装到 ${INSTALL_DIR}…"
cp -R "$SRC" "${INSTALL_DIR}/"

# ── 卸载挂载 ─────────────────────────────────────────────────────────────────
hdiutil detach "$MOUNT_POINT" -quiet 2>/dev/null || true

ok "ai-limit ${VERSION} 已安装到 ${INSTALL_DIR}/${APP_NAME}"

# ── 提示 ──────────────────────────────────────────────────────────────────────
printf "\n"
warn "首次启动提示（App 尚未公证）："
printf "    右键点击 ai-limit.app → 打开 → 仍要打开\n"
printf "\n"
info "直接打开："
printf "    open \"${INSTALL_DIR}/${APP_NAME}\"\n"
printf "\n"
