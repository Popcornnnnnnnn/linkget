import contextlib
from http.cookiejar import Cookie, MozillaCookieJar
import io
import os
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import time
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from linkget import accounts, cli, session


def cookie(name, domain, expires=None):
    return Cookie(0, name, "synthetic-secret", None, False, domain, True,
                  domain.startswith("."), "/", True, True, expires,
                  expires is None, None, None, {})


class SessionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        mocked = patch.object(accounts, "directory", return_value=Path(temporary.name) / "sessions")
        mocked.start()
        self.addCleanup(mocked.stop)
    def test_valid_requires_an_authenticated_response(self):
        for domain, data in [
            ("instagram.com", {"status": "ok", "form_data": {"username": "example"}}),
            ("x.com", {"data": {"viewer": {"user_results": {"result": {"__typename": "User", "rest_id": "123"}}}}}),
            ("bilibili.com", {"code": 0, "data": {"isLogin": True, "mid": 1}}),
            ("tiktok.com", {"message": "success", "data": {"user_id": 1}}),
            ("tiktok.com", {"message": "success", "data": {"user_id_str": "1"}}),
            ("douyin.com", {"message": "success", "data": {"user_id": 1}}),
        ]:
            self.assertEqual(session.classify_response(domain, 200, data).state, "valid")
            self.assertEqual(session.classify_response(domain, 200, {}).state, "unverified")
            self.assertEqual(session.classify_response(domain, 503, data).state, "unverified")

    def test_rejection_is_distinct_from_blocking_or_rate_limiting(self):
        cases = [
            ("instagram.com", 400, {"message": "login_required"}, "invalid"),
            ("instagram.com", 400, {"message": "challenge_required"}, "attention"),
            ("x.com", 401, {"errors": [{"code": 89}]}, "invalid"),
            ("x.com", 403, {"errors": [{"code": 326}]}, "attention"),
            ("x.com", 403, {"errors": [{"code": 353}]}, "unverified"),
            ("bilibili.com", 200, {"code": -101, "data": {"isLogin": False}}, "invalid"),
            ("bilibili.com", 200, {"code": -352}, "unverified"),
            ("douyin.com", 200, {"message": "error", "data": {"error_code": 13}}, "invalid"),
            ("douyin.com", 200, {"message": "error", "data": {"error_code": 16}}, "unverified"),
            ("tiktok.com", 200, {"message": "error", "data": {"error_code": 13}}, "invalid"),
            ("tiktok.com", 200, {"message": "success", "data": {}}, "unverified"),
            ("tiktok.com", 200, {"message": "error", "data": {"user_id": 1}}, "unverified"),
            ("tiktok.com", 403, {"message": "error", "data": {"error_code": 13}}, "unverified"),
        ]
        for domain, code, data, expected in cases:
            self.assertEqual(session.classify_response(domain, code, data).state, expected)
        for domain in session.ENDPOINTS:
            for code in (403, 429):
                self.assertEqual(session.classify_response(domain, code, None).state, "unverified")

    def test_public_profiles_and_malformed_viewer_data_do_not_prove_login(self):
        for domain, data in [
            ("instagram.com", {"status": "ok", "user": {"pk": 1}}),
            ("instagram.com", {"status": "ok", "form_data": {}}),
            ("x.com", {"screen_name": "example"}),
            ("x.com", {"data": {"user": {"result": {"__typename": "User", "rest_id": "1"}}}}),
            ("x.com", {"data": {"viewer": []}}),
            ("x.com", {"data": {"viewer": {"user_results": {"result": {"__typename": "UserUnavailable", "rest_id": "1"}}}}}),
        ]:
            self.assertEqual(session.classify_response(domain, 200, data).state, "unverified")

    def test_instagram_login_redirect_is_rejection_but_home_redirect_is_not(self):
        jar = MozillaCookieJar()
        jar.set_cookie(cookie("sessionid", ".instagram.com"))
        for location, expected in [
            ("https://www.instagram.com/accounts/login/?next=/", "invalid"),
            ("/accounts/login/", "invalid"),
            ("https://www.instagram.com/", "unverified"),
            ("https://other.test/accounts/login/", "unverified"),
        ]:
            with patch.object(session, "build_opener") as opener:
                opener.return_value.open.side_effect = HTTPError(
                    session.ENDPOINTS["instagram.com"], 302, "Redirect",
                    {"Location": location}, io.BytesIO(b""))
                self.assertEqual(session.check_session(jar, "instagram.com").state, expected)

    def test_missing_and_expired_cookies_need_no_network_request(self):
        jar = MozillaCookieJar()
        with patch.object(session, "build_opener") as opener:
            self.assertEqual(session.check_session(jar, "instagram.com").state, "missing")
            jar.set_cookie(cookie("sessionid", ".instagram.com", int(time.time()) - 60))
            self.assertEqual(session.check_session(jar, "instagram.com").state, "invalid")
            opener.assert_not_called()

    def test_timeout_never_claims_cookie_is_invalid(self):
        jar = MozillaCookieJar()
        jar.set_cookie(cookie("SESSDATA", ".bilibili.com"))
        with patch.object(session, "build_opener") as opener:
            opener.return_value.open.side_effect = TimeoutError()
            result = session.check_session(jar, "bilibili.com")
        self.assertEqual(result.state, "unverified")

    def test_export_filters_domains_and_reuses_private_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cookies.txt"

            def export(command, **kwargs):
                jar = MozillaCookieJar(str(path))
                jar.set_cookie(cookie("sessionid", ".instagram.com"))
                jar.set_cookie(cookie("unrelated", ".another-site.test"))
                jar.save(ignore_discard=True, ignore_expires=True)
                return CompletedProcess(command, 0, "", "")

            def check(jar, domain):
                self.assertEqual({c.domain for c in jar}, {".instagram.com"})
                return session.Session("valid", "Valid")

            with patch.object(session.subprocess, "run", side_effect=export), patch.object(session, "check_session", side_effect=check):
                self.assertEqual(session.prepare_session("gallery-dl", "chrome/instagram.com", path, "instagram.com").state, "valid")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("another-site.test", path.read_text())

    def test_decryption_failure_is_not_reported_as_logged_out(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cookies.txt"
            with patch.object(session.subprocess, "run", return_value=CompletedProcess([], 0, "", "2 could not be decrypted")):
                result = session.prepare_session("gallery-dl", "chrome/x.com", path, "x.com")
            self.assertEqual(result.state, "unavailable")

    def test_cli_guidance_and_cookie_cleanup_on_all_exits(self):
        for state, exit_kind in [("valid", "success"), ("unverified", "stopped"),
                                 ("missing", "stopped"), ("invalid", "stopped"),
                                 ("attention", "stopped"), ("unavailable", "stopped"),
                                 ("valid", "failed"), ("valid", "cancelled")]:
            with self.subTest(state=state, exit=exit_kind), tempfile.TemporaryDirectory() as directory:
                captured = []
                out, err = io.StringIO(), io.StringIO()

                def prepare(tool, spec, path, domain):
                    jar = MozillaCookieJar(str(path))
                    jar.set_cookie(cookie("sessionid", ".instagram.com"))
                    jar.save(ignore_discard=True)
                    captured.append(path)
                    return session.Session(state, state)

                def download(command, **kwargs):
                    if state == "missing":
                        self.assertNotIn("--cookies", command)
                    else:
                        path = Path(command[command.index("--cookies") + 1])
                        self.assertEqual(path, captured[0])
                        self.assertIn("synthetic-secret", path.read_text())
                    self.assertNotIn("--cookies-from-browser", command)
                    if exit_kind == "cancelled":
                        raise KeyboardInterrupt()
                    media = Path(command[command.index("--directory") + 1])
                    (media / "sample.jpg").write_bytes(b"image")
                    return CompletedProcess(command, int(exit_kind == "failed"))

                with patch.object(tempfile, "tempdir", directory), patch.object(cli, "find_tool", return_value="gallery-dl"), patch.object(cli, "prepare_session", side_effect=prepare), patch.object(cli.subprocess, "run", side_effect=download) as run, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = cli.main(["https://www.instagram.com/reel/example/", "--browser", "chrome", "--folder", str(Path(directory) / "output")])
                self.assertEqual(code, {"success": 0, "stopped": 1, "failed": 1, "cancelled": 130}[exit_kind])
                self.assertFalse(captured[0].parent.exists())
                self.assertNotIn("synthetic-secret", out.getvalue() + err.getvalue())
                if exit_kind == "stopped":
                    run.assert_not_called()
                    if state != "unavailable":
                        self.assertIn("interactive terminal", err.getvalue())
                if state == "invalid":
                    self.assertIn(session.LOGIN_URLS["instagram.com"], err.getvalue())
                if state == "unverified":
                    self.assertNotIn("Unverified", out.getvalue())


if __name__ == "__main__":
    unittest.main()
