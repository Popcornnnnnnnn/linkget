"""Preserve original bytes without invoking Photos or touching the user's library."""
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, cli, photos


class PhotosSafetyTests(unittest.TestCase):
    identifier = "90FA4F22-C80E-43F2-A2AF-FE5BA03A989E/L0/001"

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

    def test_verified_import_removes_staging_copy_but_keeps_library_copy(self):
        source = self.file("download", b"first image")
        library = self.root / "Photos.photoslibrary"
        library_file = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpeg"
        library_file.parent.mkdir(parents=True)
        def importer(command, **kwargs):
            retained = Path(command[-1])
            self.assertEqual(retained.parent, photos.originals_directory())
            self.assertEqual(retained.read_bytes(), source.read_bytes())
            self.assertEqual(retained.stat().st_mode & 0o777, 0o600)
            library_file.write_bytes(retained.read_bytes())
            return CompletedProcess(command, 0, "1:0:1\n" + self.identifier, "")
        with patch.object(cli.subprocess, "run", side_effect=importer), patch.object(photos, "library_directory", return_value=library):
            self.assertEqual(cli.import_photos([source], False), ([source], 0))
        source.unlink()
        self.assertEqual(list(photos.originals_directory().iterdir()), [])
        self.assertEqual(library_file.read_bytes(), b"first image")

    def test_same_name_different_content_is_distinct_and_same_content_is_reused(self):
        a = self.file("a", b"first")
        b = self.file("b", b"second")
        c = self.file("c", b"first")
        first, second, same = photos.retain_originals([a, b, c])
        self.assertNotEqual(first, second)
        self.assertEqual(first, same)
        self.assertEqual(len(list(photos.originals_directory().iterdir())), 2)
        with patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "2:0:1:2", "")) as run, patch.object(cli, "verify_library_copies"):
            self.assertEqual(cli.import_photos([a, b, c], False), ([a, b], 1))
            self.assertEqual(len(run.call_args.args[0]), 5)  # osascript, script, date option, two originals

    def test_referenced_or_changed_copy_is_not_deleted_after_successful_import(self):
        source = self.file("download", b"original bytes")
        library = self.root / "Photos.photoslibrary"
        library_file = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpeg"
        library_file.parent.mkdir(parents=True)
        library_file.write_bytes(b"different data")
        response = CompletedProcess([], 0, "1:0:1\n" + self.identifier, "")
        for kind in ("missing", "different", "symlink"):
            with self.subTest(kind=kind):
                library_file.unlink(missing_ok=True)
                if kind == "different":
                    library_file.write_bytes(b"different data")
                elif kind == "symlink":
                    library_file.symlink_to(source)
                with patch.object(cli.subprocess, "run", return_value=response), patch.object(photos, "library_directory", return_value=library):
                    with self.assertRaisesRegex(RuntimeError, "Originals are safe"):
                        cli.import_photos([source], False)
                self.assertEqual(next(photos.originals_directory().iterdir()).read_bytes(), source.read_bytes())

    def test_duplicate_is_verified_and_staging_copy_is_removed(self):
        source = self.file("download", b"original bytes")
        library = self.root / "Photos.photoslibrary"
        library_file = library / "originals/9/90FA4F22-C80E-43F2-A2AF-FE5BA03A989E.jpeg"
        library_file.parent.mkdir(parents=True)
        library_file.write_bytes(source.read_bytes())
        with patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "0:1\n" + self.identifier, "")), patch.object(photos, "library_directory", return_value=library):
            self.assertEqual(cli.import_photos([source], False), ([], 1))
        self.assertEqual(list(photos.originals_directory().iterdir()), [])

    def test_identical_copy_belonging_to_another_asset_does_not_pass(self):
        source = self.file("download", b"original bytes")
        library = self.root / "Photos.photoslibrary"
        unrelated = library / "originals/9/99999999-C80E-43F2-A2AF-FE5BA03A989E.jpeg"
        unrelated.parent.mkdir(parents=True)
        unrelated.write_bytes(source.read_bytes())
        with patch.object(photos, "library_directory", return_value=library):
            with self.assertRaisesRegex(RuntimeError, "verified copy"):
                photos.verify_library_copies([source], [self.identifier])

    def test_missing_identifiers_keep_staging_files(self):
        source = self.file("download", b"original bytes")
        with patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "1:0:1", "")):
            with self.assertRaisesRegex(RuntimeError, "Originals are safe"):
                cli.import_photos([source], False)
        self.assertEqual(next(photos.originals_directory().iterdir()).read_bytes(), source.read_bytes())

    def test_library_discovery_uses_running_app_and_ignores_internal_library(self):
        library = self.root / "Photos.photoslibrary"
        library.mkdir()
        internal = Path.home() / "Library/Photos/Libraries/Syndication.photoslibrary"
        paths = ("p42\nn" + str(internal / "database/Photos.sqlite")
                 + "\nn" + str(library / "database/Photos.sqlite")
                 + "\nn" + str(library / "database/Photos.sqlite") + "\n")
        responses = [CompletedProcess([], 0, "42\n", ""), CompletedProcess([], 0, paths, "")]
        with patch.object(photos.subprocess, "run", side_effect=responses):
            self.assertEqual(photos.library_directory(), library)

    def test_ambiguous_or_missing_open_library_is_not_guessed(self):
        first = self.root / "First.photoslibrary"
        second = self.root / "Second.photoslibrary"
        for paths in ("", "n" + str(first / "database/Photos.sqlite") + "\nn" + str(second / "database/Photos.sqlite")):
            with self.subTest(paths=paths):
                responses = [CompletedProcess([], 0, "42\n", ""), CompletedProcess([], 0, paths, "")]
                with patch.object(photos.subprocess, "run", side_effect=responses):
                    with self.assertRaisesRegex(RuntimeError, "single open Photos library"):
                        photos.library_directory()

    def test_open_library_query_failure_reports_actual_reason(self):
        responses = [CompletedProcess([], 0, "42\n", ""), CompletedProcess([], 1, "", "permission denied")]
        with patch.object(photos.subprocess, "run", side_effect=responses):
            with self.assertRaisesRegex(RuntimeError, "permission denied"):
                photos.library_directory()

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
