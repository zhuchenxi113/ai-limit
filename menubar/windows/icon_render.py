"""托盘图标绘制。

Windows 任务栏图标是纯位图（不像 macOS 状态栏那样能放任意宽度的富文本），
一个图标位显示不下"Claude 68%  CodeX 99%"这种并排文字，所以每个服务
（Claude / Codex）各用一个独立的 QSystemTrayIcon。图标使用电池框内数字，
内部色块从左到右填满剩余额度；Claude 固定品牌橙色，Codex 随任务栏深浅切换白/深灰。
百分号和完整标签文字放 tooltip 里（hover 时看）。正常额度只通过填充宽度
表达消耗量：Claude 始终使用品牌橙色，Codex 始终使用任务栏适配的黑/白，
两者都不因额度高低变色；只有抓取失败的感叹号图标使用警告色。

**多分辨率坑（2026-07-19 实测踩过）**：早期实现固定按 "size=32 逻辑像素 +
devicePixelRatio=2.0" 画一张图再让 Qt 缩放到系统实际需要的尺寸。这里的
"2.0" 是瞎猜的，跟这台机器真实的缩放比例（125%，即 devicePixelRatio=1.25）
对不上——Qt 因此以为这张图代表"32 个逻辑像素"，而 Windows 任务栏小图标的
真实逻辑尺寸是 16（`GetSystemMetrics(SM_CXSMICON)` 查证，见
`docs/reference/lessons.md`），相当于图标被当成两倍尺寸画、显示时又被
硬缩小一半，文字/图形细节全糊掉，肉眼看是一团模糊的方块，数字完全认不出。

第一次修复（不够）：改成给 16/20/24/32/40/48 六档"看起来覆盖 100%-300%
缩放"的物理像素尺寸各画一张原生分辨率的图。**这个假设是错的**——实测
（2026-07-19 二轮诊断）发现 `QIcon.pixmap(QSize(logical))` 内部还会再乘一次
`devicePixelRatio` 算出实际要的物理尺寸，例如本机 dpr=1.25 时，请求
logical=20 实际要的是 25px 的图，logical=24 要的是 30px——25、30 都不在
六档列表里，Qt 只能从最接近的现有尺寸插值缩放，产生的图跟原生画一张同尺寸
图逐像素比对，差异高达 39%-44%，这才是图标依然模糊的真正原因（不是尺寸算
错，也不是 DPI 感知缺失，这两条之前已经修过）。

真正的修复（v1，QSystemTrayIcon 时代）：不猜测会被请求哪几档物理尺寸，直接
密集覆盖一个连续区间（14~64px 每个整数），保证任何被请求到的物理尺寸都有
原生渲染的图可以精确命中，不会落入插值。实测该区间内所有测试点差异归零。

**2026-07-20 改用原生 Win32 托盘图标后（见 trayicon_win32.py）**：不再需要
上面这套"多尺寸 QIcon 猜测覆盖"策略——`QSystemTrayIcon`/`QIcon.pixmap()`
自带的 devicePixelRatio 二次换算（正是这个模糊问题的根因）已经完全绕开：
调用方直接用 `trayicon_win32.icon_pixel_size()`（`GetSystemMetrics(SM_CXSMICON)`
的原始返回值，不经 Qt 任何再换算）算出目标物理像素，只画这一个尺寸，交给
`CreateIconIndirect` 前不再经过 Qt 的尺寸匹配/插值逻辑。这条历史踩坑记录
继续保留在这里，因为 `_paint_battery` 的"按 size 比例作画、不依赖固定像素值"
设计原则依然成立，也是这个函数没有再踩同一个坑的原因。
"""
from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap

_COLOR_CLAUDE = QColor("#d97757")
_COLOR_CODEX_DARK = QColor("#f2f2f2")
_COLOR_CODEX_LIGHT = QColor("#202123")
_COLOR_ERR  = QColor("#e0a020")


def _service_color(service: str, dark_mode: bool) -> QColor:
    if service == "claude":
        return QColor(_COLOR_CLAUDE)
    return QColor(_COLOR_CODEX_DARK if dark_mode else _COLOR_CODEX_LIGHT)


