"""Task recovery acceptance using isolated files; no network or personal library."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from linkget import accounts, batch, cli, jobs, photos, recovery
from media_stub import uninspected

URL = "https://www.pixiv.net/artworks/"


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for obj, name, value in ((accounts, "directory", self.root / "settings/sessions"),
                                 (cli, "inspect_files", uninspected),
                                 (recovery, "inspect_files", uninspected)):
            mocked = patch.object(obj, name, side_effect=value) if callable(value) else patch.object(obj, name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)
        mocked = patch.object(cli.tempfile, "tempdir", str(self.root))
        mocked.start()
        self.addCleanup(mocked.stop)
        mocked = patch.object(cli, "first_run", return_value={})
        mocked.start()
        self.setup = mocked
        self.addCleanup(mocked.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def task(self):
        return jobs.all_journals()[0]

    def downloaded(self, url, folder, cookies):
        (folder / (url.rsplit("/", 1)[-1] + ".jpg")).write_bytes(url.encode())

    def test_retry_preserves_original_numbers_and_skips_completed_items(self):
        def first(url, folder, cookies):
            if url.endswith("2"):
                (folder / "2.jpg.part").write_bytes(b"partial")
                raise RuntimeError("HTTP 403")
            self.downloaded(url, folder, cookies)
        with patch.object(cli, "download_pixiv", side_effect=first):
            self.assertEqual(self.run_cli("batch", URL + "1", URL + "2", "--folder", str(self.root / "out"), "--browser", "none")[0], 1)
        task = self.task()
        self.assertEqual(task.data["items"][0]["input"], "")
        with patch.object(cli, "download_pixiv", side_effect=self.downloaded) as download:
            code, out, _ = self.run_cli("retry", task.identifier)
        self.assertEqual(code, 0)
        download.assert_called_once()
        self.assertEqual(download.call_args.args[0], URL + "2")
        self.assertIn("[2/2]", out)
        self.assertEqual(jobs.Journal.load(task.identifier).incomplete(), [])
        self.assertEqual(len(list((self.root / "out").iterdir())), 2)

    def test_retry_latest_uses_saved_destination_and_browser_none(self):
        with patch.object(cli, "download_pixiv", side_effect=RuntimeError("timeout")):
            self.run_cli(URL + "2", "--folder", str(self.root / "out"), "--browser", "none")
        with patch.object(cli, "download_pixiv", side_effect=self.downloaded), patch.object(accounts, "import_auto") as browser:
            self.assertEqual(self.run_cli("retry")[0], 0)
        browser.assert_not_called()
        self.assertTrue((self.root / "out/2.jpg").exists())

    def failed_save(self):
        with patch.object(cli, "download_pixiv", side_effect=self.downloaded), patch.object(cli, "save_folder", side_effect=OSError("disk full")):
            self.assertEqual(self.run_cli(URL + "1", "--folder", str(self.root / "out"), "--browser", "none")[0], 1)
        return self.task()

    def test_recover_saves_complete_set_without_download_and_accepts_override(self):
        task = self.failed_save()
        source = Path(task.data["items"][0]["ready"]["files"][0]["path"])
        target = self.root / "other"
        with patch.object(cli, "download_pixiv") as download:
            code, out, _ = self.run_cli("recover", task.identifier, "--folder", str(target))
        self.assertEqual(code, 0)
        download.assert_not_called()
        self.assertEqual((target / "1.jpg").read_bytes(), (URL + "1").encode())
        self.assertFalse(source.exists())
        self.assertIn("Recovered", out)

    def test_retry_reuses_a_complete_failed_save_without_browser_or_download(self):
        task = self.failed_save()
        with patch.object(cli, "download_pixiv") as download, patch.object(cli, "first_run") as setup:
            self.assertEqual(self.run_cli("retry", task.identifier)[0], 0)
        download.assert_not_called()
        setup.assert_not_called()
        self.assertTrue((self.root / "out/1.jpg").exists())

    def test_redownload_keeps_changed_old_files_and_records_their_location(self):
        task = self.failed_save()
        old = Path(task.data["items"][0]["ready"]["files"][0]["path"])
        old.write_bytes(b"changed")
        with patch.object(cli, "download_pixiv", side_effect=self.downloaded) as download:
            self.assertEqual(self.run_cli("retry", task.identifier, "--redownload")[0], 0)
        download.assert_called_once()
        self.assertEqual(old.read_bytes(), b"changed")
        self.assertIn(str(old.parent.parent), self.run_cli("recover")[1])

    def test_recovery_rejects_files_outside_the_recorded_stage(self):
        task = self.failed_save()
        outside = self.root / "personal.jpg"
        outside.write_bytes(b"keep")
        task.data["items"][0]["ready"]["files"] = jobs.snapshot([outside])
        task.write()
        self.assertEqual(self.run_cli("recover", task.identifier)[0], 1)
        self.assertEqual(outside.read_bytes(), b"keep")

    def test_public_only_retry_does_not_inherit_an_explicit_browser_profile(self):
        task = self.failed_save()
        task.data["options"].update(browser="chrome", profile="/private/browser-profile")
        task.write()
        with patch.object(cli, "download_pixiv", side_effect=self.downloaded), patch.object(accounts, "import_auto") as browser:
            self.assertEqual(self.run_cli("retry", task.identifier, "--browser", "none")[0], 0)
        browser.assert_not_called()

    def test_latest_task_selection_uses_creation_time_instead_of_random_id(self):
        first = self.failed_save()
        second = self.failed_save()
        first.data["created_ns"] = 1
        second.data["created_ns"] = 2
        first.write()
        second.write()
        self.assertEqual(jobs.select().identifier, second.identifier)

    def test_changed_source_is_kept_and_recovery_refuses_to_save(self):
        task = self.failed_save()
        source = Path(task.data["items"][0]["ready"]["files"][0]["path"])
        source.write_bytes(b"changed")
        code, out, _ = self.run_cli("recover", task.identifier)
        self.assertEqual(code, 1)
        self.assertIn("missing or changed", out)
        self.assertEqual(source.read_bytes(), b"changed")
        self.assertEqual(list((self.root / "out").iterdir()), [])

    def test_partial_set_cannot_be_reimported(self):
        def incomplete(url, folder, cookies):
            (folder / "image.jpg.part").write_bytes(b"partial")
            raise RuntimeError("timeout")
        with patch.object(cli, "download_pixiv", side_effect=incomplete):
            self.run_cli(URL + "1", "--folder", str(self.root / "out"), "--browser", "none")
        with patch.object(cli.sys, "platform", "darwin"), patch.object(cli, "import_photos") as imported:
            code, out, _ = self.run_cli("recover", self.task().identifier, "--photos")
        self.assertEqual(code, 1)
        imported.assert_not_called()
        self.assertIn("download was incomplete", out)
        self.assertTrue(list(self.root.glob("linkget-*/media/image.jpg.part")))

    def test_list_does_not_read_browser_or_launch_photos(self):
        task = self.failed_save()
        with patch.object(cli, "import_photos") as imported, patch.object(accounts, "import_auto") as browser:
            code, out, _ = self.run_cli("recover")
        self.assertEqual(code, 0)
        self.assertIn(task.identifier, out)
        self.assertIn("1 ready to save", out)
        imported.assert_not_called()
        browser.assert_not_called()

    def test_receipts_are_private_and_share_tokens_are_absent_from_reports(self):
        url = "https://www.xiaohongshu.com/explore/" + "a" * 24 + "?xsec_token=private-test-token"
        with patch.object(cli, "download_xiaohongshu", side_effect=RuntimeError("timeout")):
            self.run_cli(url, "--folder", str(self.root / "out"), "--browser", "none", "--report", str(self.root / "report.json"))
        task = self.task()
        self.assertIn("private-test-token", task.path.read_text())
        self.assertEqual(task.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(jobs.directory().stat().st_mode & 0o777, 0o700)
        self.assertNotIn("private-test-token", (self.root / "report.json").read_text())

    def test_another_process_cannot_claim_same_task(self):
        task = self.failed_save()
        with task.claim(), self.assertRaisesRegex(RuntimeError, "already running"):
            with jobs.Journal.load(task.identifier).claim():
                pass

    def test_optional_concurrency_limit_and_progress(self):
        active = maximum = 0
        lock = threading.Lock()
        def download(url, folder, cookies):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            (folder / "image.jpg.part").write_bytes(b"p" * 1000)
            threading.Event().wait(.12)
            (folder / "image.jpg.part").unlink()
            self.downloaded(url, folder, cookies)
            with lock:
                active -= 1
        with patch.object(cli, "download_pixiv", side_effect=download), patch.object(batch, "PROGRESS_INTERVAL", .01):
            code, out, _ = self.run_cli("batch", *[URL + str(i) for i in range(5)], "--jobs", "2", "--folder", str(self.root / "out"), "--browser", "none")
        self.assertEqual(code, 0)
        self.assertEqual(maximum, 2)
        self.assertIn("3 queued", out)
        self.assertIn("Downloading · 1.0 KB", out)

    def test_bad_destination_and_options_fail_before_browser_setup(self):
        target = self.root / "file"
        target.write_bytes(b"keep")
        with patch.object(cli, "first_run") as setup:
            self.assertEqual(self.run_cli(URL + "1", "--folder", str(target))[0], 1)
            with self.assertRaises(SystemExit):
                self.run_cli("batch", URL + "1", "--jobs", "0")
        setup.assert_not_called()
        self.assertEqual(target.read_bytes(), b"keep")

    def test_recovery_verifies_real_managed_bytes_before_removing_staged_original(self):
        identifier = "90FA4F22-C80E-43F2-A2AF-FE5BA03A989E/L0/001"
        library = self.root / "Test.photoslibrary"
        managed = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpg"
        managed.parent.mkdir(parents=True)
        from subprocess import CompletedProcess
        with patch.object(cli.sys, "platform", "darwin"), patch.object(cli, "download_pixiv", side_effect=self.downloaded), patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "1:0:1\n" + identifier, "")), patch.object(photos, "library_directory", return_value=library):
            self.assertEqual(self.run_cli(URL + "1", "--photos", "--browser", "none")[0], 1)
        task = self.task()
        staged = Path(task.data["items"][0]["receipt"]["files"][0]["path"])
        with patch.object(cli.sys, "platform", "darwin"), patch.object(photos, "library_directory", return_value=library):
            self.assertEqual(self.run_cli("recover", task.identifier, "--verify")[0], 1)
            self.assertTrue(staged.exists())
            managed.write_bytes(staged.read_bytes())
            self.assertEqual(self.run_cli("recover", task.identifier, "--verify")[0], 0)
        self.assertFalse(staged.exists())
        self.assertEqual(managed.read_bytes(), (URL + "1").encode())

    def test_folder_recovery_keeps_failed_photos_receipt_available_for_later_verification(self):
        identifier = "90FA4F22-C80E-43F2-A2AF-FE5BA03A989E/L0/001"
        library = self.root / "Test.photoslibrary"
        managed = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpg"
        managed.parent.mkdir(parents=True)
        from subprocess import CompletedProcess
        with patch.object(cli.sys, "platform", "darwin"), patch.object(cli, "download_pixiv", side_effect=self.downloaded), patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "1:0:1\n" + identifier, "")), patch.object(photos, "library_directory", return_value=library):
            self.assertEqual(self.run_cli(URL + "1", "--photos", "--browser", "none")[0], 1)
        task = self.task()
        staged = Path(task.data["items"][0]["receipt"]["files"][0]["path"])
        self.assertEqual(self.run_cli("recover", task.identifier, "--folder", str(self.root / "out"))[0], 0)
        self.assertTrue(staged.exists())
        self.assertEqual(jobs.Journal.load(task.identifier).needs_verification(), [1])
        managed.write_bytes(staged.read_bytes())
        with patch.object(cli.sys, "platform", "darwin"), patch.object(photos, "library_directory", return_value=library):
            self.assertEqual(self.run_cli("recover", task.identifier, "--verify")[0], 0)
        self.assertFalse(staged.exists())

    def test_live_photo_dependencies_fail_before_download_or_browser_setup(self):
        with patch.object(cli.sys, "platform", "darwin"), patch.object(cli, "find_tool", return_value=None), patch.object(cli, "first_run") as setup, patch.object(cli, "download_pixiv") as download:
            code, _, err = self.run_cli(URL + "1", "--folder", str(self.root / "out"), "--live-photos")
        self.assertEqual(code, 1)
        self.assertIn("exiftool", err)
        setup.assert_not_called()
        download.assert_not_called()
