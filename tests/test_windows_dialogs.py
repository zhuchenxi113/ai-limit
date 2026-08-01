import os
import pathlib
import sys
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from dialogs import _AlertDialog, app_icon


class AlertDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_dialog_has_product_heading_wrapped_body_and_primary_action(self):
        dialog = _AlertDialog(
            "发现新版本",
            "<p><b>0.3.23 → 0.3.24</b></p><p>安全校验说明</p>",
            "下载并安装",
            "取消",
        )
        heading = dialog.findChild(QLabel, "alertTitle")
        body = dialog.findChild(QLabel, "alertBody")
        primary = dialog.findChild(QPushButton, "primaryButton")
        secondary = dialog.findChild(QPushButton, "secondaryButton")

        self.assertEqual(heading.text(), "发现新版本")
        self.assertTrue(body.wordWrap())
        self.assertIn("安全校验说明", body.text())
        self.assertEqual(primary.text(), "下载并安装")
        self.assertTrue(primary.isDefault())
        self.assertEqual(secondary.text(), "取消")
        self.assertEqual(dialog.minimumWidth(), 440)
        self.assertEqual(dialog.maximumWidth(), 440)
        self.assertFalse(dialog.findChild(QLabel, "statusIcon").isVisible())
        self.assertFalse(app_icon().isNull())
        self.assertFalse(dialog.windowIcon().isNull())

    def test_success_dialog_shows_compact_status_icon(self):
        dialog = _AlertDialog(
            "已经是最新版本",
            "当前版本 0.3.23，无需更新。",
            "确定",
            status="success",
        )
        dialog.show()
        self.app.processEvents()
        try:
            icon = dialog.findChild(QLabel, "statusIcon")
            self.assertTrue(icon.isVisible())
            self.assertEqual(icon.text(), "✓")
            self.assertEqual(icon.size().width(), 32)
            self.assertEqual(dialog.findChild(QPushButton, "primaryButton").text(), "确定")
        finally:
            dialog.close()

    def test_ownerless_dialog_is_centered_on_the_active_screen(self):
        dialog = _AlertDialog("发现新版本", "0.3.23 → 0.3.24", "下载并安装", "取消")
        dialog.show()
        self.app.processEvents()
        try:
            screen = dialog.screen()
            expected = screen.availableGeometry().center()
            actual = dialog.frameGeometry().center()
            self.assertLessEqual(abs(actual.x() - expected.x()), 2)
            self.assertLessEqual(abs(actual.y() - expected.y()), 2)
        finally:
            dialog.close()


if __name__ == "__main__":
    unittest.main()
