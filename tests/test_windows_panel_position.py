import pathlib
import sys
import unittest

from PySide6.QtCore import QPoint, QRect, QSize
from PySide6.QtWidgets import QApplication, QMenu

ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

from usage_panel import (
    _available_geometry_excluding_taskbar,
    _logical_taskbar_rect,
    _should_hide_deactivated_panel,
)
from trayicon_win32 import _fit_popup_to_area, _MenuBoundaryFilter, _point_in_native_rect


class TaskbarCoordinateTests(unittest.TestCase):
    def test_native_rect_contains_cursor_with_win32_edge_semantics(self):
        rect = (100, 200, 124, 224)
        self.assertTrue(_point_in_native_rect((100, 200), rect))
        self.assertTrue(_point_in_native_rect((123, 223), rect))
        self.assertFalse(_point_in_native_rect((124, 223), rect))
        self.assertFalse(_point_in_native_rect((123, 224), rect))

    def test_deactivated_panel_waits_for_tray_click_to_toggle_it(self):
        self.assertFalse(_should_hide_deactivated_panel(
            is_visible=True,
            is_active=False,
            menu_visible=False,
            cursor_over_tray=True,
        ))

    def test_deactivated_panel_still_hides_for_clicks_elsewhere(self):
        self.assertTrue(_should_hide_deactivated_panel(
            is_visible=True,
            is_active=False,
            menu_visible=False,
            cursor_over_tray=False,
        ))

    def test_maps_physical_taskbar_at_125_percent_dpi(self):
        result = _logical_taskbar_rect(
            (0, 1020, 1920, 1080),
            (0, 0, 1920, 1080),
            QRect(0, 0, 1536, 864),
            1.25,
        )
        self.assertEqual((result.left(), result.top(), result.width(), result.height()),
                         (0, 816, 1536, 48))

    def test_accepts_already_virtualized_taskbar_coordinates(self):
        result = _logical_taskbar_rect(
            (0, 816, 1536, 864),
            (0, 0, 1536, 864),
            QRect(0, 0, 1536, 864),
            1.25,
        )
        self.assertEqual((result.left(), result.top(), result.width(), result.height()),
                         (0, 816, 1536, 48))

    def test_popup_moves_above_bottom_taskbar_and_inside_right_edge(self):
        result = _fit_popup_to_area(
            QPoint(1480, 840),
            QSize(240, 420),
            QRect(0, 0, 1536, 816),
        )
        self.assertEqual(result, QPoint(1296, 396))

    def test_popup_keeps_anchor_when_it_already_fits(self):
        result = _fit_popup_to_area(
            QPoint(100, 100),
            QSize(240, 420),
            QRect(0, 0, 1536, 816),
        )
        self.assertEqual(result, QPoint(100, 100))

    def test_submenu_near_bottom_is_moved_above_taskbar(self):
        result = _fit_popup_to_area(
            QPoint(1280, 730),
            QSize(250, 210),
            QRect(0, 0, 1536, 816),
        )
        self.assertEqual(result, QPoint(1280, 606))

    def test_real_qt_submenu_show_event_keeps_window_above_taskbar(self):
        app = QApplication.instance() or QApplication([])
        screen = app.primaryScreen()
        area = _available_geometry_excluding_taskbar(screen)
        submenu = QMenu("About")
        for label in ("Version", "Author", "Check for Updates", "Star", "Features", "Data Source"):
            submenu.addAction(label)
        boundary_filter = _MenuBoundaryFilter(submenu)
        submenu.installEventFilter(boundary_filter)

        try:
            submenu.popup(QPoint(area.right() - 5, area.bottom() - 5))
            app.processEvents()
            app.processEvents()
            geometry = submenu.frameGeometry()
            self.assertLessEqual(geometry.right(), area.right())
            self.assertLessEqual(geometry.bottom(), area.bottom())
        finally:
            submenu.close()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
