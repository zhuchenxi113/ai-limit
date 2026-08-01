"""基于原生 Win32 Shell_NotifyIcon 的托盘图标，替代 PySide6 QSystemTrayIcon。

背景（2026-07-20 排查）：用户反馈 Claude/Codex 两个托盘图标只能单向拖拽重新排序
（左边能往右拖，右边拖不动）。根因是 Windows 靠 `HKCU\\Control Panel\\NotifyIconSettings`
持久化每个图标的排序/固定位置，而这条记录默认以 (可执行文件路径 + uID) 作身份区分——
PySide6 的 QSystemTrayIcon 在 Windows 平台实现里不支持给每个图标设置独立的
`NOTIFYICONDATA.guidItem`（GUID），导致同一进程发出的 Claude/Codex 两个图标在这套持久化
身份体系里被当成同一个身份，Explorer 只能记住其中一个的位置，另一个自然拖不动/存不住。
Shell_NotifyIcon 官方文档给的标准解法就是给每个图标分配一个独立、稳定的 GUID
（`NIF_GUID` 标志位），但 pywin32 的 `win32gui.Shell_NotifyIcon` 包装的 NOTIFYICONDATA
元组不含 guidItem 字段，只能绕开它直接用 ctypes 调 `Shell_NotifyIconW`。

这里只实现 ai-limit-tray.py 实际用到的 QSystemTrayIcon 子集接口
（setIcon/setToolTip/setContextMenu/setVisible/show/activated），不是通用替代品。
"""
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, QTimer, Signal
from PySide6.QtGui import QCursor, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from usage_panel import _available_geometry_excluding_taskbar

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
shell32 = ctypes.windll.shell32
kernel32 = ctypes.windll.kernel32
ole32 = ctypes.windll.ole32

LRESULT = getattr(wintypes, "LRESULT", ctypes.c_ssize_t)
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_MBUTTONUP = 0x0208

WM_APP = 0x8000
_WM_TRAYICON = WM_APP + 1

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_GUID = 0x00000020
NIF_SHOWTIP = 0x00000080

DIB_RGB_COLORS = 0
BI_BITFIELDS = 3
LCS_sRGB = 0x73524742
LCS_GM_IMAGES = 4

SM_CXSMICON = 49

HWND_MESSAGE = -3


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_str(cls, text: str) -> "GUID":
        raw = "{" + text.strip("{}") + "}"
        guid = cls()
        hr = ole32.CLSIDFromString(ctypes.c_wchar_p(raw), ctypes.byref(guid))
        if hr != 0:
            raise ValueError(f"invalid GUID literal: {text!r} (hr={hr:#x})")
        return guid


ole32.CLSIDFromString.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(GUID)]
ole32.CLSIDFromString.restype = ctypes.c_long


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class NOTIFYICONIDENTIFIER(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("guidItem", GUID),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HBITMAP),
        ("hbmColor", wintypes.HBITMAP),
    ]


