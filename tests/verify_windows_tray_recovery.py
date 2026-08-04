"""Verify that a running Windows build restores both icons after Explorer rebuilds."""
import ctypes
import pathlib
import sys
import time
from ctypes import wintypes


ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

from trayicon_win32 import (
    GUID,
    NIF_GUID,
    NIM_DELETE,
    NOTIFYICONDATAW,
    NOTIFYICONIDENTIFIER,
    shell32,
    user32,
)


CLAUDE_GUID = "501b4443-6568-40a4-bc28-79b8a6c29d55"
CODEX_GUID = "6a3ddf3b-8438-472a-8074-c10bba1bc272"
HWND_BROADCAST = 0xFFFF
SMTO_ABORTIFHUNG = 0x0002

user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.POINTER(ctypes.c_size_t),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM


def _icon_rect(hwnd, uid, guid_text=None):
    identifier = NOTIFYICONIDENTIFIER()
    identifier.cbSize = ctypes.sizeof(identifier)
    identifier.hWnd = hwnd
    identifier.uID = uid
    if guid_text:
        identifier.guidItem = GUID.from_str(guid_text)
    rect = wintypes.RECT()
    hr = shell32.Shell_NotifyIconGetRect(ctypes.byref(identifier), ctypes.byref(rect))
    return hr, (rect.left, rect.top, rect.right, rect.bottom)


def _delete_icon(hwnd, uid, guid_text=None):
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(nid)
    nid.hWnd = hwnd
    nid.uID = uid
    if guid_text:
        nid.uFlags = NIF_GUID
        nid.guidItem = GUID.from_str(guid_text)
    if not shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid)):
        raise RuntimeError(f"failed to delete tray icon uid={uid}")


def main():
    hwnd = user32.FindWindowW("AiLimitTrayHostWndClass", "AiLimitTrayHost")
    if not hwnd:
        raise RuntimeError("AI Limit tray host window not found")
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    print(f"host_hwnd={int(hwnd):#x} process_id={process_id.value}")

    guid_icons = ((0, CLAUDE_GUID), (1, CODEX_GUID))
    guid_before = [_icon_rect(hwnd, *icon) for icon in guid_icons]
    use_guid = all(hr == 0 for hr, _rect in guid_before)
    icons = guid_icons if use_guid else ((0, None), (1, None))
    before = [_icon_rect(hwnd, *icon) for icon in icons]
    if any(hr != 0 for hr, _rect in before):
        raise RuntimeError(f"icons were not registered before recovery test: {before!r}")

    for icon in icons:
        _delete_icon(hwnd, *icon)

    taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
    result = ctypes.c_size_t()
    user32.SendMessageTimeoutW(
        HWND_BROADCAST,
        taskbar_created,
        0,
        0,
        SMTO_ABORTIFHUNG,
        2000,
        ctypes.byref(result),
    )
    time.sleep(1)

    after = [_icon_rect(hwnd, *icon) for icon in icons]
    if any(hr != 0 for hr, _rect in after):
        raise RuntimeError(f"icons did not recover after TaskbarCreated: {after!r}")

    print(f"before={before}")
    print(f"after={after}")


if __name__ == "__main__":
    main()
