"""Task recovery acceptance using isolated files; no network or personal library."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from linkget import accounts, batch, cli, destinations, jobs, photos, recovery
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
        stage = Path(task.data["items"][0]["stage"])
        with patch.object(cli, "download_pixiv") as download, patch.object(cli, "first_run") as setup:
            self.assertEqual(self.run_cli("retry", task.identifier)[0], 0)
        download.assert_not_called()
        setup.assert_not_called()
        self.assertTrue((self.root / "out/1.jpg").exists())
        self.assertFalse(stage.exists())

    def partial_task(self):
        def partial(url, folder, cookies):
            (folder / "1.jpg.part").write_bytes(b"partial")
            raise RuntimeError("interrupted")
        with patch.object(cli, "download_pixiv", side_effect=partial):
            self.assertEqual(self.run_cli(URL + "1", "--folder", str(self.root / "out"), "--browser", "none")[0], 1)
        return self.task(), partial

    def test_repeated_partial_failures_clean_only_after_verified_retry_success(self):
        task, partial = self.partial_task()
        first = Path(task.data["items"][0]["stage"])
        with patch.object(cli, "download_pixiv", side_effect=partial):
            self.assertEqual(self.run_cli("retry", task.identifier)[0], 1)
        second = Path(jobs.Journal.load(task.identifier).data["items"][0]["stage"])
        self.assertTrue(first.exists())
        self.assertTrue(second.exists())
        with patch.object(cli, "download_pixiv", side_effect=self.downloaded):
            self.assertEqual(self.run_cli("retry", task.identifier)[0], 0)
        row = jobs.Journal.load(task.identifier).data["items"][0]
        self.assertEqual((self.root / "out/1.jpg").read_bytes(), (URL + "1").encode())
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertEqual(row["previous_stages"], [])
        self.assertEqual(row["stage_snapshots"], {})

    def test_changed_added_or_linked_old_files_are_kept_after_success(self):
        for kind in ("changed", "added", "symlink", "directory_symlink"):
            with self.subTest(kind=kind):
                task, _ = self.partial_task()
                old = Path(task.data["items"][0]["stage"])
                external = self.root / "personal.txt"
                external.write_bytes(b"keep")
                if kind == "changed":
                    (old / "media/1.jpg.part").write_bytes(b"changed")
                elif kind == "added":
                    (old / "personal.txt").write_bytes(b"keep")
                elif kind == "symlink":
                    (old / "link").symlink_to(external)
                else:
                    (old / "link").symlink_to(self.root / "settings", target_is_directory=True)
                with patch.object(cli, "download_pixiv", side_effect=self.downloaded):
                    code, out, _ = self.run_cli("retry", task.identifier)
                self.assertEqual(code, 0)
                self.assertTrue(old.exists())
                self.assertEqual(external.read_bytes(), b"keep")
                self.assertIn("Files kept", out)

    def test_destination_corruption_never_cleans_previous_failure(self):
        task, _ = self.partial_task()
        old = Path(task.data["items"][0]["stage"])
        with patch.object(cli, "download_pixiv", side_effect=self.downloaded), patch.object(destinations.shutil, "copyfileobj", side_effect=lambda src, dst: dst.write(b"corrupt")):
            code, _, err = self.run_cli("retry", task.identifier)
        self.assertEqual(code, 1)
        self.assertIn("verification failed", err)
        self.assertTrue(old.exists())
        self.assertFalse((self.root / "out/1.jpg").exists())

    def test_cancelled_retry_keeps_all_previous_files(self):
        task, _ = self.partial_task()
        old = Path(task.data["items"][0]["stage"])
        with patch.object(cli, "download_pixiv", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_cli("retry", task.identifier)[0], 130)
        self.assertTrue(old.exists())

    def test_batch_success_does_not_clean_the_other_failed_item(self):
        def partial(url, folder, cookies):
            (folder / "partial.jpg.part").write_bytes(b"partial")
            raise RuntimeError("interrupted")
        with patch.object(cli, "download_pixiv", side_effect=partial):
            self.run_cli("batch", URL + "1", URL + "2", "--folder", str(self.root / "out"), "--browser", "none")
        task = self.task()
        old = [Path(row["stage"]) for row in task.data["items"]]
        def retry(url, folder, cookies):
            if url.endswith("2"):
                partial(url, folder, cookies)
            else:
                self.downloaded(url, folder, cookies)
        with patch.object(cli, "download_pixiv", side_effect=retry):
            self.assertEqual(self.run_cli("retry", task.identifier)[0], 1)
        self.assertFalse(old[0].exists())
        self.assertTrue(old[1].exists())

    def test_replaced_root_and_paths_outside_temp_base_are_kept(self):
        task, _ = self.partial_task()
        old = Path(task.data["items"][0]["stage"])
        moved = self.root / "kept-original"
        old.rename(moved)
        old.mkdir()
        (old / "personal.txt").write_bytes(b"keep")
        outside = self.root / "elsewhere/linkget-personal"
        outside.mkdir(parents=True)
        (outside / "personal.txt").write_bytes(b"keep")
        task.data["items"][0]["previous_stages"] = [str(outside)]
        task.write()
        with patch.object(cli, "download_pixiv", side_effect=self.downloaded):
            self.assertEqual(self.run_cli("retry", task.identifier)[0], 0)
        self.assertEqual((old / "personal.txt").read_bytes(), b"keep")
        self.assertEqual((outside / "personal.txt").read_bytes(), b"keep")
        self.assertTrue(moved.exists())

    def test_verified_redownload_removes_unchanged_complete_old_set(self):
        task = self.failed_save()
        old = Path(task.data["items"][0]["stage"])
        with patch.object(cli, "download_pixiv", side_effect=self.downloaded):
            self.assertEqual(self.run_cli("retry", task.identifier, "--redownload")[0], 0)
        self.assertFalse(old.exists())

    def test_nested_recovery_destination_is_preserved(self):
        task = self.failed_save()
        stage = Path(task.data["items"][0]["stage"])
        target = stage / "media"
        self.assertEqual(self.run_cli("recover", task.identifier, "--folder", str(target))[0], 0)
        self.assertEqual((target / "1.jpg").read_bytes(), (URL + "1").encode())
        self.assertTrue(stage.exists())

    def test_other_task_stage_reference_blocks_cleanup(self):
        task, _ = self.partial_task()
        old = Path(task.data["items"][0]["stage"])
        other = self.failed_save()
        other.data["items"][0]["previous_stages"] = [str(old)]
        other.write()
        with patch.object(cli, "download_pixiv", side_effect=self.downloaded):
            self.assertEqual(self.run_cli("retry", task.identifier)[0], 0)
        self.assertTrue(old.exists())

    def test_legacy_partial_receipt_without_ownership_record_is_kept(self):
        task, _ = self.partial_task()
        row = task.data["items"][0]
        old = Path(row["stage"])
        row.pop("stage_snapshots")
        row.pop("stage_identity")
        task.write()
        with patch.object(cli, "download_pixiv", side_effect=self.downloaded):
            self.assertEqual(self.run_cli("retry", task.identifier)[0], 0)
        self.assertTrue(old.exists())

    def test_photos_rejection_keeps_earlier_files_until_managed_copy_verifies(self):
        from subprocess import CompletedProcess
        task, _ = self.partial_task()
        old = Path(task.data["items"][0]["stage"])
        identifier = "90FA4F22-C80E-43F2-A2AF-FE5BA03A989E/L0/001"
        library = self.root / "Test.photoslibrary"
        managed = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpg"
        managed.parent.mkdir(parents=True)
        task.data["options"].pop("folder")
        task.write()
        with patch.object(cli.sys, "platform", "darwin"), patch.object(cli, "download_pixiv", side_effect=self.downloaded), patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "1:0:1\n" + identifier, "")), patch.object(photos, "library_directory", return_value=library):
            self.assertEqual(self.run_cli("retry", task.identifier)[0], 1)
        self.assertTrue(old.exists())
        row = jobs.Journal.load(task.identifier).data["items"][0]
        latest = Path(row["stage"])
        source = Path(row["receipt"]["files"][0]["path"])
        managed.write_bytes(source.read_bytes())
        with patch.object(cli.sys, "platform", "darwin"), patch.object(photos, "library_directory", return_value=library):
            self.assertEqual(self.run_cli("recover", task.identifier, "--verify")[0], 0)
        self.assertFalse(old.exists())
        self.assertFalse(latest.exists())
        self.assertEqual(managed.read_bytes(), (URL + "1").encode())

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
