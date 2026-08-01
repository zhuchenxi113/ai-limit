"""状态渠道三层身份（内部 key / 官方 ID / 官方名称）与 Windows 偏好迁移回归测试。

覆盖：key/ID 唯一、旧名称→内部 key 迁移（含 "App" 别名与当前官方名称）、
Cowork/Work 默认不勾、官方改名但 ID 不变仍能匹配、多选取最差、ID 缺失返回未知、
Windows state 迁移后写回内部 key、空选择保留为空、未知字段/
oauth_retry_until 不丢失。
"""
import json
import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

import usage
import state
import fetchers


def _component(cid, name, status):
    return {"id": cid, "name": name, "status": status}


class ChannelIdentityTests(unittest.TestCase):
    def test_keys_and_ids_are_globally_unique(self):
        keys, ids = [], []
        for service in ("claude", "codex"):
            for key, cid, _name in usage.status_channels(service):
                keys.append(key)
                ids.append(cid)
        self.assertEqual(len(keys), len(set(keys)), "内部 key 出现重复")
        self.assertEqual(len(ids), len(set(ids)), "官方组件 ID 出现重复")

    def test_expected_channels_present(self):
        claude_keys = [k for k, _c, _n in usage.status_channels("claude")]
        codex_keys = [k for k, _c, _n in usage.status_channels("codex")]
        self.assertEqual(claude_keys,
                         ["claude_code", "claude_web", "claude_cowork",
                          "claude_api", "claude_console"])
        self.assertEqual(codex_keys,
                         ["codex_app", "chatgpt_work", "codex_cli",
                          "codex_api", "codex_vscode", "codex_web"])

    def test_cowork_and_work_exist_but_not_default(self):
        self.assertIn("claude_cowork", [k for k, _c, _n in usage.status_channels("claude")])
        self.assertIn("chatgpt_work", [k for k, _c, _n in usage.status_channels("codex")])
        self.assertNotIn("claude_cowork", usage.status_default_selection("claude"))
        self.assertNotIn("chatgpt_work", usage.status_default_selection("codex"))
        self.assertEqual(usage.status_default_selection("claude"), ["claude_code"])
        self.assertEqual(usage.status_default_selection("codex"),
                         ["codex_app", "codex_cli", "codex_api"])


class NormalizationTests(unittest.TestCase):
    def test_legacy_app_alias_migrates_to_codex_app(self):
        self.assertEqual(usage.normalize_status_key("codex", "App"), "codex_app")
        self.assertEqual(usage.normalize_status_selection("codex", ["App"]), ["codex_app"])

    def test_current_official_names_migrate_to_keys(self):
        self.assertEqual(
            usage.normalize_status_selection(
                "codex", ["Codex in ChatGPT Desktop", "CLI", "Codex API"]),
            ["codex_app", "codex_cli", "codex_api"])
        self.assertEqual(
            usage.normalize_status_selection("claude", ["Claude Code"]),
            ["claude_code"])

    def test_internal_keys_pass_through_and_reorder_to_channel_order(self):
        self.assertEqual(
            usage.normalize_status_selection("codex", ["codex_api", "codex_app"]),
            ["codex_app", "codex_api"])

    def test_unknown_values_dropped_and_deduped(self):
        self.assertEqual(
            usage.normalize_status_selection("codex", ["App", "codex_app", "garbage", None]),
            ["codex_app"])
        self.assertIsNone(usage.normalize_status_key("codex", "garbage"))
        self.assertIsNone(usage.normalize_status_key("codex", None))

    def test_empty_selection_stays_empty(self):
        self.assertEqual(usage.normalize_status_selection("claude", []), [])
        self.assertEqual(usage.normalize_status_selection("codex", None), [])


class WorstStatusByIdTests(unittest.TestCase):
    def test_rename_but_id_unchanged_still_matches(self):
        # 官方把 codex_app 的 name 从 "Codex in ChatGPT Desktop" 改成新名称，
        # 但 id 不变 —— 按 ID 匹配必须继续命中，展示名跟随新官方名。
        codex_app_id = next(c for k, c, _n in usage.status_channels("codex") if k == "codex_app")
        components = [_component(codex_app_id, "Codex Desktop (renamed)", "degraded_performance")]
        result = usage.worst_status_by_id(components, ["codex_app"], "codex")
        self.assertIsNotNone(result)
        status, key, name = result
        self.assertEqual((status, key, name),
                         ("degraded_performance", "codex_app", "Codex Desktop (renamed)"))

    def test_multi_select_takes_worst(self):
        ids = {k: c for k, c, _n in usage.status_channels("codex")}
        components = [
            _component(ids["codex_app"], "Codex in ChatGPT Desktop", "operational"),
            _component(ids["codex_cli"], "CLI", "degraded_performance"),
            _component(ids["codex_api"], "Codex API", "operational"),
        ]
        status, key, _name = usage.worst_status_by_id(
            components, ["codex_app", "codex_cli", "codex_api"], "codex")
        self.assertEqual((status, key), ("degraded_performance", "codex_cli"))

    def test_tie_prefers_channel_definition_order(self):
        ids = {k: c for k, c, _n in usage.status_channels("codex")}
        components = [
            _component(ids["codex_app"], "Codex in ChatGPT Desktop", "major_outage"),
            _component(ids["codex_cli"], "CLI", "major_outage"),
        ]
        # 即使选择顺序把 cli 放前面，并列仍按渠道定义顺序取 codex_app（index 靠前）。
        _status, key, _name = usage.worst_status_by_id(
            components, ["codex_cli", "codex_app"], "codex")
        self.assertEqual(key, "codex_app")

    def test_missing_id_returns_unknown(self):
        # 已选渠道的 ID 不在接口返回里（消失 / 接口失败留空）→ None（未知）。
        components = [_component("some-other-id", "Unrelated", "operational")]
        self.assertIsNone(usage.worst_status_by_id(components, ["codex_app"], "codex"))
        self.assertIsNone(usage.worst_status_by_id([], ["codex_app"], "codex"))
        self.assertIsNone(usage.worst_status_by_id(None, ["codex_app"], "codex"))

    def test_empty_selection_returns_none(self):
        ids = {k: c for k, c, _n in usage.status_channels("codex")}
        components = [_component(ids["codex_app"], "Codex in ChatGPT Desktop", "operational")]
        self.assertIsNone(usage.worst_status_by_id(components, [], "codex"))


