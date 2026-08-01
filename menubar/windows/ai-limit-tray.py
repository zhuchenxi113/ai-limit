#!/usr/bin/env python3
"""ai-limit Windows 托盘 App（PySide6 版）。

跟 mac 版（menubar/ai-limit-app.py，rumps）功能对齐，但 GUI 框架完全不同，
是独立文件。共享 usage.py 的数据抓取逻辑和偏好设置文件格式（~/.ai-limit-menubar*）。

v1 范围（已实现）：Claude/Codex 用量图标 + 详情菜单、5h/7d 显示切换、
刷新频率、语言切换、开机自启、检查更新（Ed25519 验签和版本校验通过后，
以可见的 Inno Setup 向导安装，见 updater_win.py）。

菜单栏样式固定用"电池条+数字"组合；服务状态支持 Statuspage 组件级勾选。
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

from usage import (
    __version__, epoch_to_local,  # noqa: F401  (epoch_to_local re-exported for fetchers)
    CLAUDE_STATUS_PAGE_URL, CODEX_STATUS_PAGE_URL,
    status_channels, normalize_status_selection,
)
import state
from lang_win import detect_system_lang, tr
from icon_render import render_service_pixmap, render_placeholder_pixmap, is_dark_taskbar
from trayicon_win32 import Win32TrayIcon, icon_pixel_size
from autostart_win import is_autostart_enabled, set_autostart
from dialogs import app_icon, show_alert
import fetchers
import updater_win
from usage_panel import UsagePanel
from single_instance_win import acquire_single_instance, release_single_instance

_SYSTEM_LANG = detect_system_lang()
_REFRESH_MINS = (1, 2, 3, 4, 5)
_PROJECT_URL = "https://github.com/zhuchenxi113/ai-limit"
_AUTHOR_URL_ZH = "https://gitee.com/zhuchenxi113"
_AUTHOR_URL_EN = "https://github.com/zhuchenxi113"
_GITHUB_PAGE = _PROJECT_URL + "/releases"
_GITEE_PAGE = "https://gitee.com/zhuchenxi113/ai-limit/releases"

# 固定 GUID，让 Explorer 把 Claude/Codex 两个图标当独立身份持久化排序/固定位置
# （2026-07-20 查证：同一进程发出的多个 QSystemTrayIcon 共享同一条
# NotifyIconSettings 记录，导致其中一个图标无法被拖拽重新排序，见
# docs/reference/lessons.md）。这两个 GUID 一旦发布就不能再改，否则老用户
# 会看到"新图标"出现在默认位置，之前保存的排序/固定状态全部丢失。
_CLAUDE_ICON_GUID = "501b4443-6568-40a4-bc28-79b8a6c29d55"
_CODEX_ICON_GUID = "6a3ddf3b-8438-472a-8074-c10bba1bc272"


def _error_icon_is_warning(data: dict) -> bool:
    """Only actionable failures use the yellow warning icon.

    Connectivity failures still remain visible in the tooltip/panel, but a
    temporary offline state is not an account or quota warning.
    """
    return bool(data.get("icon_warning", True))


def _codex_tray_display(codex: dict, mode: str, lang: str):
    """Return the selected Codex quota and its tray text without cross-window fallback.

    A missing 5h window must remain unknown even when the 7d window is available;
    otherwise the icon labels a weekly percentage as 5h. ``pct=None`` is passed to
    the icon renderer, which keeps the battery outline and draws ``?`` inside it.
    """
    pct = codex["5h_left"] if mode == "5h" else codex["7d_left"]
    plan = fetchers._fmt_plan(codex.get("plan"), lang)
    if pct is None:
        unavailable = tr(lang, "当前未提供", "Currently unavailable")
        return None, f"Codex{plan}  {mode}: {unavailable}"

    reset = (fetchers.fmt_reset_epoch(codex["5h_reset"], lang) if mode == "5h"
             else fetchers.fmt_reset_epoch(codex["7d_reset"], lang))
    tip = f"Codex{plan}  {mode}: {pct}%  {tr(lang, '重置', 'reset')} {reset}"
    return pct, tip


class AiLimitTray:
    def __init__(self, app: QApplication):
        self._app = app
        self._state = state.load_state()
        self._claude, self._codex = state.load_cache()
        self._claude_status_raw = None
        self._codex_status_raw = None
        self._last_updated = None

        self._pending = []
        self._pending_lock = threading.Lock()
        self._fetch_generation = 0
        self._fetch_inflight = set()
        self._last_fetch_started = {}
        self._status_inflight = set()
        self._last_status_started = {}

        self._claude_icon = Win32TrayIcon(guid=_CLAUDE_ICON_GUID)
        self._codex_icon = Win32TrayIcon(guid=_CODEX_ICON_GUID)
        self._claude_icon.setIcon(render_placeholder_pixmap(icon_pixel_size()))
        self._codex_icon.setIcon(render_placeholder_pixmap(icon_pixel_size()))
        self._claude_icon.setToolTip("Claude Code")
        self._codex_icon.setToolTip("Codex")

        self._menu = QMenu()
        self._build_menu()
        self._claude_icon.setContextMenu(self._menu)
        self._codex_icon.setContextMenu(self._menu)

        self._panel = UsagePanel(self, is_dark_taskbar())
        self._menu.aboutToHide.connect(self._settings_menu_closed)
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
            parent=self._panel,
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

    def cursor_over_tray_icon(self) -> bool:
        return self._claude_icon.contains_cursor() or self._codex_icon.contains_cursor()

    def refresh_from_panel(self):
        self._panel.set_refreshing(self._lang())
        if not self._kick_background_fetch():
            self._render()

    def show_settings_menu(self, position: QPoint):
        self._menu.popup(position)

    def _settings_menu_closed(self):
        # Opening the menu already deactivates the panel. The menu's close does
        # not produce a second WindowDeactivate event, so hide explicitly here.
        if self._panel.isVisible():
            self._panel.hide()

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

        mode_menu = m.addMenu(tr(lang, "图标显示周期", "Icon Display Period"))
        self._window_group = QActionGroup(mode_menu)
        self._window_group.setExclusive(True)
        self._window_actions = {}
        selected_window = self._state.get("global", "5h")
        for mode, label in (("5h", tr(lang, "5 小时", "5-hour")), ("7d", tr(lang, "7 天", "7-day"))):
            act = mode_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(mode == selected_window)
            act.triggered.connect(lambda checked, m=mode: self._set_window(m, checked))
            self._window_group.addAction(act)
            self._window_actions[mode] = act

        bar_menu = m.addMenu(tr(lang, "托盘图标", "Tray Icons"))
        self._bar_service_actions = {}
        bar_services = self._state.get("bar_services") or ["claude", "codex"]
        for service, label in (("claude", "Claude Code"), ("codex", "Codex")):
            act = bar_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(service in bar_services)
            act.triggered.connect(
                lambda checked, svc=service: self._toggle_service("bar_services", svc, checked)
            )
            self._bar_service_actions[service] = act

        panel_menu = m.addMenu(tr(lang, "面板内容", "Panel Content"))
        self._panel_service_actions = {}
        panel_services = self._state.get("panel_services") or []
        for service, label in (("claude", "Claude Code"), ("codex", "Codex")):
            act = panel_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(service in panel_services)
            act.triggered.connect(
                lambda checked, svc=service: self._toggle_service("panel_services", svc, checked)
            )
            self._panel_service_actions[service] = act

        status_menu = m.addMenu(tr(lang, "服务状态", "Service Status"))
        self._status_actions = {"claude": {}, "codex": {}}
        status_specs = (
            ("claude", "Claude", CLAUDE_STATUS_PAGE_URL),
            ("codex", "Codex", CODEX_STATUS_PAGE_URL),
        )
        for service, label, page_url in status_specs:
            submenu = status_menu.addMenu(label)
            open_status = submenu.addAction(tr(lang, "打开官方状态页", "Open Official Status Page"))
            open_status.triggered.connect(lambda checked=False, url=page_url: webbrowser.open(url))
            submenu.addSeparator()
            selected = self._state.get(f"{service}_status_components") or []
            for key, _cid, official_name in status_channels(service):
                # 勾选项以内部稳定 key 标识（存 self._state / 偏好文件）；菜单文案
                # 优先显示本次 API 返回的官方名称，官方改名后跟随，key 不变。
                act = submenu.addAction(self._status_display_name(service, key, official_name))
                act.setCheckable(True)
                act.setChecked(key in selected)
                act.triggered.connect(
                    lambda checked, svc=service, k=key:
                    self._toggle_status_component(svc, k, checked)
                )
                self._status_actions[service][key] = act

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

        # Keep the language entry discoverable even when the current UI language
        # is unfamiliar. This mirrors macOS; 中文 / English are self-identifying.
        lang_menu = m.addMenu("语言 Language")
        lang_group = QActionGroup(lang_menu)
        lang_group.setExclusive(True)
        for code, label in (("auto", "跟随系统 Follow System"),
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
        about_menu = m.addMenu(
            tr(lang, f"关于（AI Limit {__version__}）", f"About (AI Limit {__version__})")
        )
        version_act = about_menu.addAction(f"AI Limit {__version__}")
        version_act.triggered.connect(lambda: webbrowser.open(_PROJECT_URL))

        author_act = about_menu.addAction(
            tr(lang, "作者：zhuchenxi", "Author: zhuchenxi")
        )
        author_act.triggered.connect(
            lambda: webbrowser.open(_AUTHOR_URL_ZH if self._lang() == "zh" else _AUTHOR_URL_EN)
        )

        self._check_update_action = about_menu.addAction(
            tr(lang, "检查更新", "Check for Updates")
        )
        self._check_update_action.triggered.connect(self._check_for_updates)

        star_act = about_menu.addAction(
            tr(lang, "⭐ 给个 Star，鼓励作者", "⭐ Star on GitHub — support the author")
        )
        star_act.triggered.connect(lambda: webbrowser.open(_PROJECT_URL))

        about_desc = about_menu.addAction(
            tr(lang, "Claude Code / Codex 额度监控", "Claude Code / Codex quota monitor")
        )
        about_desc.setEnabled(False)
        about_source = about_menu.addAction(
            tr(lang, "数据来源：本地日志 + 官方网页接口",
               "Source: local logs + official web endpoints")
        )
        about_source.setEnabled(False)

        m.addSeparator()
        quit_act = m.addAction(tr(lang, "退出", "Quit"))
        quit_act.triggered.connect(self._app.quit)

    def _set_window(self, mode, checked):
        if not checked or mode not in ("5h", "7d"):
            return
        # The period controls the number rendered in each tray icon. The detail
        # panel always shows both quota windows.
        self._state["global"] = mode
        self._state["display_windows"] = [mode]
        state.save_state(self._state)
        self._render()

    def _toggle_service(self, key, service, checked):
        selected = list(self._state.get(key) or [])
        if checked:
            if service not in selected:
                selected.append(service)
        elif service in selected:
            if key == "bar_services" and len(selected) == 1:
                self._bar_service_actions[service].setChecked(True)
                return
            selected.remove(service)
        selected = [candidate for candidate in ("claude", "codex") if candidate in selected]
        self._state[key] = selected
        state.save_state(self._state)
        if key == "bar_services":
            self._update_visibility()
        self._render()

    def _toggle_status_component(self, service, channel_key, checked):
        stkey = f"{service}_status_components"
        selected = list(self._state.get(stkey) or [])
        if checked and channel_key not in selected:
            selected.append(channel_key)
        elif not checked and channel_key in selected:
            selected.remove(channel_key)
        # 规范化：去重 + 按渠道定义顺序排列（并列取最差时顺序稳定）。
        self._state[stkey] = normalize_status_selection(service, selected)
        state.save_state(self._state)
        self._render()

    def _status_raw(self, service):
        # getattr 兜底：菜单可能在状态数据尚未初始化时先构建（含单测用 __new__ 构造）。
        if service == "claude":
            return getattr(self, "_claude_status_raw", None)
        return getattr(self, "_codex_status_raw", None)

    def _status_display_name(self, service, channel_key, official_name):
        """菜单/展示用名：本次 API 返回的官方名称优先，取不到时回退定义里的官方名称。
        匹配按官方组件 ID，官方改名后自动跟随，勾选身份（key）不受影响。"""
        raw = self._status_raw(service)
        if isinstance(raw, list):
            cid = next((c for k, c, _n in status_channels(service) if k == channel_key), None)
            if cid is not None:
                for comp in raw:
                    if comp.get("id") == cid and comp.get("name"):
                        return comp["name"]
        return official_name

    def _refresh_status_labels(self):
        """API 数据到达后，把状态子菜单勾选项的文案更新为最新官方名称。"""
        for service in ("claude", "codex"):
            for key, _cid, official_name in status_channels(service):
                act = self._status_actions.get(service, {}).get(key)
                if act is not None:
                    act.setText(self._status_display_name(service, key, official_name))

    def _set_refresh_min(self, mins):
        self._state["refresh_min"] = mins
        state.save_state(self._state)
        self._auto_timer.setInterval(self._refresh_sec() * 1000)
        self._render()

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
        """用户在"发现新版本"弹窗里确认后，后台下载并校验安装包。"""
        lang = self._lang()

        def _worker():
            try:
                import tempfile
                dest_dir = pathlib.Path(tempfile.mkdtemp(prefix="ai-limit-update-"))
                setup_path = updater_win.download_release_setup(info["asset_url"], dest_dir)
                signature_path = updater_win.download_release_signature(
                    info["signature_url"], dest_dir
                )
                trust = updater_win.verify_installer(
                    setup_path,
                    signature_path,
                    info["latest"],
                    info["asset_name"],
                )
                result = {
                    "ok": True,
                    "setup_path": str(setup_path),
                    "version": info["latest"],
                    "trust": trust,
                    "source": info.get("source"),
                    "source_url": info["asset_url"],
                }
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
        now = datetime.datetime.now().timestamp()
        to_fetch = []
        status_to_fetch = []
        with self._pending_lock:
            retry_until = self._state.get("oauth_retry_until") or {}
            for service in services:
                if (service not in self._status_inflight and
                        now - float(self._last_status_started.get(service, 0)) >= 55):
                    self._status_inflight.add(service)
                    self._last_status_started[service] = now
                    status_to_fetch.append(service)
                # Claude 只走 Firefox 网页路径，旧 OAuth 退避不能阻止它刷新。
                retry_blocked = (service != "claude" and
                                 now < float(retry_until.get(service, 0)))
                if (service not in self._fetch_inflight and
                        not retry_blocked and
                        now - float(self._last_fetch_started.get(service, 0)) >= 55):
                    self._fetch_inflight.add(service)
                    self._last_fetch_started[service] = now
                    to_fetch.append(service)
            if not to_fetch and not status_to_fetch:
                return False
            if to_fetch:
                self._fetch_generation += 1
                generation = self._fetch_generation
        for service in to_fetch:
            threading.Thread(
                target=self._async_refresh_service,
                args=(service, generation),
                daemon=True,
            ).start()
        for service in status_to_fetch:
            threading.Thread(
                target=self._async_refresh_status,
                args=(service,),
                daemon=True,
            ).start()
        return True

    def _async_refresh_service(self, service, generation):
        lang = self._lang()
        data = fetchers.fetch_claude(lang) if service == "claude" else fetchers.fetch_codex(lang)
        self._queue_pending("fetch_service", (generation, service, data))

    def _async_refresh_status(self, service):
        current_status = fetchers.fetch_service_status(service)
        self._queue_pending("status_service", (service, current_status))

    def _apply_pending(self):
        with self._pending_lock:
            pending = self._pending.pop(0) if self._pending else None
        if pending is None:
            return
        kind, payload = pending
        if kind == "fetch_service":
            generation, service, data = payload
            with self._pending_lock:
                self._fetch_inflight.discard(service)
                if generation != self._fetch_generation:
                    return
            if data.get("transient"):
                retry_after = max(60, int(data.get("retry_after") or 120))
                retry_until = dict(self._state.get("oauth_retry_until") or {})
                retry_until[service] = datetime.datetime.now().timestamp() + retry_after
                self._state["oauth_retry_until"] = retry_until
                state.save_state(self._state)
                if service == "claude":
                    self._claude = data
                    claude, codex = data, None
                else:
                    self._codex = data
                    claude, codex = None, data
                state.save_cache(self._claude, self._codex)
                state.append_history(claude, codex)
                self._render()
                return
            retry_until = dict(self._state.get("oauth_retry_until") or {})
            if service in retry_until:
                retry_until.pop(service, None)
                self._state["oauth_retry_until"] = retry_until
                state.save_state(self._state)
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
        elif kind == "status_service":
            service, current_status = payload
            with self._pending_lock:
                self._status_inflight.discard(service)
            if service == "claude":
                self._claude_status_raw = current_status
            else:
                self._codex_status_raw = current_status
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
                    parent=self._panel,
                )
                return
            latest = info.get("latest", "")
            if not latest or updater_win._version_tuple(latest) <= updater_win._version_tuple(__version__):
                show_alert(
                    tr(lang, "已经是最新版本", "Up to Date"),
                    tr(lang, f"当前版本 {__version__}，无需更新。", f"Version {__version__}; no update is needed."),
                    tr(lang, "确定", "OK"),
                    parent=self._panel,
                    status="success",
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
                    parent=self._panel,
                )
                if opened:
                    page = _GITEE_PAGE if info.get("source") == "gitee" else _GITHUB_PAGE
                    webbrowser.open(page)
                return
            if not info.get("signature_url"):
                opened = show_alert(
                    tr(lang, "发现新版本", "New Version Available"),
                    tr(lang,
                       f"当前 {__version__}，最新 {latest}，但 Windows 安装包缺少 Ed25519 发布签名，"
                       "为安全起见不能自动运行。是否前往下载页？",
                       f"Current {__version__}, latest {latest}, but the Windows installer is missing its "
                       "Ed25519 release signature and cannot be run automatically. Open the download page?"),
                    tr(lang, "打开下载页", "Open Download Page"),
                    tr(lang, "取消", "Cancel"),
                    parent=self._panel,
                )
                if opened:
                    page = _GITEE_PAGE if info.get("source") == "gitee" else _GITHUB_PAGE
                    webbrowser.open(page)
                return
            confirmed = show_alert(
                tr(lang, "发现新版本", "New Version Available"),
                tr(lang,
                   f"<p style='font-size: 12pt; font-weight: 600; color: palette(text); margin: 0 0 10px 0;'>"
                   f"{__version__}&nbsp;&nbsp;→&nbsp;&nbsp;{latest}</p>"
                   "<p>下载并验证完成后，AI Limit 会退出并打开标准安装向导。</p>"
                   "<p><b style='color: palette(text);'>安装前安全校验</b><br>"
                   "安装包必须通过 AI Limit 的 Ed25519 发布签名验证。Windows 可能另行显示"
                   "“未知发布者”或 SmartScreen 提示，也可能不显示。</p>",
                   f"<p style='font-size: 12pt; font-weight: 600; color: palette(text); margin: 0 0 10px 0;'>"
                   f"{__version__}&nbsp;&nbsp;→&nbsp;&nbsp;{latest}</p>"
                   "<p>After download and verification, AI Limit will quit and open the standard setup wizard.</p>"
                   "<p><b style='color: palette(text);'>Security check before installation</b><br>"
                   "The installer must pass AI Limit's Ed25519 release-signature verification. Windows may "
                   "separately show an Unknown Publisher or SmartScreen warning, or it may show neither.</p>"),
                tr(lang, "下载并安装", "Download and Install"),
                tr(lang, "取消", "Cancel"),
                parent=self._panel,
            )
            if confirmed:
                self._check_update_action.setEnabled(False)
                self._check_update_action.setText(tr(lang, "更新中…", "Updating…"))
                self._start_update_download(info)
        elif kind == "update_download":
            lang = self._lang()
            if payload.get("ok"):
                try:
                    updater_win.trigger_interactive_install(
                        payload["setup_path"],
                        payload["version"],
                        payload["source_url"],
                    )
                except updater_win.UpdateFailed as e:
                    payload = {
                        "ok": False,
                        "reason": e.reason,
                        "detail": e.detail,
                        "source": payload.get("source"),
                    }
                else:
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
                parent=self._panel,
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
        claude_status = fetchers.status_info(
            self._claude_status_raw,
            self._state.get("claude_status_components") or [],
            "claude",
            lang,
        )
        codex_status = fetchers.status_info(
            self._codex_status_raw,
            self._state.get("codex_status_components") or [],
            "codex",
            lang,
        )
        self._refresh_status_labels()

        self._panel.set_data(
            self._claude,
            self._codex,
            claude_status,
            codex_status,
            lang,
            self._state.get("refresh_min", 1),
            self._last_updated,
        )

        size = icon_pixel_size()

        if "error" in claude:
            warning = _error_icon_is_warning(claude)
            self._claude_icon.setIcon(render_service_pixmap(None, warning, dark, "claude", size))
            self._claude_icon.setToolTip(f"Claude Code — {claude['error']}")
            self._claude_detail_action.setText(f"Claude Code ⚠ {claude['error']}")
        elif claude:
            pct = claude["5h_left"] if mode == "5h" else claude["7d_left"]
            reset = (fetchers.fmt_reset_iso(claude["5h_reset"], lang) if mode == "5h"
                     else fetchers.fmt_reset_iso(claude["7d_reset"], lang))
            plan = fetchers._fmt_plan(claude.get("plan"), lang)
            self._claude_icon.setIcon(render_service_pixmap(pct, False, dark, "claude", size))
            tip = f"Claude Code{plan}  {mode}: {pct}%  {tr(lang, '重置', 'reset')} {reset}"
            self._claude_icon.setToolTip(tip)
            self._claude_detail_action.setText(tip)

        if "error" in codex:
            warning = _error_icon_is_warning(codex)
            self._codex_icon.setIcon(render_service_pixmap(None, warning, dark, "codex", size))
            self._codex_icon.setToolTip(f"Codex — {codex['error']}")
            self._codex_detail_action.setText(f"Codex ⚠ {codex['error']}")
        elif codex:
            pct, tip = _codex_tray_display(codex, mode, lang)
            self._codex_icon.setIcon(render_service_pixmap(pct, False, dark, "codex", size))
            self._codex_icon.setToolTip(tip)
            self._codex_detail_action.setText(tip)


def main():
    instance_handle = acquire_single_instance()
    if instance_handle is None:
        return
    show_panel = "--show-panel" in sys.argv
    if show_panel:
        sys.argv.remove("--show-panel")
    try:
        # Qt 6 在 QApplication 构造时默认启用 Per-Monitor-V2。不要提前调用旧的
        # SetProcessDpiAwareness，否则 Qt 的设置会被 Windows 以 Access Denied 拒绝。
        app = QApplication(sys.argv)
        app.setWindowIcon(app_icon())
        base_font = QFont("Microsoft YaHei UI")
        base_font.setPointSizeF(9.0)
        base_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        app.setFont(base_font)
        app.setQuitOnLastWindowClosed(False)
        tray = AiLimitTray(app)  # noqa: F841  (kept alive by local ref through app.exec loop)
        app.aboutToQuit.connect(lambda: (tray._claude_icon.hide(), tray._codex_icon.hide()))
        if show_panel:
            QTimer.singleShot(2500, lambda: tray._panel.toggle_near(tray._claude_icon))
        sys.exit(app.exec())
    finally:
        release_single_instance(instance_handle)


if __name__ == "__main__":
    main()
