from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mira.actions.desktop_actions import make_open_directory_action


class DesktopActionTests(unittest.TestCase):
    def test_open_directory_opens_existing_allowed_directory(self):
        handler = make_open_directory_action()

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
            with patch(
                "mira.actions.desktop_actions.shutil.which",
                return_value="/usr/bin/xdg-open",
            ):
                with patch("mira.actions.desktop_actions.subprocess.Popen") as popen:
                    result = handler({"directory": tmp_dir})

        self.assertTrue(result.success)
        self.assertEqual(result.action_name, "open_directory")
        self.assertEqual(result.data["path"], str(Path(tmp_dir).resolve()))
        popen.assert_called_once()
        command = popen.call_args.args[0]
        self.assertEqual(command[0], "/usr/bin/xdg-open")
        self.assertEqual(command[1], str(Path(tmp_dir).resolve()))

    def test_open_directory_rejects_missing_directory(self):
        handler = make_open_directory_action()

        result = handler({"directory": "does-not-exist-for-mira-test"})

        self.assertFalse(result.success)
        self.assertEqual(result.action_name, "open_directory")
        self.assertIn("non disponibile", result.message)

    def test_open_directory_reports_missing_xdg_open(self):
        handler = make_open_directory_action()

        with tempfile.TemporaryDirectory(dir=Path.cwd()) as tmp_dir:
            with patch("mira.actions.desktop_actions.shutil.which", return_value=None):
                result = handler({"directory": tmp_dir})

        self.assertFalse(result.success)
        self.assertEqual(result.action_name, "open_directory")
        self.assertIn("xdg-open", result.message)


if __name__ == "__main__":
    unittest.main()
