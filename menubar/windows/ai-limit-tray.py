#!/usr/bin/env python3
"""ai-limit Windows 托盘 App（PySide6 版）。

跟 mac 版（menubar/ai-limit-app.py，rumps）功能对齐，但 GUI 框架完全不同，
是独立文件。共享 usage.py 的数据抓取逻辑和偏好设置文件格式（~/.ai-limit-menubar*）。

v1 范围（已实现）：Claude/Codex 用量图标 + 详情菜单、5h/7d 显示切换、
刷新频率、语言切换、开机自启、检查更新（完整下载+签名校验+触发
Inno Setup 静默安装+自动重启，见 updater_win.py）。

v1 暂缺（后续增量）：mac 版的服务状态监控子菜单（Claude/Codex Status
Page 组件级勾选）、菜单栏样式切换（number-only/battery-only，v1 图标
固定用"电池条+数字"组合样式）。
"""
import pathlib
import sys
import threading
import webbrowser
import datetime

_HERE = pathlib.Path(__file__).resolve().parent
_REPO = _HERE.parent.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_REPO))

from PySide6.QtCore import QPoint, QTimer
from PySide6.QtGui import QAction, QActionGroup, QFont
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from usage import __version__, epoch_to_local  # noqa: F401  (epoch_to_local re-exported for fetchers)
import state
from lang_win import detect_system_lang, tr
from icon_render import render_service_icon, render_placeholder_icon, is_dark_taskbar
from autostart_win import is_autostart_enabled, set_autostart
from dialogs import show_alert
import fetchers
import updater_win
from usage_panel import UsagePanel

_SYSTEM_LANG = detect_system_lang()
_REFRESH_MINS = (1, 2, 3, 4, 5)
_GITHUB_PAGE = "https://github.com/zhuchenxi113/ai-limit/releases"
_GITEE_PAGE = "https://gitee.com/zhuchenxi113/ai-limit/releases"


