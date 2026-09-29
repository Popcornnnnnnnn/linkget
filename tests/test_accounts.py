"""Authorization storage and login guidance use synthetic cookies only."""

import contextlib
from http.cookiejar import Cookie, MozillaCookieJar
import io
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import time
import unittest
from unittest.mock import patch

from linkget import accounts, cli, session

discover_browsers = accounts.available_browsers


def write_cookies(path, domain="bilibili.com", expired=False, visitor=False):
    jar = MozillaCookieJar(str(path))
    jar.set_cookie(Cookie(0, "visitor" if visitor else session.AUTH_COOKIE[domain], "test-secret", None, False,
                         "." + domain, True, True, "/", True, True,
                         int(time.time()) + (-30 if expired else 3600), False, None, None, {}))
    jar.save(ignore_discard=True, ignore_expires=True)


class AccountTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        directory = patch.object(accounts, "directory", return_value=self.root / "sessions")
        directory.start()
        self.addCleanup(directory.stop)
        discovery = patch.object(accounts, "available_browsers", return_value=[])
        discovery.start()
        self.addCleanup(discovery.stop)

    def test_saved_authorization_permissions_expiry_and_logout(self):
        source = self.root / "input.txt"
        write_cookies(source)
        accounts.save(source, "bilibili.com")
        saved = accounts.path_for("bilibili.com")
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertEqual(saved.parent.stat().st_mode & 0o777, 0o700)
        self.assertTrue(accounts.copy_saved("bilibili.com", self.root / "copy.txt"))
        write_cookies(source, expired=True)
        accounts.save(source, "bilibili.com")
        self.assertEqual(saved.read_bytes(), (self.root / "copy.txt").read_bytes())
        write_cookies(saved, expired=True)
        self.assertFalse(accounts.copy_saved("bilibili.com", self.root / "expired.txt"))
        accounts.logout("bilibili")
        self.assertFalse(saved.exists())
        write_cookies(source, visitor=True)
        self.assertEqual(accounts.save(source, "bilibili.com").state, "missing")
        self.assertFalse(saved.exists())

    def test_login_status_and_logout_commands(self):
        def prepare(tool, spec, path, domain):
            write_cookies(path, domain)
            return session.Session("valid", "Valid")
        with patch.object(accounts, "prepare_session", side_effect=prepare), patch.object(cli, "find_tool", return_value="gallery-dl"), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(accounts.import_browser("gallery-dl", "chrome/bilibili.com", "bilibili.com").state, "valid")
            self.assertTrue(accounts.path_for("bilibili.com").is_file())
        with patch.object(accounts, "check_session", return_value=session.Session("valid", "Valid")), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["auth"]), 0)
            for site in accounts.SITES:
                self.assertIn(site, out.getvalue())
            self.assertIn("Connected", out.getvalue())
            self.assertNotIn("test-secret", out.getvalue())
            self.assertEqual(cli.main(["logout", "all"]), 0)
            self.assertFalse(accounts.path_for("bilibili.com").exists())

    def test_doctor_only_checks_tools_without_website_or_browser_access(self):
        with patch.object(accounts, "status") as check, patch.object(cli, "find_tool", return_value="tool"), patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "1.0\n", "")), patch.object(cli, "prepare_session") as browser, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["doctor"]), 0)
            text = out.getvalue()
            check.assert_not_called()
            browser.assert_not_called()
            for expected in ("Tools", "gallery-dl", "yt-dlp", "ffmpeg", "ffprobe"):
                self.assertIn(expected, text)
            for removed in ("Logins", "Connected", "instagram", "bilibili", "Browser", "Photos"):
                self.assertNotIn(removed, text)

    def test_default_public_success_never_reads_browser_or_saved_login(self):
        def download(url, folder, cookies):
            self.assertIsNone(cookies)
            (folder / "sample.jpg").write_bytes(b"image")
        with patch.object(cli, "download_douyin", side_effect=download), patch.object(cli, "prepare_session") as browser, patch.object(accounts, "copy_saved") as saved, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["https://www.douyin.com/note/123", "--folder", str(self.root / "out")]), 0)
            browser.assert_not_called()
            saved.assert_not_called()
            for field in ("Browser", "Session", "Access", "Login", "Cookies"):
                self.assertNotIn(field, out.getvalue())

    def test_saved_login_only_retried_for_explicit_auth_failure(self):
        source = self.root / "input.txt"
        write_cookies(source, "instagram.com")
        accounts.save(source, "instagram.com")
        attempts = []

        def download(command, **kwargs):
            if "--cookies" not in command:
                attempts.append("public")
                kwargs["stdout"].write("Login required\n")
                return CompletedProcess(command, 1)
            attempts.append("saved")
            (Path(command[command.index("--directory") + 1]) / "sample.jpg").write_bytes(b"image")
            snapshot = Path(command[command.index("--cookies") + 1])
            self.assertTrue(snapshot.exists())
            self.snapshot = snapshot
            return CompletedProcess(command, 0)

        with patch.object(cli, "find_tool", return_value="gallery-dl"), patch.object(cli.subprocess, "run", side_effect=download), patch.object(cli, "prepare_session") as browser, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["https://www.instagram.com/p/abc/", "--folder", str(self.root / "out")]), 0)
            self.assertEqual(attempts, ["public", "saved"])
            self.assertFalse(self.snapshot.exists())
            self.assertTrue(accounts.path_for("instagram.com").exists())
            browser.assert_not_called()
            self.assertNotIn("Login", out.getvalue())
        for message in ("HTTP Error 403: Forbidden", "HTTP Error 429", "Connection timed out", "No verified watermark-free source"):
            self.assertFalse(cli.needs_login(message))
        for message in ("'cookies' needed to access this post", "Login required", "Please log in", "Account credentials required"):
            self.assertTrue(cli.needs_login(message))

    def test_auth_failure_without_saved_login_prints_link_and_full_commands(self):
        def download(command, **kwargs):
            kwargs["stdout"].write("Account credentials required\n")
            return CompletedProcess(command, 1)
        with patch.object(tempfile, "tempdir", str(self.root)), patch.object(cli, "find_tool", return_value="gallery-dl"), patch.object(cli.subprocess, "run", side_effect=download), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["https://x.com/user/status/123", "--folder", str(self.root / "out")]), 1)
            self.assertIn("https://x.com/i/flow/login", err.getvalue())
            self.assertNotIn("linkget login", err.getvalue())
            self.assertIn("interactive terminal", err.getvalue())

    def test_bilibili_hd_restriction_is_checked_before_download(self):
        info = {"formats": [{"height": 480, "quality": 32}]}
        message = "[BiliBili] Format(s) 1080P 高码率, 1080P 高清, 720P 准高清 are missing; you have to become a premium member to download them."
        output = message + "\n" + json.dumps(info) + "\n"
        with patch.object(tempfile, "tempdir", str(self.root)), patch.object(cli, "find_tool", return_value="yt-dlp"), patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, output, "")) as run, patch.object(cli, "import_photos") as photos, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["https://www.bilibili.com/video/BV123", "--folder", str(self.root / "out")]), 1)
            self.assertEqual(run.call_count, 1)
            photos.assert_not_called()
            self.assertIn("1080p", err.getvalue())
            self.assertIn("https://passport.bilibili.com/login", err.getvalue())
            self.assertNotIn("linkget login", err.getvalue())
            self.assertFalse(list(self.root.rglob("video-info.json")))
        self.assertIsNone(cli.restricted_bilibili_quality(info, ""))  # source may only be 480p
        self.assertIsNone(cli.restricted_bilibili_quality(info, "Format(s) 1080P 高码率, 4K 超清 are missing"))
        self.assertIsNone(cli.restricted_bilibili_quality({"formats": [{"height": 1080}]}, message))

    def test_bilibili_reuses_checked_metadata_and_always_deletes_it(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if "--dump-single-json" in command:
                return CompletedProcess(command, 0, '[BiliBili] Preparing\n{"formats":[{"height":720}]}\n', "")
            metadata = Path(command[command.index("--load-info-json") + 1])
            self.assertTrue(metadata.exists())
            self.metadata = metadata
            template = Path(command[command.index("-o") + 1])
            (template.parent / "sample.mp4").write_bytes(b"video")
            return CompletedProcess(command, 0)
        with patch.object(cli, "find_tool", return_value="yt-dlp"), patch.object(cli.subprocess, "run", side_effect=run), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["https://www.bilibili.com/video/BV123", "--folder", str(self.root / "out")]), 0)
            self.assertEqual(len(calls), 2)
            self.assertFalse(self.metadata.exists())

    def test_bilibili_uses_saved_login_before_selecting_quality(self):
        source = self.root / "input.txt"
        write_cookies(source)
        accounts.save(source, "bilibili.com")
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if "--dump-single-json" in command:
                if "--cookies" not in command:
                    return CompletedProcess(command, 0, 'Format(s) 1080P 高清 are missing\n{"formats":[{"height":480,"quality":32}]}\n', "")
                self.snapshot = Path(command[command.index("--cookies") + 1])
                self.assertTrue(self.snapshot.exists())
                return CompletedProcess(command, 0, '{"formats":[{"height":1080,"quality":80}]}\n', "")
            (Path(command[command.index("-o") + 1]).parent / "sample.mp4").write_bytes(b"video")
            return CompletedProcess(command, 0)
        with patch.object(cli, "find_tool", return_value="yt-dlp"), patch.object(cli.subprocess, "run", side_effect=run), patch.object(cli, "prepare_session") as browser, contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main(["https://www.bilibili.com/video/BV123", "--folder", str(self.root / "out")]), 0)
            self.assertEqual(len(calls), 2)
            self.assertIn("--cookies", calls[0])
            self.assertNotIn("Login", out.getvalue() + err.getvalue())
            self.assertFalse(self.snapshot.exists())
            browser.assert_not_called()

    def test_common_browser_discovery_and_auto_import_skip_unusable_logins(self):
        for relative in ("Library/Application Support/Google/Chrome", "Library/Application Support/Firefox",
                         "Library/Application Support/Microsoft Edge", "Library/Safari"):
            (self.root / relative).mkdir(parents=True)
        with patch.object(Path, "home", return_value=self.root), patch.object(accounts.sys, "platform", "darwin"):
            self.assertEqual(discover_browsers(), ["chrome", "firefox", "edge", "safari"])
        accounts.set_preferences(browser="edge")
        with patch.object(accounts, "import_browser", return_value=session.Session("valid", "Valid")) as load:
            state, browser = accounts.import_auto("gallery-dl", "bilibili.com")
            self.assertEqual((state.state, browser), ("valid", "edge"))
            load.assert_called_once_with("gallery-dl", "edge/bilibili.com", "bilibili.com")
        with patch.object(accounts, "import_browser", return_value=session.Session("unavailable", "Read denied")) as load:
            state, browser = accounts.import_auto("gallery-dl", "douyin.com")
            self.assertEqual(state.state, "unavailable")
            load.assert_called_once()  # Never try another browser behind the user's back.
        accounts.logout("douyin")
        with patch.object(accounts, "import_browser") as load:
            self.assertEqual(accounts.import_auto("gallery-dl", "douyin.com")[0].state, "missing")
            load.assert_not_called()

    def test_rejected_saved_login_refreshes_browser_once_and_cleans_snapshot(self):
        source = self.root / "input.txt"
        write_cookies(source, "instagram.com")
        accounts.save(source, "instagram.com")
        attempts = []
        def download(command, **kwargs):
            if "--cookies" not in command:
                attempts.append("public")
            else:
                self.snapshot = Path(command[command.index("--cookies") + 1])
                attempts.append("fresh" if "fresh-secret" in self.snapshot.read_text() else "saved")
            if attempts[-1] != "fresh":
                kwargs["stdout"].write("Login required\n")
                return CompletedProcess(command, 1)
            (Path(command[command.index("--directory") + 1]) / "sample.jpg").write_bytes(b"image")
            return CompletedProcess(command, 0)
        accounts.set_preferences(setup=True, browser="firefox")
        def refresh(tool, domain):
            source.write_text(source.read_text().replace("test-secret", "fresh-secret"))
            accounts.save(source, domain)
            return session.Session("valid", "Valid"), "firefox"
        with patch.object(cli.sys.stdin, "isatty", return_value=True), patch.object(cli, "find_tool", return_value="gallery-dl"), patch.object(cli.subprocess, "run", side_effect=download), patch.object(accounts, "import_auto", side_effect=refresh) as browser, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["https://www.instagram.com/p/abc/", "--folder", str(self.root / "out")]), 0)
            self.assertEqual(attempts, ["public", "saved", "fresh"])
            self.assertEqual(browser.call_count, 1)
            self.assertFalse(self.snapshot.exists())
            self.assertIn("fresh-secret", accounts.path_for("instagram.com").read_text())
            self.assertNotIn("secret", out.getvalue())
            self.assertNotIn("Login", out.getvalue())

    def test_removed_login_command_does_not_read_browser(self):
        with patch.object(accounts, "import_browser") as load, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                cli.main(["login", "douyin"])
            self.assertEqual(error.exception.code, 2)
            load.assert_not_called()


if __name__ == "__main__":
    unittest.main()
