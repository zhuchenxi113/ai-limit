import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "menubar" / "windows" / "trayicon_win32.py").read_text(encoding="utf-8")


class TrayHostSourceTests(unittest.TestCase):
    def test_host_parent_stays_top_level_for_taskbar_created_broadcast(self):
        self.assertIn("_TRAY_HOST_PARENT = None", SOURCE)
        self.assertIn("_TRAY_HOST_PARENT, None, self._hinstance, None", SOURCE)

    def test_failed_guid_add_keeps_guid_and_falls_back_to_uid_identity(self):
        self.assertIn("if not ok and self._use_guid:", SOURCE)
        self.assertIn("nid.uFlags &= ~NIF_GUID", SOURCE)
        self.assertIn("ok = self._notify(NIM_ADD, nid)", SOURCE)


if __name__ == "__main__":
    unittest.main()
