import base64
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

import updater_win


class LaunchHelperTests(unittest.TestCase):
    def test_launch_is_delegated_to_hidden_helper(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_dir = pathlib.Path(temp)
            marker = temp_dir / "pending.json"
            installer = temp_dir / "O'Brien setup.exe"

            with (
                mock.patch.object(updater_win, "_UPDATE_PENDING_MARKER", marker),
                mock.patch.object(updater_win, "_mark_installer_as_internet_file"),
                mock.patch.object(updater_win.subprocess, "Popen") as popen,
            ):
                updater_win.trigger_interactive_install(
                    installer, "0.3.24", "https://example.invalid/setup.exe"
                )

            marker_data = json.loads(marker.read_text(encoding="utf-8"))
            self.assertEqual(marker_data["target_version"], "0.3.24")
            popen.assert_called_once()
            command = popen.call_args.args[0]
            encoded = command[command.index("-EncodedCommand") + 1]
            script = base64.b64decode(encoded).decode("utf-16le")
            self.assertIn("Wait-Process", script)
            self.assertIn("Start-Process -FilePath $installer", script)
            self.assertIn("O''Brien setup.exe", script)
            self.assertEqual(
                popen.call_args.kwargs["creationflags"],
                getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.assertIs(popen.call_args.kwargs["stdin"], subprocess.DEVNULL)
            self.assertIs(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)
            self.assertIs(popen.call_args.kwargs["stderr"], subprocess.DEVNULL)


if __name__ == "__main__":
    unittest.main()
