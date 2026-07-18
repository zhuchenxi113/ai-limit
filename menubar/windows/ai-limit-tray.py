#!/usr/bin/env python3
"""ai-limit Windows 托盘 App（PySide6 版）。

跟 mac 版（menubar/ai-limit-app.py，rumps）功能对齐，但 GUI 框架完全不同，
是独立文件。共享 usage.py 的数据抓取逻辑和偏好设置文件格式（~/.ai-limit-menubar*）。

v1 范围（已实现）：Claude/Codex 用量图标 + 详情菜单、5h/7d 显示切换、
刷新频率、语言切换、开机自启、检查更新（仅版本比对+跳转下载页，
下载/校验/静默安装是 Phase 4 范围，尚未实现）。

v1 暂缺（后续增量）：mac 版的服务状态监控子菜单（Claude/Codex Status
Page 组件级勾选）、菜单栏样式切换（number-only/battery-only，v1 图标
固定用"电池条+数字"组合样式）。
"""
import pathlib
import sys
import threading
import webbrowser

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO))

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from usage import __version__, epoch_to_local  # noqa: F401  (epoch_to_local re-exported for fetchers)
import state
from lang_win import detect_system_lang, tr
from icon_render import render_service_icon, render_placeholder_icon, is_dark_taskbar
from autostart_win import is_autostart_enabled, set_autostart
from dialogs import show_alert
import fetchers

_SYSTEM_LANG = detect_system_lang()
_REFRESH_MINS = (1, 2, 3, 4, 5)


