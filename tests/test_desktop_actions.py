from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mira.actions.desktop_actions import (
    make_get_project_path_action,
    make_open_app_action,
    make_open_directory_action,
    make_open_url_action,
)


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

    def test_open_url_normalizes_and_opens_https_url(self):
        handler = make_open_url_action()

        with patch("mira.actions.desktop_actions.webbrowser.open", return_value=True) as open_url:
            result = handler({"url": "example.com/docs"})

        self.assertTrue(result.success)
        self.assertEqual(result.data["url"], "https://example.com/docs")
        open_url.assert_called_once_with("https://example.com/docs")

    def test_open_url_rejects_invalid_url(self):
        handler = make_open_url_action()

        with patch("mira.actions.desktop_actions.webbrowser.open") as open_url:
            result = handler({"url": "not a url"})

        self.assertFalse(result.success)
        self.assertEqual(result.action_name, "open_url")
        self.assertEqual(result.data["reason"], "invalid_url")
        open_url.assert_not_called()

    def test_open_url_rejects_unsupported_schemes(self):
        handler = make_open_url_action()

        for raw_url in ["file:///etc/passwd", "javascript:alert(1)", "ftp://example.com"]:
            with self.subTest(raw_url=raw_url):
                with patch("mira.actions.desktop_actions.webbrowser.open") as open_url:
                    result = handler({"url": raw_url})

                self.assertFalse(result.success)
                self.assertEqual(result.action_name, "open_url")
                self.assertEqual(result.data["reason"], "invalid_url")
                self.assertEqual(result.data["requested_url"], raw_url)
                open_url.assert_not_called()

    def test_open_app_starts_allowlisted_available_app(self):
        handler = make_open_app_action()

        with patch("mira.actions.desktop_actions.shutil.which", return_value="/usr/bin/firefox"):
            with patch("mira.actions.desktop_actions.subprocess.Popen") as popen:
                result = handler({"app_name": "browser"})

        self.assertTrue(result.success)
        self.assertEqual(result.action_name, "open_app")
        self.assertEqual(result.data["resolved_app"], "firefox")
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0], ["firefox"])

    def test_open_app_rejects_non_allowlisted_app(self):
        handler = make_open_app_action()

        result = handler({"app_name": "rm"})

        self.assertFalse(result.success)
        self.assertEqual(result.action_name, "open_app")
        self.assertIn("non disponibile o non consentita", result.message)

    def test_get_project_path_reports_current_project_directory(self):
        handler = make_get_project_path_action()

        result = handler({})

        self.assertTrue(result.success)
        self.assertEqual(result.action_name, "get_project_path")
        self.assertEqual(result.data["path"], str(Path.cwd().resolve()))



if __name__ == "__main__":
    unittest.main()
