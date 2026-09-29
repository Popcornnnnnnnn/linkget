"""Expired snapshots recover without reconnecting unrelated accounts."""
import contextlib
from http.cookiejar import Cookie, MozillaCookieJar
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, cli, session


def jar_for(domains, value="new-session"):
    jar = MozillaCookieJar()
    for domain in domains:
        jar.set_cookie(Cookie(0, session.AUTH_COOKIE[domain], value, None, False,
                             "." + domain, True, True, "/", True, True,
                             None, True, None, None, {}))
    return jar


class RefreshTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for target, name, value in [(accounts, "directory", self.root / "sessions"),
                                     (cli.sys.stdin, "isatty", True), (cli, "find_tool", "gallery-dl")]:
            mock = patch.object(target, name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)
        accounts.set_preferences(setup=True, browser="chrome", profile="Profile 2")

    def saved(self, domain):
        path = accounts.path_for(domain)
        path.parent.mkdir(exist_ok=True)
        jar_for([domain], "old-session").save(str(path), ignore_discard=True)
        return path

    def export(self, tool, specification, path):
        jar = jar_for(accounts.SITES.values())
        jar.filename = str(path)
        jar.save(ignore_discard=True)
        return jar, None

    def test_all_channels_refresh_rejected_snapshots_without_signin_prompts(self):
        for site, domain in accounts.SITES.items():
            with self.subTest(site=site):
                path = self.saved(domain)
                def state(checked):
                    return session.Session("invalid", "Session rejected") if checked == domain else session.Session("valid", "Valid")
                with patch.object(accounts, "status", side_effect=state), patch.object(accounts, "export_browser", side_effect=self.export) as export, patch.object(accounts, "check_session", return_value=session.Session("valid", "Valid")), patch("builtins.input") as prompt, contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(cli.main(["auth"]), 0)
                    prompt.assert_not_called()
                    export.assert_called_once()
                    self.assertEqual(export.call_args.args[1], "chrome:Profile 2")
                    self.assertFalse(export.call_args.args[2].parent.exists())
                    self.assertIn("Connected", out.getvalue())
                    self.assertNotIn("Sign in", out.getvalue())
                    self.assertNotIn("Session rejected", out.getvalue())
                self.assertIn("new-session", path.read_text())
                self.assertNotIn("old-session", path.read_text())
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_batch_refresh_reads_same_profile_once_and_keeps_other_sites_untouched(self):
        old_x = self.saved("x.com").read_bytes()
        accounts.logout("douyin")
        with patch.object(accounts, "export_browser", side_effect=self.export) as export, patch.object(accounts, "check_session", return_value=session.Session("valid", "Valid")):
            results = accounts.refresh_saved("gallery-dl", ["youtube.com", "weibo.com", "douyin.com"])
        self.assertEqual(set(results), {"youtube.com", "weibo.com"})
        export.assert_called_once()
        self.assertEqual(accounts.path_for("x.com").read_bytes(), old_x)
        self.assertFalse(accounts.path_for("douyin.com").exists())
        self.assertIn("douyin.com", accounts.preferences()["blocked"])
        for domain in results:
            jar, _ = accounts.read_session(accounts.path_for(domain), domain)
            self.assertEqual({c.domain for c in jar}, {"." + domain})

    def test_refresh_preserves_site_profiles_and_explicit_browser_override(self):
        accounts.remember_source("youtube.com", "firefox/youtube.com:/tmp/test-profile")
        accounts.remember_source("weibo.com", "chrome/weibo.com:Profile 3")
        with patch.object(accounts, "connect_browser", return_value={}) as connect:
            accounts.refresh_saved("gallery-dl", ["youtube.com", "weibo.com"])
            self.assertEqual([call.args[1:] for call in connect.call_args_list], [("firefox", "/tmp/test-profile"), ("chrome", "Profile 3")])
            connect.reset_mock()
            accounts.refresh_saved("gallery-dl", ["youtube.com", "weibo.com"], "edge", "Default")
            connect.assert_called_once_with("gallery-dl", "edge", "Default", domains=["youtube.com", "weibo.com"])

    def test_failed_refresh_never_overwrites_snapshot_and_does_not_loop(self):
        path = self.saved("youtube.com")
        original = path.read_bytes()
        for failure in [session.Session("invalid", "Session rejected"), session.Session("missing", "Not signed in"), session.Session("unverified", "Network check failed"), session.Session("unavailable", "Browser access denied")]:
            with self.subTest(failure=failure), patch.object(accounts, "status", return_value=session.Session("invalid", "Expired")), patch.object(accounts, "export_browser", return_value=(None, failure)) as export, patch("builtins.input", return_value="q"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["auth"]), 0)
                export.assert_called_once()
                self.assertEqual(path.read_bytes(), original)

    def test_unknown_or_noninteractive_states_never_read_browser(self):
        cases = [(True, [], "unverified"), (True, [], "attention"), (True, [], "missing"),
                 (True, [], "unavailable"), (False, [], "invalid"), (True, ["--browser", "none"], "invalid")]
        for tty, options, state in cases:
            with self.subTest(tty=tty, options=options, state=state), patch.object(cli.sys.stdin, "isatty", return_value=tty), patch.object(accounts, "status", return_value=session.Session(state, "Check failed")), patch.object(accounts, "export_browser") as export, patch("builtins.input", return_value="q"), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(["auth"] + options), 0)
                export.assert_not_called()


if __name__ == "__main__":
    unittest.main()
