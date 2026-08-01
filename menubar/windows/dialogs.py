"""Small product-styled modal dialogs used by the Windows tray app."""

import pathlib
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor, QIcon, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def app_icon() -> QIcon:
    """Load the real product icon in both source and PyInstaller runtimes."""
    runtime_root = pathlib.Path(getattr(sys, "_MEIPASS", pathlib.Path(__file__).resolve().parent))
    return QIcon(str(runtime_root / "icon" / "ai-limit.ico"))


class _AlertDialog(QDialog):
    def __init__(self, title: str, message: str, ok: str,
                 cancel: str | None = None, parent: QWidget | None = None,
                 *, status: str | None = None):
        super().__init__(parent)
        self.setWindowTitle("AI Limit")
        self.setWindowIcon(app_icon())
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setFixedWidth(440)

        dark = QApplication.palette().color(QPalette.ColorRole.Window).lightness() < 128
        palette = {
            "surface": "#202020" if dark else "#f9f9f9",
            "fg": "#f5f5f5" if dark else "#1b1b1b",
            "secondary": "#b5b5b5" if dark else "#5f5f5f",
            "control": "#323232" if dark else "#ffffff",
            "hover": "#3a3a3a" if dark else "#f0f0f0",
            "border": "#4a4a4a" if dark else "#d5d5d5",
            "accent": "#4f9cf9" if dark else "#0067c0",
            "accent_hover": "#68a9fa" if dark else "#005a9e",
            "success": "#65d391" if dark else "#168447",
            "success_bg": "#193827" if dark else "#eaf6ee",
        }

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 18)
        layout.setSpacing(0)

        summary = QHBoxLayout()
        summary.setSpacing(13)
        summary.setAlignment(Qt.AlignmentFlag.AlignTop)

        status_icon = QLabel("✓")
        status_icon.setObjectName("statusIcon")
        status_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        status_icon.setFixedSize(32, 32)
        status_icon.setVisible(status == "success")
        summary.addWidget(status_icon, 0, Qt.AlignmentFlag.AlignTop)

        text = QVBoxLayout()
        text.setSpacing(6)
        heading = QLabel(title)
        heading.setObjectName("alertTitle")
        text.addWidget(heading)

        body = QLabel(message)
        body.setObjectName("alertBody")
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text.addWidget(body)
        summary.addLayout(text, 1)
        layout.addLayout(summary)

        separator = QFrame()
        separator.setObjectName("alertSeparator")
        separator.setFrameShape(QFrame.Shape.HLine)
        layout.addSpacing(20)
        layout.addWidget(separator)
        layout.addSpacing(14)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        if cancel:
            cancel_btn = QPushButton(cancel)
            cancel_btn.setObjectName("secondaryButton")
            cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            cancel_btn.clicked.connect(self.reject)
            buttons.addWidget(cancel_btn)
        self.ok_button = QPushButton(ok)
        self.ok_button.setObjectName("primaryButton")
        self.ok_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)

        self.setStyleSheet(f"""
            QDialog {{ background: {palette['surface']}; }}
            QLabel {{
                color: {palette['fg']};
                background: transparent;
                font-family: 'Microsoft YaHei UI';
            }}
            QLabel#alertTitle {{ font-size: 15pt; font-weight: 600; }}
            QLabel#alertBody {{
                color: {palette['secondary']};
                font-size: 9pt;
                line-height: 1.45;
            }}
            QLabel#statusIcon {{
                color: {palette['success']};
                background: {palette['success_bg']};
                border-radius: 16px;
                font-family: 'Segoe UI Symbol';
                font-size: 13pt;
                font-weight: 600;
            }}
            QFrame#alertSeparator {{
                color: {palette['border']};
                background: {palette['border']};
                border: 0;
                max-height: 1px;
            }}
            QPushButton {{
                min-width: 80px;
                min-height: 32px;
                padding: 0 16px;
                border-radius: 6px;
                font-family: 'Microsoft YaHei UI';
                font-size: 9pt;
                font-weight: 500;
            }}
            QPushButton#secondaryButton {{
                color: {palette['fg']};
                background: {palette['control']};
                border: 1px solid {palette['border']};
            }}
            QPushButton#secondaryButton:hover {{ background: {palette['hover']}; }}
            QPushButton#primaryButton {{
                color: white;
                background: {palette['accent']};
                border: 1px solid {palette['accent']};
            }}
            QPushButton#primaryButton:hover {{
                background: {palette['accent_hover']};
                border-color: {palette['accent_hover']};
            }}
        """)

    def showEvent(self, event):
        """Center an ownerless tray dialog on the active display."""
        super().showEvent(event)
        screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        frame = self.frameGeometry()
        frame.moveCenter(screen.availableGeometry().center())
        self.move(frame.topLeft())


def show_alert(title: str, message: str, ok: str, cancel: str | None = None,
               parent: QWidget | None = None, *, status: str | None = None) -> bool:
    """Return whether the primary action was chosen."""
    return _AlertDialog(title, message, ok, cancel, parent, status=status).exec() == QDialog.DialogCode.Accepted
