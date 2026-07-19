#!/usr/bin/env python3
"""ai-limit 菜单栏 App（rumps 版）

独立 macOS App，不依赖 SwiftBar，有自己的图标和进程。
py2app 打包：cd menubar && python3 setup.py py2app
"""
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser

import rumps
import AppKit

_REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from usage import (
    __version__,
    live_claude_plan,
    live_claude_usage,
    live_codex_web_usage,
    _classify_codex_windows,
    ClaudeWebError,
    CodexWebError,
    CodexAuthError,
    TZ_LOCAL,
    epoch_to_local,
    fetch_status_components,
    worst_status,
    CLAUDE_STATUS_COMPONENTS_URL,
    CODEX_STATUS_COMPONENTS_URL,
    CLAUDE_STATUS_PAGE_URL,
    CODEX_STATUS_PAGE_URL,
)


def _detect_system_lang() -> str:
    """GUI App 走 Cocoa 偏好语言（NSLocale），不依赖 POSIX LANG/locale——
    py2app 打包后由 Launch Services 启动，POSIX locale 环境变量通常不反映
    「系统设置 → 语言与地区」里用户的实际选择。"""
    try:
        langs = AppKit.NSLocale.preferredLanguages()
        if langs and str(langs[0]).lower().startswith("zh"):
            return "zh"
    except Exception:
        pass
    return "en"


_SYSTEM_LANG = _detect_system_lang()

# ── 常量 ─────────────────────────────────────────────────────────────────────

_STATE_PATH   = pathlib.Path.home() / ".ai-limit-menubar.json"
_CACHE_PATH   = pathlib.Path.home() / ".ai-limit-menubar-cache.json"
_HISTORY_PATH = pathlib.Path.home() / ".ai-limit-menubar-history.jsonl"
_CACHE_TTL    = 55
_HISTORY_RETENTION_SEC = 2 * 60 * 60
_REFRESH_SEC  = 60               # 兜底默认（= 1 分钟）
_REFRESH_MINS = (1, 2, 3, 4, 5)  # 用户可选的刷新频率（分钟）
_DISPLAY_MODES = ("5h", "7d")
_BAR_STYLES    = ("both", "number", "battery")  # 菜单栏样式：数字+电池 / 仅数字 / 仅电池
_LANGS         = ("zh", "en", "auto")
_SERVICES      = ("claude", "codex")
_MENU_MIN_WIDTH = 290

# 服务状态监控：可勾选的组件（Statuspage 官方组件名，原样用于抓取匹配）。
# Claude Code / App+CLI+Codex API 是默认勾选——覆盖 ai-limit 本身采集用量数据的入口；
# 其余是开源用户可能用到但本工具不采集其用量的周边接口，默认不勾，避免开箱误报。
_CLAUDE_STATUS_ALL = [
    "Claude Code",
    "claude.ai",
    "Claude API (api.anthropic.com)",
    "Claude Console (platform.claude.com)",
]
_CLAUDE_STATUS_DEFAULT = ["Claude Code"]
_CODEX_STATUS_ALL = ["App", "CLI", "Codex API", "VS Code extension", "Codex Web"]
_CODEX_STATUS_DEFAULT = ["App", "CLI", "Codex API"]

_STATUS_COLORS = {
    # Claude Status 官方色系：status.claude.com 的 pageColorData。
    "operational": "#76AD2A",
    "under_maintenance": "#2C84DB",
    "degraded_performance": "#FAA72A",
    "partial_outage": "#E86235",
    "major_outage": "#E04343",
    "critical": "#E04343",
    "unknown": "#B0AEA5",
}
# 父行状态圆点靠右贴的固定坐标（attributed string 右对齐 tab stop 用，
# 单位 pt，从菜单项内容左边距算起）。不随 base_text 长度变化，也不会像
# 多个左 tab 那样把菜单撑宽——菜单固定最小宽度 290pt（_MENU_MIN_WIDTH）。
# 222 是实测值，安全区间很窄：量过 "Claude Code 方案：Pro" 在菜单字体下的
# 自然宽度约 141pt，这个值必须比它大（右对齐 tab 的 location 若小于当前
# 文字已到达的位置，NSTextTab 不生效，状态文字直接消失，不是报错，肉眼
# 只会看到"状态"两个字不见了）；同时又不能比 290 大太多，撑宽整个菜单
# （之前设 230 时实测撑宽了 5pt）。改这个值前用真机截图量两头，别只凭感觉调。
_STATUS_RIGHT_TAB_X = 222
# 子菜单勾选行的显示文案：只有 "claude.ai" 需要覆盖——它是域名，本来就该
# 全小写（不是拼错），但跟同一列表里 "Claude Code"/"Claude API" 这些 Title
# Case 并排时显得很突兀，这里只换显示文案，匹配抓取数据仍用原始 API 组件名。
_STATUS_CHECKBOX_LABEL = {
    "claude.ai": ("Claude 网页版 (claude.ai)", "Claude Web (claude.ai)"),
}
_ZH_WEEKDAYS   = "一二三四五六日"
_EN_WEEKDAYS   = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_EN_RESET_PAD  = 8
_PROJECT_URL   = "https://github.com/zhuchenxi113/ai-limit"
_AUTHOR_URL_ZH = "https://gitee.com/zhuchenxi113"
_AUTHOR_URL_EN = "https://github.com/zhuchenxi113"
_RELEASES_API_URL  = "https://api.github.com/repos/zhuchenxi113/ai-limit/releases/latest"
_RELEASES_PAGE_URL = _PROJECT_URL + "/releases"
# Gitee 国内可直连，作为 GitHub 连不上时（常见于未配代理的用户）的兜底。
# 注意：Gitee 官方 /releases/latest 接口实测有 bug，返回的不是真正最新版
# （曾返回 v0.3.10 而实际最新是 v0.3.11）；改用列表按创建时间倒序取第一条才准确。
_GITEE_RELEASES_API_URL  = "https://gitee.com/api/v5/repos/zhuchenxi113/ai-limit/releases?per_page=1&direction=desc"
_GITEE_RELEASES_PAGE_URL = "https://gitee.com/zhuchenxi113/ai-limit/releases"
# 一键更新：只测试用，指向本地 file:// JSON，覆盖 GitHub/Gitee 两个真实源，
# 用于 Stage 3 端到端联调（不依赖真实公开 Release）。生产环境不设置这个变量。
_RELEASE_FEED_OVERRIDE = os.environ.get("AI_LIMIT_RELEASE_FEED_OVERRIDE")
# Release 资产文件名约定：ai-limit-<version>.dmg（RUNBOOK.md 有明确要求不能改）。
# Gitee releases 接口会混入自动生成的 v<version>.zip/.tar.gz 源码包，必须按文件名过滤。
_DMG_ASSET_RE = re.compile(r"^ai-limit-.*\.dmg$")
_UPDATE_FAILED_MARKER = pathlib.Path.home() / ".ai-limit-update-failed.json"
_UPDATER_SCRIPT_NAME = "ai-limit-updater.sh"
_LAUNCH_AGENT_LABEL = "com.zhuchenxi.ai-limit"
_LAUNCH_AGENT_PLIST = pathlib.Path.home() / "Library/LaunchAgents" / f"{_LAUNCH_AGENT_LABEL}.plist"
_APP_EXECUTABLE     = pathlib.Path("/Applications/AI Limit.app/Contents/MacOS/ai-limit")

# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _login_item_enabled():
    return _LAUNCH_AGENT_PLIST.exists()

