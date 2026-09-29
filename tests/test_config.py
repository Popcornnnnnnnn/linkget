"""Saved destinations and per-run overrides, isolated from personal data and Photos."""
import contextlib
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, cli


class DestinationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        mocked = patch.object(accounts, "directory", return_value=self.root / "data/sessions")
        mocked.start()
        self.addCleanup(mocked.stop)

    def command(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            result = cli.main(list(args))
        self.assertEqual(result, 0)
        return out.getvalue()

    def download(self, *options):
        def media(url, folder, cookies):
            (folder / "sample.jpg").write_bytes(b"image")
        with patch.object(cli, "download_douyin", side_effect=media):
            return self.command("https://www.douyin.com/note/123", "--browser", "none", *options)

    def test_query_shows_initial_photos_without_setup_or_writing_preferences(self):
        with patch.object(cli, "first_run") as setup, patch.object(cli.subprocess, "run") as external:
            self.assertIn("Photos", self.command("config"))
            setup.assert_not_called()
            external.assert_not_called()
        self.assertFalse((self.root / "data/preferences.json").exists())

    def test_current_folder_is_resolved_at_download_time(self):
        previous = Path.cwd()
        one, two = self.root / "one", self.root / "two"
        one.mkdir()
        two.mkdir()
        try:
            os.chdir(one)
            self.assertIn("Current folder", self.command("config", "--folder"))
            os.chdir(two)
            output = self.download()
        finally:
            os.chdir(previous)
        self.assertIn(str(two.resolve()), output)
        self.assertTrue((two / "sample.jpg").exists())
        self.assertFalse((one / "sample.jpg").exists())

    def test_fixed_relative_folder_is_saved_as_absolute_and_preserves_browser_settings(self):
        accounts.set_preferences(browser="firefox", setup=True, blocked=["x.com"])
        previous = Path.cwd()
        try:
            os.chdir(self.root)
            self.command("config", "--folder", "saved files")
        finally:
            os.chdir(previous)
        expected = self.root.resolve() / "saved files"
        self.assertFalse(expected.exists())  # setting a default does not create folders
        self.assertIn(str(expected), self.command("config"))
        self.download()
        self.assertTrue((expected / "sample.jpg").exists())
        self.assertEqual(accounts.preferences()["browser"], "firefox")
        self.assertEqual(accounts.preferences()["blocked"], ["x.com"])

    def test_one_time_overrides_do_not_change_saved_destination(self):
        default = self.root / "default"
        override = self.root / "override"
        self.command("config", "--folder", str(default))
        before = accounts.preferences()
        self.download("--folder", str(override))
        self.assertTrue((override / "sample.jpg").exists())
        self.assertFalse(default.exists())
        with patch.object(cli.sys, "platform", "darwin"), patch.object(cli, "import_photos", side_effect=lambda files, date: (files, 0)) as photos:
            self.assertIn("Photos", self.download("--photos", "--date-now"))
            photos.assert_called_once()
            self.assertTrue(photos.call_args.args[1])
        self.assertEqual(accounts.preferences(), before)
        self.command("config", "--photos")
        self.assertEqual(accounts.preferences()["destination"], {"mode": "photos"})
        self.assertIn("Photos", self.command("config"))

    def test_invalid_combinations_and_file_path_do_not_mutate_default(self):
        self.command("config", "--photos")
        before = accounts.preferences()
        for args in (["--photos", "--folder"], ["config", "--photos", "--folder"], ["config", "extra"], ["config", "--date-now"]):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main(args)
        existing_file = self.root / "file"
        existing_file.write_text("keep")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["config", "--folder", str(existing_file)]), 1)
        self.assertEqual(accounts.preferences(), before)

    def test_date_now_with_folder_default_fails_before_setup_or_download(self):
        self.command("config", "--folder")
        with patch.object(cli, "first_run") as setup, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(["https://www.douyin.com/note/123", "--date-now"])
        setup.assert_not_called()


if __name__ == "__main__":
    unittest.main()
