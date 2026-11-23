"""Offline tests for linkget update notifications and `linkget upgrade`."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from linkget import cli, update


class _FakeTTYStream(io.StringIO):
    def __init__(self, tty: bool = True):
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


class UpdateTests(unittest.TestCase):
    def test_semver_comparison(self):
        self.assertTrue(update.is_newer_version("0.3.1", "0.3.0"))
        self.assertTrue(update.is_newer_version("v0.4.0", "0.3.1"))
        self.assertTrue(update.is_newer_version("0.3.1", "0.3.1.dev1"))
        self.assertTrue(update.is_newer_version("0.3.1.dev2", "0.3.1.dev1"))
        self.assertFalse(update.is_newer_version("0.3.1", "0.3.1"))
        self.assertFalse(update.is_newer_version("v0.3.0", "0.3.1"))
        self.assertFalse(update.is_newer_version("0.3.1.dev1", "0.3.1"))

    def test_should_check_updates_skip_rules(self):
        tty_err = _FakeTTYStream(True)
        non_tty_err = _FakeTTYStream(False)
        clean_env = {}

        self.assertTrue(update.should_check_updates(["sites"], env=clean_env, stderr=tty_err))
        self.assertFalse(update.should_check_updates(["sites"], env=clean_env, stderr=non_tty_err))
        self.assertFalse(update.should_check_updates(["sites"], env={"LINKGET_NO_UPDATE_CHECK": "1"}, stderr=tty_err))
        self.assertFalse(update.should_check_updates(["sites"], env={"CI": "true"}, stderr=tty_err))
        self.assertFalse(update.should_check_updates(["sites", "--json"], env=clean_env, stderr=tty_err))
        self.assertFalse(update.should_check_updates(["sites", "--non-interactive"], env=clean_env, stderr=tty_err))
        self.assertFalse(update.should_check_updates(["upgrade"], env=clean_env, stderr=tty_err))
        self.assertFalse(update.should_check_updates(["version"], env=clean_env, stderr=tty_err))
        self.assertFalse(update.should_check_updates(["help"], env=clean_env, stderr=tty_err))
        self.assertFalse(update.should_check_updates(["read", "show", "task-1"], env=clean_env, stderr=tty_err))

    def test_fresh_and_stale_cache_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "update-check.json"
            now = 1_700_000_000.0

            # Missing cache spawns background refresh without blocking or printing
            err = _FakeTTYStream(True)
            spawned = []
            notice = update.maybe_notify_update(
                "0.3.1", ["sites"], cache_path=cache_file, env={}, stderr=err, spawn_refresh=spawned.append, now=now
            )
            self.assertIsNone(notice)
            self.assertEqual(err.getvalue(), "")
            self.assertEqual(spawned, [cache_file])

            # Fresh cache with newer version prints notice and does not spawn
            cache_file.write_text(json.dumps({"checked_at": now - 60, "latest_version": "0.3.2"}))
            err = _FakeTTYStream(True)
            spawned = []
            notice = update.maybe_notify_update(
                "0.3.1", ["sites"], cache_path=cache_file, env={}, stderr=err, spawn_refresh=spawned.append, now=now
            )
            self.assertIn("linkget v0.3.2", err.getvalue())
            self.assertIn("linkget upgrade", err.getvalue())
            self.assertEqual(spawned, [])

            # Fresh cache with current version prints nothing and does not spawn
            cache_file.write_text(json.dumps({"checked_at": now - 60, "latest_version": "0.3.1"}))
            err = _FakeTTYStream(True)
            spawned = []
            notice = update.maybe_notify_update(
                "0.3.1", ["sites"], cache_path=cache_file, env={}, stderr=err, spawn_refresh=spawned.append, now=now
            )
            self.assertIsNone(notice)
            self.assertEqual(err.getvalue(), "")
            self.assertEqual(spawned, [])

            # Stale cache spawns refresh and still prints cached newer version
            cache_file.write_text(
                json.dumps({"checked_at": now - update.CHECK_INTERVAL_SECONDS - 10, "latest_version": "0.3.3"})
            )
            err = _FakeTTYStream(True)
            spawned = []
            notice = update.maybe_notify_update(
                "0.3.1", ["sites"], cache_path=cache_file, env={}, stderr=err, spawn_refresh=spawned.append, now=now
            )
            self.assertIn("linkget v0.3.3", err.getvalue())
            self.assertEqual(spawned, [cache_file])

    def test_upgrade_check_json_and_mocked_homebrew_execution(self):
        release = update.ReleaseInfo(
            tag_name="v0.3.2",
            version="0.3.2",
            release_url="https://github.com/Popcornnnnnnnn/linkget/releases/tag/v0.3.2",
            wheel_url="https://github.com/Popcornnnnnnnn/linkget/releases/download/v0.3.2/linkget-0.3.2-py3-none-any.whl",
        )
        with tempfile.TemporaryDirectory() as tmp:
            cache_file = Path(tmp) / "update-check.json"
            out = io.StringIO()
            err = io.StringIO()
            code = update.run_upgrade(
                ["--check", "--json"],
                current_version="0.3.1",
                cache_path=cache_file,
                fetch_fn=lambda timeout=10.0: release,
                stdout=out,
                stderr=err,
                file_path="/opt/homebrew/Cellar/linkget/0.3.1/libexec/linkget/update.py",
            )
            self.assertEqual(code, 0)
            payload = json.loads(out.getvalue())
            self.assertEqual(payload["current"], "0.3.1")
            self.assertEqual(payload["latest"], "0.3.2")
            self.assertTrue(payload["update_available"])
            self.assertEqual(payload["install_method"], "homebrew")
            self.assertTrue(cache_file.is_file())

            # Mocked Homebrew upgrade runs brew update and brew upgrade
            ran = []
            def fake_run(cmd, check=False):
                ran.append(cmd)
                return SimpleNamespace(returncode=0)

            out = io.StringIO()
            code = update.run_upgrade(
                [],
                current_version="0.3.1",
                cache_path=cache_file,
                fetch_fn=lambda timeout=10.0: release,
                runner=fake_run,
                stdout=out,
                stderr=err,
                file_path="/opt/homebrew/Cellar/linkget/0.3.1/libexec/linkget/update.py",
            )
            self.assertEqual(code, 0)
            self.assertEqual(
                ran,
                [["brew", "update"], ["brew", "upgrade", "Popcornnnnnnnn/tap/linkget"]],
            )

            # Up to date short-circuits without running brew
            ran = []
            out = io.StringIO()
            code = update.run_upgrade(
                [],
                current_version="0.3.2",
                cache_path=cache_file,
                fetch_fn=lambda timeout=10.0: release,
                runner=fake_run,
                stdout=out,
                stderr=err,
                file_path="/opt/homebrew/Cellar/linkget/0.3.2/libexec/linkget/update.py",
            )
            self.assertEqual(code, 0)
            self.assertEqual(ran, [])
            self.assertIn("already up to date", out.getvalue())

    def test_cli_upgrade_and_help_upgrade(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["upgrade", "--help"]), 0)
        self.assertIn("linkget upgrade", out.getvalue())
        self.assertIn("--check", out.getvalue())

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["help", "upgrade"]), 0)
        self.assertIn("linkget upgrade", out.getvalue())


if __name__ == "__main__":
    unittest.main()
