"""Deterministic offline regression tests for lifecycle invariants and concurrency fixes."""
import argparse
import contextlib
import io
import json
import os
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from linkget import (
    accounts,
    batch,
    cli,
    content,
    destinations,
    jobs,
    live,
    ownership,
    photos,
    read_formats,
    read_store,
    read_tasks,
    recovery,
    runtime,
    toutiao,
)
from media_stub import uninspected


class LifecycleInvariantTests(unittest.TestCase):
    identifier_a = "90FA4F22-C80E-43F2-A2AF-FE5BA03A989E/L0/001"
    identifier_b = "A1FB5033-D91F-4403-B3B0-0F6CB14B09AF/L0/001"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        for obj, name, value in (
            (accounts, "directory", self.root / "linkget/sessions"),
            (cli, "inspect_files", uninspected),
            (recovery, "inspect_files", uninspected),
            (cli, "first_run", {}),
        ):
            patcher = (
                patch.object(obj, name, side_effect=value)
                if callable(value)
                else patch.object(obj, name, return_value=value)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
        tmp_patch = patch.object(cli.tempfile, "tempdir", str(self.root))
        tmp_patch.start()
        self.addCleanup(tmp_patch.stop)
        interval_patch = patch.object(batch, "SITE_INTERVAL", 0)
        interval_patch.start()
        self.addCleanup(interval_patch.stop)

    def _run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def _write_owned(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with ownership.Writer(path, register=True) as writer:
            writer.write(data)

    # --- 1. Cross-task shared original protection & atomic shared staging ---

    def test_import_photos_keeps_shared_original_referenced_by_other_task_receipt(self):
        payload = b"shared photos payload"
        library = self.root / "Photos.photoslibrary"
        managed_a = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpg"
        managed_b = library / "originals/A/A1FB5033-D91F-4403-B3B0-0F6CB14B09AF.jpg"
        managed_a.parent.mkdir(parents=True)
        managed_b.parent.mkdir(parents=True)

        source_a = self.root / "src-a/same.jpg"
        source_b = self.root / "src-b/same.jpg"
        source_a.parent.mkdir()
        source_b.parent.mkdir()
        source_a.write_bytes(payload)
        source_b.write_bytes(payload)

        task_a = jobs.Journal.create(["https://www.pixiv.net/artworks/1"], argparse.Namespace())
        task_b = jobs.Journal.create(["https://www.pixiv.net/artworks/2"], argparse.Namespace())

        # Task A stages the shared original and fails verification initially; Task B observes it
        # and also fails verification, leaving an unverified receipt pointing to the same original.
        def import_without_copy(command, **kwargs):
            return CompletedProcess(command, 0, "1:0:1\n" + self.identifier_a, "")

        with jobs.item(task_a, 1), patch.object(destinations.subprocess, "run", side_effect=import_without_copy), \
                patch.object(photos, "library_directory", return_value=library):
            with self.assertRaises(RuntimeError):
                destinations.import_photos([source_a], False, None, photos.retain_originals, photos.verify_library_copies)

        staged = Path(jobs.Journal.load(task_a.identifier).data["items"][0]["receipt"]["staged"][0]["path"])
        self.assertTrue(staged.is_file())

        def import_b_without_copy(command, **kwargs):
            return CompletedProcess(command, 0, "1:0:1\n" + self.identifier_b, "")

        with jobs.item(task_b, 1), patch.object(destinations.subprocess, "run", side_effect=import_b_without_copy), \
                patch.object(photos, "library_directory", return_value=library):
            with self.assertRaises(RuntimeError):
                destinations.import_photos([source_b], False, None, photos.retain_originals, photos.verify_library_copies)

        self.assertEqual(jobs.Journal.load(task_b.identifier).data["items"][0]["receipt"]["staged"], [])
        self.assertEqual(
            jobs.Journal.load(task_b.identifier).data["items"][0]["receipt"]["files"][0]["path"],
            str(staged),
        )

        # Now Task A's Photos library copy appears and Task A runs recover --verify.
        # Because Task B still holds an unverified receipt for the same original, Task A MUST keep it.
        managed_a.write_bytes(payload)
        with patch.object(cli.sys, "platform", "darwin"), patch.object(photos, "library_directory", return_value=library):
            code_a, out_a, err_a = self._run_cli("recover", task_a.identifier, "--verify")
        self.assertEqual(code_a, 0, err_a)
        self.assertIn("0 staged copies removed · 1 kept", out_a)
        self.assertTrue(staged.is_file(), "Shared original was unlinked while Task B still referenced it")

        # Task B can now verify using the preserved shared original.
        managed_b.write_bytes(payload)
        with patch.object(cli.sys, "platform", "darwin"), patch.object(photos, "library_directory", return_value=library):
            code_b, _, err_b = self._run_cli("recover", task_b.identifier, "--verify")
        self.assertEqual(code_b, 0, err_b)
        self.assertEqual(jobs.Journal.load(task_b.identifier).needs_verification(), [])
        self.assertTrue(staged.is_file())

        # Once Task B's receipt is gone, re-verifying Task A cleans up the owned original.
        with patch.object(cli.sys, "platform", "darwin"), patch.object(photos, "library_directory", return_value=library):
            code_a2, out_a2, err_a2 = self._run_cli("recover", task_a.identifier, "--verify")
        self.assertEqual(code_a2, 0, out_a2 + err_a2)
        self.assertIn("1 staged copies removed", out_a2)
        self.assertFalse(staged.exists())

    def test_import_photos_direct_keeps_shared_original_referenced_by_active_task(self):
        payload = b"concurrent shared original"
        library = self.root / "Photos.photoslibrary"
        managed = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpg"
        managed.parent.mkdir(parents=True)
        source = self.root / "dl/same.jpg"
        source.parent.mkdir()
        source.write_bytes(payload)

        task_a = jobs.Journal.create(["https://www.pixiv.net/artworks/10"], argparse.Namespace())
        task_b = jobs.Journal.create(["https://www.pixiv.net/artworks/20"], argparse.Namespace())

        def osascript_a(command, **kwargs):
            retained = Path(command[-1])
            # Simulate Task B recording a receipt referencing the exact same staged original
            # while Task A is inside osascript.
            task_b.update(1, receipt={"files": [{"path": str(retained), "sha256": jobs.digest(retained)}], "staged": [], "identifiers": [self.identifier_b]})
            managed.write_bytes(retained.read_bytes())
            return CompletedProcess(command, 0, "1:0:1\n" + self.identifier_a, "")

        with jobs.item(task_a, 1), patch.object(destinations.subprocess, "run", side_effect=osascript_a), \
                patch.object(photos, "library_directory", return_value=library):
            saved, skipped = destinations.import_photos([source], False, None, photos.retain_originals, photos.verify_library_copies)
        self.assertEqual((saved, skipped), ([source], 0))
        staged_files = list(photos.originals_directory().iterdir())
        self.assertEqual(len(staged_files), 1)
        self.assertEqual(staged_files[0].read_bytes(), payload)

    def test_corrupt_other_journal_fails_closed_and_keeps_staged_original(self):
        payload = b"fail closed bytes"
        library = self.root / "Photos.photoslibrary"
        managed = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpg"
        managed.parent.mkdir(parents=True)
        source = self.root / "dl/img.jpg"
        source.parent.mkdir()
        source.write_bytes(payload)

        jobs.directory().mkdir(parents=True, exist_ok=True)
        (jobs.directory() / "20260101T000000-deadbeef1234.json").write_text("{corrupt")

        def osascript(command, **kwargs):
            managed.write_bytes(Path(command[-1]).read_bytes())
            return CompletedProcess(command, 0, "1:0:1\n" + self.identifier_a, "")

        with patch.object(destinations.subprocess, "run", side_effect=osascript), \
                patch.object(photos, "library_directory", return_value=library):
            destinations.import_photos([source], False, None, photos.retain_originals, photos.verify_library_copies)
        self.assertEqual(len(list(photos.originals_directory().iterdir())), 1)

    def test_concurrent_retain_originals_is_atomic_and_assigns_single_owner(self):
        source = self.root / "src/photo.jpg"
        source.parent.mkdir()
        source.write_bytes(b"atomic original content")
        barrier = threading.Barrier(2)
        real_copy = photos.shutil.copyfileobj
        copy_calls = []

        def serialized_copy(src, dst):
            copy_calls.append(threading.get_ident())
            time.sleep(0.05)
            real_copy(src, dst)

        results, errors = [], []

        def worker():
            try:
                barrier.wait(timeout=3)
                results.append(photos.retain_originals([source]))
            except BaseException as exc:
                errors.append(exc)

        with patch.object(photos.shutil, "copyfileobj", side_effect=serialized_copy):
            threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
                self.assertFalse(t.is_alive())

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(copy_calls), 1)
        owners = [r.proof(r[0]).created for r in results]
        self.assertEqual(sorted(owners), [False, True])
        entries = list(photos.originals_directory().iterdir())
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].read_bytes(), b"atomic original content")

    def test_concurrent_retain_pairs_is_atomic_and_preserves_single_owner(self):
        src_dir = self.root / "pair-src"
        src_dir.mkdir()
        img = src_dir / "live.jpg"
        mov = src_dir / "live.mov"
        img.write_bytes(b"live photo still")
        mov.write_bytes(b"live photo motion")
        pair = ownership.FileList((img, mov), [ownership.FileProof.observe(img), ownership.FileProof.observe(mov)])

        # Intra-call duplicate retains created=True proof.
        dedup_root = self.root / "dedup-retained"
        deduped = live.retain_pairs([pair, pair], dedup_root)
        self.assertTrue(deduped.proof(deduped[0]).created)
        self.assertTrue(deduped.proof(deduped[1]).created)

        # Concurrent callers serialize cleanly without partial reads or FileExistsError.
        race_root = self.root / "race-retained"
        barrier = threading.Barrier(2)
        real_copy = live.shutil.copyfileobj
        copy_calls = []

        def serialized_copy(src, dst):
            copy_calls.append(threading.get_ident())
            time.sleep(0.03)
            real_copy(src, dst)

        results, errors = [], []

        def worker():
            try:
                barrier.wait(timeout=3)
                results.append(live.retain_pairs([pair], race_root))
            except BaseException as exc:
                errors.append(exc)

        with patch.object(live.shutil, "copyfileobj", side_effect=serialized_copy):
            threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5)
                self.assertFalse(t.is_alive())

        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(len(copy_calls), 2)  # Only the winning thread copies img + mov
        img_owners = [r.proof(race_root / "live.jpg").created for r in results]
        mov_owners = [r.proof(race_root / "live.mov").created for r in results]
        self.assertEqual(sorted(img_owners), [False, True])
        self.assertEqual(sorted(mov_owners), [False, True])
        self.assertEqual(sorted(p.name for p in race_root.iterdir()), ["live.jpg", "live.mov"])

    # --- 2. Batch coordinator deadlock on unhandled exception ---

    @patch.object(batch, "site_key", side_effect=lambda link: link)
    def test_batch_unhandled_worker_exception_wakes_worker_blocked_in_interaction(self, _site_key):
        both_started = threading.Barrier(2)
        worker_blocked = threading.Event()
        woken_up = threading.Event()

        def fake_download(item_args, setup, records):
            both_started.wait(timeout=3)
            if item_args.link.endswith("1"):
                worker_blocked.wait(timeout=3)
                # Uncaught exception exits run_item and raises from future.result() in coordinator.
                raise TypeError("simulated unexpected worker error")
            worker_blocked.set()
            try:
                runtime.batch_interaction(lambda: time.sleep(0.05))
            except KeyboardInterrupt:
                woken_up.set()
                raise
            return 0

        journal_holder = []

        def make_journal(links, args):
            j = jobs.Journal.create(links, args)
            journal_holder.append(j)
            return j

        args = argparse.Namespace(folder=self.root / "out", jobs=2, redownload=False)
        parser = argparse.ArgumentParser()
        outcome = {}

        def run_coordinator():
            try:
                outcome["result"] = batch.run_batch(
                    args,
                    parser,
                    ["https://example.com/1", "https://example.com/2"],
                    [],
                    fake_download,
                    lambda a: {},
                    lambda *a: None,
                    make_journal,
                )
            except BaseException as exc:
                outcome["error"] = exc

        # Delay interaction pickup slightly so worker 2 is waiting when worker 1's future fails,
        # OR even if worker 2 enters interaction during shutdown, state['cancelled'] must wake it.
        coord = threading.Thread(target=run_coordinator)
        coord.start()
        coord.join(timeout=4)
        self.assertFalse(coord.is_alive(), "Batch coordinator deadlocked waiting for worker in batch_interaction")
        self.assertIsInstance(outcome.get("error"), TypeError)
        loaded = jobs.Journal.load(journal_holder[0].identifier)
        self.assertEqual(loaded.data["items"][0]["status"], "failed")
        self.assertIn(loaded.data["items"][1]["status"], {"cancelled", "completed"})

    @patch.object(batch, "site_key", side_effect=lambda link: link)
    def test_batch_coordinator_loop_exception_wakes_blocked_interaction_worker(self, _site_key):
        interaction_submitted = threading.Event()
        worker_exited = threading.Event()

        def fake_download(item_args, setup, records):
            interaction_submitted.set()
            try:
                runtime.batch_interaction(lambda: "never-drained")
            finally:
                worker_exited.set()
            return 0

        real_wait = batch.wait

        def exploding_wait(fs, *a, **kw):
            interaction_submitted.wait(timeout=3)
            # Yield briefly so worker queues into state['interactions'] before coordinator crashes.
            time.sleep(0.05)
            raise RuntimeError("coordinator failure")

        args = argparse.Namespace(folder=self.root / "out", jobs=1, redownload=False)
        parser = argparse.ArgumentParser()
        outcome = {}

        def run_coordinator():
            try:
                with patch.object(batch, "wait", side_effect=exploding_wait):
                    batch.run_batch(
                        args,
                        parser,
                        ["https://example.com/1"],
                        [],
                        fake_download,
                        lambda a: {},
                        lambda *a: None,
                        jobs.Journal.create,
                    )
            except BaseException as exc:
                outcome["error"] = exc

        coord = threading.Thread(target=run_coordinator)
        coord.start()
        coord.join(timeout=4)
        self.assertFalse(coord.is_alive(), "Coordinator deadlocked after loop exception")
        self.assertTrue(worker_exited.is_set())
        self.assertIsInstance(outcome.get("error"), RuntimeError)

    # --- 3. cli.py --redownload receipt preservation ---

    def test_redownload_preserves_unverified_photos_receipt_until_verified(self):
        url = "https://www.pixiv.net/artworks/77"
        library = self.root / "Test.photoslibrary"
        managed = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpg"
        managed.parent.mkdir(parents=True)

        def download_ok(u, folder, cookies):
            self._write_owned(folder / "77.jpg", b"original-image-bytes")

        with patch.object(cli.sys, "platform", "darwin"), \
                patch.object(cli, "download_pixiv", side_effect=download_ok), \
                patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "1:0:1\n" + self.identifier_a, "")), \
                patch.object(photos, "library_directory", return_value=library):
            code, _, _ = self._run_cli(url, "--photos", "--browser", "none")
        self.assertEqual(code, 1)

        task = jobs.all_journals()[0]
        original_receipt = task.data["items"][0]["receipt"]
        staged = Path(original_receipt["staged"][0]["path"])
        self.assertTrue(staged.is_file())

        # Running retry --redownload when the download fails must NOT wipe the unverified receipt.
        with patch.object(cli.sys, "platform", "darwin"), \
                patch.object(cli, "download_pixiv", side_effect=RuntimeError("network down")):
            code_retry, _, _ = self._run_cli("retry", task.identifier, "--redownload")
        self.assertEqual(code_retry, 1)
        reloaded = jobs.Journal.load(task.identifier)
        self.assertEqual(reloaded.data["items"][0]["receipt"], original_receipt)
        self.assertTrue(staged.is_file())

        # Running retry --redownload --folder must also preserve the unverified Photos receipt.
        with patch.object(cli, "download_pixiv", side_effect=download_ok):
            code_folder, _, err_folder = self._run_cli("retry", task.identifier, "--redownload", "--folder", str(self.root / "out"))
        self.assertEqual(code_folder, 0, err_folder)
        reloaded2 = jobs.Journal.load(task.identifier)
        self.assertEqual(reloaded2.needs_verification(), [1])
        self.assertTrue(staged.is_file())

        # Later recover --verify still works and cleans up the original once managed copy matches.
        managed.write_bytes(staged.read_bytes())
        with patch.object(cli.sys, "platform", "darwin"), patch.object(photos, "library_directory", return_value=library):
            code_verify, _, err_verify = self._run_cli("recover", task.identifier, "--verify")
        self.assertEqual(code_verify, 0, err_verify)
        self.assertFalse(staged.exists())

    # --- 4. Detached read_tasks / read_store / content / read_formats monotonicity & atomicity ---

    def _make_read_receipt(self, **extra):
        identity = "20261127T120000-abcdef123456"
        value = {
            "schema": 1,
            "id": identity,
            "kind": "translate",
            "argv": ["translate", str(self.root / "read-src")],
            "status": "queued",
            "phase": "queued",
            "created": time.time() - 60,
            "outputs": [],
            "pid": None,
            **extra,
        }
        read_store.write_json(read_tasks.paths(identity)[0], value)
        return identity

    def test_refresh_respects_worker_lock_even_when_active_check_returns_false(self):
        with patch.object(read_store, "root", return_value=self.root / "private"):
            identity = self._make_read_receipt(status="running", phase="translate", pid=999999)
            claim = read_tasks.paths(identity)[2]
            with read_store.lock(claim):
                with patch.object(read_tasks, "active", return_value=False):
                    refreshed = read_tasks.refresh(identity)
            self.assertEqual(refreshed["status"], "running")
            # Once the worker lock is released and process is not active, refresh marks interrupted.
            with patch.object(read_tasks, "active", return_value=False):
                stopped = read_tasks.refresh(identity)
            self.assertEqual(stopped["status"], "interrupted")

    def test_cancel_and_update_and_worker_enforce_terminal_monotonicity(self):
        with patch.object(read_store, "root", return_value=self.root / "private"):
            identity = self._make_read_receipt(status="completed", phase="completed", exit_code=0, finished=time.time())
            with contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(read_tasks.cancel([identity]), 0)
            after_cancel = read_tasks.load(identity)
            self.assertEqual(after_cancel["status"], "completed")
            self.assertEqual(after_cancel["phase"], "completed")
            self.assertNotIn("cancel_requested", after_cancel)
            self.assertNotIn("Cancellation requested", out.getvalue())

            # Direct update() cannot mutate a terminal receipt either.
            after_update = read_tasks.update(identity, status="failed", phase="bad", cancel_requested=True)
            self.assertEqual(after_update["status"], "completed")
            self.assertEqual(after_update["phase"], "completed")

            # Worker finally block must not overwrite an already-terminal receipt.
            id2 = self._make_read_receipt(status="queued", phase="queued")
            def complete_externally(_argv):
                record, lock, _, _ = read_tasks.paths(id2)
                with read_store.lock(lock, wait=True):
                    val = read_tasks.load(id2)
                    val.update(status="cancelled", phase="cancelled", finished=time.time())
                    read_store.write_json(record, val)
                return 0

            with patch.object(content, "main", side_effect=complete_externally):
                read_tasks.worker(id2)
            self.assertEqual(read_tasks.load(id2)["status"], "cancelled")

    def test_submit_post_spawn_error_does_not_clobber_spawned_worker(self):
        with patch.object(read_store, "root", return_value=self.root / "private"):
            class FakeChild:
                pid = 42424
                def wait(self):
                    return 0

            # Pre-spawn failure DOES mark task failed.
            with patch.object(read_tasks.subprocess, "Popen", side_effect=OSError("spawn failed")):
                with self.assertRaises(OSError):
                    read_tasks.submit(["translate", str(self.root)], "translate")
            records = sorted(read_tasks.directory().glob("*.json"))
            self.assertEqual(len(records), 1)
            self.assertEqual(read_tasks.load(records[0].stem)["status"], "failed")

            # Post-spawn Thread.start RuntimeError MUST NOT mark spawned worker failed.
            with patch.object(read_tasks.subprocess, "Popen", return_value=FakeChild()), \
                    patch.object(read_tasks.threading.Thread, "start", side_effect=RuntimeError("no threads")), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                code = read_tasks.submit(["translate", str(self.root)], "translate")
            self.assertEqual(code, 0)
            task_id = out.getvalue().splitlines()[0].removeprefix("Task: ")
            loaded = read_tasks.load(task_id)
            self.assertEqual(loaded["status"], "queued")
            self.assertEqual(loaded["pid"], 42424)

    def test_post_commit_interrupt_never_regresses_completed_read_job(self):
        with patch.object(read_store, "root", return_value=self.root / "private"):
            folder = self.root / "read-out"
            local_media = self.root / "clip.mp4"
            local_media.write_bytes(b"media")
            model = self.root / "whisper.bin"
            model.touch()

            recognized = {
                "text": "Hello world",
                "segments": [{"text": "Hello world", "start": 0, "end": 2}],
                "content_complete": True,
                "content_basis": "full_video_local_asr",
                "recognition_verified": False,
                "media_evidence": {"duration": 2, "audio": True},
            }

            args = argparse.Namespace(
                url=str(local_media),
                folder=folder,
                whisper_model=model,
                language="en",
                formats="md,txt",
                subtitles="never",
                ocr=False,
                frames=False,
                frame_ocr=False,
                max_frames=8,
                chunk_seconds=600,
                vad_model=None,
                browser="none",
                profile=None,
                rendered_backend=None,
                no_assets=True,
                keep_media=False,
                page_data=None,
                resume=False,
                refresh=True,
                stdout=True,
                stdout_format="txt",
                json=False,
            )

            class ExplodingStdout:
                def isatty(self):
                    return False
                def write(self, text):
                    if "Hello world" in text:
                        raise KeyboardInterrupt("interrupted after commit")
                    return len(text)
                def flush(self):
                    pass

            with patch.object(cli, "find_tool", return_value="tool"), \
                    patch.object(content.read_asr, "probe", return_value=(2.0, True)), \
                    patch.object(toutiao, "transcribe", return_value=recognized), \
                    patch.object(content.sys, "stdout", ExplodingStdout()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(KeyboardInterrupt):
                    content.read_videos(args)

            job_data = read_store.read_json(folder / ".read-job.json")
            self.assertEqual(job_data["status"], "completed")
            self.assertTrue((folder / "content.json").is_file())
            self.assertEqual(read_store.unfinished(), [])

            # Direct save_job downgrade attempt is also blocked.
            read_store.save_job(folder, {**job_data, "status": "failed"})
            self.assertEqual(read_store.read_json(folder / ".read-job.json")["status"], "completed")
            # Even if index lagged as 'running', unfinished() trusts completed .read-job.json.
            read_store.write_json(
                read_store.root() / "read-jobs" / (job_data["id"] + ".json"),
                {"folder": str(folder), "status": "running", "created": job_data["created"]},
            )
            self.assertEqual(read_store.unfinished(), [])

    def test_toutiao_retry_cleans_leftover_download_directory_and_rejects_symlink(self):
        with patch.object(read_store, "root", return_value=self.root / "private"):
            folder = self.root / "toutiao-retry"
            folder.mkdir(mode=0o700)
            model = self.root / "whisper.bin"
            model.touch()
            page = self.root / "toutiao-page.json"
            page.write_text("{}")

            options = {
                "whisper_model": str(model),
                "language": "zh",
                "formats": "md",
                "subtitles": "auto",
                "ocr": False,
                "frames": False,
                "frame_ocr": False,
                "max_frames": 8,
                "chunk_seconds": 600,
                "vad_model": None,
                "browser": "none",
                "profile": None,
                "rendered_backend": None,
                "no_assets": True,
                "keep_media": False,
                "page_data": str(page),
            }
            url = "https://www.toutiao.com/video/7123456789012345678/"
            read_store.checkpoint(folder, url, options)

            # Leave an interrupted .toutiao-download directory behind.
            stale = folder / ".toutiao-download"
            stale.mkdir(mode=0o700)
            (stale / "partial.mp4").write_bytes(b"half")

            validated = {
                "kind": "video",
                "title": "Demo",
                "media_url": "https://cdn.example/video.mp4",
                "duration": 3.0,
            }
            recognized = {
                "text": "你好世界",
                "segments": [{"text": "你好世界", "start": 0, "end": 3}],
                "content_complete": True,
                "content_basis": "full_video_local_asr",
                "recognition_verified": False,
                "media_evidence": {"duration": 3, "audio": True},
            }

            def fake_download(media_url, dest):
                dest.write_bytes(b"complete-video")
                return {"bytes": 14}

            def fake_inspect(files):
                return [{"file": p.name, "kind": "video", "probe_ok": True, "audio": True, "duration": "3"} for p in files]

            args = argparse.Namespace(
                **{**options, "whisper_model": model, "page_data": page},
                url=url,
                folder=folder,
                resume=True,
                refresh=True,
                stdout=False,
                json=False,
            )
            with patch.object(cli, "find_tool", return_value="tool"), \
                    patch.object(toutiao, "validate_page", return_value=validated), \
                    patch.object(toutiao, "download_video", side_effect=fake_download), \
                    patch.object(content.read_asr, "probe", return_value=(3.0, True)), \
                    patch.object(cli, "inspect_files", side_effect=fake_inspect), \
                    patch.object(toutiao, "transcribe", return_value=recognized), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = content.read_videos(args)
            self.assertEqual(code, 0)
            self.assertFalse(stale.exists())
            self.assertEqual(read_store.read_json(folder / ".read-job.json")["status"], "completed")

            # Symlinked .toutiao-download is refused.
            folder2 = self.root / "toutiao-symlink"
            folder2.mkdir(mode=0o700)
            outside = self.root / "outside-dir"
            outside.mkdir()
            (outside / "keep.txt").write_text("safe")
            read_store.checkpoint(folder2, url, options)
            (folder2 / ".toutiao-download").symlink_to(outside, target_is_directory=True)
            args2 = argparse.Namespace(
                **{**options, "whisper_model": model, "page_data": page},
                url=url,
                folder=folder2,
                resume=True,
                refresh=True,
                stdout=False,
                json=False,
            )
            with patch.object(cli, "find_tool", return_value="tool"), \
                    patch.object(toutiao, "validate_page", return_value=validated), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaisesRegex(ValueError, "substituted Toutiao download directory"):
                    content.read_videos(args2)
            self.assertEqual((outside / "keep.txt").read_text(), "safe")

    def test_write_text_is_atomic_and_never_leaves_partial_export_on_error(self):
        folder = self.root / "exports"
        folder.mkdir()
        target = folder / "content.md"

        with patch.object(read_formats.os, "fsync", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                read_formats.write_text(target, "# Complete export\n")

        self.assertFalse(target.exists(), "Failed write_text left a partial export file on disk")
        self.assertEqual(list(folder.iterdir()), [], "Failed write_text left a temporary file behind")

        # Retrying write_text now succeeds cleanly.
        read_formats.write_text(target, "# Complete export\n")
        self.assertEqual(target.read_text(), "# Complete export\n")
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

        # Idempotent identical write succeeds; conflicting write preserves original.
        read_formats.write_text(target, "# Complete export\n")
        with self.assertRaisesRegex(ValueError, "different text"):
            read_formats.write_text(target, "# Conflicting export\n")
        self.assertEqual(target.read_text(), "# Complete export\n")

    def test_redownload_failed_photos_import_preserves_recoverable_receipt(self):
        payload = b"recoverable redownload payload"
        library = self.root / "Photos.photoslibrary"
        managed = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpg"
        managed.parent.mkdir(parents=True)
        source = self.root / "dl/photo.jpg"
        source.parent.mkdir()
        source.write_bytes(payload)

        task = jobs.Journal.create(["https://example.com/p/1"], argparse.Namespace())
        stage = self.root / "linkget-redownload-stage"
        stage.mkdir()
        staged_dl = stage / "photo.jpg"
        self._write_owned(staged_dl, payload)
        ready = {"files": [proof.record() for proof in [ownership.FileProof.from_record(jobs.snapshot([staged_dl])[0])]], "live_pairs": [], "evidence": {}}

        def osascript_first_fail_verify(command, **kwargs):
            return CompletedProcess(command, 0, "1:0:1\n" + self.identifier_a, "")

        # Initial import gets identifier_a, then library verification fails -> recoverable receipt kept.
        with jobs.item(task, 1), patch.object(destinations.subprocess, "run", side_effect=osascript_first_fail_verify), \
                patch.object(photos, "library_directory", return_value=library):
            with self.assertRaises(RuntimeError):
                destinations.import_photos([staged_dl], False, None, photos.retain_originals, photos.verify_library_copies)

        saved_receipt = jobs.Journal.load(task.identifier).data["items"][0]["receipt"]
        self.assertEqual(saved_receipt["identifiers"], [self.identifier_a])
        staged_orig = Path(saved_receipt["staged"][0]["path"])
        self.assertTrue(staged_orig.is_file())
        task.update(1, ready=ready, stage=str(stage), status="failed")

        # Simulate retry --redownload start (cli._has_retained_receipt preserves receipt) and failing second osascript.
        self.assertTrue(cli._has_retained_receipt(jobs.Journal.load(task.identifier).data["items"][0]))
        redl_dir = self.root / "redl-source"
        redl_dir.mkdir()
        redl_file = redl_dir / "photo.jpg"
        self._write_owned(redl_file, payload)

        def osascript_retry_fails(command, **kwargs):
            return CompletedProcess(command, 1, "", "Photos busy")

        with jobs.item(task, 1), patch.object(destinations.subprocess, "run", side_effect=osascript_retry_fails):
            with self.assertRaisesRegex(RuntimeError, "Photos could not import"):
                destinations.import_photos([redl_file], False, None, photos.retain_originals, photos.verify_library_copies)

        after_retry = jobs.Journal.load(task.identifier).data["items"][0]["receipt"]
        self.assertEqual(after_retry["identifiers"], [self.identifier_a])
        self.assertEqual(after_retry["staged"], saved_receipt["staged"])

        # Now Photos library copy appears and recover --verify completes using the preserved receipt.
        managed.write_bytes(payload)
        with patch.object(cli.sys, "platform", "darwin"), patch.object(photos, "library_directory", return_value=library):
            code, out, err = self._run_cli("recover", task.identifier, "--verify")
        self.assertEqual(code, 0, out + err)
        self.assertIn("1 staged copies removed", out)
        self.assertEqual(jobs.Journal.load(task.identifier).needs_verification(), [])
        self.assertFalse(staged_orig.exists())

    def test_partial_photos_cleanup_prunes_receipt_for_second_verification(self):
        payload_shared = b"shared item payload"
        payload_unique = b"unique item payload"
        library = self.root / "Photos.photoslibrary"
        managed_a1 = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpg"
        managed_a2 = library / "originals/A/A1FB5033-D91F-4403-B3B0-0F6CB14B09AF.jpg"
        managed_b = library / "originals/B/B2CC6144-E02A-4514-C4C1-107DC25C10B0.jpg"
        identifier_c = "B2CC6144-E02A-4514-C4C1-107DC25C10B0/L0/001"
        for target in (managed_a1, managed_a2, managed_b):
            target.parent.mkdir(parents=True, exist_ok=True)

        dl_a = self.root / "dl-a"
        dl_b = self.root / "dl-b"
        dl_a.mkdir()
        dl_b.mkdir()
        src_a1 = dl_a / "one.jpg"
        src_a2 = dl_a / "two.jpg"
        src_b = dl_b / "one.jpg"
        self._write_owned(src_a1, payload_shared)
        self._write_owned(src_a2, payload_unique)
        self._write_owned(src_b, payload_shared)

        task_a = jobs.Journal.create(["https://example.com/a"], argparse.Namespace())
        task_b = jobs.Journal.create(["https://example.com/b"], argparse.Namespace())

        def osascript_a(command, **kwargs):
            return CompletedProcess(command, 0, f"2:0:1:2\n{self.identifier_a}\t{self.identifier_b}", "")

        with jobs.item(task_a, 1), patch.object(destinations.subprocess, "run", side_effect=osascript_a), \
                patch.object(photos, "library_directory", return_value=library):
            with self.assertRaises(RuntimeError):
                destinations.import_photos([src_a1, src_a2], False, None, photos.retain_originals, photos.verify_library_copies)

        def osascript_b(command, **kwargs):
            return CompletedProcess(command, 0, f"1:0:1\n{identifier_c}", "")

        with jobs.item(task_b, 1), patch.object(destinations.subprocess, "run", side_effect=osascript_b), \
                patch.object(photos, "library_directory", return_value=library):
            with self.assertRaises(RuntimeError):
                destinations.import_photos([src_b], False, None, photos.retain_originals, photos.verify_library_copies)

        rec_a = jobs.Journal.load(task_a.identifier).data["items"][0]["receipt"]
        orig_shared = Path(rec_a["staged"][0]["path"])
        orig_unique = Path(rec_a["staged"][1]["path"])

        # Task A verifies while Task B still references orig_shared: orig_unique is removed and pruned from receipt.
        managed_a1.write_bytes(payload_shared)
        managed_a2.write_bytes(payload_unique)
        with patch.object(cli.sys, "platform", "darwin"), patch.object(photos, "library_directory", return_value=library):
            code1, out1, err1 = self._run_cli("recover", task_a.identifier, "--verify")
        self.assertEqual(code1, 0, out1 + err1)
        self.assertIn("1 staged copies removed · 1 kept", out1)
        self.assertFalse(orig_unique.exists())
        self.assertTrue(orig_shared.is_file())
        pruned_rec = jobs.Journal.load(task_a.identifier).data["items"][0]["receipt"]
        self.assertEqual([e["path"] for e in pruned_rec["files"]], [str(orig_shared)])
        self.assertEqual([e["path"] for e in pruned_rec["staged"]], [str(orig_shared)])
        self.assertEqual(pruned_rec["identifiers"], [self.identifier_a])

        # Task B verifies and clears its receipt, then Task A verifies again cleanly without failing on deleted orig_unique.
        managed_b.write_bytes(payload_shared)
        with patch.object(cli.sys, "platform", "darwin"), patch.object(photos, "library_directory", return_value=library):
            code_b, _, err_b = self._run_cli("recover", task_b.identifier, "--verify")
        self.assertEqual(code_b, 0, err_b)
        with patch.object(cli.sys, "platform", "darwin"), patch.object(photos, "library_directory", return_value=library):
            code2, out2, err2 = self._run_cli("recover", task_a.identifier, "--verify")
        self.assertEqual(code2, 0, out2 + err2)
        self.assertIn("1 staged copies removed", out2)
        self.assertFalse(orig_shared.exists())
        self.assertEqual(jobs.Journal.load(task_a.identifier).needs_verification(), [])

    def test_read_tasks_refresh_holds_worker_lock_across_status_write(self):
        with patch.object(read_store, "root", return_value=self.root / "private"):
            read_tasks.directory().mkdir(parents=True, exist_ok=True)
            identifier = "20260101T000001-abcdef012345"
            record, _, claim, _ = read_tasks.paths(identifier)
            read_store.write_json(record, {
                "schema": 1,
                "id": identifier,
                "kind": "read",
                "argv": ["https://example.com/a"],
                "created": time.time() - 60,
                "status": "running",
                "phase": "read",
                "outputs": [],
                "pid": 999999,
            })

            # While worker holds .worker.lock, refresh does not interrupt.
            with read_store.lock(claim):
                kept = read_tasks.refresh(identifier)
            self.assertEqual(kept["status"], "running")

            # When refresh acquires .worker.lock, the lock remains held across the terminal status write.
            real_write = read_store.write_json
            lock_state = []

            def spy_write(path, value):
                if path == record:
                    fd = os.open(claim, os.O_RDONLY | os.O_NOFOLLOW)
                    try:
                        try:
                            read_tasks.fcntl.flock(fd, read_tasks.fcntl.LOCK_EX | read_tasks.fcntl.LOCK_NB)
                            lock_state.append("unlocked")
                        except BlockingIOError:
                            lock_state.append("locked")
                    finally:
                        os.close(fd)
                return real_write(path, value)

            with patch.object(read_tasks.read_store, "write_json", side_effect=spy_write):
                stopped = read_tasks.refresh(identifier)
            self.assertEqual(lock_state, ["locked"])
            self.assertEqual(stopped["status"], "interrupted")

    def test_read_tasks_worker_reconciles_completed_read_job_on_post_commit_interrupt(self):
        with patch.object(read_store, "root", return_value=self.root / "private"):
            read_tasks.directory().mkdir(parents=True, exist_ok=True)
            folder = self.root / "done-read"
            folder.mkdir(mode=0o700)
            read_store.write_json(folder / "content.json", {"schema_version": 2, "text": "Complete"})
            read_store.write_json(folder / ".read-job.json", {
                "schema": read_store.SCHEMA,
                "id": "job123",
                "folder": str(folder),
                "source": None,
                "options": {},
                "status": "completed",
                "created": time.time(),
            })

            # 1) Worker interrupted right after committing .read-job.json settles as completed (exit 0).
            task_worker = "20260101T000002-abcdef012345"
            rec_w, _, _, _ = read_tasks.paths(task_worker)
            read_store.write_json(rec_w, {
                "schema": 1,
                "id": task_worker,
                "kind": "read",
                "argv": ["https://example.com/w"],
                "created": time.time(),
                "status": "queued",
                "phase": "queued",
                "outputs": [],
                "pid": None,
            })

            def interrupt_after_commit(argv):
                read_tasks.record_output(folder)
                raise KeyboardInterrupt("SIGINT after commit")

            with patch.object(content, "main", side_effect=interrupt_after_commit):
                exit_code = read_tasks.worker(task_worker)
            self.assertEqual(exit_code, 0)
            loaded_w = read_tasks.load(task_worker)
            self.assertEqual(loaded_w["status"], "completed")
            self.assertEqual(loaded_w["exit_code"], 0)
            self.assertIsNone(loaded_w["failure"])

            # 2) Refresh after a hard crash following .read-job.json commit also settles as completed.
            task_crash = "20260101T000003-abcdef012345"
            rec_c, _, _, _ = read_tasks.paths(task_crash)
            read_store.write_json(rec_c, {
                "schema": 1,
                "id": task_crash,
                "kind": "read",
                "argv": ["https://example.com/c"],
                "created": time.time() - 60,
                "status": "running",
                "phase": "read",
                "outputs": [str(folder)],
                "pid": 999999,
                "cancel_requested": True,
            })
            refreshed = read_tasks.refresh(task_crash)
            self.assertEqual(refreshed["status"], "completed")
            self.assertEqual(refreshed["exit_code"], 0)


if __name__ == "__main__":
    unittest.main()