def _set_login_item(enabled: bool):
    if enabled:
        _LAUNCH_AGENT_PLIST.parent.mkdir(parents=True, exist_ok=True)
        _LAUNCH_AGENT_PLIST.write_text(
            f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{_LAUNCH_AGENT_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{_APP_EXECUTABLE}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
</dict>
</plist>
""",
            encoding="utf-8",
        )
    else:
        try:
            _LAUNCH_AGENT_PLIST.unlink()
        except FileNotFoundError:
            pass

def _tr(lang, zh, en):
    return en if lang == "en" else zh

def _version_tuple(v: str):
    # 容忍预发布后缀（如 0.3.13-dev / 0.3.13-rc1）：每段只取前导数字，无数字记 0，
    # 避免 int("13-dev") 抛 ValueError 导致「检查更新」静默不弹窗。
    out = []
    for p in v.lstrip("v").split("."):
        m = re.match(r"\d+", p)
        out.append(int(m.group()) if m else 0)
    return tuple(out)

def _show_alert(title, message, ok, cancel=None) -> bool:
    """rumps.alert() 包的是 AppKit 已废弃的 NSAlert 便捷构造器
    （alertWithMessageText_defaultButton_alternateButton_otherButton_informativeTextWithFormat_），
    在当前 macOS 版本下静默不弹窗、直接返回——实测确认（见 lessons）。
    这里改用现代 NSAlert API（alloc/init + setMessageText_ + addButtonWithTitle_）自己拼，可正常显示。
    返回是否点了第一个按钮（ok）。"""
    alert = AppKit.NSAlert.alloc().init()
    alert.setMessageText_(title)
    alert.setInformativeText_(message)
    alert.addButtonWithTitle_(ok)
    if cancel:
        alert.addButtonWithTitle_(cancel)
    return alert.runModal() == AppKit.NSAlertFirstButtonReturn

def _pick_dmg_asset(assets):
    """从 Release assets[] 里挑出 ai-limit-<version>.dmg。Gitee 会混入自动生成的
    源码包（v0.3.19.zip/.tar.gz），必须按文件名过滤，不能直接取 assets[0]。
    找不到返回 (None, None)（防御性：某次发版忘了传 DMG 资产）。"""
    for a in assets or []:
        name = a.get("name", "")
        if _DMG_ASSET_RE.match(name):
            return a.get("browser_download_url"), name
    return None, None

def _fetch_latest_release_info(timeout=6) -> dict:
    """后台线程调用：查最新 Release tag + DMG 资产下载链接。优先 GitHub；连不上
    （常见于未配代理的用户，GitHub 在国内常被墙）时退到 Gitee（国内可直连）。
    不抛异常，两边都失败才返回 {"error": True}。"""
    import urllib.request

    def _get_json(url):
        req = urllib.request.Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "ai-limit-menubar"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())

    def _result(latest, source, assets):
        out = {"latest": latest, "source": source}
        asset_url, asset_name = _pick_dmg_asset(assets)
        if asset_url:
            out["asset_url"] = asset_url
            out["asset_name"] = asset_name
        return out

    # 仅测试用：指向本地 file://JSON，覆盖 GitHub/Gitee 两个真实源，用于离线
    # 端到端联调（Stage 3），不发布真实公开 Release 也能走通全流程。
    if _RELEASE_FEED_OVERRIDE:
        try:
            data = _get_json(_RELEASE_FEED_OVERRIDE)
            return _result(data["tag_name"].lstrip("v"), "github", data.get("assets"))
        except Exception:
            return {"error": True}

    try:
        data = _get_json(_RELEASES_API_URL)
        return _result(data["tag_name"].lstrip("v"), "github", data.get("assets"))
    except Exception:
        pass

    try:
        data = _get_json(_GITEE_RELEASES_API_URL)
        return _result(data[0]["tag_name"].lstrip("v"), "gitee", data[0].get("assets"))
    except Exception:
        return {"error": True}

# ── 一键更新：下载 + 签名公证校验 ────────────────────────────────────────────
# 这两个函数故意设计成不依赖 self，可以脱离整个 rumps App 独立测试
# （见私仓 docs/adr/0004-in-app-auto-update.md 的 Stage 1 测试策略）。

class _UpdateFailed(Exception):
    """下载/校验任一步失败时抛出，被上层统一捕获归一化成
    {"ok": False, "reason": ..., "detail": ...} 结果。"""
    def __init__(self, reason, detail):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}")

_MAX_DOWNLOAD_BYTES = 500 * 1024 * 1024  # 硬上限，防止响应异常导致无限写入

def _download_release_dmg(url, dest_dir, timeout=30, total_timeout=600):
    """下载 Release DMG 到 dest_dir/update.dmg。分块读取，Content-Length 存在时
    核对字节数一致，全部写完才 os.replace 原子改名——中断产物（.part）不会被
    误认成下载完成。磁盘空间检查基于 dest_dir 所在卷：标准单卷 Mac 上 tmp 目录
    和 /Applications 是同一个 APFS 容器，检查 dest_dir 足够，不需要分别查两处。"""
    import urllib.request

    dest_dir = pathlib.Path(dest_dir)
    part_path = dest_dir / "update.dmg.part"
    final_path = dest_dir / "update.dmg"

    req = urllib.request.Request(url, headers={"User-Agent": "ai-limit-menubar"})
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except Exception as e:
        raise _UpdateFailed("download_failed", f"无法连接下载地址：{e}") from e

    with resp:
        declared_size = resp.headers.get("Content-Length")
        declared_size = int(declared_size) if declared_size and declared_size.isdigit() else None

        if declared_size:
            need = declared_size * 3
            free = shutil.disk_usage(dest_dir).free
            if free < need:
                raise _UpdateFailed(
                    "insufficient_disk_space",
                    f"磁盘空间不足：需要约 {need // (1024 * 1024)} MB，"
                    f"剩余 {free // (1024 * 1024)} MB",
                )

        start = time.monotonic()
        written = 0
        try:
            with open(part_path, "wb") as f:
                while True:
                    if time.monotonic() - start > total_timeout:
                        raise _UpdateFailed("timeout", "下载耗时过长")
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > _MAX_DOWNLOAD_BYTES:
                        raise _UpdateFailed("download_failed", "下载内容超出预期大小上限")
                    f.write(chunk)
        except _UpdateFailed:
            raise
        except Exception as e:
            raise _UpdateFailed("download_failed", str(e)) from e

    if written == 0:
        raise _UpdateFailed("download_failed", "下载内容为空")
    if declared_size is not None and written != declared_size:
        raise _UpdateFailed(
            "download_failed",
            f"下载字节数不符：期望 {declared_size}，实际 {written}",
        )

    os.replace(part_path, final_path)
    return final_path

def _detach_dmg(mnt_dir, attempts=3):
    for _ in range(attempts):
        proc = subprocess.run(
            ["hdiutil", "detach", str(mnt_dir), "-quiet"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            return
        time.sleep(1)
    subprocess.run(["hdiutil", "detach", str(mnt_dir), "-quiet", "-force"],
                    capture_output=True, text=True, timeout=30)

def _verify_dmg(dmg_path, expected_version, dest_dir):
    """签名公证三连校验（与 RUNBOOK.md 发版校验同一套标准，不发明新标准）+
    版本号交叉核对。全部通过后把 .app 复制出挂载点（detach 后挂载点内容不再
    可用），返回校验通过的本地 .app 路径。任一步失败抛 _UpdateFailed。"""
    dmg_path = pathlib.Path(dmg_path)
    dest_dir = pathlib.Path(dest_dir)

    proc = subprocess.run(
        ["xcrun", "stapler", "validate", str(dmg_path)],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0 or "The validate action worked!" not in proc.stdout:
        raise _UpdateFailed("stapler_failed", (proc.stdout + proc.stderr).strip()[:500])

    # spctl 的判定输出在 stderr，不是 stdout——实测确认，不能只看 stdout。
    proc = subprocess.run(
        ["spctl", "--assess", "--type", "install", "--verbose", str(dmg_path)],
        capture_output=True, text=True, timeout=30,
    )
    if (proc.returncode != 0
            or "accepted" not in proc.stderr
            or "Notarized Developer ID" not in proc.stderr):
        raise _UpdateFailed("spctl_failed", (proc.stdout + proc.stderr).strip()[:500])

    mnt_dir = dest_dir / "mnt"
    mnt_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["hdiutil", "attach", str(dmg_path), "-mountpoint", str(mnt_dir),
         "-nobrowse", "-readonly", "-quiet"],
        capture_output=True, text=True, timeout=60,
    )
    if proc.returncode != 0:
        raise _UpdateFailed("mount_failed", (proc.stdout + proc.stderr).strip()[:500])

    try:
        apps = list(mnt_dir.glob("*.app"))  # 不硬编码 "AI Limit.app"，对未来改名更健壮
        if len(apps) != 1:
            raise _UpdateFailed(
                "app_not_found",
                f"挂载点里找到 {len(apps)} 个 .app，期望恰好 1 个",
            )
        mounted_app = apps[0]

        # 只检查签名（Authority=Developer ID Application），不要求内部 .app 自己
        # 也带 "Notarization Ticket=stapled"——标准 sign-and-notarize.sh 流程只
        # staple 外层 DMG，不单独 staple 里面的 .app（实测确认：正常构建的 DMG
        # 挂载后 codesign -dv 内部 .app 没有这一行，只有历史事故修复时额外手工
        # staple 过的那份特殊 DMG 才有）。公证/notarized 这件事已经由上面 DMG
        # 级别的 stapler validate + spctl --assess 判定过，这里不重复要求。
        proc = subprocess.run(
            ["codesign", "-dv", "--verbose=2", str(mounted_app)],
            capture_output=True, text=True, timeout=30,
        )
        combined = proc.stdout + proc.stderr
        if proc.returncode != 0 or "Authority=Developer ID Application" not in combined:
            raise _UpdateFailed("codesign_failed", combined.strip()[:500])

        # 版本号交叉核对：防止资产链接指向错误/过期的包（超出 RUNBOOK 三连，
        # 呼应"额度数据失败不能沿用旧值"同一条原则——不能假装校验通过）。
        proc = subprocess.run(
            ["plutil", "-extract", "CFBundleShortVersionString", "raw",
             str(mounted_app / "Contents/Info.plist")],
            capture_output=True, text=True, timeout=10,
        )
        actual_version = proc.stdout.strip()
        if proc.returncode != 0 or actual_version != expected_version:
            raise _UpdateFailed(
                "version_mismatch",
                f"DMG 内版本号 {actual_version!r} 与预期 {expected_version!r} 不符",
            )

        verified_dir = dest_dir / "verified"
        verified_dir.mkdir(parents=True, exist_ok=True)
        target = verified_dir / mounted_app.name
        shutil.copytree(mounted_app, target, symlinks=True)  # symlinks=True 等价 cp -R
        return target
    finally:
        _detach_dmg(mnt_dir)

def _native_bar(pct, width=4):
    filled = round(max(0, min(100, pct)) / 100 * width)
    return "▰" * filled + "▱" * (width - filled)

def _fmt_plan(plan, lang="zh"):
    if not plan or plan == "?":
        return ""
    plan = str(plan).replace("_", " ").title()
    return f" Plan: {plan}" if lang == "en" else f" 方案：{plan}"

def _fmt_reset_dt(dt, lang):
    today = datetime.datetime.now(TZ_LOCAL).date()
    target = dt.date()
    days = (target - today).days
    next_week = target.isocalendar()[:2] > today.isocalendar()[:2]
    if lang == "en":
        if days == 0:    wd = "today"
        elif days == 1:  wd = "tomorrow"
        elif days == 2:  wd = "2 days"
        elif next_week:  wd = f"next {_EN_WEEKDAYS[dt.weekday()]}"
        else:            wd = _EN_WEEKDAYS[dt.weekday()]
        return f"{dt:%H:%M}  {wd}"
    if days == 0:    wd = "今天"
    elif days == 1:  wd = "明天"
    elif days == 2:  wd = "后天"
    elif next_week:  wd = f"下周{_ZH_WEEKDAYS[dt.weekday()]}"
    else:            wd = f"周{_ZH_WEEKDAYS[dt.weekday()]}"
    if len(wd) < 3:
        wd += "　" * (3 - len(wd))
    return f"{wd} {dt:%H:%M}"

def _fmt_reset_epoch(epoch, lang="zh"):
    try:
        return _fmt_reset_dt(epoch_to_local(int(epoch)), lang)
    except Exception:
        return "?"

def _fmt_reset_iso(iso, lang="zh"):
    try:
        return _fmt_reset_dt(datetime.datetime.fromisoformat(iso).astimezone(TZ_LOCAL), lang)
    except Exception:
        return "?"

def _window_shorthand(window_minutes):
    """按窗口实际分钟数生成 "5h"/"7d" 这类短标签，不假设 Codex 固定是 5h+7d 两档。

    2026-07-13 起 OpenAI 后端把 Codex 的 5 小时窗口并入了周窗口——primary_window
    的 limit_window_seconds 变成 604800（7 天），但字段位置仍叫 primary。之前这里
    直接硬编码"primary=5h/secondary=7d"，导致周额度被贴上"5h"标签显示。
    """
    if not window_minutes:
        return None
    hours = window_minutes / 60
    if hours < 24:
        return f"{round(hours) or 1}h"
    return f"{round(hours / 24)}d"

# ── 状态 / 缓存 ──────────────────────────────────────────────────────────────

def _load_state():
    # lang: "auto"（默认）= 跟随系统，每次启动按 NSLocale 实时判定；
    # "zh"/"en" = 用户在菜单里显式选过，永久优先于系统语言。
    state = {"global": "5h", "lang": "auto",
             "bar_services": list(_SERVICES),    # 菜单栏图标显示哪些（不允许全空）
             "panel_services": list(_SERVICES),  # 详情面板显示哪些（允许全空）
             "bar_style": "both",                # 菜单栏样式：both/number/battery
             "refresh_min": 1,
             "claude_status_components": list(_CLAUDE_STATUS_DEFAULT),  # 允许全空=不显示状态点
             "codex_status_components": list(_CODEX_STATUS_DEFAULT)}
    try:
        raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            if raw.get("global") in _DISPLAY_MODES:
                state["global"] = raw["global"]
            if raw.get("lang") in _LANGS:
                state["lang"] = raw["lang"]
            # 迁移：旧版本只有单一 services 字段，同时充当菜单栏与面板
            legacy = None
            if isinstance(raw.get("services"), list):
                legacy = [s for s in raw["services"] if s in _SERVICES]
            # bar_services 不允许全空：空则忽略，回退到旧 services 或默认
            if isinstance(raw.get("bar_services"), list):
                f = [s for s in raw["bar_services"] if s in _SERVICES]
                if f:
                    state["bar_services"] = f
            elif legacy:
                state["bar_services"] = legacy
            # panel_services 允许全空（用户可只看菜单栏）
            if isinstance(raw.get("panel_services"), list):
                state["panel_services"] = [s for s in raw["panel_services"] if s in _SERVICES]
            elif legacy is not None:
                state["panel_services"] = legacy
            if raw.get("bar_style") in _BAR_STYLES:
                state["bar_style"] = raw["bar_style"]
            if raw.get("refresh_min") in _REFRESH_MINS:
                state["refresh_min"] = raw["refresh_min"]
            if isinstance(raw.get("claude_status_components"), list):
                state["claude_status_components"] = [
                    c for c in raw["claude_status_components"] if c in _CLAUDE_STATUS_ALL]
            if isinstance(raw.get("codex_status_components"), list):
                state["codex_status_components"] = [
                    c for c in raw["codex_status_components"] if c in _CODEX_STATUS_ALL]
    except Exception:
        pass
    return state

def _save_state(state):
    try:
        _STATE_PATH.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass

def _load_cache():
    try:
        raw = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        age = datetime.datetime.now().timestamp() - float(raw.get("cached_at", 0))
        if age <= _CACHE_TTL:
            return raw.get("claude"), raw.get("codex")
    except Exception:
        pass
    return None, None

def _save_cache(claude, codex):
    try:
        _CACHE_PATH.write_text(
            json.dumps({
                "cached_at": datetime.datetime.now().timestamp(),
                "claude": claude,
                "codex": codex,
            }, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass

def _history_snapshot(data):
    if data is None:
        return None
    if isinstance(data, dict) and "error" in data:
        return {"error": str(data.get("error", ""))[:200]}
    if not isinstance(data, dict):
        return None
    return {
        "5h_left": data.get("5h_left"),
        "7d_left": data.get("7d_left"),
        "5h_reset": data.get("5h_reset"),
        "7d_reset": data.get("7d_reset"),
        "plan": data.get("plan"),
    }

def _append_history(claude, codex):
    """保留最近 2 小时菜单栏刷新结果，用于事后解释额度跳变。

    只写归一化后的百分比/重置时间/错误文本，不记录 cookie、组织 ID、原始响应
    或请求头。
    """
    try:
        now = datetime.datetime.now(TZ_LOCAL)
        entry = {
            "ts": now.isoformat(timespec="seconds"),
            "epoch": now.timestamp(),
        }
        if claude is not None:
            entry["claude"] = _history_snapshot(claude)
        if codex is not None:
            entry["codex"] = _history_snapshot(codex)
        if "claude" not in entry and "codex" not in entry:
            return

        cutoff = entry["epoch"] - _HISTORY_RETENTION_SEC
        kept = []
        try:
            for line in _HISTORY_PATH.read_text(encoding="utf-8").splitlines():
                try:
                    old = json.loads(line)
                except Exception:
                    continue
                if float(old.get("epoch", 0)) >= cutoff:
                    kept.append(old)
        except FileNotFoundError:
            pass
        kept.append(entry)
        _HISTORY_PATH.write_text(
            "\n".join(json.dumps(row, ensure_ascii=False) for row in kept) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass

# ── 数据获取 ─────────────────────────────────────────────────────────────────

def _fetch_claude(lang):
    import socket, urllib.error
    try:
        data = live_claude_usage()
        five_h = data.get("five_hour") or {}
        seven_d = data.get("seven_day") or {}
        try:
            plan = live_claude_plan()
        except Exception:
            plan = None
        return {
            "5h_left":  int(round(100 - float(five_h.get("utilization", 0)))),
            "7d_left":  int(round(100 - float(seven_d.get("utilization", 0)))),
            "5h_reset": five_h.get("resets_at"),
            "7d_reset": seven_d.get("resets_at"),
            "plan":     plan,
        }
    except ClaudeWebError as e:
        kind = getattr(e, "kind", "generic")
        if kind == "cloudflare":
            msg = _tr(lang, "被拦截，打开用量页勿关", "Blocked, open Claude usage, keep open")
        elif kind == "auth":
            msg = _tr(lang, "需在浏览器重新登录 claude.ai", "Re-login at claude.ai in browser")
        else:
            msg = str(e)
            if "JSON" in msg or "DOCTYPE" in msg or "html" in msg.lower():
                msg = _tr(lang, "网络不可用或需重新登录 claude.ai", "Network error or re-login at claude.ai required")
        return {"error": msg}
    except (socket.timeout, TimeoutError):
        return {"error": _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")}
    except urllib.error.URLError:
        return {"error": _tr(lang, "网络不可用", "Network unavailable")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

def _fetch_codex(lang):
    import socket, urllib.error
    try:
        _ts, rl = live_codex_web_usage()
        # 按窗口实际时长分类（≤6h 归"短档"，其余归"长档"），不按 primary/secondary
        # 字段位置——2026-07-13 OpenAI 临时移除 5 小时限额后，唯一剩下的窗口出现在
        # primary_window 字段里，但实际时长是 7 天。缺的那一档保留 None，交给渲染层
        # 显示 "?" 占位，而不是隐藏整行：官方说明是"temporarily"，随时可能恢复，
        # 隐藏/复现整行会让菜单栏布局跟着 OpenAI 开关这个限额忽隐忽现。
        short_win, long_win = _classify_codex_windows(rl)
        return {
            "5h_left":  int(round(100 - short_win.get("used_percent", 0))) if short_win else None,
            "7d_left":  int(round(100 - long_win.get("used_percent", 0))) if long_win else None,
            "5h_reset": short_win.get("resets_at") if short_win else None,
            "7d_reset": long_win.get("resets_at") if long_win else None,
            "5h_label": _window_shorthand(short_win.get("window_minutes")) if short_win else "5h",
            "7d_label": _window_shorthand(long_win.get("window_minutes")) if long_win else "7d",
            "plan":     rl.get("plan_type") or "?",
        }
    except CodexAuthError:
        return {"error": _tr(lang,
            "无 Codex 权限（可能未订阅或需重新登录）",
            "No Codex access (subscription required or re-login needed)")}
    except CodexWebError as e:
        msg = str(e)
        if "timed out" in msg or "urlopen" in msg:
            msg = _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")
        return {"error": msg}
    except (socket.timeout, TimeoutError):
        return {"error": _tr(lang, "网络超时，请稍后重试", "Network timeout, please retry later")}
    except urllib.error.URLError:
        return {"error": _tr(lang, "网络不可用", "Network unavailable")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}

def _fetch_status(components_url):
    """包一层 usage.fetch_status_components：把失败的 None 转成字符串 "unknown"，
    跟"服务被禁用没抓"用的 None 区分开——调用方看到 "unknown" 就必须显示 ❓，
    看到 None 就维持上次的值（服务被禁用，不是失败）。"""
    components = fetch_status_components(components_url)
    return components if components is not None else "unknown"

# ── AppKit 辅助 ───────────────────────────────────────────────────────────────

def _status_button(app):
    """返回 NSStatusItem.button()；rumps 在不同版本里把它存在不同属性下。"""
    # 已知 rumps 0.4 在 _nsapp.nsstatusitem，但版本间不一致；做一次探测
    candidates = ("_status_item", "_status_bar_item", "_nsstatusitem")
    for attr in candidates:
        item = getattr(app, attr, None)
        if item and hasattr(item, "button"):
            return item.button()
    # rumps 0.4.x 路径：app._nsapp.nsstatusitem
    nsapp = getattr(app, "_nsapp", None)
    if nsapp is not None:
        item = getattr(nsapp, "nsstatusitem", None)
        if item and hasattr(item, "button"):
            return item.button()
    # 兜底：扫一遍 app 所有属性，找一个 .button() 看起来对的
    for name in dir(app):
        if name.startswith("__"):
            continue
        try:
            item = getattr(app, name)
        except Exception:
            continue
        if item is not None and hasattr(item, "button") and callable(getattr(item, "button", None)):
            try:
                btn = item.button()
                if hasattr(btn, "setTitle_") and hasattr(btn, "setImage_"):
                    return btn
            except Exception:
                continue
    return None


def _set_bar_title(app, text):
    """纯文字标题（用作 SF Symbol 不可用时的兜底）。"""
    btn = _status_button(app)
    if btn is not None:
        btn.setImage_(None)
        btn.setAttributedTitle_(AppKit.NSAttributedString.alloc().initWithString_(""))
        btn.setTitle_(text)
        btn.setImagePosition_(0)  # NSNoImage
        return
    app.title = text


def _sf_battery_image(pct, point_size=14):
    """返回对应百分比的 SF Symbol 电池 NSImage（5 档量化）。

    粒度：0(<13) / 25 / 50 / 75 / 100(≥88)。
    不在这里上色——会作为 template 一起整合进 composite，由 AppKit 在状态
    栏上下文里和系统 Wi-Fi、电池等一起决定实际颜色（vibrancy/明暗自适应）。
    """
    if pct >= 88:
        name = "battery.100"
    elif pct >= 63:
        name = "battery.75"
    elif pct >= 38:
        name = "battery.50"
    elif pct >= 13:
        name = "battery.25"
    else:
        name = "battery.0"
    img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        return None
    cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        point_size, AppKit.NSFontWeightMedium
    )
    return img.imageWithSymbolConfiguration_(cfg)


def _battery_attachment(pct, font):
    """SF Symbol 电池包成 NSTextAttachment，可塞进 NSAttributedString 里跟文字一行排。

    image 设 template，菜单栏会把它当系统图标处理（vibrancy + 亮暗自适应），
    跟 Wi-Fi / 系统电池图标在同一渲染通道。
    """
    bat = _sf_battery_image(pct)
    if bat is None:
        return None
    bat.setTemplate_(True)
    attach = AppKit.NSTextAttachment.alloc().init()
    attach.setImage_(bat)
    sz = bat.size()
    # 垂直微调：让电池中线大致对齐文字中线
    y_offset = (font.capHeight() - sz.height) / 2
    attach.setBounds_(AppKit.NSMakeRect(0, y_offset, sz.width, sz.height))
    return AppKit.NSAttributedString.attributedStringWithAttachment_(attach)


def _nscolor_from_hex(hex_color):
    raw = hex_color.lstrip("#")
    try:
        r = int(raw[0:2], 16) / 255
        g = int(raw[2:4], 16) / 255
        b = int(raw[4:6], 16) / 255
    except Exception:
        r = g = b = 0.6
    return AppKit.NSColor.colorWithCalibratedRed_green_blue_alpha_(r, g, b, 1.0)


def _status_dot_attachment(status, font):
    size = 9.0
    img = AppKit.NSImage.alloc().initWithSize_(AppKit.NSMakeSize(size, size))
    img.lockFocus()
    color = _nscolor_from_hex(_STATUS_COLORS.get(status, _STATUS_COLORS["unknown"]))
    color.setFill()
    AppKit.NSBezierPath.bezierPathWithOvalInRect_(
        AppKit.NSMakeRect(0, 0, size, size)
    ).fill()
    img.unlockFocus()

    attach = AppKit.NSTextAttachment.alloc().init()
    attach.setImage_(img)
    y_offset = (font.capHeight() - size) / 2
    attach.setBounds_(AppKit.NSMakeRect(0, y_offset, size, size))
    return AppKit.NSAttributedString.attributedStringWithAttachment_(attach)


def _render_attributed_title(items, style="both"):
    """构建状态栏 attributed title：文字交给 NSStatusBarButton 原生渲染（拿到
    系统 vibrancy 和亮暗自适应），电池作为内联 template image 附件。

    旧方案是把整条画成位图（NSImage.lockFocus + labelColor），但 bitmap 里
    的文字是一次性栅格化的灰度，拿不到状态栏文字的 vibrancy，视觉上比系统
    时钟、菜单文字偏暗。
    """
    font = AppKit.NSFont.menuBarFontOfSize_(0)
    text_attrs = {AppKit.NSFontAttributeName: font}
    mas = AppKit.NSMutableAttributedString.alloc().init()

    def append_text(s):
        mas.appendAttributedString_(
            AppKit.NSAttributedString.alloc().initWithString_attributes_(s, text_attrs)
        )

    for i, (label, pct, err) in enumerate(items):
        prefix = "  " if i > 0 else ""
        if err:
            append_text(f"{prefix}{label} ⚠️")
            continue
        if style == "number":
            append_text(f"{prefix}{label} {pct}%")
        elif style == "battery":
            append_text(f"{prefix}{label} ")
            bat_attach = _battery_attachment(pct, font)
            if bat_attach is not None:
                mas.appendAttributedString_(bat_attach)
        else:  # both：数字 + 电池
            append_text(f"{prefix}{label} {pct}% ")
            bat_attach = _battery_attachment(pct, font)
            if bat_attach is not None:
                mas.appendAttributedString_(bat_attach)

    if mas.length() == 0:
        # 冷启动还没抓到数据：菜单栏不允许全空，故空列表只可能是加载态，
        # 显示中性「加载中」省略号，不要用 ⚠️（那是真·抓取失败的语义）
        append_text("ai-limit…")
    return mas


def _set_bar_with_batteries(app, items, style="both"):
    """把 attributed title（文字 + 电池附件）安到状态栏按钮上。"""
    btn = _status_button(app)
    if btn is None:
        raise RuntimeError("no status button")
    btn.setImage_(None)
    btn.setTitle_("")
    btn.setAttributedTitle_(_render_attributed_title(items, style))

def _noop(_):
    """无副作用 callback，仅用于让 macOS 把无动作菜单项也按常规文字色渲染。
    AppKit 会把 NSMenuItem.target=nil 的项自动灰化，setEnabled_(True) 也救不了；
    挂一个真实 callback（哪怕什么都不做）才会让 macOS 视为正常项。"""
    pass


def _disable(menu_item):
    """让菜单项显式灰色（仅用于'上次刷新'这种刻意的次要信息）。"""
    menu_item._menuitem.setEnabled_(False)
    return menu_item


def _inert(menu_item):
    """挂 no-op callback，让 macOS 按常规文字色渲染（不灰），点击无效果。"""
    menu_item.set_callback(_noop)
    return menu_item

def _status_info(raw, selected, lang):
    """算父行要贴右边的状态信息：'状态 + 小圆点'，不带组件名（具体是哪个组件
    点开子菜单看），没有可显示内容时返回空串。

    raw: None(还没抓到，冷启动瞬间)/ "unknown"(抓取失败) / list(成功，全量组件)
    selected: 用户在子菜单里勾选的组件名列表，允许为空（不显示状态点）
    "状态/Status" 前缀：不加的话圆点容易被误认成额度充足的信号，跟同一面板
    里的百分比数字混淆。
    """
    if not selected or raw is None:
        return None
    label = _tr(lang, "状态", "Status")
    result = None if raw == "unknown" else worst_status(raw, selected)
    if result is None:
        return label, "unknown"
    status, _name = result
    return label, status


def _set_header_title(menu_item, base_text, status_info):
    """父行标题：base_text 靠左正常渲染；status_info（若非空）用 attributed
    string 的右对齐 tab stop 贴到 _STATUS_RIGHT_TAB_X 这个固定坐标。

    为什么不用普通 title + 多个 '\\t'：左 tab 只会跳到"当前文字末尾之后的
    第 N 个 tab stop"，base_text 长度一变（"Pro" vs "Plus"）落点就跟着漂移，
    两行对不齐；而且为了够到右边，塞多个 tab 会把 NSMenu 的自适应宽度顶大
    （踩过这个坑，菜单变得很宽）。右对齐 tab stop 是绝对坐标，不看前面文字
    多长都精确落在同一列，且不会撑宽菜单。
    """
    ns_item = menu_item._menuitem
    if not status_info:
        ns_item.setAttributedTitle_(None)
        menu_item.title = base_text
        return
    status_label, status = status_info
    para = AppKit.NSMutableParagraphStyle.alloc().init()
    tab = AppKit.NSTextTab.alloc().initWithTextAlignment_location_options_(
        AppKit.NSTextAlignmentRight, _STATUS_RIGHT_TAB_X, {}
    )
    para.setTabStops_([tab])
    attrs = {
        AppKit.NSParagraphStyleAttributeName: para,
        AppKit.NSFontAttributeName: AppKit.NSFont.menuFontOfSize_(0),
    }
    font = AppKit.NSFont.menuFontOfSize_(0)
    attributed = AppKit.NSMutableAttributedString.alloc().init()
    attributed.appendAttributedString_(
        AppKit.NSAttributedString.alloc().initWithString_attributes_(
            f"{base_text}\t{status_label} ", attrs)
    )
    dot = _status_dot_attachment(status, font)
    attributed.appendAttributedString_(dot)
    ns_item.setAttributedTitle_(attributed)


_ERROR_ROW_MAX_WIDTH = 210.0
# 异常态详情行（⚠️ 出现时）的安全像素宽度上限，必须小于 _MENU_MIN_WIDTH（290pt）。
# NSMenu 没有 maximumWidth，宽度由"最宽的那一行菜单项"自适应撑出来；正常态的
# _detail_text / _set_header_title 都靠固定 tab-stop/字符预算精心控制过，不会超；
# 但异常态曾经只按字符数 [:60] 截断错误文案，跟真实渲染像素宽度无关——英文错误
# 句（如 "No Codex access (subscription required or re-login needed)"）实际渲染
# 宽度远超 290pt，导致面板在报错时明显变宽。
#
# 这个值不是"文字宽度"本身的安全上限，而是"文字宽度 + NSMenuItem 自身的图标/
# 勾选栏/边距开销"之后仍要落在 290pt 内的预算——实测这部分开销约 58~60pt（同样的
# 文字用 NSAttributedString 量出的宽度和最终 NSMenu 真实宽度对不上，差的就是这个
# 开销）。第一版直接把 250pt 当成文字宽度上限，忽略了这部分开销，导致报错文案裁到
# 250pt 时，整行实际还是把菜单撑到了 306~312pt——比正常态的 290pt 宽一截，用户截图
# 实测能看出来（对比两张图左边缘对不齐）。这版改成 210pt 是实测二分找出来的安全值
# （220pt 实测仍是 290pt，235pt 实测变成 293pt，超了）。改这个值前必须用真机截图或
# `size of menu 1 of menu bar item` 直接量 NSMenu 真实宽度核对，不能只算文字宽度。

def _fit_error_text(prefix, text, max_width=_ERROR_ROW_MAX_WIDTH):
    """把异常态提示文案按真实像素宽度（而非字符数）裁尾并加省略号，
    确保这一行绝不会比正常态更宽、撑大整个下拉菜单。"""
    font = AppKit.NSFont.menuFontOfSize_(0)
    attrs = {AppKit.NSFontAttributeName: font}

    def _width(s):
        return AppKit.NSAttributedString.alloc().initWithString_attributes_(
            s, attrs
        ).size().width

    full = prefix + text
    if _width(full) <= max_width:
        return full
    lo, hi = 0, len(text)
    best = prefix + "…"
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = prefix + text[:mid].rstrip() + "…"
        if _width(candidate) <= max_width:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _detail_text(mode, pct, reset, lang):
    # U+2007 figure space = same pixel width as a digit; prevents tab-stop drift
    # when single-digit pct gets 2 ASCII spaces (narrower than 2 digits)
    fig = " "
    # pct=None：该档窗口这次没有数据（如被 OpenAI 临时移除），显示 "?" 占位，
    # 不能 str(None) 变成字面 "None%"
    pct_str = "?" if pct is None else str(pct)
    pct_padded = pct_str.rjust(3, fig)
    if lang == "en":
        return f"  {mode}\t{pct_padded}% left   \t↻ {reset}"
    return f"  {mode}\t{pct_padded}% 剩余\t↻ {reset}"

# ── 主 App ────────────────────────────────────────────────────────────────────

class AiLimitApp(rumps.App):
    def __init__(self):
        super().__init__("…", quit_button=None)
        self._state = _load_state()
        self._claude = None
        self._codex  = None
        # 服务状态（Statuspage 组件列表原始数据）：None=还没抓到，"unknown"=抓取失败，
        # list=成功。不像 self._claude/_codex 那样在禁用时保留旧值就够——失败要显式
        # 展示 unknown，不能沿用上一次的颜色（同额度数据不兜底旧值的原则）。
        self._claude_status_raw = None
        self._codex_status_raw  = None
        # 后台线程把抓取结果放这里，由主线程的 _apply_pending 定时器接力
        self._pending = None
        self._pending_lock = threading.Lock()
        # 检查更新：同样模式，后台线程查完放这里，_apply_pending 接力弹窗
        self._update_checking = False
        self._update_pending = None
        self._update_lock = threading.Lock()
        # 一键更新（下载+校验）：同样的后台线程写变量+锁 -> 主线程轮询模式
        self._updating = False
        self._download_pending = None
        self._download_lock = threading.Lock()
        self._last_refresh_str = "…"   # 折进刷新频率子菜单标题，无单独菜单行
        self._build_menu()
        # 自动刷新定时器手动管理（间隔可在运行时按用户选择的频率重建），
        # 不用 @rumps.timer 装饰器——那是静态绑定，改不了间隔。
        self._auto_timer = rumps.Timer(self._auto_refresh, self._refresh_sec())
        self._auto_timer.start()

    def _refresh_sec(self):
        return self._state.get("refresh_min", 1) * 60

    def _lang(self):
        """当前生效语言：菜单里选了"中文"/"English"就用该选择（持久化覆盖），
        选"跟随系统"（或旧状态文件没有该字段）则每次启动按 NSLocale 实时判定——
        不把检测结果写回 state，避免被其他偏好的保存操作连带固化成"伪用户选择"。"""
        choice = self._state["lang"]
        return choice if choice in ("zh", "en") else _SYSTEM_LANG

    # ── 菜单构建 ──────────────────────────────────────────────────────────────

    def _build_status_submenu(self, header, service, all_names, page_url, items_out):
        """给 header 挂子菜单：第一行跳转官方 status 页，然后是可勾选组件列表，
        最后一行灰字提示多选规则。items_out 是外部传入的空 dict，用来收 name→MenuItem
        方便后续 _update_status_checks 刷新勾选标记。返回 (open_item, hint_item)。"""
        open_item = rumps.MenuItem("", callback=lambda _: webbrowser.open(page_url))
        header.add(open_item)
        header.add(None)
        for name in all_names:
            it = rumps.MenuItem(name, callback=self._make_toggle_status(service, name))
            items_out[name] = it
            header.add(it)
        header.add(None)
        hint_item = _disable(rumps.MenuItem(""))
        header.add(hint_item)
        return open_item, hint_item

    def _make_toggle_status(self, service, name):
        return lambda _: self._toggle_status_component(service, name)

    def _toggle_status_component(self, service, name):
        key = f"{service}_status_components"
        all_names = _CLAUDE_STATUS_ALL if service == "claude" else _CODEX_STATUS_ALL
        sel = set(self._state.get(key) or [])
        if name in sel:
            sel.discard(name)
        else:
            sel.add(name)
        # 存回去时按固定顺序排，跟 worst_status 的并列取舍规则保持一致，
        # 不依赖用户勾选的先后手序
        self._state[key] = [n for n in all_names if n in sel]
        _save_state(self._state)
        self._update_status_checks()
        self._render()

    def _update_status_checks(self):
        lang = self._lang()
        for service, items, key in (
            ("claude", self._claude_status_items, "claude_status_components"),
            ("codex", self._codex_status_items, "codex_status_components"),
        ):
            sel = self._state.get(key) or []
            for name, item in items.items():
                label = _STATUS_CHECKBOX_LABEL.get(name, (name, name))[0 if lang == "zh" else 1]
                item.title = ("✓ " if name in sel else "  ") + label
        self._claude_status_open.title = _tr(lang, "打开 Claude Status 页", "Open Claude Status page")
        self._codex_status_open.title  = _tr(lang, "打开 OpenAI Status 页", "Open OpenAI Status page")
        hint = _tr(lang, "多选时显示最差状态", "Worst selected status is shown")
        self._claude_status_hint.title = hint
        self._codex_status_hint.title  = hint

    def _build_menu(self):
        lang = self._lang()

        # Claude 区块（详情行挂 no-op callback 避免 macOS 自动灰化；段头改成子菜单
        # 入口后不需要——挂了子菜单的 NSMenuItem 本身就不会被灰化，也不能再有
        # 独立点击动作，跳转链接挪进子菜单第一行）
        self._claude_header = rumps.MenuItem("Claude Code")
        self._claude_5h     = _inert(rumps.MenuItem("  5h  …"))
        self._claude_7d     = _inert(rumps.MenuItem("  7d  …"))
        self._claude_status_items = {}
        self._claude_status_open, self._claude_status_hint = self._build_status_submenu(
            self._claude_header, "claude", _CLAUDE_STATUS_ALL,
            CLAUDE_STATUS_PAGE_URL, self._claude_status_items)

        # CodeX 区块
        self._codex_header = rumps.MenuItem("CodeX")
        self._codex_5h     = _inert(rumps.MenuItem("  5h  …"))
        self._codex_7d     = _inert(rumps.MenuItem("  7d  …"))
        self._codex_status_items = {}
        self._codex_status_open, self._codex_status_hint = self._build_status_submenu(
            self._codex_header, "codex", _CODEX_STATUS_ALL,
            CODEX_STATUS_PAGE_URL, self._codex_status_items)

        # 刷新频率子菜单（1–5 分钟单选）。标题同时携带"上次刷新"时间，
        # 二者合一行——NSMenu 每个 item 自占一行，无法两项共行。
        self._rate_items = {}
        self._rate_menu = rumps.MenuItem("")
        for m in _REFRESH_MINS:
            it = rumps.MenuItem("", callback=self._make_set_rate(m))
            self._rate_items[m] = it
            self._rate_menu.add(it)

        # 菜单栏显示子菜单
        self._mode_5h = rumps.MenuItem("5 小时" if lang == "zh" else "5 hours",
                                       callback=self._set_mode_5h)
        self._mode_7d = rumps.MenuItem("7 天" if lang == "zh" else "7 days",
                                       callback=self._set_mode_7d)
        mode_label = "菜单栏显示周期" if lang == "zh" else "Menu bar period"
        self._mode_menu = rumps.MenuItem(mode_label)
        self._mode_menu.add(self._mode_5h)
        self._mode_menu.add(self._mode_7d)

        # 语言子菜单——父行标题固定双语"语言 Language"，见 _update_lang_checks 注释
        self._lang_auto = rumps.MenuItem("跟随系统 Follow System", callback=self._set_lang_auto)
        self._lang_zh   = rumps.MenuItem("中文", callback=self._set_lang_zh)
        self._lang_en   = rumps.MenuItem("English", callback=self._set_lang_en)
        self._lang_menu = rumps.MenuItem("语言 Language")
        self._lang_menu.add(self._lang_auto)
        self._lang_menu.add(self._lang_zh)
        self._lang_menu.add(self._lang_en)

        # 菜单栏图标子菜单：显示哪些服务 + 样式（数字+电池 / 仅数字 / 仅电池）
        self._bar_claude = rumps.MenuItem("Claude Code", callback=self._toggle_bar_claude)
        self._bar_codex  = rumps.MenuItem("CodeX",       callback=self._toggle_bar_codex)
        self._bar_style_both = rumps.MenuItem("", callback=self._make_set_bar_style("both"))
        self._bar_style_num  = rumps.MenuItem("", callback=self._make_set_bar_style("number"))
        self._bar_style_bat  = rumps.MenuItem("", callback=self._make_set_bar_style("battery"))
        self._bar_menu = rumps.MenuItem("")
        self._bar_menu.add(self._bar_claude)
        self._bar_menu.add(self._bar_codex)
        self._bar_menu.add(None)
        self._bar_menu.add(self._bar_style_both)
        self._bar_menu.add(self._bar_style_num)
        self._bar_menu.add(self._bar_style_bat)

        # 详情面板子菜单：显示哪些服务（允许全空，只看菜单栏）
        self._panel_claude = rumps.MenuItem("Claude Code", callback=self._toggle_panel_claude)
        self._panel_codex  = rumps.MenuItem("CodeX",       callback=self._toggle_panel_codex)
        self._panel_menu = rumps.MenuItem("")
        self._panel_menu.add(self._panel_claude)
        self._panel_menu.add(self._panel_codex)

        # 开机自启
        self._login_item = rumps.MenuItem(
            "开机自启" if lang == "zh" else "Launch at Login",
            callback=self._toggle_login_item,
        )
        self._update_login_item_check()

        # 操作项
        self._refresh_item = rumps.MenuItem(
            "立即刷新" if lang == "zh" else "Refresh now",
            callback=self._force_refresh,
        )
        self._claude_dash = rumps.MenuItem(
            "打开 Claude 用量页" if lang == "zh" else "Open Claude usage",
            callback=lambda _: webbrowser.open("https://claude.ai/settings/usage"),
        )
        self._codex_dash = rumps.MenuItem(
            "打开 CodeX 分析页" if lang == "zh" else "Open CodeX analytics",
            callback=lambda _: webbrowser.open("https://chatgpt.com/codex/cloud/settings/analytics"),
        )

        # 关于子菜单
        about_label = f"关于（ai-limit {__version__}）" if lang == "zh" else f"About (ai-limit {__version__})"
        self._about_menu   = rumps.MenuItem(about_label)
        self._about_ver    = rumps.MenuItem(f"ai-limit {__version__}",
                                            callback=lambda _: webbrowser.open(_PROJECT_URL))
        self._about_author = rumps.MenuItem(
            "作者：zhuchenxi" if lang == "zh" else "Author: zhuchenxi",
            callback=lambda _: webbrowser.open(_AUTHOR_URL_ZH if self._lang() == "zh" else _AUTHOR_URL_EN),
        )
        self._about_desc   = _disable(rumps.MenuItem(
            "Claude Code / CodeX 额度监控" if lang == "zh" else "Claude Code / CodeX quota monitor"
        ))
        self._about_src    = _disable(rumps.MenuItem(
            "数据来源：本地日志 + 官方网页接口" if lang == "zh" else "Source: local logs + official web endpoints"
        ))
        self._check_update_item = rumps.MenuItem(
            "检查更新" if lang == "zh" else "Check for Updates",
            callback=self._check_for_updates,
        )
        self._about_menu.add(self._about_ver)
        self._about_menu.add(self._about_author)
        self._about_menu.add(self._check_update_item)

        # Star on GitHub（放在关于子菜单里，_about_menu 之后才 add）
        self._star_item = rumps.MenuItem(
            "⭐ 给个 Star，鼓励作者" if lang == "zh" else "⭐ Star on GitHub — support the author",
            callback=lambda _: webbrowser.open(_PROJECT_URL),
        )
        self._about_menu.add(self._star_item)
        self._about_menu.add(self._about_desc)
        self._about_menu.add(self._about_src)

        # 退出
        self._quit_item = rumps.MenuItem(
            "退出" if lang == "zh" else "Quit",
            callback=rumps.quit_application,
        )

        self.menu = [
            self._claude_header,
            self._claude_5h,
            self._claude_7d,
            None,
            self._codex_header,
            self._codex_5h,
            self._codex_7d,
            None,
            self._rate_menu,
            self._refresh_item,
            None,
            self._mode_menu,
            self._bar_menu,
            self._panel_menu,
            self._lang_menu,
            self._login_item,
            None,
            self._claude_dash,
            self._codex_dash,
            None,
            self._about_menu,
            None,
            self._quit_item,
        ]
        # NSMenu otherwise shrinks to the longest localized label, so the
        # Chinese and English panels visibly jump between different widths.
        self.menu._menu.setMinimumWidth_(_MENU_MIN_WIDTH)
        self._update_mode_checks()
        self._update_lang_checks()
        self._update_bar_checks()
        self._update_panel_checks()
        self._update_rate_checks()
        self._update_status_checks()

    # ── 数据更新 ──────────────────────────────────────────────────────────────
    #
    # 原则：网络抓取一律在后台线程跑，绝对不阻塞主 UI 线程，否则切换菜单时
    # macOS 会显示转圈光标。
    # 流程：
    #   主线程触发    → 立即用 _load_cache() 重画一次（瞬时响应）
    #                → 启动后台线程 _async_refresh()
    #   后台线程     → 调 _fetch_claude / _fetch_codex（耗时几秒）
    #                → 把结果塞进 self._pending（加锁）
    #   主线程定时器 → _apply_pending 每 0.4s 检查 _pending，有就 apply + 重画

    @rumps.timer(0.3)
    def _init_render(self, sender):
        """启动后立即用缓存重画 + 后台拉一次最新数据。"""
        self._refresh_from_cache()
        self._kick_background_fetch()
        self._check_update_failure_marker()
        # 仅测试用：Stage 3 端到端联调没有人工点"检查更新"菜单项的手段，
        # 用同一个 autotest 环境变量在启动后自动触发一次，和上面跳过确认弹窗
        # 是同一个开关、同一个含义——生产环境不会设置，行为不变。
        if os.environ.get("AI_LIMIT_AUTOTEST_SKIP_CONFIRM") == "1":
            self._check_for_updates(None)
        sender.stop()

    def _check_update_failure_marker(self):
        """一键更新 helper 脚本失败时会写这个 marker 文件，启动时检查一次、
        提示一次、然后删除——只提示一次，不能反复弹。marker 内容损坏（比如写
        到一半被打断）也当作"有失败发生但细节不明"处理，不能因为解析失败就
        卡住启动。"""
        if not _UPDATE_FAILED_MARKER.exists():
            return
        detail = ""
        try:
            data = json.loads(_UPDATE_FAILED_MARKER.read_text(encoding="utf-8"))
            detail = data.get("detail", "")
        except Exception:
            pass
        try:
            _UPDATE_FAILED_MARKER.unlink()
        except FileNotFoundError:
            pass
        lang = self._lang()
        _show_alert(
            _tr(lang, "自动更新失败", "Auto-Update Failed"),
            _tr(lang,
                f"已回退到当前版本 {__version__}。{detail}\n可前往下载页手动更新。",
                f"Rolled back to current version {__version__}. {detail}\n"
                "You can update manually from the download page.",
            ),
            ok=_tr(lang, "好", "OK"),
        )

    def _auto_refresh(self, _):
        """按用户选择的频率后台拉一次（由 self._auto_timer 驱动，间隔可调）。"""
        self._kick_background_fetch()

    @rumps.timer(0.4)
    def _apply_pending(self, _):
        """主线程接力点：把后台线程取到的数据 apply 到 UI。

        重点：服务被禁用时不要清空内存里的旧数据。后台线程对禁用服务返回
        None 表示"没拉新的"，不是"清空"——保留上次的值，重新启用时菜单栏
        瞬间显示该服务的最近一次缓存，避免 1-2s 网络抓取的等待感。
        """
        with self._pending_lock:
            pending = self._pending
            self._pending = None
        if pending is not None:
            claude, codex, claude_status, codex_status = pending
            if claude is not None:
                self._claude = claude
            if codex is not None:
                self._codex = codex
            # 状态字段同样遵守"None=没抓（服务禁用），不是清空"；但抓取失败时
            # usage.py 返回的是字符串 "unknown"（不是 None），照样会走进这里覆盖
            # 掉上一次的好值——失败必须显式变成 ❓，不能沿用旧颜色。
            if claude_status is not None:
                self._claude_status_raw = claude_status
            if codex_status is not None:
                self._codex_status_raw = codex_status
            _save_cache(self._claude, self._codex)
            _append_history(claude, codex)
            self._render()

        with self._update_lock:
            update_result = self._update_pending
            self._update_pending = None
        if update_result is not None:
            self._update_checking = False
            self._check_update_item.title = _tr(self._lang(), "检查更新", "Check for Updates")
            self._show_update_result(update_result)

        with self._download_lock:
            download_result = self._download_pending
            self._download_pending = None
        if download_result is not None:
            self._apply_download_result(download_result)

    def _apply_download_result(self, result):
        """一键更新的下载+校验结果，主线程消费。成功：不显示中间态，直接触发
        退出重启（用户点"立即更新"时已经确认过，不加二次确认）。失败：复位
        菜单文字 + 弹提示，保留"打开下载页"作为兜底出路。"""
        lang = self._lang()
        if result.get("ok"):
            self._trigger_restart_update(result["app_path"])
            return  # 即将退出，不需要再复位 self._updating

        self._updating = False
        self._check_update_item.title = _tr(lang, "检查更新", "Check for Updates")
        detail = result.get("detail", "")
        opened = _show_alert(
            _tr(lang, "更新失败", "Update Failed"),
            _tr(lang,
                f"自动更新未完成（{detail}）。是否打开下载页手动安装？",
                f"Automatic update did not complete ({detail}). "
                "Open the download page to install manually?",
            ),
            ok=_tr(lang, "打开下载页", "Open Download Page"),
            cancel=_tr(lang, "取消", "Cancel"),
        )
        if opened:
            page_url = (_GITEE_RELEASES_PAGE_URL if result.get("source") == "gitee"
                        else _RELEASES_PAGE_URL)
            webbrowser.open(page_url)

    def _refresh_from_cache(self):
        """主线程瞬时操作：读短缓存重画，不碰网络。"""
        claude, codex = _load_cache()
        # 不按 services 过滤——内存里保留两份数据，UI 显示由 _render 控
        if claude is not None:
            self._claude = claude
        if codex is not None:
            self._codex = codex
        self._render()

    def _kick_background_fetch(self):
        """启动后台线程抓数据；线程内不要碰任何 UI 对象。"""
        t = threading.Thread(target=self._async_refresh, daemon=True)
        t.start()

    def _async_refresh(self):
        """后台线程：抓数据 → 写共享变量。不能调任何 rumps/AppKit UI。"""
        lang = self._lang()
        need = set(self._state.get("bar_services") or []) | set(self._state.get("panel_services") or [])
        claude = _fetch_claude(lang) if "claude" in need else None
        codex  = _fetch_codex(lang)  if "codex"  in need else None
        # 组件状态：拉全量列表，不按用户勾选过滤——过滤放渲染层，这样用户在
        # 子菜单里改勾选时不用重新发请求，即勾即生效。
        claude_status = _fetch_status(CLAUDE_STATUS_COMPONENTS_URL) if "claude" in need else None
        codex_status  = _fetch_status(CODEX_STATUS_COMPONENTS_URL)  if "codex"  in need else None
        with self._pending_lock:
            self._pending = (claude, codex, claude_status, codex_status)

    def _render(self):
        lang     = self._lang()
        mode     = self._state["global"]
        bar_svc   = self._state.get("bar_services") or list(_SERVICES)
        panel_svc = self._state.get("panel_services") or []
        style     = self._state.get("bar_style", "both")
        show_claude = "claude" in panel_svc   # 面板维度（下方区块沿用此变量）
        show_codex  = "codex"  in panel_svc
        claude = self._claude or {}
        codex  = self._codex  or {}

        # 菜单栏标题：[Claude 68% ⌬]  [CodeX 99% ⌬]
        # 电池是原生 SF Symbol，Apple 亲手画的 iPhone 风格，向量永不糊
        bar_items = []
        if "claude" in bar_svc:
            if "error" in claude:
                if claude["error"] != "no_data":   # no_data=还没读到数据,跳过(全跳过则落到兜底 ai-limit…);not_logged_in/异常才报 ⚠️
                    bar_items.append(("Claude", 0, True))
            elif claude:
                pct = claude["5h_left"] if mode == "5h" else claude["7d_left"]
                bar_items.append(("Claude", pct, False))
        if "codex" in bar_svc:
            if "error" in codex:
                if codex["error"] != "no_data":   # 同上:no_data 跳过,not_logged_in/异常才报 ⚠️
                    bar_items.append(("CodeX", 0, True))
            elif codex:
                pct = codex["5h_left"] if mode == "5h" else codex["7d_left"]
                if pct is None:  # 选中的窗口这次没数据（如 Codex 只返回一档额度），退到另一档
                    pct = codex["7d_left"] if mode == "5h" else codex["5h_left"]
                if pct is not None:
                    bar_items.append(("CodeX", pct, False))
        try:
            _set_bar_with_batteries(self, bar_items, style)
        except Exception:
            # SF Symbol 不可用时（很老的 macOS）回退到 ▰▱ 文字版
            parts = []
            for lbl, pct, err in bar_items:
                if err:
                    parts.append(f"{lbl} ⚠️")
                elif style == "number":
                    parts.append(f"{lbl} {pct}%")
                elif style == "battery":
                    parts.append(f"{lbl} {_native_bar(pct)}")
                else:
                    parts.append(f"{lbl} {pct}% {_native_bar(pct)}")
            _set_bar_title(self, "  ".join(parts) if parts else "ai-limit…")

        # Claude 区块 —— 服务被关时整段隐藏
        self._claude_header._menuitem.setHidden_(not show_claude)
        self._claude_5h._menuitem.setHidden_(not show_claude)
        self._claude_7d._menuitem.setHidden_(not show_claude)
        claude_sel = self._state.get("claude_status_components", _CLAUDE_STATUS_DEFAULT)
        claude_status_info = _status_info(self._claude_status_raw, claude_sel, lang)
        if show_claude:
            if "error" in claude:
                _set_header_title(self._claude_header, "Claude Code ⚠️", claude_status_info)
                self._claude_5h.title = _fit_error_text("  ", claude["error"])
                self._claude_7d._menuitem.setHidden_(True)
            elif claude:
                plan = _fmt_plan(claude.get("plan"), lang)
                _set_header_title(self._claude_header, f"Claude Code{plan}", claude_status_info)
                c5_reset = _fmt_reset_iso(claude["5h_reset"], lang)
                c7_reset = _fmt_reset_iso(claude["7d_reset"], lang)
                self._claude_5h.title = _detail_text("5h", claude["5h_left"], c5_reset, lang)
                self._claude_7d.title = _detail_text("7d", claude["7d_left"], c7_reset, lang)

        # CodeX 区块
        self._codex_header._menuitem.setHidden_(not show_codex)
        self._codex_5h._menuitem.setHidden_(not show_codex)
        self._codex_7d._menuitem.setHidden_(not show_codex)
        codex_sel = self._state.get("codex_status_components", _CODEX_STATUS_DEFAULT)
        codex_status_info = _status_info(self._codex_status_raw, codex_sel, lang)
        if show_codex:
            if "error" in codex:
                _set_header_title(self._codex_header, "CodeX ⚠️", codex_status_info)
                self._codex_5h.title = _fit_error_text("  ", codex["error"])
                self._codex_7d._menuitem.setHidden_(True)
            elif codex:
                plan = _fmt_plan(codex.get("plan"), lang)
                _set_header_title(self._codex_header, f"CodeX{plan}", codex_status_info)
                # 两档都固定显示；缺数据的那一档 pct=None/reset=None 会被
                # _detail_text / _fmt_reset_epoch 渲染成 "?"，不隐藏整行——
                # OpenAI 官方说 5 小时限额是"临时"移除，随时可能恢复，隐藏/
                # 复现整行会让菜单栏布局跟着限额开关忽隐忽现。
                self._codex_5h._menuitem.setHidden_(False)
                self._codex_7d._menuitem.setHidden_(False)
                x5_reset = _fmt_reset_epoch(codex["5h_reset"], lang)
                x7_reset = _fmt_reset_epoch(codex["7d_reset"], lang)
                self._codex_5h.title = _detail_text(codex.get("5h_label") or "5h", codex["5h_left"], x5_reset, lang)
                self._codex_7d.title = _detail_text(codex.get("7d_label") or "7d", codex["7d_left"], x7_reset, lang)

        # 刷新时间：折进刷新频率子菜单标题（见 _update_rate_checks）
        self._last_refresh_str = datetime.datetime.now(TZ_LOCAL).strftime("%H:%M:%S")
        self._update_rate_checks()

    # ── 模式 / 语言切换 ──────────────────────────────────────────────────────

    def _set_mode_5h(self, _):
        self._state["global"] = "5h"
        _save_state(self._state)
        self._update_mode_checks()
        self._render()  # 只换显示窗口，数据没变，直接重画

    def _set_mode_7d(self, _):
        self._state["global"] = "7d"
        _save_state(self._state)
        self._update_mode_checks()
        self._render()

    def _update_mode_checks(self):
        lang = self._lang()
        mode = self._state["global"]
        self._mode_5h.title = ("✓ " if mode == "5h" else "  ") + _tr(lang, "5 小时", "5 hours")
        self._mode_7d.title = ("✓ " if mode == "7d" else "  ") + _tr(lang, "7 天", "7 days")
        self._mode_menu.title = _tr(lang,
            f"菜单栏显示周期（{_tr(lang, '5 小时', '5 hours') if mode == '5h' else _tr(lang, '7 天', '7 days')}）",
            f"Menu bar period ({_tr(lang, '5 hours', '5 hours') if mode == '5h' else '7 days'})",
        )

    # ── 刷新频率 ────────────────────────────────────────────────────────────
    def _make_set_rate(self, minutes):
        return lambda _: self._set_refresh_rate(minutes)

    def _set_refresh_rate(self, minutes):
        if minutes not in _REFRESH_MINS or minutes == self._state.get("refresh_min"):
            self._update_rate_checks()  # 重画勾选，即使没变也保持一致
            return
        self._state["refresh_min"] = minutes
        _save_state(self._state)
        # 重建定时器间隔：stop → 改 interval → start（rumps 在 start 时才读取 interval）
        self._auto_timer.stop()
        self._auto_timer.interval = self._refresh_sec()
        self._auto_timer.start()
        self._update_rate_checks()

    def _update_rate_checks(self):
        lang = self._lang()
        cur = self._state.get("refresh_min", 1)
        for m, it in self._rate_items.items():
            it.title = ("✓ " if m == cur else "  ") + _tr(lang, f"{m} 分钟", f"{m} min")
        ts = self._last_refresh_str
        self._rate_menu.title = _tr(lang,
            f"刷新频率（{cur} 分钟）   上次: {ts}",
            f"Refresh interval ({cur} min)   last: {ts}")

    def _set_lang_auto(self, _):
        self._state["lang"] = "auto"
        _save_state(self._state)
        self._update_lang_checks()
        self._update_mode_checks()
        self._update_bar_checks()
        self._update_panel_checks()
        self._refresh_static_labels()
        self._render()

    def _set_lang_zh(self, _):
        self._state["lang"] = "zh"
        _save_state(self._state)
        self._update_lang_checks()
        # 重画所有 i18n 文本（详情行 / 段头 / "上次刷新" 等）
        self._update_mode_checks()
        self._update_bar_checks()
        self._update_panel_checks()
        self._refresh_static_labels()
        self._render()

    def _set_lang_en(self, _):
        self._state["lang"] = "en"
        _save_state(self._state)
        self._update_lang_checks()
        self._update_mode_checks()
        self._update_bar_checks()
        self._update_panel_checks()
        self._refresh_static_labels()
        self._render()

    def _refresh_static_labels(self):
        """语言切换后，更新所有不依赖数据的菜单文字。"""
        lang = self._lang()
        self._refresh_item.title = _tr(lang, "立即刷新", "Refresh now")
        self._claude_dash.title = _tr(lang, "打开 Claude 用量页", "Open Claude usage")
        self._codex_dash.title  = _tr(lang, "打开 CodeX 分析页", "Open CodeX analytics")
        self._about_menu.title  = _tr(lang,
            f"关于（ai-limit {__version__}）",
            f"About (ai-limit {__version__})",
        )
        self._about_author.title = _tr(lang, "作者：zhuchenxi", "Author: zhuchenxi")
        self._about_desc.title   = _tr(lang,
            "Claude Code / CodeX 额度监控",
            "Claude Code / CodeX quota monitor",
        )
        self._about_src.title    = _tr(lang,
            "数据来源：本地日志 + 官方网页接口",
            "Source: local logs + official web endpoints",
        )
        if not self._update_checking:
            self._check_update_item.title = _tr(lang, "检查更新", "Check for Updates")
        self._update_login_item_check()
        self._update_status_checks()
        self._update_rate_checks()
        self._star_item.title    = _tr(lang, "⭐ 给个 Star，鼓励作者", "⭐ Star on GitHub — support the author")
        self._quit_item.title    = _tr(lang, "退出", "Quit")

    def _update_lang_checks(self):
        choice = self._state["lang"]
        lang = self._lang()
        # "跟随系统 Follow System" 双语——子菜单是独立 NSMenu，宽度不受主面板
        # 290pt 约束，安全加。这是三个选项里唯一不自解释的一个（"中文"/
        # "English" 本身就是目标语言的名字，误入陌生语言界面的用户也认得出）。
        self._lang_auto.title = ("✓ " if choice == "auto" else "  ") + "跟随系统 Follow System"
        self._lang_zh.title   = ("✓ " if choice == "zh"   else "  ") + "中文"
        self._lang_en.title   = ("✓ " if choice == "en"   else "  ") + "English"
        sel = {"zh": "中文", "en": "English"}.get(choice, "跟随系统")
        # 父行标题固定带"语言 Language"前缀（不跟随 lang 切换）——这是误入
        # 陌生语言界面的用户唯一能找到切语言入口的线索，本身受主面板宽度约束
        # 不能太长，括号里的当前选择摘要就不重复双语了，点进子菜单自然看得懂。
        self._lang_menu.title = f"语言 Language（{sel}）"

    # ── 菜单栏图标 / 详情面板 切换 ──────────────────────────────────────────

    def _toggle_bar_claude(self, _):
        self._toggle_bar("claude")

    def _toggle_bar_codex(self, _):
        self._toggle_bar("codex")

    def _toggle_bar(self, service):
        svc = list(self._state.get("bar_services") or list(_SERVICES))
        if service in svc:
            svc.remove(service)
        else:
            svc.append(service)
        if not svc:
            # 菜单栏不允许全空（否则只剩空白点击区，找不到入口），回退保留
            svc = [service]
        self._state["bar_services"] = svc
        _save_state(self._state)
        self._update_bar_checks()
        self._render()
        self._kick_background_fetch()

    def _toggle_panel_claude(self, _):
        self._toggle_panel("claude")

    def _toggle_panel_codex(self, _):
        self._toggle_panel("codex")

    def _toggle_panel(self, service):
        svc = list(self._state.get("panel_services") or [])
        if service in svc:
            svc.remove(service)
        else:
            svc.append(service)
        # 面板允许全空（用户可只看菜单栏）
        self._state["panel_services"] = svc
        _save_state(self._state)
        self._update_panel_checks()
        self._render()
        self._kick_background_fetch()

    def _make_set_bar_style(self, style):
        return lambda _: self._set_bar_style(style)

    def _set_bar_style(self, style):
        if style not in _BAR_STYLES:
            return
        self._state["bar_style"] = style
        _save_state(self._state)
        self._update_bar_checks()
        self._render()

    def _toggle_login_item(self, _):
        _set_login_item(not _login_item_enabled())
        self._update_login_item_check()

    def _update_login_item_check(self):
        lang = self._lang()
        enabled = _login_item_enabled()
        suffix = " ✓" if enabled else ""
        self._login_item.title = _tr(lang, "开机自启", "Launch at Login") + suffix

    def _update_bar_checks(self):
        lang = self._lang()
        bar = self._state.get("bar_services") or list(_SERVICES)
        self._bar_claude.title = ("✓ " if "claude" in bar else "  ") + "Claude Code"
        self._bar_codex.title  = ("✓ " if "codex"  in bar else "  ") + "CodeX"
        style = self._state.get("bar_style", "both")
        self._bar_style_both.title = ("✓ " if style == "both"    else "  ") + _tr(lang, "数字 + 电池", "Number + battery")
        self._bar_style_num.title  = ("✓ " if style == "number"  else "  ") + _tr(lang, "仅数字", "Number only")
        self._bar_style_bat.title  = ("✓ " if style == "battery" else "  ") + _tr(lang, "仅电池", "Battery only")
        summary = _tr(lang, "全部", "All") if len(bar) == 2 else (
            "Claude Code" if "claude" in bar else "CodeX"
        )
        self._bar_menu.title = _tr(lang, f"菜单栏图标（{summary}）", f"Menu bar icons ({summary})")

    def _update_panel_checks(self):
        lang = self._lang()
        panel = self._state.get("panel_services") or []
        self._panel_claude.title = ("✓ " if "claude" in panel else "  ") + "Claude Code"
        self._panel_codex.title  = ("✓ " if "codex"  in panel else "  ") + "CodeX"
        if not panel:
            summary = _tr(lang, "无", "None")
        elif len(panel) == 2:
            summary = _tr(lang, "全部", "All")
        else:
            summary = "Claude Code" if "claude" in panel else "CodeX"
        self._panel_menu.title = _tr(lang, f"详情面板（{summary}）", f"Detail panel ({summary})")

    # ── 立即刷新 ──────────────────────────────────────────────────────────────

    def _force_refresh(self, _):
        try:
            _CACHE_PATH.unlink()
        except Exception:
            pass
        # 后台拉，不卡 UI；新数据 ≤几秒内通过 _apply_pending 落到菜单上
        self._kick_background_fetch()

    # ── 检查更新 ──────────────────────────────────────────────────────────────

    def _check_for_updates(self, _):
        if self._update_checking or self._updating:
            return
        self._update_checking = True
        self._check_update_item.title = _tr(self._lang(), "检查更新…", "Checking for updates…")
        threading.Thread(target=self._async_check_update, daemon=True).start()

    def _async_check_update(self):
        """后台线程：查 GitHub 最新 Release。不能调任何 rumps/AppKit UI。"""
        result = _fetch_latest_release_info()
        with self._update_lock:
            self._update_pending = result

    def _show_update_result(self, result):
        lang = self._lang()
        if result.get("error"):
            _show_alert(
                _tr(lang, "检查更新失败", "Update Check Failed"),
                _tr(lang,
                    "无法连接 GitHub，请检查网络后重试。",
                    "Could not reach GitHub. Check your network and try again.",
                ),
                ok=_tr(lang, "好", "OK"),
            )
            return
        latest = result["latest"]
        if _version_tuple(latest) <= _version_tuple(__version__):
            _show_alert(
                _tr(lang, "已是最新版本", "You're Up to Date"),
                _tr(lang,
                    f"当前版本 {__version__} 已是最新。",
                    f"Current version {__version__} is the latest.",
                ),
                ok=_tr(lang, "好", "OK"),
            )
            return

        if not result.get("asset_url"):
            # 防御性兜底：没在 Release 里找到 DMG 资产（比如某次发版漏传），
            # 回退到旧的"打开下载页"手动流程。
            opened = _show_alert(
                _tr(lang, "发现新版本", "Update Available"),
                _tr(lang,
                    f"最新版本 {latest}，当前版本 {__version__}。是否打开下载页？",
                    f"Latest version {latest}, current version {__version__}. Open the download page?",
                ),
                ok=_tr(lang, "打开下载页", "Open Download Page"),
                cancel=_tr(lang, "取消", "Cancel"),
            )
            if opened:
                page_url = _GITEE_RELEASES_PAGE_URL if result.get("source") == "gitee" else _RELEASES_PAGE_URL
                webbrowser.open(page_url)
            return

        # 唯一一次确认：点了"立即更新"之后不再有二次确认，直接下载校验完就
        # 退出重启（对齐 Claude App / Codex App / Trae 国际版的一键更新体验）。
        # 仅测试用：Stage 3 端到端联调需要在真实冻结环境里跑通全流程但没有
        # 人工点击 NSAlert 的手段，用一个显式的 autotest 环境变量跳过这一次
        # 确认——只影响这一个弹窗，不影响其它任何提示；生产环境不会设置这个
        # 变量，行为和现在完全一致。
        if os.environ.get("AI_LIMIT_AUTOTEST_SKIP_CONFIRM") == "1":
            update_now = True
        else:
            update_now = _show_alert(
                _tr(lang, "发现新版本", "Update Available"),
                _tr(lang,
                    f"最新版本 {latest}，当前版本 {__version__}。是否立即更新？",
                    f"Latest version {latest}, current version {__version__}. Update now?",
                ),
                ok=_tr(lang, "立即更新", "Update Now"),
                cancel=_tr(lang, "取消", "Cancel"),
            )
        if update_now:
            self._start_update(result)

    def _start_update(self, result):
        if self._updating:
            return
        self._updating = True
        self._check_update_item.title = _tr(self._lang(), "更新中…", "Updating…")
        threading.Thread(
            target=self._async_download_and_verify_update,
            args=(result["asset_url"], result["latest"], result.get("source")),
            daemon=True,
        ).start()

    def _async_download_and_verify_update(self, asset_url, expected_version, source):
        """后台线程：下载 + 签名公证校验一键更新的 DMG。不能调任何 rumps/AppKit
        UI。任一步失败清理本次临时目录，成功则保留（helper 脚本负责最终清理）。"""
        dest_dir = pathlib.Path(tempfile.mkdtemp(prefix="ai-limit-update-"))
        try:
            dmg_path = _download_release_dmg(asset_url, dest_dir)
            app_path = _verify_dmg(dmg_path, expected_version, dest_dir)
            result = {"ok": True, "app_path": app_path}
        except _UpdateFailed as e:
            shutil.rmtree(dest_dir, ignore_errors=True)
            result = {"ok": False, "reason": e.reason, "detail": e.detail, "source": source}
        except Exception as e:
            shutil.rmtree(dest_dir, ignore_errors=True)
            result = {"ok": False, "reason": "unexpected", "detail": str(e), "source": source}
        with self._download_lock:
            self._download_pending = result

    def _trigger_restart_update(self, verified_app_path):
        """校验通过后：把 helper 脚本拷到本次更新的临时目录、detached 启动、
        立即退出主进程，交给 helper 完成"等旧进程退出 -> 替换 -> 拉起新版"。
        目标路径动态推导（不硬编码 /Applications/AI Limit.app），非打包运行
        环境（开发时直接跑 .py）直接拒绝，因为推导不出真实的 .app bundle。"""
        lang = self._lang()
        if not getattr(sys, "frozen", False):
            self._updating = False
            self._check_update_item.title = _tr(lang, "检查更新", "Check for Updates")
            _show_alert(
                _tr(lang, "无法自动更新", "Cannot Auto-Update"),
                _tr(lang,
                    "当前不是打包运行环境，无法自动重启更新，请手动下载安装。",
                    "Not running as a packaged app; cannot auto-restart. Please install manually.",
                ),
                ok=_tr(lang, "好", "OK"),
            )
            return

        target_app = pathlib.Path(sys.executable).resolve().parents[2]
        helper_src = target_app / "Contents" / "Resources" / _UPDATER_SCRIPT_NAME
        dest_dir = verified_app_path.parent.parent  # <tmp>/verified/AI Limit.app -> <tmp>
        helper_copy = dest_dir / _UPDATER_SCRIPT_NAME
        log_path = dest_dir / "updater.log"

        try:
            shutil.copy2(helper_src, helper_copy)
            os.chmod(helper_copy, 0o755)
            subprocess.Popen(
                [str(helper_copy), str(verified_app_path), str(target_app),
                 str(os.getpid()), str(log_path), str(_UPDATE_FAILED_MARKER), str(dest_dir)],
                start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True,
            )
        except Exception as e:
            self._updating = False
            self._check_update_item.title = _tr(lang, "检查更新", "Check for Updates")
            _show_alert(
                _tr(lang, "更新失败", "Update Failed"),
                _tr(lang, f"无法启动更新程序：{e}", f"Could not start updater: {e}"),
                ok=_tr(lang, "好", "OK"),
            )
            return

        rumps.quit_application(None)


if __name__ == "__main__":
    AiLimitApp().run()
