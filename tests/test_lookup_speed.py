"""Lookup counts and safety with isolated files and an offline Photos adapter."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

from linkget import accounts, hashing, jobs, links, media, photos, runtime


class DigestReuseTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "image.jpg"
        self.source.write_bytes(b"original")

    def test_inspection_retention_and_receipts_reuse_unchanged_hashes(self):
        reads = []
        original_open = Path.open
        def opened(path, mode="r", *args, **kwargs):
            if mode == "rb":
                reads.append(path)
            return original_open(path, mode, *args, **kwargs)
        with patch.object(accounts, "directory", return_value=self.root / "settings/sessions"), patch.object(media.shutil, "which", return_value=None), patch.object(Path, "open", opened), jobs.item(None, 1):
            record = media.inspect_files([self.source])[0]
            retained = photos.retain_originals([self.source])
            first = jobs.snapshot(retained)
            second = jobs.snapshot(retained)
        self.assertEqual(record["sha256"], first[0]["sha256"])
        self.assertEqual(first, second)
        self.assertEqual(reads.count(self.source), 2)  # hash once, copy once
        self.assertEqual(reads.count(retained[0]), 1)
        # A new task does a new read; no persistent/global content cache.
        with patch.object(Path, "open", opened), jobs.item(None, 1):
            hashing.digest(self.source)
        self.assertEqual(reads.count(self.source), 3)

    def test_same_size_edit_and_replacement_invalidate_digest(self):
        with hashing.digest_session():
            old = hashing.digest(self.source)
            info = self.source.stat()
            self.source.write_bytes(b"modified")
            os.utime(self.source, ns=(info.st_atime_ns, info.st_mtime_ns))
            changed = hashing.digest(self.source)
            self.assertNotEqual(old, changed)
            replacement = self.root / "replacement"
            replacement.write_bytes(b"replaced")
            os.utime(replacement, ns=(info.st_atime_ns, info.st_mtime_ns))
            replacement.replace(self.source)
            self.assertNotEqual(changed, hashing.digest(self.source))

    def test_change_during_hash_is_rejected(self):
        factory = hashlib.sha256
        source = self.source
        class ChangingHash:
            def __init__(self):
                self.value = factory()
            def update(self, data):
                self.value.update(data)
                source.write_bytes(b"modified")
            def hexdigest(self):
                return self.value.hexdigest()
        with patch.object(hashing.hashlib, "sha256", ChangingHash), hashing.digest_session():
            with self.assertRaisesRegex(RuntimeError, "changed during"):
                hashing.digest(source)

    def test_nested_import_scope_reuses_outer_cache(self):
        with patch.object(hashing.hashlib, "sha256", wraps=hashlib.sha256) as hashed:
            with hashing.digest_session():
                hashing.digest(self.source)
                with hashing.digest_session():
                    hashing.digest(self.source)
                hashing.digest(self.source)
            self.assertEqual(hashed.call_count, 1)
            hashing.digest(self.source)
            self.assertEqual(hashed.call_count, 2)


class LibraryLookupTests(unittest.TestCase):
    def test_one_scan_per_bucket_and_both_live_resources_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            library = root / "Test.photoslibrary"
            files, identifiers = [], []
            for index in range(64):
                asset = f"90000000-0000-0000-0000-{index:012X}"
                source = root / f"{index}.jpg"
                source.write_bytes(str(index).encode())
                target = library / "originals/9" / (asset + ".jpeg")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
                files.append(source)
                identifiers.append(asset + "/L0/001")
            motion = root / "motion.mov"
            motion.write_bytes(b"motion track")
            (library / "originals/9" / (identifiers[0].split('/')[0] + ".mov")).write_bytes(motion.read_bytes())
            files.append(motion)
            identifiers.append(identifiers[0])
            with patch.object(photos, "library_directory", return_value=library), patch.object(Path, "iterdir", autospec=True, side_effect=Path.iterdir) as scanned:
                photos.verify_library_copies(files, identifiers)
                self.assertEqual(scanned.call_count, 1)
                motion.write_bytes(b"broken track")
                with self.assertRaisesRegex(RuntimeError, "verified copy"):
                    photos.verify_library_copies(files, identifiers)
                self.assertEqual(scanned.call_count, 2)

    def test_final_verification_reads_previously_cached_source_and_library(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.jpg"
            source.write_bytes(b"original")
            asset = "90FA4F22-C80E-43F2-A2AF-FE5BA03A989E"
            library = root / "Test.photoslibrary"
            target = library / "originals/9" / (asset + ".jpg")
            target.parent.mkdir(parents=True)
            target.write_bytes(source.read_bytes())
            with hashing.digest_session(), patch.object(hashing.hashlib, "sha256", wraps=hashlib.sha256) as hashed, patch.object(photos, "library_directory", return_value=library):
                hashing.digest(source)
                hashing.digest(target)
                self.assertEqual(hashed.call_count, 2)
                photos.verify_library_copies([source], [asset + "/L0/001"])
                self.assertEqual(hashed.call_count, 4)


class Response(io.BytesIO):
    code = 302
    def __init__(self, location):
        super().__init__()
        self.headers = {"Location": location}


class ResolutionReuseTests(unittest.TestCase):
    def state(self):
        return {"cancelled": threading.Event(), "resolution_cache": links.ResolutionCache()}

    def normalize(self, text, state):
        runtime._batch.state = state
        try:
            return links.normalize_link(text)
        finally:
            del runtime._batch.state

    def test_concurrent_shared_text_uses_one_network_resolution(self):
        state = self.state()
        barrier = threading.Barrier(4)
        entered, release = threading.Event(), threading.Event()
        def opened(request, **kwargs):
            entered.set()
            self.assertTrue(release.wait(2))
            return Response("https://www.douyin.com/note/123")
        def worker(index):
            barrier.wait(timeout=2)
            return self.normalize(f"分享{index} https://v.douyin.com/abc/", state)
        with patch.object(links, "build_opener") as build, ThreadPoolExecutor(max_workers=4) as pool:
            build.return_value.open.side_effect = opened
            futures = [pool.submit(worker, i) for i in range(4)]
            self.assertTrue(entered.wait(2))
            release.set()
            self.assertEqual([f.result(timeout=2) for f in futures], ["https://www.douyin.com/note/123"] * 4)
            self.assertEqual(build.return_value.open.call_count, 1)
            self.normalize("https://v.douyin.com/abc/", self.state())
            self.assertEqual(build.return_value.open.call_count, 2)

    def test_tokens_stay_separate_and_invalid_inputs_still_rejected(self):
        state = self.state()
        post = "https://www.xiaohongshu.com/explore/" + "a" * 24
        def opened(request, **kwargs):
            return Response(post + "?" + request.full_url.split("?", 1)[1])
        with patch.object(links, "build_opener") as build:
            build.return_value.open.side_effect = opened
            for token in ("one", "two", "one"):
                self.assertEqual(self.normalize("https://xhslink.com/abc?xsec_token=" + token, state), post + "?xsec_token=" + token)
            self.assertEqual(build.return_value.open.call_count, 2)
            with self.assertRaises(ValueError):
                self.normalize("https://xhslink.com/abc https://xhslink.com/def", state)
            self.assertEqual(build.return_value.open.call_count, 2)

    def test_failure_is_not_cached_and_redirect_boundary_is_kept(self):
        state = self.state()
        with patch.object(links, "build_opener") as build:
            build.return_value.open.side_effect = [URLError("offline"), Response("https://www.douyin.com/video/123")]
            with self.assertRaises(RuntimeError):
                self.normalize("https://v.douyin.com/abc/", state)
            self.assertEqual(self.normalize("https://v.douyin.com/abc/", state), "https://www.douyin.com/video/123")
            self.assertEqual(build.return_value.open.call_count, 2)
            build.return_value.open.side_effect = lambda *a, **k: Response("https://outside.example/video/123")
            with self.assertRaisesRegex(ValueError, "outside its platform"):
                self.normalize("https://v.douyin.com/other/", state)
            self.assertEqual(build.return_value.open.call_count, 3)

    def test_waiter_cancels_without_waiting_for_network_owner(self):
        state = self.state()
        entered, release = threading.Event(), threading.Event()
        def owner():
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test owner stalled")
            return "post"
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(state["resolution_cache"].resolve, "url", owner)
            self.assertTrue(entered.wait(2))
            def waiter():
                runtime._batch.state = state
                try:
                    return state["resolution_cache"].resolve("url", lambda: self.fail("second network owner"))
                finally:
                    del runtime._batch.state
            second = pool.submit(waiter)
            state["cancelled"].set()
            try:
                with self.assertRaises(KeyboardInterrupt):
                    second.result(timeout=1)
            finally:
                release.set()
            self.assertEqual(first.result(timeout=2), "post")

    def test_shared_failure_releases_waiters_and_next_attempt_retries(self):
        cache = links.ResolutionCache()
        entered, release = threading.Event(), threading.Event()
        def failed():
            entered.set()
            if not release.wait(2):
                raise RuntimeError("test owner stalled")
            raise RuntimeError("network failure")
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(cache.resolve, "url", failed)
            self.assertTrue(entered.wait(2))
            second = pool.submit(cache.resolve, "url", failed)
            release.set()
            for result in (first, second):
                with self.assertRaisesRegex(RuntimeError, "network failure"):
                    result.result(timeout=2)
        self.assertEqual(cache.resolve("url", lambda: "new result"), "new result")

    def test_resolver_timeout_is_propagated_without_hanging(self):
        def timeout():
            raise TimeoutError("network timeout")
        with self.assertRaisesRegex(TimeoutError, "network timeout"):
            links.ResolutionCache().resolve("url", timeout)


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("osacompile"), "macOS AppleScript required")
class PhotosScriptTests(unittest.TestCase):
    def run_importer(self, names, imported, existing=None, date_now=False):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            script = Path(photos.__file__).with_name("import.applescript")
            compiled = root / "import.scpt"
            subprocess.run(["/usr/bin/osacompile", "-o", str(compiled), str(script)], check=True, capture_output=True)
            paths = []
            for name in names:
                path = root / name
                path.write_bytes(b"offline fixture")
                paths.append(str(path))
            def literal(value):
                if isinstance(value, list):
                    return "{" + ", ".join(literal(v) for v in value) + "}"
                return json.dumps(value)
            driver = root / "fake.applescript"
            driver.write_text('''on run argv
    set importer to load script POSIX file (item 1 of argv)
    script fakePhotos
        property parent : importer
        property queryCount : 0
        property importedNames : ''' + literal(imported) + '''
        property existingNames : ''' + literal(list(existing or [])) + '''
        on run inputs
            set output to continue run inputs
            return output & linefeed & "queries=" & (my queryCount)
        end run
        on existingIdentifier(currentName)
            set my queryCount to (my queryCount) + 1
            if my existingNames contains currentName then return "EXISTING/L0/001"
            return ""
        end existingIdentifier
        on importFiles(sourceFiles)
            return my importedNames
        end importFiles
        on importedIdentity(importedItem, dateMode, importedAt)
            return importedItem
        end importedIdentity
    end script
    return run script fakePhotos with parameters (items 2 thru -1 of argv)
end run
''')
            return subprocess.run(["/usr/bin/osascript", str(driver), str(compiled), "--date-now" if date_now else "--keep-date", *paths], text=True, capture_output=True, timeout=10)

    def test_shuffled_results_duplicates_and_motion_first_pair_need_one_lookup_each(self):
        result = self.run_importer(["new.jpg", "existing.jpg", "linkget-live-pair.mov", "linkget-live-pair.jpg"], [["linkget-live-pair.jpg", "LIVE/L0/001"], ["new.jpg", "NEW/L0/001"]], existing=["existing.jpg"], date_now=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "3:1:1:3:4\nNEW/L0/001\tEXISTING/L0/001\tLIVE/L0/001\tLIVE/L0/001\nqueries=3")

    def test_existing_live_pair_queries_only_once(self):
        result = self.run_importer(["linkget-live-pair.jpg", "linkget-live-pair.mov"], [], existing=["linkget-live-pair.jpg"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "0:2\nEXISTING/L0/001\tEXISTING/L0/001\nqueries=1")

    def test_unknown_or_repeated_import_result_and_incomplete_pair_fail_safely(self):
        for names, imported in ((["first.jpg"], [["unknown.jpg", "X"]]), (["first.jpg", "second.jpg"], [["first.jpg", "X"], ["first.jpg", "X"]]), (["linkget-live-pair.mov"], []), (["linkget-live-pair.jpg"], [])):
            with self.subTest(names=names, imported=imported):
                self.assertNotEqual(self.run_importer(names, imported).returncode, 0)
