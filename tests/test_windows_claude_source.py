import importlib.util
import os
import pathlib
import sys
import types
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

import fetchers
import icon_render
import usage

USAGE_DATA = {
    "five_hour": {"utilization": 12, "resets_at": "2026-07-20T12:00:00Z"},
    "seven_day": {"utilization": 34, "resets_at": "2026-07-27T12:00:00Z"},
}


def load_tray_module(name):
    spec = importlib.util.spec_from_file_location(name, WINDOWS_DIR / "ai-limit-tray.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ClaudeSourceTests(unittest.TestCase):
    def test_firefox_session_is_preferred_and_bypasses_oauth(self):
        with mock.patch.object(fetchers, "live_claude_usage",
                               return_value=USAGE_DATA) as get_usage, \
             mock.patch.object(fetchers, "live_claude_plan", return_value="Max") as get_plan:
            result = fetchers.fetch_claude("zh")
        get_usage.assert_called_once_with(browser="firefox")
        get_plan.assert_called_once_with(browser="firefox")
        self.assertEqual((result["source"], result["5h_left"], result["7d_left"]),
                         ("firefox", 88, 66))

    def test_missing_firefox_session_returns_login_warning_without_oauth(self):
        no_session = fetchers.ClaudeWebError("not logged in", kind="browser_session")
        with mock.patch.object(fetchers, "live_claude_usage", side_effect=no_session):
            result = fetchers.fetch_claude("en")
        self.assertEqual(result["error"], "Sign in to claude.ai with Firefox")

    def test_cloudflare_error_does_not_silently_hit_oauth(self):
        blocked = fetchers.ClaudeWebError("blocked", kind="cloudflare")
        with mock.patch.object(fetchers, "live_claude_usage", side_effect=blocked):
            result = fetchers.fetch_claude("en")
        self.assertIn("error", result)

    def test_cookie_context_only_probes_firefox(self):
        cookie = lambda name, value: types.SimpleNamespace(name=name, value=value)
        fake = types.SimpleNamespace(
            chrome=mock.Mock(side_effect=AssertionError("Chrome called")),
            edge=mock.Mock(side_effect=AssertionError("Edge called")),
            firefox=mock.Mock(return_value=[
                cookie("sessionKey", "secret"), cookie("lastActiveOrg", "org-123")]),
        )
        with mock.patch.dict(sys.modules, {"browser_cookie3": fake}):
            org_id, _headers = usage._claude_web_context("https://claude.ai", browser="firefox")
        self.assertEqual(org_id, "org-123")
        fake.firefox.assert_called_once_with(domain_name=".claude.ai")
        fake.chrome.assert_not_called()
        fake.edge.assert_not_called()

    def test_settings_menu_has_no_data_source_control(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        module = load_tray_module("ai_limit_tray_test")
        app = module.QApplication.instance() or module.QApplication([])
        tray = module.AiLimitTray.__new__(module.AiLimitTray)
        tray._app = app
        tray._state = module.state.load_state()
        tray._menu = module.QMenu()
        with mock.patch.object(module, "is_autostart_enabled", return_value=False):
            tray._build_menu()
        labels = [action.text() for action in tray._menu.actions()]
        self.assertNotIn("Claude 数据源", labels)
        self.assertNotIn("Claude Data Source", labels)

    def test_about_submenu_matches_mac_content_and_reuses_updater(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        module = load_tray_module("ai_limit_tray_about_test")
        app = module.QApplication.instance() or module.QApplication([])
        tray = module.AiLimitTray.__new__(module.AiLimitTray)
        tray._app = app
        tray._state = module.state.load_state()
        tray._state["lang"] = "zh"
        tray._menu = module.QMenu()
        tray._check_for_updates = mock.Mock()
        with mock.patch.object(module, "is_autostart_enabled", return_value=False):
            tray._build_menu()

        top_labels = [action.text() for action in tray._menu.actions()]
        about_label = f"关于（AI Limit {module.__version__}）"
        self.assertIn(about_label, top_labels)
        self.assertNotIn("检查更新", top_labels)
        self.assertNotIn("项目主页", top_labels)

        about_action = next(action for action in tray._menu.actions()
                            if action.text() == about_label)
        about_actions = about_action.menu().actions()
        labels = [action.text() for action in about_actions]
        self.assertEqual(labels, [
            f"AI Limit {module.__version__}",
            "作者：zhuchenxi",
            "检查更新",
            "⭐ 给个 Star，鼓励作者",
            "Claude Code / Codex 额度监控",
            "数据来源：本地日志 + 官方网页接口",
        ])
        self.assertFalse(about_actions[-2].isEnabled())
        self.assertFalse(about_actions[-1].isEnabled())
        tray._check_update_action.trigger()
        tray._check_for_updates.assert_called_once()

    def test_language_menu_stays_bilingual_in_english_ui(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        module = load_tray_module("ai_limit_tray_language_menu_test")
        app = module.QApplication.instance() or module.QApplication([])
        tray = module.AiLimitTray.__new__(module.AiLimitTray)
        tray._app = app
        tray._state = module.state.load_state()
        tray._state["lang"] = "en"
        tray._menu = module.QMenu()
        tray._check_for_updates = mock.Mock()
        with mock.patch.object(module, "is_autostart_enabled", return_value=False):
            tray._build_menu()

        language_action = next(
            action for action in tray._menu.actions()
            if action.text() == "语言 Language"
        )
        self.assertEqual(
            [action.text() for action in language_action.menu().actions()],
            ["跟随系统 Follow System", "中文", "English"],
        )

    def test_refresh_interval_change_rerenders_panel_immediately(self):
        module = load_tray_module("ai_limit_tray_refresh_interval_test")
        tray = module.AiLimitTray.__new__(module.AiLimitTray)
        tray._state = {"refresh_min": 1}
        tray._auto_timer = mock.Mock()
        tray._render = mock.Mock()

        with mock.patch.object(module.state, "save_state") as save_state:
            tray._set_refresh_min(5)

        self.assertEqual(tray._state["refresh_min"], 5)
        save_state.assert_called_once_with(tray._state)
        tray._auto_timer.setInterval.assert_called_once_with(5 * 60 * 1000)
        tray._render.assert_called_once_with()


class CodexSourceAndErrorIconTests(unittest.TestCase):
    @staticmethod
    def _pixmap_colors(pixmap):
        image = pixmap.toImage()
        return {
            image.pixelColor(x, y).name().lower()
            for y in range(image.height())
            for x in range(image.width())
            if image.pixelColor(x, y).alpha() > 0
        }

    def test_codex_uses_firefox_web_source(self):
        rate_limits = {
            "primary": {"used_percent": 30, "window_minutes": 10080, "resets_at": 123},
            "secondary": None,
            "plan_type": "plus",
        }
        with mock.patch.object(fetchers, "live_codex_web_usage",
                               return_value=(None, rate_limits)) as get_usage:
            result = fetchers.fetch_codex("en")
        get_usage.assert_called_once_with(browser="firefox")
        self.assertEqual((result["source"], result["7d_left"]), ("firefox", 70))

    def test_missing_5h_does_not_fall_back_to_7d_in_tray(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        module = load_tray_module("ai_limit_tray_missing_5h_test")
        codex = {
            "5h_left": None,
            "7d_left": 70,
            "5h_reset": None,
            "7d_reset": 123,
            "plan": "plus",
        }
        pct, tip = module._codex_tray_display(codex, "5h", "zh")
        self.assertIsNone(pct)
        self.assertEqual(tip, "Codex 方案：Plus  5h: 当前未提供")
        self.assertNotIn("70", tip)
        self.assertNotIn("重置", tip)

    def test_codex_network_failure_is_not_a_warning_icon(self):
        failure = usage.CodexWebError("offline", kind="network")
        with mock.patch.object(fetchers, "live_codex_web_usage", side_effect=failure):
            result = fetchers.fetch_codex("en")
        self.assertIn("error", result)
        self.assertFalse(result["icon_warning"])

    def test_missing_codex_firefox_session_requests_login(self):
        failure = usage.CodexWebError("no cookies", kind="browser_session")
        with mock.patch.object(fetchers, "live_codex_web_usage", side_effect=failure):
            result = fetchers.fetch_codex("en")
        self.assertEqual(result["error"], "Sign in to chatgpt.com with Firefox")

    def test_codex_firefox_session_without_access_token_requests_login(self):
        failure = usage.CodexWebError("please log in", kind="auth")
        with mock.patch.object(fetchers, "live_codex_web_usage", side_effect=failure):
            result = fetchers.fetch_codex("zh")
        self.assertEqual(result["error"], "请在 Firefox 登录 chatgpt.com")

    def test_codex_cookie_loader_only_probes_firefox(self):
        cookie = lambda name, value: types.SimpleNamespace(name=name, value=value)
        fake = types.SimpleNamespace(
            chrome=mock.Mock(side_effect=AssertionError("Chrome called")),
            edge=mock.Mock(side_effect=AssertionError("Edge called")),
            firefox=mock.Mock(return_value=[cookie("session", "secret")]),
        )
        with mock.patch.dict(sys.modules, {"browser_cookie3": fake}):
            cookies = usage._load_chatgpt_cookies(browser="firefox")
        self.assertEqual(cookies, [("session", "secret")])
        fake.firefox.assert_called_once_with(domain_name=".chatgpt.com")
        fake.chrome.assert_not_called()
        fake.edge.assert_not_called()

    def test_network_failure_renders_neutral_but_login_failure_warns(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        module = load_tray_module("ai_limit_tray_error_icon_test")
        self.assertFalse(module._error_icon_is_warning(
            {"error": "Network unavailable", "icon_warning": False}
        ))
        self.assertTrue(module._error_icon_is_warning(
            {"error": "Sign in to claude.ai with Firefox"}
        ))

    def test_codex_warning_icon_keeps_codex_identity_color(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        app = icon_render.QApplication.instance() if hasattr(icon_render, "QApplication") else None
        if app is None:
            from PySide6.QtWidgets import QApplication
            app = QApplication.instance() or QApplication([])
        codex = icon_render.render_service_pixmap(None, True, False, "codex", 32)
        claude = icon_render.render_service_pixmap(None, True, False, "claude", 32)
        codex_colors = self._pixmap_colors(codex)
        claude_colors = self._pixmap_colors(claude)
        self.assertNotIn("#e0a020", codex_colors)
        self.assertIn("#202123", codex_colors)
        self.assertIn("#e0a020", claude_colors)


if __name__ == "__main__":
    unittest.main()
