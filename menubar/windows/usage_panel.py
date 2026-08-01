"""Windows 托盘左键弹出的紧凑用量面板。"""
import ctypes
import datetime
import webbrowser

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from usage import __version__, CLAUDE_STATUS_PAGE_URL, CODEX_STATUS_PAGE_URL
import fetchers
from lang_win import tr


CLAUDE_USAGE_URL = "https://claude.ai/settings/usage"
CODEX_ANALYTICS_URL = "https://chatgpt.com/codex/cloud/settings/analytics"
_TAIL_HEIGHT = 7
_TAIL_HALF_WIDTH = 6
_PANEL_RADIUS = 8


class _WinRect(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long), ("top", ctypes.c_long),
        ("right", ctypes.c_long), ("bottom", ctypes.c_long),
    ]


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _WinRect),
        ("rcWork", _WinRect),
        ("dwFlags", ctypes.c_ulong),
    ]


_user32 = ctypes.windll.user32
_user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
_user32.FindWindowW.restype = ctypes.c_void_p
_user32.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(_WinRect)]
_user32.GetWindowRect.restype = ctypes.c_int
_user32.MonitorFromWindow.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
_user32.MonitorFromWindow.restype = ctypes.c_void_p
_user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_MonitorInfo)]
_user32.GetMonitorInfoW.restype = ctypes.c_int


def _logical_taskbar_rect(taskbar, monitor, screen_rect: QRect,
                          device_pixel_ratio: float) -> QRect | None:
    """把 Win32 任务栏坐标映射到 Qt 的逻辑坐标。

    DPI 感知状态不同会让 GetWindowRect 返回物理或已虚拟化坐标；通过显示器
    宽度与 QScreen 宽度的关系判断是否需要除以 DPR，不能盲目固定换算。
    """
    native_width = monitor[2] - monitor[0]
    native_height = monitor[3] - monitor[1]
    dpr = max(1.0, float(device_pixel_ratio or 1.0))
    if abs(native_width - screen_rect.width()) <= 2 and \
            abs(native_height - screen_rect.height()) <= 2:
        scale = 1.0
    elif abs(native_width / dpr - screen_rect.width()) <= 2 and \
            abs(native_height / dpr - screen_rect.height()) <= 2:
        scale = dpr
    else:
        return None

    left = screen_rect.left() + round((taskbar[0] - monitor[0]) / scale)
    top = screen_rect.top() + round((taskbar[1] - monitor[1]) / scale)
    right = screen_rect.left() + round((taskbar[2] - monitor[0]) / scale)
    bottom = screen_rect.top() + round((taskbar[3] - monitor[1]) / scale)
    return QRect(left, top, max(0, right - left), max(0, bottom - top))


def _available_geometry_excluding_taskbar(screen) -> QRect:
    """即使任务栏设为自动隐藏，也把当前可见任务栏当作不可覆盖区域。"""
    area = QRect(screen.availableGeometry())
    try:
        hwnd = _user32.FindWindowW("Shell_TrayWnd", None)
        if not hwnd:
            return area
        taskbar = _WinRect()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(taskbar)):
            return area
        monitor_handle = _user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if not _user32.GetMonitorInfoW(monitor_handle, ctypes.byref(info)):
            return area
        taskbar_tuple = (taskbar.left, taskbar.top, taskbar.right, taskbar.bottom)
        monitor_tuple = (
            info.rcMonitor.left, info.rcMonitor.top,
            info.rcMonitor.right, info.rcMonitor.bottom,
        )
        logical = _logical_taskbar_rect(
            taskbar_tuple, monitor_tuple, screen.geometry(), screen.devicePixelRatio())
        if logical is None or not logical.intersects(screen.geometry()):
            return area

        screen_rect = screen.geometry()
        if logical.top() > screen_rect.top() and logical.bottom() >= screen_rect.bottom() - 1:
            area.setBottom(min(area.bottom(), logical.top() - 1))
        elif logical.bottom() < screen_rect.bottom() and logical.top() <= screen_rect.top() + 1:
            area.setTop(max(area.top(), logical.bottom() + 1))
        elif logical.left() > screen_rect.left() and logical.right() >= screen_rect.right() - 1:
            area.setRight(min(area.right(), logical.left() - 1))
        elif logical.right() < screen_rect.right() and logical.left() <= screen_rect.left() + 1:
            area.setLeft(max(area.left(), logical.right() + 1))
    except Exception:
        pass
    return area


