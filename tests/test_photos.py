"""Preserve original bytes without invoking Photos or touching the user's library."""
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, cli, photos


class PhotosSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        mocked = patch.object(accounts, "directory", return_value=self.root / "linkget/sessions")
        mocked.start()
        self.addCleanup(mocked.stop)

    def file(self, folder, data):
        path = self.root / folder / "same.jpg"
        path.parent.mkdir()
        path.write_bytes(data)
        return path

    def test_original_survives_download_cleanup_and_is_used_for_import(self):
        source = self.file("download", b"first image")
        def importer(command, **kwargs):
            retained = Path(command[-1])
            self.assertEqual(retained.parent, photos.originals_directory())
            self.assertEqual(retained.read_bytes(), source.read_bytes())
            self.assertEqual(retained.stat().st_mode & 0o777, 0o600)
            return CompletedProcess(command, 0, "1:0:1", "")
        with patch.object(cli.subprocess, "run", side_effect=importer):
            self.assertEqual(cli.import_photos([source], False), ([source], 0))
        source.unlink()
        self.assertEqual(next(photos.originals_directory().iterdir()).read_bytes(), b"first image")

    def test_same_name_different_content_is_distinct_and_same_content_is_reused(self):
        a = self.file("a", b"first")
        b = self.file("b", b"second")
        c = self.file("c", b"first")
        first, second, same = photos.retain_originals([a, b, c])
        self.assertNotEqual(first, second)
        self.assertEqual(first, same)
        self.assertEqual(len(list(photos.originals_directory().iterdir())), 2)
        with patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "2:0:1:2", "")) as run:
            self.assertEqual(cli.import_photos([a, b, c], False), ([a, b], 1))
            self.assertEqual(len(run.call_args.args[0]), 5)  # osascript, script, date option, two originals

    def test_failed_import_keeps_original_and_never_claims_success(self):
        source = self.file("a", b"video-or-image")
        with patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 1, "", "Not authorized")):
            with self.assertRaisesRegex(RuntimeError, "Originals are safe"):
                cli.import_photos([source], False)
        self.assertEqual(next(photos.originals_directory().iterdir()).read_bytes(), source.read_bytes())

    def test_partial_original_copy_is_removed_and_download_retained(self):
        source = self.file("a", b"original")
        def fail(source, target):
            target.write(b"partial")
            raise OSError("disk full")
        with patch.object(photos.shutil, "copyfileobj", side_effect=fail):
            with self.assertRaises(OSError):
                photos.retain_originals([source])
        self.assertTrue(source.is_file())
        self.assertEqual(list(photos.originals_directory().iterdir()), [])

    def test_folder_failure_removes_partial_destination_and_retains_source(self):
        source = self.file("a", b"original")
        destination = self.root / "out"
        with patch.object(cli.shutil, "copyfileobj", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                cli.save_folder([source], destination)
        self.assertFalse((destination / source.name).exists())
        self.assertTrue(source.is_file())

    def test_numbered_collision_is_also_deduplicated(self):
        source = self.file("a", b"original")
        destination = self.root / "out"
        destination.mkdir()
        (destination / source.name).write_bytes(b"different content")
        cli.save_folder([source], destination)
        self.assertEqual(cli.save_folder([source], destination), ([], 1))
        self.assertEqual(len(list(destination.iterdir())), 2)


if __name__ == "__main__":
    unittest.main()
