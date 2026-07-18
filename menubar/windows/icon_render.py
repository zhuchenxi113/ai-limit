"""托盘图标绘制。

Windows 任务栏图标是纯位图（不像 macOS 状态栏那样能放任意宽度的富文本），
一个图标位显示不下"Claude 68%  CodeX 99%"这种并排文字，所以每个服务
（Claude / Codex）各用一个独立的 QSystemTrayIcon，图标画成电池条+
百分比数字，完整的标签文字放 tooltip 里（hover 时看）。

WARN_THRESHOLD/CRIT_THRESHOLD 阈值配色跟 usage.py 的 CLI 版一致（30/60 行）。
"""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

WARN_THRESHOLD = 20
CRIT_THRESHOLD = 10

_COLOR_OK   = QColor("#2fae4a")
_COLOR_WARN = QColor("#e0a020")
_COLOR_CRIT = QColor("#d94f4f")
_COLOR_ERR  = QColor("#9a9a9a")


def _fill_color(pct: int) -> QColor:
    if pct >= WARN_THRESHOLD:
        return _COLOR_OK
    if pct >= CRIT_THRESHOLD:
        return _COLOR_WARN
    return _COLOR_CRIT


def render_service_icon(pct: int | None, err: bool, dark_mode: bool, size: int = 32) -> QPixmap:
    """单个服务的托盘图标：圆角矩形电池条按 pct 填充 + 居中百分比数字。
    err=True 时画一个 "!" 占位；pct=None 时画 "?" 占位（数据还没到/该档暂无）。
    """
    dpr = 2.0
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)

    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    fg = QColor("#f0f0f0") if dark_mode else QColor("#2a2a2a")
    margin = 3.0
    rect = QRectF(margin, margin + 4, size - margin * 2, size - margin * 2 - 8)

    painter.setPen(fg)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(rect, 3, 3)

    if err:
        painter.setBrush(_COLOR_ERR)
    elif pct is not None:
        fill_w = rect.width() * max(0, min(100, pct)) / 100
        fill_rect = QRectF(rect.left(), rect.top(), fill_w, rect.height())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_fill_color(pct))
        painter.drawRoundedRect(fill_rect, 2, 2)

    painter.setPen(fg)
    font = QFont("Segoe UI", 9, QFont.Weight.Bold)
    painter.setFont(font)
    label = "!" if err else ("?" if pct is None else str(pct))
    text_rect = QRectF(0, 0, size, size)
    painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)

    painter.end()
    return px


def render_placeholder_icon(size: int = 32) -> QPixmap:
    """启动后真实数据抓到之前的占位图标，避免托盘区空白。"""
    dpr = 2.0
    px = QPixmap(int(size * dpr), int(size * dpr))
    px.setDevicePixelRatio(dpr)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#9a9a9a"))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    rect = QRectF(3, 7, size - 6, size - 14)
    painter.drawRoundedRect(rect, 3, 3)
    painter.end()
    return px


def is_dark_taskbar() -> bool:
    """深色/浅色任务栏判断：优先 Qt 6.5+ colorScheme API，失败退回注册表。"""
    try:
        from PySide6.QtGui import QGuiApplication
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme is not None:
            from PySide6.QtCore import Qt as _Qt
            return scheme == _Qt.ColorScheme.Dark
    except Exception:
        pass
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "SystemUsesLightTheme")
            return value == 0
    except Exception:
        return False