class AiLimitTray:
    def __init__(self, app: QApplication):
        self._app = app
        self._state = state.load_state()
        self._claude = None
        self._codex = None

        self._pending = None
        self._pending_lock = threading.Lock()

        self._claude_icon = QSystemTrayIcon()
        self._codex_icon = QSystemTrayIcon()
        self._claude_icon.setIcon(render_placeholder_icon())
        self._codex_icon.setIcon(render_placeholder_icon())
        self._claude_icon.setToolTip("Claude Code")
        self._codex_icon.setToolTip("Codex")

        self._menu = QMenu()
        self._build_menu()
        self._claude_icon.setContextMenu(self._menu)
        self._codex_icon.setContextMenu(self._menu)

        self._apply_timer = QTimer()
        self._apply_timer.timeout.connect(self._apply_pending)
        self._apply_timer.start(400)

        self._auto_timer = QTimer()
        self._auto_timer.timeout.connect(self._kick_background_fetch)
        self._auto_timer.start(self._refresh_sec() * 1000)

        self._render()
        self._update_visibility()
        self._claude_icon.show()
        self._codex_icon.show()
        self._kick_background_fetch()

    # ── 状态辅助 ──────────────────────────────────────────────────────────

    def _refresh_sec(self) -> int:
        return self._state.get("refresh_min", 1) * 60

    def _lang(self) -> str:
        choice = self._state.get("lang", "auto")
        return choice if choice in ("zh", "en") else _SYSTEM_LANG

    def _update_visibility(self):
        bar_svc = self._state.get("bar_services") or ["claude", "codex"]
        self._claude_icon.setVisible("claude" in bar_svc)
        self._codex_icon.setVisible("codex" in bar_svc)

    # ── 菜单构建 ──────────────────────────────────────────────────────────

    def _build_menu(self):
        lang = self._lang()
        m = self._menu
        m.clear()

        self._claude_detail_action = m.addAction("Claude Code")
        self._claude_detail_action.setEnabled(False)
        self._codex_detail_action = m.addAction("Codex")
        self._codex_detail_action.setEnabled(False)
        m.addSeparator()

        mode_menu = m.addMenu(tr(lang, "显示周期", "Display Window"))
        mode_group = QActionGroup(mode_menu)
        mode_group.setExclusive(True)
        for mode, label in (("5h", tr(lang, "5 小时", "5-hour")), ("7d", tr(lang, "7 天", "7-day"))):
            act = mode_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._state.get("global") == mode)
            act.triggered.connect(lambda checked, m=mode: self._set_mode(m))
            mode_group.addAction(act)

        refresh_menu = m.addMenu(tr(lang, "刷新频率", "Refresh Interval"))
        refresh_group = QActionGroup(refresh_menu)
        refresh_group.setExclusive(True)
        for mins in _REFRESH_MINS:
            label = tr(lang, f"{mins} 分钟", f"{mins} min")
            act = refresh_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._state.get("refresh_min", 1) == mins)
            act.triggered.connect(lambda checked, mn=mins: self._set_refresh_min(mn))
            refresh_group.addAction(act)

        lang_menu = m.addMenu(tr(lang, "语言", "Language"))
        lang_group = QActionGroup(lang_menu)
        lang_group.setExclusive(True)
        for code, label in (("auto", tr(lang, "跟随系统", "Follow System")),
                             ("zh", "中文"), ("en", "English")):
            act = lang_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._state.get("lang", "auto") == code)
            act.triggered.connect(lambda checked, c=code: self._set_lang(c))
            lang_group.addAction(act)

        m.addSeparator()
        autostart_act = m.addAction(tr(lang, "开机自启", "Start at Login"))
        autostart_act.setCheckable(True)
        autostart_act.setChecked(is_autostart_enabled())
        autostart_act.triggered.connect(self._toggle_autostart)

        m.addSeparator()
        self._check_update_action = m.addAction(tr(lang, "检查更新", "Check for Updates"))
        self._check_update_action.triggered.connect(self._check_for_updates)

        about_act = m.addAction(tr(lang, "项目主页", "Project Homepage"))
        about_act.triggered.connect(lambda: webbrowser.open("https://github.com/zhuchenxi113/ai-limit"))

        m.addSeparator()
        quit_act = m.addAction(tr(lang, "退出", "Quit"))
        quit_act.triggered.connect(self._app.quit)

    def _set_mode(self, mode):
        self._state["global"] = mode
        state.save_state(self._state)
        self._render()

    def _set_refresh_min(self, mins):
        self._state["refresh_min"] = mins
        state.save_state(self._state)
        self._auto_timer.setInterval(self._refresh_sec() * 1000)

    def _set_lang(self, code):
        self._state["lang"] = code
        state.save_state(self._state)
        self._build_menu()
        self._render()

    def _toggle_autostart(self, checked):
        set_autostart(checked, sys.executable)

    def _check_for_updates(self):
        lang = self._lang()
        self._check_update_action.setEnabled(False)
        self._check_update_action.setText(tr(lang, "检查中…", "Checking…"))

        def _worker():
            try:
                import urllib.request, json as _json
                req = urllib.request.Request(
                    "https://api.github.com/repos/zhuchenxi113/ai-limit/releases/latest",
                    headers={"User-Agent": "ai-limit"},
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    data = _json.loads(resp.read().decode("utf-8"))
                latest = data.get("tag_name", "").lstrip("v")
                result = {"ok": True, "latest": latest}
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            with self._pending_lock:
                self._pending = ("update_result", result)

        threading.Thread(target=_worker, daemon=True).start()

    # ── 后台抓取 / 主线程接力 ──────────────────────────────────────────────

    def _kick_background_fetch(self):
        threading.Thread(target=self._async_refresh, daemon=True).start()

    def _async_refresh(self):
        lang = self._lang()
        bar_svc = self._state.get("bar_services") or ["claude", "codex"]
        claude = fetchers.fetch_claude(lang) if "claude" in bar_svc else None
        codex = fetchers.fetch_codex(lang) if "codex" in bar_svc else None
        with self._pending_lock:
            self._pending = ("fetch_result", (claude, codex))

    def _apply_pending(self):
        with self._pending_lock:
            pending = self._pending
            self._pending = None
        if pending is None:
            return
        kind, payload = pending
        if kind == "fetch_result":
            claude, codex = payload
            if claude is not None:
                self._claude = claude
            if codex is not None:
                self._codex = codex
            state.save_cache(self._claude, self._codex)
            state.append_history(claude, codex)
            self._render()
        elif kind == "update_result":
            lang = self._lang()
            self._check_update_action.setEnabled(True)
            self._check_update_action.setText(tr(lang, "检查更新", "Check for Updates"))
            if not payload.get("ok"):
                show_alert(
                    tr(lang, "检查更新失败", "Update Check Failed"),
                    str(payload.get("error", "")),
                    tr(lang, "好", "OK"),
                )
                return
            latest = payload.get("latest", "")
            if latest and latest != __version__:
                opened = show_alert(
                    tr(lang, "发现新版本", "New Version Available"),
                    tr(lang, f"当前 {__version__}，最新 {latest}。是否前往下载页？",
                             f"Current {__version__}, latest {latest}. Open download page?"),
                    tr(lang, "打开下载页", "Open Download Page"),
                    tr(lang, "取消", "Cancel"),
                )
                if opened:
                    webbrowser.open("https://github.com/zhuchenxi113/ai-limit/releases")
            else:
                show_alert(
                    tr(lang, "已是最新版本", "Up to Date"),
                    tr(lang, f"当前版本 {__version__} 已是最新。", f"Version {__version__} is up to date."),
                    tr(lang, "好", "OK"),
                )

    # ── 渲染 ──────────────────────────────────────────────────────────────

    def _render(self):
        lang = self._lang()
        mode = self._state.get("global", "5h")
        dark = is_dark_taskbar()
        claude = self._claude or {}
        codex = self._codex or {}

        if "error" in claude:
            self._claude_icon.setIcon(render_service_icon(None, True, dark))
            self._claude_icon.setToolTip(f"Claude Code — {claude['error']}")
            self._claude_detail_action.setText(f"Claude Code ⚠ {claude['error']}")
        elif claude:
            pct = claude["5h_left"] if mode == "5h" else claude["7d_left"]
            reset = (fetchers.fmt_reset_iso(claude["5h_reset"], lang) if mode == "5h"
                     else fetchers.fmt_reset_iso(claude["7d_reset"], lang))
            plan = fetchers._fmt_plan(claude.get("plan"), lang)
            self._claude_icon.setIcon(render_service_icon(pct, False, dark))
            tip = f"Claude Code{plan}  {mode}: {pct}%  {tr(lang, '重置', 'reset')} {reset}"
            self._claude_icon.setToolTip(tip)
            self._claude_detail_action.setText(tip)

        if "error" in codex:
            self._codex_icon.setIcon(render_service_icon(None, True, dark))
            self._codex_icon.setToolTip(f"Codex — {codex['error']}")
            self._codex_detail_action.setText(f"Codex ⚠ {codex['error']}")
        elif codex:
            pct = codex["5h_left"] if mode == "5h" else codex["7d_left"]
            if pct is None:
                pct = codex["7d_left"] if mode == "5h" else codex["5h_left"]
            reset = (fetchers.fmt_reset_epoch(codex["5h_reset"], lang) if mode == "5h"
                     else fetchers.fmt_reset_epoch(codex["7d_reset"], lang))
            plan = fetchers._fmt_plan(codex.get("plan"), lang)
            label = "?" if pct is None else str(pct)
            self._codex_icon.setIcon(render_service_icon(pct, False, dark))
            tip = f"Codex{plan}  {mode}: {label}%  {tr(lang, '重置', 'reset')} {reset}"
            self._codex_icon.setToolTip(tip)
            self._codex_detail_action.setText(tip)


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("System tray not available on this system.", file=sys.stderr)
        sys.exit(1)
    tray = AiLimitTray(app)  # noqa: F841  (kept alive by local ref through app.exec loop)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