class StatusInfoTests(unittest.TestCase):
    def test_no_selection_returns_none(self):
        self.assertIsNone(fetchers.status_info([], [], "codex", "en"))

    def test_raw_none_is_loading(self):
        info = fetchers.status_info(None, ["codex_app"], "codex", "en")
        self.assertEqual(info["status"], "loading")

    def test_raw_unknown_is_neutral_unknown(self):
        info = fetchers.status_info("unknown", ["codex_app"], "codex", "en")
        self.assertEqual(info["status"], "unknown")

    def test_missing_id_renders_neutral_unknown_not_stale(self):
        components = [_component("other", "Unrelated", "operational")]
        info = fetchers.status_info(components, ["codex_app"], "codex", "en")
        self.assertEqual(info["status"], "unknown")

    def test_worst_component_name_prefers_api_name(self):
        codex_app_id = next(c for k, c, _n in usage.status_channels("codex") if k == "codex_app")
        components = [_component(codex_app_id, "Codex Desktop (renamed)", "major_outage")]
        info = fetchers.status_info(components, ["codex_app"], "codex", "en")
        self.assertEqual(info["status"], "major_outage")
        self.assertEqual(info["component"], "Codex Desktop (renamed)")


class WindowsStateMigrationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._path = pathlib.Path(self._tmp.name) / ".ai-limit-menubar.json"
        self._orig_path = state._STATE_PATH
        state._STATE_PATH = self._path

    def tearDown(self):
        state._STATE_PATH = self._orig_path
        self._tmp.cleanup()

    def _write(self, obj):
        self._path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")

    def test_legacy_names_migrate_and_write_back_internal_keys(self):
        self._write({
            "claude_status_components": ["Claude Code"],
            "codex_status_components": ["Codex in ChatGPT Desktop", "CLI", "Codex API"],
        })
        loaded = state.load_state()
        self.assertEqual(loaded["claude_status_components"], ["claude_code"])
        self.assertEqual(loaded["codex_status_components"],
                         ["codex_app", "codex_cli", "codex_api"])
        # 迁移必须一次性写回磁盘的内部 key，不是只在内存里。
        on_disk = json.loads(self._path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["codex_status_components"],
                         ["codex_app", "codex_cli", "codex_api"])
        self.assertEqual(on_disk["claude_status_components"], ["claude_code"])

    def test_app_alias_migrates_in_state(self):
        self._write({"codex_status_components": ["App", "CLI"]})
        loaded = state.load_state()
        self.assertEqual(loaded["codex_status_components"], ["codex_app", "codex_cli"])

    def test_empty_selection_preserved_not_reset_to_default(self):
        self._write({"claude_status_components": [], "codex_status_components": []})
        loaded = state.load_state()
        self.assertEqual(loaded["claude_status_components"], [])
        self.assertEqual(loaded["codex_status_components"], [])

    def test_already_internal_keys_not_rewritten(self):
        self._write({
            "claude_status_components": ["claude_code"],
            "codex_status_components": ["codex_app", "codex_cli", "codex_api"],
            "display_windows": ["5h"],
        })
        mtime_before = self._path.stat().st_mtime_ns
        loaded = state.load_state()
        self.assertEqual(loaded["claude_status_components"], ["claude_code"])
        # 已经是内部 key，不需要写回（避免每次启动无谓改写文件）。
        self.assertEqual(self._path.stat().st_mtime_ns, mtime_before)

    def test_old_multi_window_selection_migrates_to_single_icon_period(self):
        self._write({
            "global": "7d",
            "display_windows": ["5h", "7d"],
            "oauth_retry_until": {"codex": 1234567890.0},
            "mac_only_future_field": {"nested": True},
            "codex_status_components": ["App"],
        })
        loaded = state.load_state()
        self.assertEqual(loaded["global"], "7d")
        self.assertEqual(loaded["display_windows"], ["7d"])
        self.assertEqual(loaded["oauth_retry_until"], {"codex": 1234567890.0})
        # 另一平台 / 未来版本写入的未知字段必须原样保留，不被抹掉。
        self.assertEqual(loaded["mac_only_future_field"], {"nested": True})
        # 写回后磁盘上未知字段仍在。
        on_disk = json.loads(self._path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["mac_only_future_field"], {"nested": True})
        self.assertEqual(on_disk["global"], "7d")
        self.assertEqual(on_disk["display_windows"], ["7d"])
        self.assertEqual(on_disk["oauth_retry_until"], {"codex": 1234567890.0})

    def test_single_window_without_global_becomes_icon_period(self):
        self._write({"display_windows": ["7d"]})
        loaded = state.load_state()
        self.assertEqual(loaded["global"], "7d")
        self.assertEqual(loaded["display_windows"], ["7d"])

    def test_missing_status_fields_fall_back_to_defaults(self):
        self._write({"display_windows": ["5h"]})
        loaded = state.load_state()
        self.assertEqual(loaded["claude_status_components"], ["claude_code"])
        self.assertEqual(loaded["codex_status_components"],
                         ["codex_app", "codex_cli", "codex_api"])


if __name__ == "__main__":
    unittest.main()