class BITMAPV5HEADER(ctypes.Structure):
    _fields_ = [
        ("bV5Size", wintypes.DWORD),
        ("bV5Width", ctypes.c_long),
        ("bV5Height", ctypes.c_long),
        ("bV5Planes", wintypes.WORD),
        ("bV5BitCount", wintypes.WORD),
        ("bV5Compression", wintypes.DWORD),
        ("bV5SizeImage", wintypes.DWORD),
        ("bV5XPelsPerMeter", ctypes.c_long),
        ("bV5YPelsPerMeter", ctypes.c_long),
        ("bV5ClrUsed", wintypes.DWORD),
        ("bV5ClrImportant", wintypes.DWORD),
        ("bV5RedMask", wintypes.DWORD),
        ("bV5GreenMask", wintypes.DWORD),
        ("bV5BlueMask", wintypes.DWORD),
        ("bV5AlphaMask", wintypes.DWORD),
        ("bV5CSType", wintypes.DWORD),
        ("bV5Endpoints", ctypes.c_byte * 36),
        ("bV5GammaRed", wintypes.DWORD),
        ("bV5GammaGreen", wintypes.DWORD),
        ("bV5GammaBlue", wintypes.DWORD),
        ("bV5Intent", wintypes.DWORD),
        ("bV5ProfileData", wintypes.DWORD),
        ("bV5ProfileSize", wintypes.DWORD),
        ("bV5Reserved", wintypes.DWORD),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.RegisterClassW.restype = wintypes.ATOM
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.CreateWindowExW.restype = wintypes.HWND
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
user32.RegisterWindowMessageW.restype = wintypes.UINT
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int
user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
user32.CreateIconIndirect.restype = wintypes.HICON
user32.DestroyIcon.argtypes = [wintypes.HICON]
user32.DestroyIcon.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetCursorPos.restype = wintypes.BOOL
gdi32.CreateDIBSection.argtypes = [
    wintypes.HDC, ctypes.POINTER(BITMAPV5HEADER), wintypes.UINT,
    ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.CreateBitmap.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.UINT, wintypes.UINT, wintypes.LPCVOID]
gdi32.CreateBitmap.restype = wintypes.HBITMAP
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
shell32.Shell_NotifyIconW.restype = wintypes.BOOL
shell32.Shell_NotifyIconGetRect.argtypes = [
    ctypes.POINTER(NOTIFYICONIDENTIFIER), ctypes.POINTER(wintypes.RECT),
]
shell32.Shell_NotifyIconGetRect.restype = ctypes.c_long
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE


def icon_pixel_size() -> int:
    """当前系统托盘小图标应该渲染的物理像素边长（随 DPI 变化）。"""
    size = user32.GetSystemMetrics(SM_CXSMICON)
    return size if size > 0 else 16


def _point_in_native_rect(point: tuple[int, int], rect: tuple[int, int, int, int]) -> bool:
    """Match Win32 PtInRect semantics (right and bottom edges are exclusive)."""
    x, y = point
    left, top, right, bottom = rect
    return left <= x < right and top <= y < bottom


def _hicon_from_pixmap(pixmap: QPixmap):
    image = pixmap.toImage().convertToFormat(pixmap.toImage().Format.Format_ARGB32_Premultiplied)
    w, h = image.width(), image.height()
    if w <= 0 or h <= 0:
        return None

    header = BITMAPV5HEADER()
    header.bV5Size = ctypes.sizeof(BITMAPV5HEADER)
    header.bV5Width = w
    header.bV5Height = -h  # 负值 = top-down，跟 QImage 扫描行顺序一致
    header.bV5Planes = 1
    header.bV5BitCount = 32
    header.bV5Compression = BI_BITFIELDS
    header.bV5RedMask = 0x00FF0000
    header.bV5GreenMask = 0x0000FF00
    header.bV5BlueMask = 0x000000FF
    header.bV5AlphaMask = 0xFF000000
    header.bV5CSType = LCS_sRGB
    header.bV5Intent = LCS_GM_IMAGES

    screen_dc = user32.GetDC(None)
    bits_ptr = ctypes.c_void_p()
    hbm_color = gdi32.CreateDIBSection(
        screen_dc, ctypes.byref(header), DIB_RGB_COLORS,
        ctypes.byref(bits_ptr), None, 0,
    )
    user32.ReleaseDC(None, screen_dc)
    if not hbm_color or not bits_ptr:
        return None

    # QImage Format_ARGB32_Premultiplied 的内存字节序（小端 B,G,R,A）跟这里
    # 32bpp BGRA DIB 的掩码顺序完全一致，可以整块 memmove，不用逐像素转换。
    raw = image.constBits()  # PySide6: 已经是长度正确的 memoryview，不需要 setsize
    stride = w * 4
    if image.bytesPerLine() == stride:
        ctypes.memmove(bits_ptr, bytes(raw), stride * h)
    else:
        src = bytes(raw)
        line_stride = image.bytesPerLine()
        for y in range(h):
            ctypes.memmove(
                bits_ptr.value + y * stride,
                src[y * line_stride: y * line_stride + stride],
                stride,
            )

    mask_row_bytes = ((w + 15) // 16) * 2
    mask_buf = ctypes.create_string_buffer(mask_row_bytes * h)  # 全零 = 不遮罩，交给 alpha 通道
    hbm_mask = gdi32.CreateBitmap(w, h, 1, 1, mask_buf)
    if not hbm_mask:
        gdi32.DeleteObject(hbm_color)
        return None

    icon_info = ICONINFO()
    icon_info.fIcon = True
    icon_info.xHotspot = 0
    icon_info.yHotspot = 0
    icon_info.hbmMask = hbm_mask
    icon_info.hbmColor = hbm_color
    hicon = user32.CreateIconIndirect(ctypes.byref(icon_info))
    # CreateIconIndirect 内部会复制这两张位图，原始句柄用完即可释放
    # （2026-07-20 查证：Shell_NotifyIcon/CreateIconIndirect 均在调用返回后
    # 即可安全 DeleteObject/DestroyIcon 原始资源，不会影响已提交的图标）。
    gdi32.DeleteObject(hbm_color)
    gdi32.DeleteObject(hbm_mask)
    return hicon or None


_REASON_MAP = {
    WM_LBUTTONUP: QSystemTrayIcon.ActivationReason.Trigger,
    WM_LBUTTONDBLCLK: QSystemTrayIcon.ActivationReason.DoubleClick,
    WM_MBUTTONUP: QSystemTrayIcon.ActivationReason.MiddleClick,
}


def _fit_popup_to_area(anchor: QPoint, popup_size: QSize, area: QRect) -> QPoint:
    """Keep a popup fully inside the usable screen area."""
    max_x = max(area.left(), area.right() - popup_size.width() + 1)
    max_y = max(area.top(), area.bottom() - popup_size.height() + 1)
    return QPoint(
        max(area.left(), min(anchor.x(), max_x)),
        max(area.top(), min(anchor.y(), max_y)),
    )


class _MenuBoundaryFilter(QObject):
    """Move every level of a tray menu inside the usable screen area."""

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and isinstance(watched, QMenu):
            QTimer.singleShot(0, lambda menu=watched: self._reposition(menu))
        return super().eventFilter(watched, event)

    @staticmethod
    def _reposition(menu: QMenu) -> None:
        if not menu.isVisible():
            return
        screen = QApplication.screenAt(menu.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        position = _fit_popup_to_area(
            menu.pos(),
            menu.size(),
            _available_geometry_excluding_taskbar(screen),
        )
        if position != menu.pos():
            menu.move(position)


class _TrayHost:
    """进程内单例：一个消息专用隐藏窗口，承载所有 Win32TrayIcon 的回调消息。"""

    _instance = None

    def __init__(self):
        self._icons: dict[int, "Win32TrayIcon"] = {}
        # CreateWindowExW 会在返回前就同步派发 WM_CREATE 等消息给 _wndproc_impl
        # （SendMessage 语义，不是排队的 PostMessage），所以 _taskbar_created_msg
        # 必须在创建窗口之前就赋好值，否则回调里第一次访问这个属性会 AttributeError。
        self._taskbar_created_msg = user32.RegisterWindowMessageW("TaskbarCreated")
        self._wndproc = WNDPROC(self._wndproc_impl)
        class_name = "AiLimitTrayHostWndClass"
        self._hinstance = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = self._hinstance
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = class_name
        user32.RegisterClassW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(
            0, class_name, "AiLimitTrayHost", 0,
            0, 0, 0, 0,
            wintypes.HWND(HWND_MESSAGE), None, self._hinstance, None,
        )

    @classmethod
    def instance(cls) -> "_TrayHost":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def register(self, uid: int, icon: "Win32TrayIcon") -> None:
        self._icons[uid] = icon

    def _wndproc_impl(self, hwnd, msg, wparam, lparam):
        if self._taskbar_created_msg and msg == self._taskbar_created_msg:
            for icon in self._icons.values():
                icon._on_taskbar_created()
            return 0
        if msg == _WM_TRAYICON:
            icon = self._icons.get(wparam)
            if icon is not None:
                icon._on_message(lparam)
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


class Win32TrayIcon(QObject):
    """ai-limit-tray.py 需要的 QSystemTrayIcon 子集，内部走带 GUID 的 Shell_NotifyIconW。"""

    activated = Signal(object)  # emits QSystemTrayIcon.ActivationReason

    _next_uid = 0

    def __init__(self, guid: str, parent=None):
        super().__init__(parent)
        self._uid = Win32TrayIcon._next_uid
        Win32TrayIcon._next_uid += 1
        self._guid = GUID.from_str(guid)
        self._host = _TrayHost.instance()
        self._host.register(self._uid, self)
        self._pixmap: QPixmap | None = None
        self._tooltip = ""
        self._menu = None
        self._menu_boundary_filter = _MenuBoundaryFilter(self)
        self._visible = False
        self._added = False
        self._hicon = None

    def setIcon(self, pixmap: QPixmap) -> None:
        self._pixmap = pixmap
        if self._visible:
            self._push(update_icon=True)

    def setToolTip(self, text: str) -> None:
        self._tooltip = (text or "")[:127]
        if self._visible:
            self._push(update_icon=False)

    def setContextMenu(self, menu) -> None:
        self._menu = menu

    def _install_menu_boundary_filters(self) -> None:
        if self._menu is None:
            return
        self._menu.installEventFilter(self._menu_boundary_filter)
        for submenu in self._menu.findChildren(QMenu):
            submenu.installEventFilter(self._menu_boundary_filter)

    def setVisible(self, visible: bool) -> None:
        if visible == self._visible:
            return
        self._visible = visible
        if visible:
            self._push(update_icon=True, add=not self._added)
        else:
            self._remove()

    def show(self) -> None:
        self.setVisible(True)

    def hide(self) -> None:
        self.setVisible(False)

    def contains_cursor(self) -> bool:
        """Return whether the physical cursor is currently over this tray icon."""
        if not self._added:
            return False
        identifier = NOTIFYICONIDENTIFIER()
        identifier.cbSize = ctypes.sizeof(identifier)
        identifier.hWnd = self._host.hwnd
        identifier.uID = self._uid
        identifier.guidItem = self._guid
        rect = wintypes.RECT()
        if shell32.Shell_NotifyIconGetRect(ctypes.byref(identifier), ctypes.byref(rect)) != 0:
            return False
        point = wintypes.POINT()
        if not user32.GetCursorPos(ctypes.byref(point)):
            return False
        return _point_in_native_rect(
            (point.x, point.y), (rect.left, rect.top, rect.right, rect.bottom)
        )

    def _push(self, update_icon: bool, add: bool = False) -> None:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._host.hwnd
        nid.uID = self._uid
        nid.uFlags = NIF_MESSAGE | NIF_TIP | NIF_GUID | NIF_SHOWTIP
        nid.uCallbackMessage = _WM_TRAYICON
        nid.guidItem = self._guid
        nid.szTip = self._tooltip

        new_hicon = None
        if update_icon and self._pixmap is not None:
            new_hicon = _hicon_from_pixmap(self._pixmap)
            if new_hicon:
                nid.hIcon = new_hicon
                nid.uFlags |= NIF_ICON

        message = NIM_ADD if add else NIM_MODIFY
        ok = shell32.Shell_NotifyIconW(message, ctypes.byref(nid))
        if not ok and message == NIM_MODIFY:
            # explorer.exe 可能刚重启、还没收到我们已经错过的 TaskbarCreated，
            # MODIFY 一个不存在的图标会失败，退回尝试 ADD 兜底。
            ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        if ok:
            self._added = True
        if new_hicon:
            if self._hicon:
                user32.DestroyIcon(self._hicon)
            self._hicon = new_hicon

    def _remove(self) -> None:
        if self._added:
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = self._host.hwnd
            nid.uID = self._uid
            nid.uFlags = NIF_GUID
            nid.guidItem = self._guid
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self._added = False
        if self._hicon:
            user32.DestroyIcon(self._hicon)
            self._hicon = None

    def _on_taskbar_created(self) -> None:
        self._added = False
        if self._visible:
            self._push(update_icon=True, add=True)

    def _on_message(self, lparam: int) -> None:
        if lparam == WM_RBUTTONUP:
            if self._menu is not None:
                # The menu can be rebuilt after a language change, so discover
                # current submenus on every opening instead of caching them.
                self._install_menu_boundary_filters()
                anchor = QCursor.pos()
                screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
                position = anchor
                if screen is not None:
                    self._menu.ensurePolished()
                    position = _fit_popup_to_area(
                        anchor,
                        self._menu.sizeHint(),
                        _available_geometry_excluding_taskbar(screen),
                    )
                self._menu.popup(position)
            return
        reason = _REASON_MAP.get(lparam)
        if reason is not None:
            self.activated.emit(reason)