def _paint_battery(painter: QPainter, size: int, pct, err: bool,
                   fg: QColor, error_fill: QColor):
    """在 [0,0,size,size] 画布上，按这个 size 的原生比例画电池条+数字。
    所有尺寸都是 size 的比例，不是固定像素值，保证不同 size 调用画出来的
    是同一个设计在不同分辨率下的原生渲染，不是互相缩放的结果。
    """
    # 真实托盘最小只有 16px：主体尽量铺满槽位，把空间优先留给数字。
    margin = size * 0.025
    cap_w = max(1.0, size * 0.06)
    rect = QRectF(
        margin,
        size * 0.10,
        size - margin * 2 - cap_w,
        size * 0.80,
    )
    line_w = max(1.0, size * 0.055)

    pen = painter.pen()
    pen.setColor(fg)
    pen.setWidthF(line_w)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    radius = size * 0.09
    painter.drawRoundedRect(rect, radius, radius)

    # 电池正极帽。保持简单实心，避免极小尺寸下多一圈抗锯齿造成模糊。
    cap_h = size * 0.28
    cap = QRectF(rect.right() + line_w * 0.35,
                 rect.center().y() - cap_h / 2,
                 cap_w,
                 cap_h)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(fg)
    painter.drawRoundedRect(cap, cap_w * 0.35, cap_w * 0.35)

    fill_rect = None
    fill_color = None
    if err:
        painter.setBrush(error_fill)
        painter.setPen(Qt.PenStyle.NoPen)
        fill_rect = rect.adjusted(line_w, line_w, -line_w, -line_w)
        fill_color = QColor(error_fill)
        painter.drawRoundedRect(fill_rect, size * 0.05, size * 0.05)
    elif pct is not None:
        # 电池内部按剩余额度整块填充：100% 全满，消耗后右边缘向左退。
        inner = rect.adjusted(line_w * 1.15, line_w * 1.15,
                              -line_w * 1.15, -line_w * 1.15)
        fill_w = inner.width() * max(0, min(100, pct)) / 100
        fill_rect = QRectF(inner.left(), inner.top(), fill_w, inner.height())
        painter.setPen(Qt.PenStyle.NoPen)
        # 额度只改变填充宽度，不改变服务识别色。
        fill_color = QColor(fg)
        painter.setBrush(fill_color)
        painter.drawRoundedRect(fill_rect, size * 0.045, size * 0.045)

    painter.setPen(fg)
    label = "!" if err else ("?" if pct is None else str(pct))
    font = QFont("Segoe UI", weight=QFont.Weight.Bold)
    # 两位数优先保证一眼可读；100 单独缩小以容纳三位。
    font.setPixelSize(max(7, round(size * (0.52 if len(label) <= 2 else 0.38))))
    painter.setFont(font)
    text_rect = QRectF(rect.left(), rect.top() - size * 0.03,
                       rect.width(), rect.height())

    if fill_rect is not None and fill_rect.width() > 0:
        # 同一组数字分区绘制：落在色块上的部分用反差色，空白区用服务色。
        # 这样填充边缘穿过数字时也不会丢失笔画。
        inside_text = (QColor("#202020") if fill_color.lightnessF() >= 0.55
                       else QColor("#f5f5f5"))
        painter.save()
        painter.setClipRect(fill_rect)
        painter.setPen(inside_text)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()

        painter.save()
        painter.setClipRect(QRectF(fill_rect.right(), rect.top(),
                                   max(0.0, rect.right() - fill_rect.right()),
                                   rect.height()))
        painter.setPen(fg)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)
        painter.restore()
    else:
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, label)


def render_service_pixmap(pct: int | None, err: bool, dark_mode: bool,
                          service: str, size: int) -> QPixmap:
    """单个服务的托盘图标：电池框 + 整块剩余额度填充 + 居中数字。
    err=True 时画一个 "!" 占位；pct=None 时画 "?" 占位（数据还没到/该档暂无）。
    size 必须是调用方已经用原生 API（GetSystemMetrics(SM_CXSMICON)）算好的
    目标物理像素边长，这里只按这一个尺寸原生作画，不做任何缩放/插值。
    """
    fg = _service_color(service, dark_mode)
    # Claude 的告警使用黄色；Codex 无论正常还是异常都保持任务栏适配的
    # 黑/白身份色，避免两个服务的错误图标看起来完全一样。
    error_fill = QColor(_COLOR_ERR if service == "claude" else fg)
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    _paint_battery(painter, size, pct, err, fg, error_fill)
    painter.end()
    return px


def render_placeholder_pixmap(size: int) -> QPixmap:
    """启动后真实数据抓到之前的占位图标，避免托盘区空白。"""
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QColor("#9a9a9a"))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    margin = size * 0.09
    rect = QRectF(margin, size * 0.22, size - margin * 2, size - size * 0.44)
    painter.drawRoundedRect(rect, size * 0.09, size * 0.09)
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
