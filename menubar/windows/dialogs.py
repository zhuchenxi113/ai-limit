"""弹窗封装。Qt 的 QMessageBox 是标准可靠 API，不需要 mac 版因
rumps.alert() 失效而手写 NSAlert 的那种 workaround。
"""
from PySide6.QtWidgets import QMessageBox, QWidget


def show_alert(title: str, message: str, ok: str, cancel: str | None = None,
               parent: QWidget | None = None) -> bool:
    """返回是否点了第一个按钮（ok）。签名对齐 mac 版 _show_alert。"""
    # The usage panel is always-on-top. Without a parent, this modal dialog can
    # open behind it and make the still-visible panel look frozen.
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(message)
    ok_btn = box.addButton(ok, QMessageBox.ButtonRole.AcceptRole)
    if cancel:
        box.addButton(cancel, QMessageBox.ButtonRole.RejectRole)
    box.exec()
    return box.clickedButton() is ok_btn
