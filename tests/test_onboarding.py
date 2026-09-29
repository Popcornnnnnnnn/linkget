"""First-run and sign-in recovery acceptance; isolated from real browsers and Photos."""
import contextlib
from http.cookiejar import Cookie, MozillaCookieJar
import io
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import threading
import unittest
from unittest.mock import patch

from linkget import accounts, cli, session


def add_cookie(jar, domain, name="sessionid"):
    jar.set_cookie(Cookie(0, name, "test-secret", None, False, "." + domain, True,
                         True, "/", True, True, None, True, None, None, {}))


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for target, attribute, value in [(accounts, "directory", self.root / "sessions"),
                                         (accounts, "available_browsers", ["chrome", "firefox"]),
                                         (accounts, "default_browser", "firefox"),
                                         (cli.sys.stdin, "isatty", True),
                                         (cli, "find_tool", "gallery-dl")]:
            mocked = patch.object(target, attribute, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def export_and_download(self, command, **kwargs):
        if "--cookies-export" in command:
            self.exports.append(command)
            path = Path(command[command.index("--cookies-export") + 1])
            self.exports_path = path
            jar = MozillaCookieJar(str(path))
            add_cookie(jar, "bilibili.com", "SESSDATA")
            add_cookie(jar, "douyin.com")
            add_cookie(jar, "unrelated.test")
            jar.save(ignore_discard=True)
        else:
            (Path(command[command.index("--directory") + 1]) / "sample.jpg").write_bytes(b"image")
        return CompletedProcess(command, 0, "", "")

    def state(self, jar, domain):
        return session.Session("valid", "Valid") if domain in {"bilibili.com", "douyin.com"} else session.Session("missing", "Not signed in")

    def test_first_run_reads_one_browser_verifies_sites_then_downloads_and_remembers(self):
        self.exports = []
        args = ["https://x.com/user/status/123", "--folder", str(self.root / "out")]
        with patch("builtins.input", side_effect=["1", ""]) as prompt, patch.object(cli.subprocess, "run", side_effect=self.export_and_download), patch.object(accounts, "check_session", side_effect=self.state), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(args), 0)
            self.assertEqual(prompt.call_count, 2)
            self.assertIn("Firefox (default browser)", out.getvalue())
            self.assertIn("Connected", out.getvalue())
            self.assertNotIn("test-secret", out.getvalue())
            self.assertNotIn("linkget login", out.getvalue())
            self.assertEqual(len(self.exports), 1)
            self.assertEqual(self.exports[0][self.exports[0].index("--cookies-from-browser") + 1], "firefox")
            self.assertFalse(self.exports_path.exists())
            self.assertEqual(accounts.preferences()["browser"], "firefox")
            saved = accounts.path_for("douyin.com")
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("unrelated.test", saved.read_text())
            self.assertEqual(cli.main(args), 0)
            self.assertEqual(prompt.call_count, 2)
            self.assertEqual(len(self.exports), 1)

    def test_skip_does_not_read_browser_and_public_download_continues(self):
        args = ["https://x.com/user/status/123", "--folder", str(self.root / "out")]
        with patch("builtins.input", return_value="0") as prompt, patch.object(accounts, "connect_browser") as connect, patch.object(cli.subprocess, "run", side_effect=self.export_and_download), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(args), 0)
            self.assertTrue(accounts.preferences()["setup"])
            self.assertEqual(cli.main(args), 0)
            prompt.assert_called_once()
            connect.assert_not_called()

    def test_access_denial_does_not_try_other_browsers_or_save_logins(self):
        states = {domain: session.Session("unavailable", "Browser access denied") for domain in accounts.SITES.values()}
        with patch("builtins.input", side_effect=["1", ""]), patch.object(accounts, "connect_browser", return_value=states) as connect, patch.object(cli.subprocess, "run", side_effect=self.export_and_download), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["https://x.com/user/status/123", "--folder", str(self.root / "out")]), 0)
            connect.assert_called_once()
            self.assertIsNone(accounts.preferences().get("browser"))
            self.assertFalse(accounts.directory().exists())

    def test_unconfirmed_session_never_overwrites_a_working_saved_session(self):
        self.exports = []
        source = self.root / "old.txt"
        jar = MozillaCookieJar(str(source))
        add_cookie(jar, "douyin.com")
        jar.save(ignore_discard=True)
        accounts.save(source, "douyin.com")
        previous = accounts.path_for("douyin.com").read_bytes()
        with patch.object(session.subprocess, "run", side_effect=self.export_and_download), patch.object(accounts, "check_session", return_value=session.Session("unverified", "Rate limited")):
            results = accounts.connect_browser("gallery-dl", "chrome")
        self.assertEqual(results["douyin.com"].state, "unverified")
        self.assertEqual(accounts.path_for("douyin.com").read_bytes(), previous)
        self.assertFalse(accounts.path_for("bilibili.com").exists())
        self.assertFalse(self.exports_path.exists())

    def test_login_required_waits_then_resumes_same_download(self):
        accounts.set_preferences(setup=True, browser="firefox")
        attempts = []
        def run(command, **kwargs):
            attempts.append("signed-in" if "--cookies" in command else "public")
            if "--cookies" not in command:
                kwargs["stdout"].write("Login required\n")
                return CompletedProcess(command, 1)
            (Path(command[command.index("--directory") + 1]) / "sample.jpg").write_bytes(b"image")
            return CompletedProcess(command, 0)
        def connect(tool, specification, domain):
            self.assertEqual(specification, "firefox/x.com")
            path = self.root / "new.txt"
            jar = MozillaCookieJar(str(path))
            add_cookie(jar, "x.com", "auth_token")
            jar.save(ignore_discard=True)
            accounts.save(path, domain)
            return session.Session("valid", "Valid")
        with patch.object(accounts, "import_auto", return_value=(session.Session("missing", "Not signed in"), "firefox")), patch.object(accounts, "import_browser", side_effect=connect), patch.object(cli.subprocess, "run", side_effect=run), patch("builtins.input", return_value="") as prompt, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["https://x.com/user/status/123", "--folder", str(self.root / "out")]), 0)
            self.assertEqual(attempts, ["public", "signed-in"])
            prompt.assert_called_once()
            self.assertIn(session.LOGIN_URLS["x.com"], out.getvalue())
            self.assertIn("Firefox", out.getvalue())
            self.assertNotIn("linkget login", out.getvalue())
            self.assertNotIn("Retry", out.getvalue())

    def test_auth_connects_before_download_and_guides_missing_browser_login(self):
        accounts.set_preferences(setup=True, browser="firefox")
        with patch.object(accounts, "status", return_value=session.Session("missing", "Not saved")), patch.object(accounts, "import_browser", side_effect=[session.Session("missing", "Not signed in"), session.Session("valid", "Valid")]) as connect, patch("builtins.input", side_effect=["bilibili", "", "q"]) as prompt, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["auth"]), 0)
            self.assertEqual(connect.call_count, 2)
            self.assertEqual(connect.call_args.args[1], "firefox/bilibili.com")
            self.assertEqual(prompt.call_count, 3)
            self.assertIn(session.LOGIN_URLS["bilibili.com"], out.getvalue())
            self.assertIn("Connected", out.getvalue())
            self.assertNotIn("linkget login", out.getvalue())

    def test_auth_reconnects_logged_out_site_without_redundant_signin_prompt(self):
        accounts.set_preferences(setup=True, browser="chrome")
        accounts.logout("bilibili")
        def prepare(tool, spec, path, domain):
            jar = MozillaCookieJar(str(path))
            add_cookie(jar, domain, "SESSDATA")
            jar.save(ignore_discard=True)
            return session.Session("valid", "Valid")
        with patch.object(accounts, "status", return_value=session.Session("valid", "Valid")), patch.object(accounts, "prepare_session", side_effect=prepare) as connect, patch("builtins.input", side_effect=[""]) as prompt, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["auth"]), 0)
            connect.assert_called_once()
            self.assertEqual(prompt.call_count, 1)
            self.assertIn("Enter to connect bilibili", prompt.call_args_list[0].args[0])
            self.assertNotIn("Sign in", out.getvalue())
            self.assertNotIn("bilibili.com", accounts.preferences()["blocked"])
            self.assertTrue(accounts.path_for("bilibili.com").exists())

    def test_auth_noninteractive_only_queries_and_never_prompts_or_imports(self):
        with patch.object(cli.sys.stdin, "isatty", return_value=False), patch.object(accounts, "status", return_value=session.Session("missing", "Not saved")), patch.object(accounts, "import_browser") as connect, patch("builtins.input") as prompt, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["auth"]), 0)
            connect.assert_not_called()
            prompt.assert_not_called()

    def test_network_check_failure_keeps_login_and_never_offers_default_reconnect(self):
        accounts.set_preferences(setup=True, browser="chrome")
        path = accounts.path_for("tiktok.com")
        path.parent.mkdir()
        jar = MozillaCookieJar(str(path))
        add_cookie(jar, "tiktok.com")
        jar.save(ignore_discard=True)
        original = path.read_bytes()
        def state(domain):
            return session.Session("unverified", "Network check failed") if domain == "tiktok.com" else session.Session("valid", "Valid")
        with patch.object(accounts, "status", side_effect=state), patch.object(accounts, "import_browser") as connect, patch("builtins.input", return_value="") as prompt, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["auth"]), 0)
            self.assertIn("Network error · login unchanged", out.getvalue())
            self.assertIn("Press Enter to exit", prompt.call_args.args[0])
            connect.assert_not_called()
            self.assertEqual(path.read_bytes(), original)

    def test_auth_checks_websites_concurrently_and_prints_results_in_site_order(self):
        # A sequential implementation cannot reach this barrier before it times out.
        ready = threading.Barrier(len(accounts.SITES), timeout=3)
        def check(domain):
            ready.wait()
            return session.Session("valid", "Valid")
        with patch.object(accounts, "status", side_effect=check), patch.object(accounts, "import_browser") as connect, contextlib.redirect_stdout(io.StringIO()) as out:
            states = cli.show_logins()
        self.assertEqual(list(states), list(accounts.SITES))
        self.assertEqual(set(states.values()), {"valid"})
        lines = [line.split()[0] for line in out.getvalue().splitlines() if "Connected" in line]
        self.assertEqual(lines, list(accounts.SITES))
        self.assertIn("Checking", out.getvalue())
        connect.assert_not_called()

    def test_help_and_noninteractive_download_never_start_setup(self):
        with patch("builtins.input") as prompt, patch.object(accounts, "connect_browser") as connect, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["help"]), 0)
            self.assertNotIn("login SITE", out.getvalue())
            with patch.object(cli.sys.stdin, "isatty", return_value=False), patch.object(cli.subprocess, "run", side_effect=self.export_and_download):
                self.assertEqual(cli.main(["https://x.com/user/status/123", "--folder", str(self.root / "out")]), 0)
            prompt.assert_not_called()
            connect.assert_not_called()
            self.assertFalse(accounts.preferences())


if __name__ == "__main__":
    unittest.main()
