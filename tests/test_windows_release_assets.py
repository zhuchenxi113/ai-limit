import pathlib
import sys
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

import prepare_windows_release_assets


class WindowsReleaseAssetsTests(unittest.TestCase):
    def test_prepares_and_signs_platform_asset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            canonical = pathlib.Path(temp_dir) / (
                "ai-limit-windows-0.3.26-setup.exe"
            )
            canonical.write_bytes(b"MZrelease candidate")
            signed_names = []

            def fake_signer(installer, _key):
                installer = pathlib.Path(installer)
                signed_names.append(installer.name)
                signature = installer.with_name(installer.name + ".sig")
                signature.write_text(installer.name, encoding="utf-8")
                return signature, {
                    "key_id": "test-key",
                    "sha256": installer.name,
                }

            prepared = prepare_windows_release_assets.prepare_release_assets(
                canonical, "test-key", signer=fake_signer
            )

            self.assertEqual(signed_names, [canonical.name])
            self.assertEqual(
                [item[0].name for item in prepared],
                [canonical.name],
            )
            self.assertTrue(canonical.with_name(canonical.name + ".sig").is_file())

    def test_rejects_installer_without_platform_qualified_name(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            legacy = pathlib.Path(temp_dir) / "ai-limit-0.3.26-setup.exe"
            legacy.write_bytes(b"MZrelease candidate")

            with self.assertRaisesRegex(ValueError, "ai-limit-windows"):
                prepare_windows_release_assets.prepare_release_assets(
                    legacy, "test-key", signer=lambda *_: None
                )


if __name__ == "__main__":
    unittest.main()
