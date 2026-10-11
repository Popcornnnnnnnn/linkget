"""Batch acceptance through the real CLI; no network, browser or Photos access."""
import contextlib
import io
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import threading
import unittest
from unittest.mock import patch
from media_stub import uninspected

from linkget import accounts, cli, links

URL = "https://www.pixiv.net/artworks/"


class BatchTests(unittest.TestCase):
    def setUp(self):
        probe = patch.object(cli, "inspect_files", side_effect=uninspected)
        probe.start()
        self.addCleanup(probe.stop)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for obj, name, value in [(accounts, "directory", self.root / "settings" / "sessions"),
                                 (cli.tempfile, "tempdir", str(self.root)),
                                 (cli.sys.stdin, "isatty", False)]:
            mocked = patch.object(obj, name, return_value=value) if name != "tempdir" else patch.object(obj, name, value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def run_batch(self, links, download, *options):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(cli, "download_pixiv", side_effect=download), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = cli.main(["batch", *links, "--folder", str(self.root / "output"), "--browser", "none", *options])
        return result, stdout.getvalue(), stderr.getvalue()

    def test_middle_failure_preserves_partial_files_and_later_items_still_save(self):
        calls = []

        def download(url, folder, cookies):
            calls.append(url)
            number = url.rsplit("/", 1)[-1]
            if number == "2":
                (folder / "second.jpg.part").write_bytes(b"partial")
                raise RuntimeError("Pixiv request failed (HTTP 403).")
            (folder / (number + ".jpg")).write_bytes(number.encode())

        result, out, err = self.run_batch([URL + str(n) for n in (1, 2, 3)], download)
        self.assertEqual(result, 1)
        self.assertCountEqual(calls, [URL + str(n) for n in (1, 2, 3)])
        self.assertEqual(sorted(p.name for p in (self.root / "output").iterdir()), ["1.jpg", "3.jpg"])
        self.assertEqual(len(list(self.root.glob("linkget-*/media/second.jpg.part"))), 1)
        self.assertIn("2 completed · 1 failed", out)
        self.assertIn("Failed items", out)
        self.assertIn("Access refused", err)

    def test_invalid_input_is_an_item_failure_not_a_batch_abort(self):
        def download(url, folder, cookies):
            (folder / "valid.jpg").write_bytes(b"valid")

        result, out, err = self.run_batch(["not a URL", URL + "1"], download)
        self.assertEqual(result, 1)
        self.assertTrue((self.root / "output/valid.jpg").is_file())
        self.assertIn("1 completed · 1 failed", out)
        self.assertIn("Paste one post link", err)

    def test_piped_sharing_text_keeps_each_lines_url_and_ignores_blank_lines(self):
        calls = []

        def download(url, folder, cookies):
            calls.append(url)
            (folder / (url.rsplit("/", 1)[-1] + ".jpg")).write_bytes(url.encode())

        with patch.object(cli.sys, "stdin", io.StringIO("分享 " + URL + "1\n\n看看 " + URL + "2\n")):
            result, out, _ = self.run_batch([], download)
        self.assertEqual(result, 0)
        self.assertCountEqual(calls, [URL + "1", URL + "2"])
        self.assertIn("2 completed · 0 failed", out)

    def test_interactive_collection_runs_browser_setup_only_once(self):
        def download(url, folder, cookies):
            (folder / (url.rsplit("/", 1)[-1] + ".jpg")).write_bytes(url.encode())

        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch("builtins.input", side_effect=[URL + "1", URL + "2", ""]), patch.object(cli, "first_run", return_value={}) as setup:
            result, _, _ = self.run_batch([], download)
        self.assertEqual(result, 0)
        setup.assert_called_once()

    def test_repeated_media_reuses_existing_duplicate_handling(self):
        def download(url, folder, cookies):
            (folder / "same.jpg").write_bytes(b"same")

        result, out, _ = self.run_batch([URL + "1", URL + "1"], download)
        self.assertEqual(result, 0)
        self.assertEqual(len(list((self.root / "output").iterdir())), 1)
        self.assertIn("No new files", out)
        self.assertIn("1 duplicate", out)

    def test_repeated_short_links_share_resolution_but_download_each_item(self):
        calls = []
        def download(url, folder, cookies=None, **kwargs):
            calls.append(url)
            (folder / "same.jpg").write_bytes(b"same media")
        out, err = io.StringIO(), io.StringIO()
        with patch.object(links, "_resolve_short_link", return_value="https://www.douyin.com/video/123") as resolve, patch.object(cli, "download_douyin", side_effect=download), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = cli.main(["batch", "分享 https://v.douyin.com/abc/", "看看 https://v.douyin.com/abc/", "--folder", str(self.root / "output"), "--browser", "none", "--jobs", "2"])
        self.assertEqual(result, 0, err.getvalue())
        resolve.assert_called_once()
        self.assertEqual(calls, ["https://www.douyin.com/video/123"] * 2)
        self.assertEqual(len(list((self.root / "output").iterdir())), 1)
        self.assertIn("2 completed · 0 failed", out.getvalue())

    def test_cancel_stops_remaining_items_and_preserves_current_download(self):
        calls = []

        def download(url, folder, cookies):
            calls.append(url)
            (folder / "image.jpg.part").write_bytes(b"partial")
            raise KeyboardInterrupt

        result, out, err = self.run_batch([URL + "1", URL + "2"], download)
        self.assertEqual(result, 130)
        self.assertTrue(calls)
        self.assertTrue(set(calls) <= {URL + "1", URL + "2"})
        self.assertEqual(list((self.root / "output").glob("*")), [])
        self.assertIn("2 not completed", out)
        self.assertIn("Files kept", err)

    def test_every_link_downloads_concurrently_and_photos_imports_are_serial(self):
        downloads = threading.Barrier(4)
        importing = threading.Lock()
        imported = []

        def download(url, folder, cookies):
            downloads.wait(timeout=3)
            (folder / "image.jpg").write_bytes(url.encode())

        def save(files, date):
            self.assertTrue(importing.acquire(blocking=False), "Photos imports overlapped")
            try:
                imported.append(files[0].read_bytes())
                threading.Event().wait(0.03)
                return files, 0
            finally:
                importing.release()

        with patch.object(cli.sys, "platform", "darwin"), patch.object(cli, "download_pixiv", side_effect=download), patch.object(cli, "import_photos", side_effect=save), contextlib.redirect_stdout(io.StringIO()):
            result = cli.main(["batch", *[URL + str(n) for n in range(4)], "--photos", "--browser", "none"])
        self.assertEqual(result, 0)
        self.assertCountEqual(imported, [(URL + str(n)).encode() for n in range(4)])

    def test_login_requests_run_serially_on_the_calling_thread(self):
        downloads = threading.Barrier(3)
        caller = threading.get_ident()
        requested = []

        def download(args, setup, records):
            downloads.wait(timeout=3)
            self.assertTrue(cli.recover_login(args, "pixiv.net", "Login needed"))
            return 0

        def login(args, domain, reason, proactive):
            self.assertEqual(threading.get_ident(), caller)
            requested.append(args.link)
            return True

        with patch.object(cli, "download", side_effect=download), patch.object(cli, "recover_login_interactive", side_effect=login):
            result, out, _ = self.run_batch([URL + str(n) for n in range(3)], None)
        self.assertEqual(result, 0)
        self.assertEqual(len(requested), 3)
        self.assertIn("3 downloads running in parallel", out)

    def test_cancelling_login_releases_waiting_downloads(self):
        downloads = threading.Barrier(2)

        def download(args, setup, records):
            downloads.wait(timeout=3)
            try:
                cli.recover_login(args, "pixiv.net", "Login needed")
            except KeyboardInterrupt:
                return 130
            return 0

        with patch.object(cli, "download", side_effect=download), patch.object(cli, "recover_login_interactive", side_effect=KeyboardInterrupt):
            result, out, _ = self.run_batch([URL + "1", URL + "2"], None)
        self.assertEqual(result, 130)
        self.assertIn("2 not completed", out)

    def test_empty_input_and_invalid_global_options_do_not_start_setup(self):
        with patch.object(cli, "first_run") as setup, patch.object(cli, "download_pixiv") as download, contextlib.redirect_stderr(io.StringIO()):
            for options in ([], ["--date-now"], ["--profile", "/profile"]):
                with patch.object(cli.sys, "stdin", io.StringIO("")), self.assertRaises(SystemExit):
                    cli.main(["batch", "--folder", str(self.root / "output"), "--browser", "none", *options])
            setup.assert_not_called()
            download.assert_not_called()

    def test_photos_destination_date_option_and_settings_are_preserved(self):
        accounts.set_preferences(destination={"mode": "folder", "path": str(self.root / "default")})
        before = accounts.preferences()

        def download(url, folder, cookies):
            (folder / "image.jpg").write_bytes(url.encode())

        with patch.object(cli.sys, "platform", "darwin"), patch.object(cli, "download_pixiv", side_effect=download), patch.object(cli, "import_photos", side_effect=lambda files, date: (files, 0)) as photos, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["batch", URL + "1", URL + "2", "--photos", "--date-now", "--browser", "none"]), 0)
        self.assertEqual(photos.call_count, 2)
        self.assertTrue(all(call.args[1] for call in photos.call_args_list))
        self.assertEqual(accounts.preferences(), before)
        self.assertFalse((self.root / "default").exists())

    def test_engine_failures_have_specific_guidance_without_reading_a_browser(self):
        cases = [("HTTP Error 429: Too Many Requests", "Rate limit"),
                 ("HTTP Error 403: Forbidden", "Access refused"),
                 ("SSL connection reset", "Network"),
                 ("HTTP Error 404: Not Found", "Unavailable"),
                 ("Captcha challenge", "Website check")]
        for message, label in cases:
            with self.subTest(message=message):
                def download(command, stdout, **kwargs):
                    stdout.write(message)
                    return CompletedProcess(command, 1)

                out, err = io.StringIO(), io.StringIO()
                with patch.object(cli, "find_tool", return_value="gallery-dl"), patch.object(cli, "run_process", side_effect=download), patch.object(accounts, "import_auto") as browser, patch.object(cli, "recover_login") as login, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    self.assertEqual(cli.main(["batch", "https://x.com/user/status/1", "--folder", str(self.root / "output"), "--browser", "none"]), 1)
                self.assertIn(label, err.getvalue())
                self.assertIn("Next", err.getvalue())
                browser.assert_not_called()
                login.assert_not_called()


if __name__ == "__main__":
    unittest.main()
