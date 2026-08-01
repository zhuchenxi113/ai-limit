import pathlib
import sys
import unittest
import uuid


ROOT = pathlib.Path(__file__).resolve().parents[1]
WINDOWS_DIR = ROOT / "menubar" / "windows"
sys.path[:0] = [str(WINDOWS_DIR), str(ROOT)]

from single_instance_win import acquire_single_instance, release_single_instance


@unittest.skipUnless(sys.platform == "win32", "Windows named mutex test")
class SingleInstanceTests(unittest.TestCase):
    def test_second_acquire_is_rejected_until_first_handle_closes(self):
        name = rf"Local\AI-Limit-Test-{uuid.uuid4()}"
        first = acquire_single_instance(name)
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(acquire_single_instance(name))
        finally:
            release_single_instance(first)

        third = acquire_single_instance(name)
        self.assertIsNotNone(third)
        release_single_instance(third)


if __name__ == "__main__":
    unittest.main()