class AiLimitTray:
    def __init__(self, app: QApplication):
        self._app = app
        self._state = state.load_state()
        self._claude, self._codex = state.load_cache()
        self._last_updated = None

        self._pending = []
        self._pending_lock = threading.Lock()
        self._fetch_generation = 0

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

        self._panel = UsagePanel(self, is_dark_taskbar())
        self._claude_icon.activated.connect(
            lambda reason: self._toggle_panel(self._claude_icon, reason)
        )
        self._codex_icon.activated.connect(
            lambda reason: self._toggle_panel(self._codex_icon, reason)
        )

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
        self._check_update_failure_marker()

    def _check_update_failure_marker(self):
        """启动时检查一次上次自动更新有没有走完，只提示一次。"""
        result = updater_win.check_update_pending_marker(__version__)
        if result is None:
            return
        lang = self._lang()
        show_alert(
            tr(lang, "自动更新可能未完成", "Auto-Update May Be Incomplete"),
            tr(lang, f"当前仍是 {__version__}，上次触发的更新目标是 {result.get('target_version', '?')}。"
                     f"可前往下载页手动更新。",
                     f"Still on {__version__}; the last update targeted {result.get('target_version', '?')}. "
                     "You can update manually from the download page."),
            tr(lang, "好", "OK"),
        )

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

    def _toggle_panel(self, icon, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._panel.toggle_near(icon)

    def refresh_from_panel(self):
        self._panel.set_refreshing(self._lang())
        self._kick_background_fetch()

    def show_settings_menu(self, position: QPoint):
        self._menu.popup(position)

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
            info = updater_win.fetch_latest_release_info()
            self._queue_pending("update_check", info)

        threading.Thread(target=_worker, daemon=True).start()

    def _start_update_download(self, info: dict):
        """用户在"发现新版本"弹窗里点了确认，后台下载+校验+触发安装。"""
        lang = self._lang()

        def _worker():
            try:
                import tempfile
                dest_dir = pathlib.Path(tempfile.mkdtemp(prefix="ai-limit-update-"))
                setup_path = updater_win.download_release_setup(info["asset_url"], dest_dir)
                updater_win.verify_installer(setup_path, info["latest"])
                result = {"ok": True, "setup_path": str(setup_path), "version": info["latest"]}
            except updater_win.UpdateFailed as e:
                result = {"ok": False, "reason": e.reason, "detail": e.detail, "source": info.get("source")}
            except Exception as e:
                result = {"ok": False, "reason": "unknown", "detail": str(e), "source": info.get("source")}
            self._queue_pending("update_download", result)

        threading.Thread(target=_worker, daemon=True).start()

    # ── 后台抓取 / 主线程接力 ──────────────────────────────────────────────

    def _queue_pending(self, kind, payload):
        with self._pending_lock:
            self._pending.append((kind, payload))

    def _kick_background_fetch(self):
        # 托盘和面板是两个独立显示维度；任一处需要就必须抓取。
        services = set(self._state.get("bar_services") or [])
        services.update(self._state.get("panel_services") or [])
        with self._pending_lock:
            self._fetch_generation += 1
            generation = self._fetch_generation
        for service in services:
            threading.Thread(
                target=self._async_refresh_service,
                args=(service, generation),
                daemon=True,
            ).start()

    def _async_refresh_service(self, service, generation):
        lang = self._lang()
        data = fetchers.fetch_claude(lang) if service == "claude" else fetchers.fetch_codex(lang)
        self._queue_pending("fetch_service", (generation, service, data))

    def _apply_pending(self):
        with self._pending_lock:
            pending = self._pending.pop(0) if self._pending else None
        if pending is None:
            return
        kind, payload = pending
        if kind == "fetch_service":
            generation, service, data = payload
            with self._pending_lock:
                if generation != self._fetch_generation:
                    return
            if service == "claude":
                self._claude = data
                claude, codex = data, None
            else:
                self._codex = data
                claude, codex = None, data
            state.save_cache(self._claude, self._codex)
            state.append_history(claude, codex)
            self._last_updated = datetime.datetime.now().astimezone()
            self._render()
        elif kind == "update_check":
            lang = self._lang()
            self._check_update_action.setEnabled(True)
            self._check_update_action.setText(tr(lang, "检查更新", "Check for Updates"))
            info = payload
            if info.get("error"):
                show_alert(
                    tr(lang, "检查更新失败", "Update Check Failed"),
                    tr(lang, "网络不可用或 GitHub/Gitee 均无法访问", "Network unavailable or both GitHub/Gitee unreachable"),
                    tr(lang, "好", "OK"),
                )
                return
            latest = info.get("latest", "")
            if not latest or updater_win._version_tuple(latest) <= updater_win._version_tuple(__version__):
                show_alert(
                    tr(lang, "已是最新版本", "Up to Date"),
                    tr(lang, f"当前版本 {__version__} 已是最新。", f"Version {__version__} is up to date."),
                    tr(lang, "好", "OK"),
                )
                return
            if not info.get("asset_url"):
                # 找到了新版本但这次 Release 没有 Windows 安装包资产（比如刚发版
                # 还没传完），只能退到手动下载页，不能假装能自动更新。
                opened = show_alert(
                    tr(lang, "发现新版本", "New Version Available"),
                    tr(lang, f"当前 {__version__}，最新 {latest}，但未找到 Windows 安装包，是否前往下载页？",
                             f"Current {__version__}, latest {latest}, but no Windows installer asset found. Open download page?"),
                    tr(lang, "打开下载页", "Open Download Page"),
                    tr(lang, "取消", "Cancel"),
                )
                if opened:
                    page = _GITEE_PAGE if info.get("source") == "gitee" else _GITHUB_PAGE
                    webbrowser.open(page)
                return
            confirmed = show_alert(
                tr(lang, "发现新版本", "New Version Available"),
                tr(lang, f"当前 {__version__}，最新 {latest}。是否立即更新？更新时会退出并重启。",
                         f"Current {__version__}, latest {latest}. Update now? The app will quit and restart."),
                tr(lang, "立即更新", "Update Now"),
                tr(lang, "取消", "Cancel"),
            )
            if confirmed:
                self._check_update_action.setEnabled(False)
                self._check_update_action.setText(tr(lang, "更新中…", "Updating…"))
                self._start_update_download(info)
        elif kind == "update_download":
            lang = self._lang()
            if payload.get("ok"):
                updater_win.trigger_silent_install(payload["setup_path"], payload["version"])
                self._app.quit()
                return
            self._check_update_action.setEnabled(True)
            self._check_update_action.setText(tr(lang, "检查更新", "Check for Updates"))
            detail = payload.get("detail", "")
            opened = show_alert(
                tr(lang, "更新失败", "Update Failed"),
                tr(lang, f"自动更新未完成（{detail}）。是否打开下载页手动安装？",
                         f"Automatic update did not complete ({detail}). Open the download page to install manually?"),
                tr(lang, "打开下载页", "Open Download Page"),
                tr(lang, "取消", "Cancel"),
            )
            if opened:
                page = _GITEE_PAGE if payload.get("source") == "gitee" else _GITHUB_PAGE
                webbrowser.open(page)

    # ── 渲染 ──────────────────────────────────────────────────────────────

    def _render(self):
        lang = self._lang()
        mode = self._state.get("global", "5h")
        dark = is_dark_taskbar()
        claude = self._claude or {}
        codex = self._codex or {}

        self._panel.set_data(
            self._claude,
            self._codex,
            lang,
            self._state.get("refresh_min", 1),
            self._last_updated,
        )

        if "error" in claude:
            self._claude_icon.setIcon(render_service_icon(None, True, dark, "claude"))
            self._claude_icon.setToolTip(f"Claude Code — {claude['error']}")
            self._claude_detail_action.setText(f"Claude Code ⚠ {claude['error']}")
        elif claude:
            pct = claude["5h_left"] if mode == "5h" else claude["7d_left"]
            reset = (fetchers.fmt_reset_iso(claude["5h_reset"], lang) if mode == "5h"
                     else fetchers.fmt_reset_iso(claude["7d_reset"], lang))
            plan = fetchers._fmt_plan(claude.get("plan"), lang)
            self._claude_icon.setIcon(render_service_icon(pct, False, dark, "claude"))
            tip = f"Claude Code{plan}  {mode}: {pct}%  {tr(lang, '重置', 'reset')} {reset}"
            self._claude_icon.setToolTip(tip)
            self._claude_detail_action.setText(tip)

        if "error" in codex:
            self._codex_icon.setIcon(render_service_icon(None, True, dark, "codex"))
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
            self._codex_icon.setIcon(render_service_icon(pct, False, dark, "codex"))
            tip = f"Codex{plan}  {mode}: {label}%  {tr(lang, '重置', 'reset')} {reset}"
            self._codex_icon.setToolTip(tip)
            self._codex_detail_action.setText(tip)


def main():
    # 显式声明 Per-Monitor-V2 DPI 感知，必须在 QApplication 构造前调用——
    # 不声明的话，未声明 DPI 感知的旧式进程会被系统整体拉伸渲染结果来
    # "凑合适配"当前缩放比例，托盘图标这种小尺寸位图会被拉出明显的模糊
    # （2026-07-19 实测踩过，跟 icon_render.py 的多分辨率修复是两个独立
    # 但会叠加的模糊成因，见 docs/reference/lessons.md）。
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        pass
    app = QApplication(sys.argv)
    base_font = QFont("Microsoft YaHei UI")
    base_font.setPointSizeF(9.0)
    base_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(base_font)
    app.setQuitOnLastWindowClosed(False)
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("System tray not available on this system.", file=sys.stderr)
        sys.exit(1)
    tray = AiLimitTray(app)  # noqa: F841  (kept alive by local ref through app.exec loop)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
