"""Compatibility reports use generated metadata and never request websites."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import socket
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, photos

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/check_compatibility.py"
spec = importlib.util.spec_from_file_location("compatibility_command", SCRIPT)
compatibility = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compatibility)


class CompatibilityTests(unittest.TestCase):
    def test_default_checks_are_offline_and_report_platform_unchecked(self):
        with patch.object(compatibility.subprocess, "run", side_effect=AssertionError("process")), \
                patch("socket.create_connection", side_effect=AssertionError("network")), \
                patch.object(accounts, "directory", side_effect=AssertionError("accounts")), \
                patch.object(photos, "library_directory", side_effect=AssertionError("Photos")):
            report = compatibility.check()
        self.assertEqual(report["mode"], "offline")
        self.assertEqual(report["platform"]["status"], "not_checked")
        self.assertIn(report["status"], {"passed", "partial"})
        self.assertEqual(len(report["fixtures"]), 7)
        self.assertTrue(all(row["status"] == "passed" for row in report["fixtures"]))
        self.assertFalse(report["environment"]["engine_probe_requested"])

    def test_optional_probes_are_bounded_and_never_emit_raw_output(self):
        completed = subprocess.CompletedProcess([], 0, "https://example.test/?token=PRIVATE\n", "Authorization: PRIVATE")
        with patch.object(compatibility.shutil, "which", return_value="/trusted/engine"), \
                patch.object(compatibility.subprocess, "run", return_value=completed) as run:
            report = compatibility.environment(True)
        self.assertEqual(run.call_count, 4)
        for call in run.call_args_list:
            self.assertEqual(call.kwargs["timeout"], 8)
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
            self.assertNotIn("shell", call.kwargs)
        self.assertNotIn("PRIVATE", json.dumps(report))
        self.assertTrue(all(row["probe"] == "unrecognized_or_failed" for row in report["engines"].values()))

    def test_missing_executable_is_distinct_from_missing_python_package(self):
        with patch.object(compatibility.shutil, "which", return_value=None), \
                patch.object(compatibility.metadata, "version", return_value="1.2.3"), \
                patch.object(compatibility.subprocess, "run") as run:
            report = compatibility.environment(True)
        run.assert_not_called()
        row = report["engines"]["gallery-dl"]
        self.assertEqual(row["probe"], "missing")
        self.assertFalse(row["executable_available"])
        self.assertEqual(row["python_package_version"], "1.2.3")
        self.assertIsNone(row["executable_version"])

    def test_fixture_contract_drift_fails_without_guessing_platform_failure(self):
        fixture = json.loads(compatibility.FIXTURES.read_text())
        fixture["routes"][0]["engine"] = "unexpected-engine"
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fixture.json"
            path.write_text(json.dumps(fixture))
            results = compatibility.fixture_checks(path)
        self.assertEqual(results[0]["status"], "failed")
        self.assertEqual(results[0]["layer"], "linkget_route_or_command_contract")
        self.assertTrue(all(row["status"] == "passed" for row in results[1:]))

    def test_fixture_paths_cannot_write_outside_generated_directory(self):
        fixture = json.loads(compatibility.FIXTURES.read_text())
        fixture["galleries"][0]["files"][0]["name"] = "../outside.jpg"
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "fixture.json"
            path.write_text(json.dumps(fixture))
            with self.assertRaisesRegex(ValueError, "filename"):
                compatibility.fixture_checks(path)

    def test_unexpected_error_does_not_pass_a_negative_fixture(self):
        with patch.object(compatibility.media, "gallery_evidence", side_effect=RuntimeError("unrelated failure")):
            results = compatibility.fixture_checks()
        self.assertEqual(results[4]["status"], "failed")

    def test_upstream_network_drift_is_blocked_and_reported_as_failure(self):
        class NetworkAttempt(unittest.TestCase):
            def runTest(self):
                socket.getaddrinfo("must-not-resolve.invalid", 443)
        suite = unittest.TestSuite([NetworkAttempt()])
        with patch.object(compatibility.unittest.defaultTestLoader, "loadTestsFromModule", return_value=suite):
            report = compatibility.extractor_checks()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["tests_run"], 1)
        self.assertEqual(len(report["failed_test_ids"]), 1)
        self.assertNotIn("must-not-resolve", json.dumps(report))

    def test_installed_but_unimportable_extractor_is_not_reported_as_missing_package(self):
        @unittest.skip("Existing suite could not import its extractor")
        class ImportFailure(unittest.TestCase):
            def runTest(self):
                pass
        with patch.object(compatibility.unittest.defaultTestLoader, "loadTestsFromModule",
                          return_value=unittest.TestSuite([ImportFailure()])), \
                patch.object(compatibility.util, "find_spec", return_value=object()):
            report = compatibility.extractor_checks()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["skip_reason"], "extractor_import_failed")

    def test_require_extractor_rejects_skipped_contract_and_output_never_overwrites(self):
        report = {"status": "partial", "extractor": {"status": "partial"}}
        with tempfile.TemporaryDirectory() as root, patch.object(compatibility, "check", return_value=report), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            output = Path(root) / "report.json"
            self.assertEqual(compatibility.main(["--output", str(output)]), 0)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            original = output.read_bytes()
            self.assertEqual(compatibility.main(["--output", str(output)]), 2)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(compatibility.main(["--require-extractor"]), 1)

    def test_failed_contract_causes_nonzero_and_reports_only_fixed_identifiers(self):
        report = {"status": "failed", "extractor": {"status": "passed"}}
        with patch.object(compatibility, "check", return_value=report), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(compatibility.main([]), 1)
        self.assertEqual(json.loads(out.getvalue()), report)


if __name__ == "__main__":
    unittest.main()
