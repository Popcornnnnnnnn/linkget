"""Live pair safety and import bookkeeping; no Photos access or UI activation."""
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, cli, live, photos


class LivePairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.sources = self.root / 'sources'
        self.sources.mkdir()
        self.image = self.sources / 'linkget-live-test.jpg'
        self.video = self.image.with_suffix('.mov')
        self.image.write_bytes(b'image')
        self.video.write_bytes(b'motion')
        self.pair = (self.image, self.video)

    def test_collision_keeps_pair_names_aligned_and_repeat_is_skipped(self):
        folder = self.root / 'saved'
        folder.mkdir()
        (folder / self.video.name).write_bytes(b'existing')
        self.assertEqual(live.save_pairs([self.pair], folder), ([self.image, self.video], 0))
        self.assertEqual((folder / 'linkget-live-test-1.jpg').read_bytes(), b'image')
        self.assertEqual((folder / 'linkget-live-test-1.mov').read_bytes(), b'motion')
        self.assertEqual(live.save_pairs([self.pair], folder), ([], 2))
        self.assertEqual((folder / self.video.name).read_bytes(), b'existing')

    def test_partial_pair_copy_removes_only_files_created_by_this_save(self):
        folder = self.root / 'saved'
        actual_copy = live.shutil.copyfileobj
        calls = 0
        def fail_second(source, output):
            nonlocal calls
            calls += 1
            if calls == 2:
                output.write(b'partial')
                raise OSError('disk full')
            actual_copy(source, output)
        with patch.object(live.shutil, 'copyfileobj', side_effect=fail_second):
            with self.assertRaises(OSError):
                live.save_pairs([self.pair], folder)
        self.assertEqual(list(folder.iterdir()), [])
        self.assertTrue(self.image.exists())
        self.assertTrue(self.video.exists())

    def test_retained_component_race_never_deletes_existing_file(self):
        folder = self.root / 'retained'
        target = folder / self.image.name
        real_open = Path.open
        def racing_open(path, mode='r', *args, **kwargs):
            if path == target and mode == 'xb':
                with real_open(path, 'wb') as output:
                    output.write(b'another process')
            return real_open(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', racing_open):
            with self.assertRaises(FileExistsError):
                live.retain_pairs([self.pair], folder)
        self.assertEqual(target.read_bytes(), b'another process')

    def test_content_mismatch_preserves_retained_original(self):
        folder = self.root / 'retained'
        folder.mkdir()
        target = folder / self.image.name
        target.write_bytes(b'previous data')
        with self.assertRaisesRegex(RuntimeError, 'content check'):
            live.retain_pairs([self.pair], folder)
        self.assertEqual(target.read_bytes(), b'previous data')

    def test_photos_receives_persistent_pair_and_keeps_raw_sources(self):
        raw_image = self.sources / 'original.jpg'
        raw_video = self.sources / 'original.mp4'
        raw_image.write_bytes(b'raw picture')
        raw_video.write_bytes(b'raw video')
        sources = [raw_image, raw_video]
        with patch.object(accounts, 'directory', return_value=self.root / 'settings/sessions'):
            def importer(command, **kwargs):
                paths = list(map(Path, command[3:]))
                self.assertEqual([p.name for p in paths], [self.image.name, self.video.name])
                self.assertTrue(all(p.parent == photos.originals_directory() for p in paths))
                self.assertEqual(len(list(photos.originals_directory().iterdir())), 4)
                return CompletedProcess(command, 0, '2:0:1:2', '')
            with patch.object(cli.subprocess, 'run', side_effect=importer):
                self.assertEqual(cli.import_photos(sources, False, {(raw_image.name, raw_video.name): self.pair}), (sources, 0))
            with patch.object(cli.subprocess, 'run', return_value=CompletedProcess([], 0, '0:2', '')):
                self.assertEqual(cli.import_photos(sources, False, {(raw_image.name, raw_video.name): self.pair}), ([], 2))


if __name__ == '__main__':
    unittest.main()
