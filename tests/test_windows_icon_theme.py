import importlib.util
import pathlib
import sys
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

from PySide6.QtCore import Qt

import icon_render


def _load_tray_module():
    path = WINDOWS_DIR / "ai-limit-tray.py"
    spec = importlib.util.spec_from_file_location("ai_limit_tray_theme_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _RegistryKey:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class TaskbarThemeTests(unittest.TestCase):
    def test_claude_transparent_area_text_uses_orange_in_both_themes(self):
        for dark_mode in (False, True):
            orange = icon_render._service_color("claude", dark_mode)
            text = icon_render._outside_text_color("claude", dark_mode, orange)
            self.assertEqual(text.name(), orange.name())

    def test_claude_filled_area_text_uses_light_contrast(self):
        orange = icon_render._service_color("claude", dark_mode=False)
        for dark_mode in (False, True):
            outside = icon_render._outside_text_color("claude", dark_mode, orange)
            inside = icon_render._filled_text_color("claude", orange, outside)
            self.assertEqual(inside.name(), "#f5f5f5")

    def test_claude_uses_bold_in_both_themes(self):
        for dark_mode in (False, True):
            self.assertEqual(
                icon_render._service_font_weight("claude", dark_mode),
                icon_render.QFont.Weight.Bold,
            )

    def test_codex_keeps_bold_in_both_themes(self):
        for dark_mode in (False, True):
            self.assertEqual(
                icon_render._service_font_weight("codex", dark_mode),
                icon_render.QFont.Weight.Bold,
            )

    def test_codex_transparent_area_text_keeps_adaptive_service_color(self):
        for dark_mode in (False, True):
            fg = icon_render._service_color("codex", dark_mode)
            text = icon_render._outside_text_color("codex", dark_mode, fg)
            self.assertEqual(text.name(), fg.name())

    def test_codex_filled_area_text_keeps_fill_contrast(self):
        for dark_mode, expected in ((True, "#202020"), (False, "#f5f5f5")):
            fg = icon_render._service_color("codex", dark_mode)
            outside = icon_render._outside_text_color("codex", dark_mode, fg)
            inside = icon_render._filled_text_color("codex", fg, outside)
            self.assertEqual(inside.name(), expected)

    @mock.patch("winreg.QueryValueEx", return_value=(1, 4))
    @mock.patch("winreg.OpenKey", return_value=_RegistryKey())
    def test_light_taskbar_wins_over_dark_qt_application_scheme(self, _open, _query):
        style_hints = mock.Mock()
        style_hints.colorScheme.return_value = Qt.ColorScheme.Dark
        with mock.patch.object(icon_render.QGuiApplication, "styleHints", return_value=style_hints):
            self.assertFalse(icon_render.is_dark_taskbar())

    @mock.patch("winreg.QueryValueEx", return_value=(0, 4))
    @mock.patch("winreg.OpenKey", return_value=_RegistryKey())
    def test_dark_taskbar_wins_over_light_qt_application_scheme(self, _open, _query):
        style_hints = mock.Mock()
        style_hints.colorScheme.return_value = Qt.ColorScheme.Light
        with mock.patch.object(icon_render.QGuiApplication, "styleHints", return_value=style_hints):
            self.assertTrue(icon_render.is_dark_taskbar())

    @mock.patch("winreg.OpenKey", side_effect=OSError("registry unavailable"))
    def test_qt_application_scheme_is_only_a_fallback(self, _open):
        style_hints = mock.Mock()
        style_hints.colorScheme.return_value = Qt.ColorScheme.Dark
        with mock.patch.object(icon_render.QGuiApplication, "styleHints", return_value=style_hints):
            self.assertTrue(icon_render.is_dark_taskbar())

    def test_theme_monitor_renders_only_after_taskbar_mode_changes(self):
        tray_module = _load_tray_module()
        tray = object.__new__(tray_module.AiLimitTray)
        tray._last_dark_taskbar = True
        tray._render = mock.Mock()

        with mock.patch.object(tray_module, "is_dark_taskbar", return_value=False):
            tray._sync_taskbar_theme()
            tray._sync_taskbar_theme()

        self.assertFalse(tray._last_dark_taskbar)
        tray._render.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
