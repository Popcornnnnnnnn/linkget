"""Behavior checks run only against temporary files; Photos is never invoked."""
import contextlib
import io
import os
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from linkget import cli


class CommandTests(unittest.TestCase):
    def test_bare_folder_before_url_uses_current_directory(self):
        from subprocess import CompletedProcess
        def download(command, **kwargs):
            (Path(command[command.index("--directory") + 1]) / "sample.jpg").write_bytes(b"image")
            return CompletedProcess(command, 0)
        with tempfile.TemporaryDirectory() as temp:
            previous = Path.cwd()
            try:
                os.chdir(temp)
                with patch.object(cli, "find_tool", return_value="gallery-dl"), patch.object(cli.subprocess, "run", side_effect=download), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(cli.main(["--folder", "https://x.com/user/status/123", "--browser", "none"]), 0)
            finally:
                os.chdir(previous)
            self.assertTrue((Path(temp) / "sample.jpg").exists())

    def test_mistyped_command_shows_help_without_starting_browser_setup(self):
        with patch.object(cli, "first_run") as setup, contextlib.redirect_stderr(io.StringIO()) as err:
            with self.assertRaises(SystemExit):
                cli.main(["site", "bilibili"])
            self.assertIn("linkget sites", err.getvalue())
            setup.assert_not_called()

    def test_download_size_does_not_double_count_parts_or_merges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contents = {"clip.f1.mp4.part": 400, "clip.f1.mp4": 400,
                        "clip.f2.m4a.part": 100, "clip.temp.mp4": 250,
                        "clip.mp4": 520, "clip.ytdl": 200,
                        "clip.f1.mp4.part-Frag1": 100}
            for name, size in contents.items():
                (root / name).write_bytes(b"x" * size)
            self.assertEqual(cli.downloaded_bytes(root), 500)
            for name in ("clip.f1.mp4.part", "clip.f1.mp4", "clip.f2.m4a.part"):
                (root / name).unlink()
            self.assertEqual(cli.downloaded_bytes(root), 520)

    def test_summary_counts_only_newly_saved_media(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "output"
            folder.mkdir()
            (folder / "old.jpg").write_bytes(b"o" * 2_000_000)

            def download(command, **kwargs):
                output = Path(command[command.index("--directory") + 1])
                (output / "old.jpg").write_bytes(b"o" * 2_000_000)
                (output / "new.jpg").write_bytes(b"p" * 3_000_000)
                (output / "new.mp4").write_bytes(b"v" * 5_000_000)
                return CompletedProcess(command, 0)

            args = ["https://x.com/user/status/123", "--folder", str(folder), "--browser", "none"]
            output = io.StringIO()
            with patch.object(cli, "find_tool", return_value="gallery-dl"), patch.object(cli.subprocess, "run", side_effect=download), contextlib.redirect_stdout(output):
                self.assertEqual(cli.main(args), 0)
                self.assertIn("1 photo, 1 video saved · 8.0 MB", output.getvalue())
                self.assertIn("1 duplicate", output.getvalue())
                output.seek(0)
                output.truncate()
                self.assertEqual(cli.main(args), 0)
                self.assertIn("No new files", output.getvalue())
                self.assertIn("3 duplicates", output.getvalue())
                self.assertNotIn("MB", output.getvalue())

    def test_photos_reports_which_files_were_added(self):
        from subprocess import CompletedProcess
        files = [Path("existing.jpg"), Path("new.mp4")]
        with patch.object(cli, "retain_originals", side_effect=lambda files: files), patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "1:1:2\n", "")):
            self.assertEqual(cli.import_photos(files, False), ([files[1]], 1))
        for response in ("1:1:3", "2:0:1:1", "1:1", "invalid"):
            with patch.object(cli, "retain_originals", side_effect=lambda files: files), patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, response, "")):
                with self.assertRaises(RuntimeError):
                    cli.import_photos(files, False)

    def test_animation_stops_and_clears_on_failure_and_cancel(self):
        class Terminal(io.StringIO):
            def __init__(self):
                super().__init__()
                self.frames = 0
                self.animated = threading.Event()

            def isatty(self):
                return True

            def write(self, text):
                result = super().write(text)
                if "Downloading" in text:
                    self.frames += 1
                    if self.frames >= 2:
                        self.animated.set()
                return result

        for error in (RuntimeError, KeyboardInterrupt):
            output = Terminal()
            with patch.dict(os.environ, {"TERM": "xterm-256color"}), contextlib.redirect_stdout(output):
                with self.assertRaises(error):
                    with cli.activity("Downloading"):
                        self.assertTrue(output.animated.wait(1))
                        raise error()
            self.assertTrue(output.getvalue().endswith("\r\033[2K"))
            self.assertFalse(any(t.name == "linkget-progress" for t in threading.enumerate()))

    def test_redirected_activity_has_no_terminal_controls(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with cli.activity("Downloading"):
                pass
        self.assertIn("Downloading", output.getvalue())
        self.assertNotIn("\033", output.getvalue())
        self.assertNotIn("\r", output.getvalue())

    def test_supported_content_and_rejected_hosts(self):
        for url, expected in [
            ("https://www.instagram.com/reel/abc/?igsh=x", "gallery-dl"),
            ("https://twitter.com/user/status/123/video/1", "gallery-dl"),
            ("https://www.bilibili.com/video/BV123?p=2", "yt-dlp"),
            ("https://t.bilibili.com/123", "gallery-dl"),
        ]:
            self.assertEqual(cli.route(url)[1], expected)
        for url in ("https://x.com.evil.test/user/status/1", "https://x.com/user", "https://name:pass@x.com/u/status/1"):
            with self.assertRaises(ValueError):
                cli.route(url)

    def test_folder_skips_identical_preserves_collision(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "clip.mp4"
            source.write_bytes(b"new video")
            output = root / "output"
            output.mkdir()
            existing = output / source.name
            existing.write_bytes(b"existing video")
            self.assertEqual(cli.save_folder([source], output), ([source], 0))
            self.assertEqual(existing.read_bytes(), b"existing video")
            self.assertEqual((output / "clip-1.mp4").read_bytes(), b"new video")
            existing.write_bytes(b"new video")
            self.assertEqual(cli.save_folder([source], output), ([], 1))

    def test_missing_tools_explained_before_download(self):
        stderr = io.StringIO()
        with patch.object(cli, "find_tool", return_value=None), patch.object(cli.subprocess, "run") as run, contextlib.redirect_stderr(stderr):
            result = cli.main(["https://x.com/user/status/123", "--folder", "/tmp/not-created-linkget", "--browser", "none"])
        self.assertEqual(result, 1)
        self.assertIn("brew install gallery-dl", stderr.getvalue())
        run.assert_not_called()

    def test_folder_download_and_failure_preservation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            downloader = root / "gallery-dl"
            downloader.write_text("#!/bin/sh\nwhile [ \"$1\" != \"--directory\" ]; do shift; done\nshift\nprintf sample > \"$1/sample.jpg\"\nexit \"${LINKGET_TEST_EXIT:-0}\"\n")
            downloader.chmod(0o755)
            stage = root / "stage"

            def make_stage(**kwargs):
                stage.mkdir()
                return str(stage)

            with patch.object(cli, "find_tool", return_value=str(downloader)), patch.object(cli.tempfile, "mkdtemp", side_effect=make_stage), patch.object(cli, "import_photos") as photos, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                args = ["https://x.com/user/status/123", "--folder", str(root / "output"), "--browser", "none"]
                self.assertEqual(cli.main(args), 0)
                self.assertFalse(stage.exists())
                self.assertEqual((root / "output/sample.jpg").read_bytes(), b"sample")
                photos.assert_not_called()
                with patch.dict(os.environ, {"LINKGET_TEST_EXIT": "1"}):
                    self.assertEqual(cli.main(args), 1)
                self.assertTrue((stage / "media/sample.jpg").exists())
                self.assertTrue((stage / "download.log").exists())

    def test_photos_bad_count_prevents_success(self):
        from subprocess import CompletedProcess
        with patch.object(cli, "retain_originals", side_effect=lambda files: files), patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "0:0\n", "")):
            with self.assertRaises(RuntimeError):
                cli.import_photos([Path("fake.jpg")], False)

    def test_prompt_preserves_full_unquoted_share_link(self):
        from subprocess import CompletedProcess
        url = "https://x.com/user/status/123?s=20&t=test[]*"

        def download(command, **kwargs):
            self.assertEqual(command[-1], url)
            output = Path(command[command.index("--directory") + 1])
            (output / "sample.jpg").write_bytes(b"sample")
            return CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as folder:
            with patch.object(sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value=url), patch.object(cli, "find_tool", return_value="gallery-dl"), patch.object(cli.subprocess, "run", side_effect=download), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["--folder", folder, "--browser", "none"]), 0)
            self.assertEqual((Path(folder) / "sample.jpg").read_bytes(), b"sample")


if __name__ == "__main__":
    unittest.main()
