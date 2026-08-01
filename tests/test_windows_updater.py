import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
import importlib.util
import os
from unittest import mock

from Cryptodome.PublicKey import ECC


ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

import updater_win
import update_signing


def load_tray_module(name):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    spec = importlib.util.spec_from_file_location(name, WINDOWS_DIR / "ai-limit-tray.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _version_info(major, minor, build, revision=0):
    return {
        "FileVersionMS": (major << 16) | minor,
        "FileVersionLS": (build << 16) | revision,
    }


class WindowsUpdaterTests(unittest.TestCase):
    def _verify_with(self, signature_status, version=(0, 3, 23, 0),
                     expected="0.3.23"):
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = pathlib.Path(temp_dir) / "update.exe"
            installer.write_bytes(b"MZtest installer")
            completed = subprocess.CompletedProcess([], 0, signature_status + "\n", "")
            fake_win32api = types.SimpleNamespace(
                GetFileVersionInfo=lambda *_: _version_info(*version)
            )
            with (mock.patch.object(updater_win.subprocess, "run", return_value=completed),
                  mock.patch.object(update_signing, "verify_detached_signature"),
                  mock.patch.dict(sys.modules, {"win32api": fake_win32api})):
                return updater_win.verify_installer(
                    installer,
                    installer.with_name(installer.name + ".sig"),
                    expected,
                    f"ai-limit-{expected}-setup.exe",
                )

    def test_release_asset_must_match_declared_version_exactly(self):
        assets = [
            {"name": "ai-limit-0.3.22-setup.exe", "browser_download_url": "old"},
            {"name": "ai-limit-0.3.23-setup.exe", "browser_download_url": "new"},
        ]
        self.assertEqual(
            updater_win._pick_setup_asset(assets, "0.3.23"),
            ("new", "ai-limit-0.3.23-setup.exe"),
        )

    def test_release_signature_must_match_exact_installer_name(self):
        assets = [
            {"name": "ai-limit-0.3.22-setup.exe.sig", "browser_download_url": "old"},
            {"name": "ai-limit-0.3.23-setup.exe.sig", "browser_download_url": "new"},
        ]
        self.assertEqual(
            updater_win._pick_signature_asset(
                assets, "ai-limit-0.3.23-setup.exe"
            ),
            ("new", "ai-limit-0.3.23-setup.exe.sig"),
        )

    def test_plain_http_or_unrelated_download_host_is_rejected(self):
        for url in (
            "http://github.com/example/setup.exe",
            "https://example.com/ai-limit-0.3.23-setup.exe",
        ):
            with self.subTest(url=url), self.assertRaises(updater_win.UpdateFailed) as caught:
                updater_win._validate_download_url(url)
            self.assertEqual(caught.exception.reason, "unsafe_download_url")

    def test_unsigned_installer_is_allowed_after_version_check(self):
        self.assertEqual(self._verify_with("NotSigned"), "unsigned")

    def test_real_powershell_authenticode_call_accepts_a_path_argument(self):
        status = updater_win._authenticode_status(sys.executable)
        self.assertIn(status, {
            "Valid", "NotSigned", "HashMismatch", "NotTrusted",
            "NotSupportedFileFormat", "UnknownError",
        })

    def test_authenticode_powershell_never_opens_a_console_window(self):
        completed = subprocess.CompletedProcess([], 0, "NotSigned\n", "")
        with mock.patch.object(
            updater_win.subprocess, "run", return_value=completed
        ) as run:
            self.assertEqual(
                updater_win._authenticode_status("C:/Temp/update.exe"),
                "NotSigned",
            )

        self.assertEqual(
            run.call_args.kwargs["creationflags"],
            getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def test_valid_signature_is_preserved_as_stronger_trust_state(self):
        self.assertEqual(self._verify_with("Valid"), "signed")

    def test_broken_signature_is_not_downgraded_to_unsigned(self):
        with self.assertRaises(updater_win.UpdateFailed) as caught:
            self._verify_with("HashMismatch")
        self.assertEqual(caught.exception.reason, "signature_invalid")

    def test_version_comparison_does_not_accept_prefix_collision(self):
        with self.assertRaises(updater_win.UpdateFailed) as caught:
            self._verify_with("NotSigned", version=(0, 3, 20, 0), expected="0.3.2")
        self.assertEqual(caught.exception.reason, "version_mismatch")

    def test_non_pe_file_is_rejected_before_signature_check(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            installer = root / "update.exe"
            installer.write_bytes(b"not an executable")
            with mock.patch.object(updater_win.subprocess, "run") as run:
                with self.assertRaises(updater_win.UpdateFailed) as caught:
                    updater_win.verify_installer(
                        installer, root / "update.exe.sig", "0.3.23",
                        "ai-limit-0.3.23-setup.exe",
                    )
        self.assertEqual(caught.exception.reason, "invalid_installer")
        run.assert_not_called()

    def test_invalid_ed25519_signature_is_rejected_before_authenticode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            installer = root / "update.exe"
            installer.write_bytes(b"MZtest")
            with (mock.patch.object(
                      update_signing, "verify_detached_signature",
                      side_effect=update_signing.SignatureError("bad signature")),
                  mock.patch.object(updater_win, "_authenticode_status") as auth):
                with self.assertRaises(updater_win.UpdateFailed) as caught:
                    updater_win.verify_installer(
                        installer, root / "update.exe.sig", "0.3.23",
                        "ai-limit-0.3.23-setup.exe",
                    )
        self.assertEqual(caught.exception.reason, "update_signature_invalid")
        auth.assert_not_called()

    def test_missing_ed25519_signature_is_rejected_before_authenticode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            installer = root / "update.exe"
            installer.write_bytes(b"MZtest")
            with mock.patch.object(updater_win, "_authenticode_status") as auth:
                with self.assertRaises(updater_win.UpdateFailed) as caught:
                    updater_win.verify_installer(
                        installer, root / "missing.sig", "0.3.23",
                        "ai-limit-0.3.23-setup.exe",
                    )
        self.assertEqual(caught.exception.reason, "update_signature_invalid")
        auth.assert_not_called()

    def test_ed25519_document_detects_installer_tampering(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = pathlib.Path(temp_dir) / "ai-limit-0.3.23-setup.exe"
            installer.write_bytes(b"MZoriginal")
            private_key = ECC.generate(curve="Ed25519")
            public_der = private_key.public_key().export_key(format="DER")
            with (mock.patch.object(update_signing, "PUBLIC_KEY_DER", public_der),
                  mock.patch.object(
                      update_signing, "KEY_ID",
                      __import__("hashlib").sha256(public_der).hexdigest()[:16])):
                document = update_signing.build_signature_document(
                    installer, private_key.export_key(format="DER", use_pkcs8=True)
                )
                update_signing.verify_signature_document(
                    installer, document, installer.name
                )
                with self.assertRaises(update_signing.SignatureError):
                    update_signing.verify_signature_document(
                        installer, document, "ai-limit-0.3.24-setup.exe"
                    )
                installer.write_bytes(b"MZtampered")
                with self.assertRaises(update_signing.SignatureError):
                    update_signing.verify_signature_document(
                        installer, document, installer.name
                    )

    def test_mark_of_the_web_records_internet_zone_and_source(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = pathlib.Path(temp_dir) / "update.exe"
            installer.write_bytes(b"MZ")
            updater_win._mark_installer_as_internet_file(
                installer, "https://github.com/example/setup.exe"
            )
            zone = pathlib.Path(f"{installer}:Zone.Identifier").read_text(encoding="utf-8")
        self.assertIn("ZoneId=3", zone)
        self.assertIn("HostUrl=https://github.com/example/setup.exe", zone)

    def test_interactive_launch_has_no_silent_installer_arguments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            installer = root / "update.exe"
            installer.write_bytes(b"MZ")
            marker = root / "pending.json"
            with (mock.patch.object(updater_win, "_UPDATE_PENDING_MARKER", marker),
                  mock.patch.object(updater_win, "_mark_installer_as_internet_file") as mark,
                  mock.patch.object(updater_win.subprocess, "Popen") as popen):
                updater_win.trigger_interactive_install(
                    installer, "0.3.23", "https://github.com/example/setup.exe"
                )

            mark.assert_called_once_with(installer, "https://github.com/example/setup.exe")
            popen.assert_called_once()
            command = popen.call_args.args[0]
            self.assertNotIn("/SILENT", command)
            self.assertNotIn("/VERYSILENT", command)
            self.assertEqual(
                __import__("json").loads(marker.read_text(encoding="utf-8"))["target_version"],
                "0.3.23",
            )

    def test_failed_shell_launch_removes_pending_marker(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            installer = root / "update.exe"
            installer.write_bytes(b"MZ")
            marker = root / "pending.json"
            with (mock.patch.object(updater_win, "_UPDATE_PENDING_MARKER", marker),
                  mock.patch.object(updater_win, "_mark_installer_as_internet_file"),
                  mock.patch.object(
                      updater_win.subprocess, "Popen", side_effect=OSError("blocked")
                  )):
                with self.assertRaises(updater_win.UpdateFailed) as caught:
                    updater_win.trigger_interactive_install(
                        installer, "0.3.23", "https://github.com/example/setup.exe"
                    )
            self.assertEqual(caught.exception.reason, "launch_failed")
            self.assertFalse(marker.exists())

    def test_marker_write_failure_does_not_launch_installer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = pathlib.Path(temp_dir)
            installer = root / "update.exe"
            installer.write_bytes(b"MZ")
            marker = root / "missing-parent" / "pending.json"
            with (mock.patch.object(updater_win, "_UPDATE_PENDING_MARKER", marker),
                  mock.patch.object(updater_win, "_mark_installer_as_internet_file"),
                  mock.patch.object(updater_win.subprocess, "Popen") as popen):
                with self.assertRaises(updater_win.UpdateFailed) as caught:
                    updater_win.trigger_interactive_install(
                        installer, "0.3.23", "https://github.com/example/setup.exe"
                    )
            self.assertEqual(caught.exception.reason, "marker_failed")
            popen.assert_not_called()

    def test_tray_quits_only_after_interactive_installer_launches(self):
        module = load_tray_module("ai_limit_tray_updater_success_test")
        tray = module.AiLimitTray.__new__(module.AiLimitTray)
        tray._pending_lock = __import__("threading").Lock()
        tray._pending = [("update_download", {
            "ok": True,
            "setup_path": "C:/Temp/update.exe",
            "version": "0.3.23",
            "source_url": "https://github.com/example/setup.exe",
            "source": "github",
        })]
        tray._app = mock.Mock()
        tray._check_update_action = mock.Mock()
        tray._panel = None
        tray._lang = mock.Mock(return_value="zh")

        with (mock.patch.object(module.updater_win, "trigger_interactive_install") as launch,
              mock.patch.object(module, "show_alert") as alert):
            tray._apply_pending()

        launch.assert_called_once_with(
            "C:/Temp/update.exe", "0.3.23", "https://github.com/example/setup.exe"
        )
        tray._app.quit.assert_called_once_with()
        alert.assert_not_called()

    def test_tray_stays_running_when_installer_launch_is_blocked(self):
        module = load_tray_module("ai_limit_tray_updater_failure_test")
        tray = module.AiLimitTray.__new__(module.AiLimitTray)
        tray._pending_lock = __import__("threading").Lock()
        tray._pending = [("update_download", {
            "ok": True,
            "setup_path": "C:/Temp/update.exe",
            "version": "0.3.23",
            "source_url": "https://github.com/example/setup.exe",
            "source": "github",
        })]
        tray._app = mock.Mock()
        tray._check_update_action = mock.Mock()
        tray._panel = None
        tray._lang = mock.Mock(return_value="zh")
        failure = module.updater_win.UpdateFailed("motw_failed", "blocked")

        with (mock.patch.object(module.updater_win, "trigger_interactive_install",
                               side_effect=failure),
              mock.patch.object(module, "show_alert", return_value=False) as alert):
            tray._apply_pending()

        tray._app.quit.assert_not_called()
        tray._check_update_action.setEnabled.assert_called_with(True)
        alert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