def _should_hide_deactivated_panel(*, is_visible: bool, is_active: bool,
                                   menu_visible: bool, cursor_over_tray: bool) -> bool:
    """Keep the panel visible until a tray-icon click can perform the toggle.

    Clicking a tray icon deactivates the panel before Windows delivers the tray
    activation callback. Hiding here would make the later toggle show it again.
    """
    return is_visible and not is_active and not menu_visible and not cursor_over_tray


def _apply_windows_surface(widget: QWidget, dark: bool) -> None:
    """透明自绘窗口不使用 DWM 的矩形非客户区外框。"""
    try:
        hwnd = int(widget.winId())
        dwm = ctypes.windll.dwmapi
        # DWMWA_NCRENDERING_POLICY = 2，DWMNCRP_DISABLED = 1。
        # 圆角、边框和尖角都由 paintEvent 统一绘制；让 DWM 再处理非客户区
        # 会把整个透明顶层窗口当成矩形，在尖角外套出第二层轮廓。
        nc_disabled = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(
            hwnd, 2, ctypes.byref(nc_disabled), ctypes.sizeof(nc_disabled)
        )
        dark_value = ctypes.c_int(1 if dark else 0)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_value), ctypes.sizeof(dark_value))
    except Exception:
        pass


def _draw_icon(painter: QPainter, kind: str, rect: QRectF, color: QColor) -> None:
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(color, 1.35, Qt.PenStyle.SolidLine,
               Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    center = rect.center()

    if kind == "refresh":
        arc = QRectF(center.x() - 7, center.y() - 7, 14, 14)
        painter.drawArc(arc, 38 * 16, 292 * 16)
        path = QPainterPath()
        path.moveTo(arc.right() - 0.5, arc.top() + 1.3)
        path.lineTo(arc.right() - 0.8, arc.top() + 5.4)
        path.lineTo(arc.right() - 4.7, arc.top() + 3.4)
        painter.drawPath(path)
    elif kind == "more":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        for dx in (-5.5, 0, 5.5):
            painter.drawEllipse(QRectF(center.x() + dx - 1.0, center.y() - 1.0, 2.0, 2.0))
    elif kind == "external":
        painter.drawRoundedRect(QRectF(center.x() - 6, center.y() - 4, 9.5, 9.5), 1, 1)
        path = QPainterPath()
        path.moveTo(center.x() - 0.5, center.y() - 5)
        path.lineTo(center.x() + 5, center.y() - 5)
        path.lineTo(center.x() + 5, center.y() + 0.5)
        path.moveTo(center.x() + 4.5, center.y() - 4.5)
        path.lineTo(center.x() - 1, center.y() + 1)
        painter.drawPath(path)


class IconButton(QPushButton):
    def __init__(self, kind: str, tooltip: str, color: str, parent=None):
        super().__init__(parent)
        self._kind = kind
        self._color = QColor(color)
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(30, 30)
        self.setObjectName("iconButton")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        _draw_icon(painter, self._kind, QRectF(self.rect()), self._color)


class LinkButton(QPushButton):
    def __init__(self, text: str, color: str, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        self.setText(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self.setObjectName("linkButton")

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        _draw_icon(painter, "external", QRectF(8, 7, 20, 20), self._color)


class StatusLink(QWidget):
    """Borderless, clickable status label with a separately colored dot."""
    clicked = Signal()

    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        self._palette = palette
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(3)
        self.prefix = QLabel()
        self.prefix.setObjectName("statusPrefix")
        self.dot = QLabel("●")
        self.dot.setObjectName("statusDot")
        row.addWidget(self.prefix)
        row.addWidget(self.dot)

    def set_status(self, info: dict, lang: str) -> None:
        self.prefix.setText(tr(lang, "状态：", "Status:"))
        self.dot.setStyleSheet(f"color: {info['color']}; font-size: 9pt;")
        detail = info["text"]
        if info.get("component"):
            detail += f" · {info['component']}"
        self.setToolTip(detail)

    def enterEvent(self, event) -> None:
        self.prefix.setStyleSheet(f"color: {self._palette['fg']};")
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.prefix.setStyleSheet("")
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if (event.button() == Qt.MouseButton.LeftButton and
                self.rect().contains(event.position().toPoint())):
            self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class UsageRow(QWidget):
    def __init__(self, palette: dict, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(4)
        line = QHBoxLayout()
        line.setSpacing(5)
        self.period = QLabel()
        self.period.setObjectName("period")
        self.period.setFixedWidth(24)
        self.value = QLabel()
        self.value.setObjectName("quotaValue")
        self.reset = QLabel()
        self.reset.setObjectName("secondary")
        line.addWidget(self.period)
        line.addWidget(self.value)
        line.addStretch()
        line.addWidget(self.reset)
        box.addLayout(line)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        box.addWidget(self.progress)
        self._palette = palette

    def update_value(self, period: str, pct, reset: str, color: str) -> None:
        self.period.setText(period)
        self.value.setText("—" if pct is None else f"{pct}%")
        self.reset.setText(reset)
        self.progress.setVisible(pct is not None)
        self.progress.setValue(0 if pct is None else max(0, min(100, int(pct))))
        self.progress.setStyleSheet(
            f"QProgressBar {{ background: {self._palette['track']}; border: none; border-radius: 2px; }}"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 2px; }}"
        )


class ServiceSection(QFrame):
    def __init__(self, name: str, color: str, palette: dict,
                 status_page_url: str, parent=None):
        super().__init__(parent)
        self._color = color
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 4, 0, 4)
        box.setSpacing(7)
        header = QHBoxLayout()
        header.setSpacing(7)
        self.title = QLabel(name)
        self.title.setObjectName("serviceTitle")
        self.plan = QLabel()
        self.plan.setObjectName("planBadge")
        self.status = StatusLink(palette)
        self.status.clicked.connect(lambda: webbrowser.open(status_page_url))
        header.addWidget(self.title)
        header.addWidget(self.plan)
        header.addStretch()
        header.addWidget(self.status)
        box.addLayout(header)
        self.error = QLabel()
        self.error.setObjectName("error")
        self.error.setWordWrap(True)
        box.addWidget(self.error)
        self.rows = [UsageRow(palette), UsageRow(palette)]
        for row in self.rows:
            box.addWidget(row)

    def set_data(self, data, status_info, lang: str, is_claude: bool) -> None:
        if status_info:
            self.status.set_status(status_info, lang)
            self.status.show()
        else:
            self.status.hide()
        if data is None:
            self.error.setProperty("hasError", False)
            self.error.setProperty("isWarning", False)
            self.error.setText(tr(lang, "正在获取真实数据…", "Fetching live data…"))
            self.error.setVisible(True)
            self.plan.hide()
            for row in self.rows:
                row.hide()
            self.error.style().unpolish(self.error)
            self.error.style().polish(self.error)
            return

        error = data.get("error")
        self.error.setProperty("hasError", bool(error))
        self.error.setProperty("isWarning", bool(error and data.get("transient")))
        self.error.setVisible(bool(error))
        for row in self.rows:
            row.setVisible(not error)
        if error:
            self.error.setText(str(error))
            self.plan.hide()
            self.error.style().unpolish(self.error)
            self.error.style().polish(self.error)
            return

        raw_plan = data.get("plan")
        plan = "" if not raw_plan or raw_plan == "?" else str(raw_plan).replace("_", " ").title()
        if data.get("source") == "snapshot":
            source = tr(lang, "缓存", "Cached")
            plan = f"{plan} · {source}" if plan else source
        self.plan.setText(plan)
        self.plan.setVisible(bool(plan))
        labels = ("5h", "7d") if is_claude else (
            data.get("5h_label") or "5h", data.get("7d_label") or "7d"
        )
        reset_formatter = fetchers.fmt_reset_iso if is_claude else fetchers.fmt_reset_epoch
        for row, label, key in zip(self.rows, labels, ("5h", "7d")):
            pct = data.get(f"{key}_left")
            if pct is None:
                reset_text = tr(lang, "当前未提供", "Currently unavailable")
            else:
                reset = reset_formatter(data.get(f"{key}_reset"), lang)
                reset_text = tr(lang, f"重置 {reset}", f"Reset {reset}")
            row.update_value(label, pct, reset_text, self._color)


class UsagePanel(QWidget):
    def __init__(self, tray, dark: bool):
        super().__init__()
        self._tray = tray
        self._dark = dark
        self._anchor_point = None
        self._tail_side = "bottom"
        self._tail_x = 0.0
        self.setWindowTitle("AI Limit")
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                            Qt.WindowType.WindowStaysOnTopHint |
                            Qt.WindowType.NoDropShadowWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedWidth(344)

        self._palette = {
            "surface": "#202020" if dark else "#f9f9f9",
            "control": "rgba(255,255,255,18)" if dark else "rgba(255,255,255,220)",
            "hover": "rgba(255,255,255,30)" if dark else "rgba(0,0,0,11)",
            "fg": "#f5f5f5" if dark else "#1b1b1b",
            "secondary": "#b5b5b5" if dark else "#616161",
            "border": "rgba(255,255,255,24)" if dark else "rgba(0,0,0,24)",
            "divider": "rgba(255,255,255,18)" if dark else "rgba(0,0,0,18)",
            "track": "rgba(255,255,255,26)" if dark else "rgba(0,0,0,16)",
        }

        self._layout = QVBoxLayout(self)
        self._update_content_margins()
        self._layout.setSpacing(8)
        header = QHBoxLayout()
        header.setSpacing(2)
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self.title = QLabel("AI Limit")
        self.title.setObjectName("title")
        self.updated = QLabel()
        self.updated.setObjectName("secondary")
        title_box.addWidget(self.title)
        title_box.addWidget(self.updated)
        header.addLayout(title_box)
        header.addStretch()
        self.refresh_button = IconButton("refresh", "立即刷新", self._palette["fg"])
        self.refresh_button.clicked.connect(self._request_refresh)
        header.addWidget(self.refresh_button)
        self.more_button = IconButton("more", "更多设置", self._palette["fg"])
        self.more_button.clicked.connect(self._show_menu)
        header.addWidget(self.more_button)
        self._layout.addLayout(header)

        self.claude = ServiceSection(
            "Claude Code", "#d97757", self._palette, CLAUDE_STATUS_PAGE_URL
        )
        self._layout.addWidget(self.claude)
        divider = QFrame()
        divider.setObjectName("divider")
        divider.setFixedHeight(1)
        self._layout.addWidget(divider)
        codex_color = "#f2f2f2" if dark else "#202123"
        self.codex = ServiceSection(
            "Codex", codex_color, self._palette, CODEX_STATUS_PAGE_URL
        )
        self._layout.addWidget(self.codex)

        links = QHBoxLayout()
        links.setSpacing(7)
        self.claude_link = LinkButton("", self._palette["fg"])
        self.claude_link.clicked.connect(lambda: webbrowser.open(CLAUDE_USAGE_URL))
        self.codex_link = LinkButton("", self._palette["fg"])
        self.codex_link.clicked.connect(lambda: webbrowser.open(CODEX_ANALYTICS_URL))
        links.addWidget(self.claude_link)
        links.addWidget(self.codex_link)
        self._layout.addLayout(links)

        self.version = QLabel(f"v{__version__}")
        self.version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.version.setObjectName("secondary")
        self._layout.addWidget(self.version)
        self._apply_style()

    def _update_content_margins(self) -> None:
        top = 12 + (_TAIL_HEIGHT if self._tail_side == "top" else 0)
        bottom = 11 + (_TAIL_HEIGHT if self._tail_side == "bottom" else 0)
        self._layout.setContentsMargins(14, top, 14, bottom)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        tail_top = self._tail_side == "top"
        body_top = _TAIL_HEIGHT if tail_top else 0
        body_height = self.height() - _TAIL_HEIGHT
        body = QRectF(0.5, body_top + 0.5, self.width() - 1, body_height - 1)
        shape = QPainterPath()
        shape.addRoundedRect(body, _PANEL_RADIUS, _PANEL_RADIUS)

        tip_y = 0.5 if tail_top else self.height() - 0.5
        base_y = body.top() + 0.5 if tail_top else body.bottom() - 0.5
        tail = QPainterPath()
        tail.moveTo(self._tail_x - _TAIL_HALF_WIDTH, base_y)
        tail.lineTo(self._tail_x, tip_y)
        tail.lineTo(self._tail_x + _TAIL_HALF_WIDTH, base_y)
        tail.closeSubpath()
        shape = shape.united(tail)

        painter.setPen(QPen(QColor("#4a4a4a" if self._dark else "#c8c8c8"), 1))
        painter.setBrush(QColor(self._palette["surface"]))
        painter.drawPath(shape)
        painter.end()
        super().paintEvent(event)

    def _apply_style(self) -> None:
        p = self._palette
        self.setStyleSheet(f"""
            UsagePanel {{ background: transparent; border: none; }}
            QFrame {{ border: none; background: transparent; }}
            QFrame#divider {{ background: {p['divider']}; }}
            QLabel {{ color: {p['fg']}; font-family: 'Microsoft YaHei UI'; font-size: 9pt; }}
            QLabel#title {{ font-size: 13pt; font-weight: 600; }}
            QLabel#serviceTitle {{ font-size: 10pt; font-weight: 500; }}
            QLabel#quotaValue {{ font-size: 12pt; font-weight: 500; }}
            QLabel#period, QLabel#secondary {{ color: {p['secondary']}; font-size: 8pt; }}
            QLabel#statusPrefix {{ color: {p['secondary']}; font-size: 8pt; font-weight: 400; }}
            QLabel#error {{ color: {p['secondary']}; font-size: 8pt; padding: 3px 0; }}
            QLabel#error[hasError="true"] {{ color: #c42b1c; }}
            QLabel#error[isWarning="true"] {{ color: #c78d00; }}
            QLabel#planBadge {{ color: {p['secondary']}; background: {p['control']};
                border-radius: 5px; padding: 1px 6px; font-size: 8pt; }}
            QPushButton {{ color: {p['fg']}; border: none; font-family: 'Microsoft YaHei UI'; }}
            QPushButton#iconButton {{ background: transparent; border-radius: 6px; }}
            QPushButton#iconButton:hover {{ background: {p['hover']}; }}
            QPushButton#linkButton {{ background: {p['control']}; border: 1px solid {p['border']};
                border-radius: 7px; padding-left: 20px; font-size: 8pt; font-weight: 400; }}
            QPushButton#linkButton:hover {{ background: {p['hover']}; }}
        """)

    def set_data(self, claude, codex, claude_status, codex_status,
                 lang: str, refresh_minutes: int,
                 updated_at: datetime.datetime | None = None) -> None:
        self._set_language(lang)
        panel_services = self._tray._state.get("panel_services") or []
        self.claude.setVisible("claude" in panel_services)
        self.codex.setVisible("codex" in panel_services)
        self.claude.set_data(claude, claude_status, lang, True)
        self.codex.set_data(codex, codex_status, lang, False)
        if claude is None and codex is None:
            self.updated.setText(tr(lang, "正在获取真实数据…", "Fetching live data…"))
        elif updated_at is None:
            self.updated.setText(tr(lang, f"缓存数据 · 每 {refresh_minutes} 分钟",
                                    f"Cached data · every {refresh_minutes} min"))
        else:
            stamp = updated_at.strftime("%H:%M")
            self.updated.setText(tr(lang, f"{stamp} 更新 · 每 {refresh_minutes} 分钟",
                                    f"Updated {stamp} · every {refresh_minutes} min"))
        self.refresh_button.setEnabled(True)
        self.adjustSize()
        if self.isVisible() and self._anchor_point is not None:
            self._place_at_anchor()

    def set_refreshing(self, lang: str) -> None:
        self._set_language(lang)
        self.updated.setText(tr(lang, "正在刷新…", "Refreshing…"))
        self.refresh_button.setEnabled(False)

    def _set_language(self, lang: str) -> None:
        """Update every static panel label when the selected language changes."""
        self.refresh_button.setToolTip(tr(lang, "立即刷新", "Refresh now"))
        self.more_button.setToolTip(tr(lang, "更多设置", "More settings"))
        self.claude_link.setText(tr(lang, "Claude 用量页", "Claude usage"))
        self.codex_link.setText(tr(lang, "Codex 分析页", "Codex analytics"))

    def _request_refresh(self) -> None:
        self._tray.refresh_from_panel()

    def _show_menu(self) -> None:
        self._tray.show_settings_menu(self.more_button.mapToGlobal(QPoint(0, self.more_button.height() + 4)))

    def event(self, event) -> bool:
        result = super().event(event)
        if event.type() == QEvent.Type.WindowDeactivate:
            QTimer.singleShot(0, self._hide_after_window_deactivate)
        return result

    def _hide_after_window_deactivate(self) -> None:
        menu = getattr(self._tray, "_menu", None)
        menu_visible = bool(menu and menu.isVisible())
        cursor_over_tray = self._tray.cursor_over_tray_icon()
        if _should_hide_deactivated_panel(
                is_visible=self.isVisible(),
                is_active=self.isActiveWindow(),
                menu_visible=menu_visible,
                cursor_over_tray=cursor_over_tray):
            self.hide()

    def toggle_near(self, icon) -> None:
        if self.isVisible():
            self.hide()
            return
        self._anchor_point = QCursor.pos()
        self._place_at_anchor()
        self.show()
        _apply_windows_surface(self, self._dark)
        # 原生窗口创建后 Qt/Windows 才能给出最终尺寸；再定位一次，避免首帧尺寸
        # 与显示后尺寸不同而向下侵入任务栏。
        QTimer.singleShot(0, self._place_at_anchor)
        self.raise_()
        self.activateWindow()

    def _place_at_anchor(self) -> None:
        anchor = self._anchor_point or QCursor.pos()
        screen = QApplication.screenAt(anchor)
        if screen is None:
            screen = QApplication.primaryScreen()
        tail_side = "bottom" if anchor.y() > screen.geometry().center().y() else "top"
        if tail_side != self._tail_side:
            self._tail_side = tail_side
            self._update_content_margins()
        self.adjustSize()
        area = _available_geometry_excluding_taskbar(screen)
        x = anchor.x() - self.width() + 28
        if self._tail_side == "bottom":
            # Refresh can change the panel height after it is already open.
            # Recompute from a fixed bottom edge every time so it grows upward,
            # never down into the taskbar.
            bottom_edge = min(area.bottom() - 7, anchor.y() - 32)
            y = bottom_edge - self.height()
        else:
            y = max(area.top() + 7, anchor.y() + 32)
        x = max(area.left() + 7, min(x, area.right() - self.width() - 7))
        y = max(area.top() + 7, min(y, area.bottom() - self.height() - 7))
        self._tail_x = max(
            _PANEL_RADIUS + _TAIL_HALF_WIDTH,
            min(anchor.x() - x, self.width() - _PANEL_RADIUS - _TAIL_HALF_WIDTH),
        )
        self.move(x, y)
        self.update()
