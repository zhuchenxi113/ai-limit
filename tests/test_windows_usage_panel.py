import os
import pathlib
import sys
import unittest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont

from usage_panel import UsagePanel


class _TrayStub:
    def __init__(self):
        self._state = {"panel_services": ["claude", "codex"]}


class UsagePanelLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_english_updates_static_links_and_tooltips(self):
        panel = UsagePanel(_TrayStub(), dark=False)
        panel.set_data(None, None, None, None, "en", 1)

        self.assertEqual(panel.claude_link.text(), "Claude usage")
        self.assertEqual(panel.codex_link.text(), "Codex analytics")
        self.assertEqual(panel.refresh_button.toolTip(), "Refresh now")
        self.assertEqual(panel.more_button.toolTip(), "More settings")

    def test_product_title_uses_semibold_weight(self):
        panel = UsagePanel(_TrayStub(), dark=False)
        panel.ensurePolished()
        self.assertGreaterEqual(panel.title.font().weight(), QFont.Weight.DemiBold)

    def test_language_switch_updates_existing_panel_controls(self):
        panel = UsagePanel(_TrayStub(), dark=False)
        panel.set_data(None, None, None, None, "zh", 1)
        panel.set_data(None, None, None, None, "en", 1)

        self.assertNotIn("用量页", panel.claude_link.text())
        self.assertNotIn("分析页", panel.codex_link.text())
        self.assertNotIn("刷新", panel.refresh_button.toolTip())
        self.assertNotIn("设置", panel.more_button.toolTip())


if __name__ == "__main__":
    unittest.main()
