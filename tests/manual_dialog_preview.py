"""Open the product alert dialog for manual visual inspection."""

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

from PySide6.QtWidgets import QApplication

from dialogs import _AlertDialog


app = QApplication([])
dialog = _AlertDialog(
    "发现新版本",
    "<p style='font-size: 12pt; font-weight: 600; margin: 0 0 10px 0;'>"
    "0.3.23&nbsp;&nbsp;→&nbsp;&nbsp;0.3.24</p>"
    "<p>下载并验证完成后，AI Limit 会退出并打开标准安装向导。</p>"
    "<p><b>安装前安全校验</b><br>安装包必须通过 AI Limit 的 Ed25519 发布签名验证。"
    "Windows 可能另行显示“未知发布者”或 SmartScreen 提示，也可能不显示。</p>",
    "下载并安装",
    "取消",
)
raise SystemExit(dialog.exec())
