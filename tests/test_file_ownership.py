"""Writer-to-consumer evidence; generated media and mocked Photos only."""
import io
import argparse
import contextlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from linkget import cli, destinations, douyin, jobs, ownership, photos, recovery, transfers, xiaohongshu
import test_transfer_recovery as gallery_fixture
from test_transfer_recovery import Response, NOTE, URL, png

RED = png((255, 0, 0))
BLUE = png((0, 0, 255))


def replace(path, data=BLUE):
    path = Path(path)
    alternate = path.with_name('external-replacement.tmp')
    alternate.write_bytes(data)
    os.replace(alternate, path)


class FileOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.flow = gallery_fixture.GalleryRecoveryTests()
        self.flow.setUp()
        self.addCleanup(self.flow.doCleanups)
        self.root = self.flow.root
        previous = self.flow.request
        def request(value, **kwargs):
            response = previous(value, **kwargs)
            return response if '/explore/' in value.full_url else Response(RED)
        mock = patch.object(xiaohongshu, 'build_opener')
        mock.start().return_value.open.side_effect = request
        self.addCleanup(mock.stop)

    def single(self):
        self.flow.failed_urls.clear()
        self.flow.names = ('one',)
        return self.flow.command(URL, '--browser', 'none', '--folder', str(self.root/'saved'))

    def task_source(self):
        task = jobs.all_journals()[0]
        return task, Path(task.data['items'][0]['media_progress']['items'][0]['path'])

    def test_native_completion_requires_writer_evidence_for_different_and_same_bytes(self):
        complete = transfers.TransferSet.complete
        for value in (BLUE, RED):
            replaced = []
            def attack(checkpoint, index, path, *args, **kwargs):
                if not replaced:
                    replace(path, value)
                    replaced.append(Path(path))
                return complete(checkpoint, index, path, *args, **kwargs)
            with self.subTest(same_content=value == RED), patch.object(transfers.TransferSet, 'complete', attack):
                code, output = self.single()
            self.assertEqual(code, 1, output)
            self.assertEqual(replaced[0].read_bytes(), value)
            self.assertFalse(list((self.root/'saved').iterdir()))

    def test_douyin_completion_carries_its_writer_evidence(self):
        page = self.root/'page.json'
        item = {'aweme_id': '123', 'images': [{'url_list': ['https://cdn.example/still'], 'live_photo_type': 0}]}
        page.write_text(json.dumps(item))
        complete = transfers.TransferSet.complete
        replaced = []
        def attack(checkpoint, index, path, *args, **kwargs):
            replace(path)
            replaced.append(Path(path))
            return complete(checkpoint, index, path, *args, **kwargs)
        with patch.object(douyin, 'build_opener') as opener, patch.object(transfers.TransferSet, 'complete', attack):
            opener.return_value.open.return_value = Response(RED)
            code, output = self.flow.command('https://www.douyin.com/note/123', '--page-data', str(page),
                                              '--browser', 'none', '--folder', str(self.root/'saved'))
        self.assertEqual(code, 1, output)
        self.assertEqual(replaced[0].read_bytes(), BLUE)
        self.assertFalse(list((self.root/'saved').iterdir()))

    def test_native_publication_never_overwrites_existing_final_name(self):
        publish = ownership.Writer.publish
        occupied = []
        def attack(writer, target):
            Path(target).write_bytes(BLUE)
            occupied.append(Path(target))
            return publish(writer, target)
        with patch.object(ownership.Writer, 'publish', attack):
            code, output = self.single()
        self.assertEqual(code, 1, output)
        self.assertEqual(occupied[0].read_bytes(), BLUE)

    def test_partial_replacement_cannot_become_published_native_media(self):
        link = os.link
        replaced = []
        def attack(source, target, **kwargs):
            if str(source).endswith('.part') and not replaced:
                replace(source)
                replaced.append(Path(source))
            return link(source, target, **kwargs)
        with patch.object(ownership.os, 'link', attack):
            code, output = self.single()
        self.assertEqual(code, 1, output)
        self.assertEqual(replaced[0].read_bytes(), BLUE)
        self.assertFalse(list((self.root/'saved').iterdir()))

    def test_duplicate_target_changed_after_hash_does_not_complete_or_delete_source(self):
        folder = self.root/'saved'
        folder.mkdir()
        target = folder/f'xiaohongshu_{NOTE}_001_original.png'
        target.write_bytes(RED)
        digest = jobs.digest
        attacked = []
        def attack(path, **kwargs):
            value = digest(path, **kwargs)
            if Path(path).resolve() == target.resolve() and not attacked:
                replace(target)
                attacked.append(target)
            return value
        with patch.object(jobs, 'digest', side_effect=attack):
            code, output = self.single()
        self.assertEqual(code, 1, output)
        task, source = self.task_source()
        self.assertEqual(source.read_bytes(), RED)
        self.assertEqual(target.read_bytes(), BLUE)
        self.assertEqual(task.incomplete(), [1])

    def test_new_and_duplicate_destinations_remain_checked_after_save_returns(self):
        save = cli.save_folder
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate):
                folder = self.root/('saved-duplicate' if duplicate else 'saved-new')
                folder.mkdir()
                target = folder/f'xiaohongshu_{NOTE}_001_original.png'
                if duplicate:
                    target.write_bytes(RED)
                def attack(files, path):
                    result = save(files, path)
                    replace(target)
                    return result
                self.flow.failed_urls.clear()
                self.flow.names = ('one',)
                with patch.object(cli, 'save_folder', side_effect=attack):
                    code, output = self.flow.command(URL, '--browser', 'none', '--folder', str(folder))
                self.assertEqual(code, 1, output)
                task, source = self.task_source()
                self.assertEqual(source.read_bytes(), RED)
                self.assertTrue(task.data['items'][0]['destination_files'])
                self.assertEqual(target.read_bytes(), BLUE)

    def test_legacy_retention_callback_hash_race_never_reaches_mock_import(self):
        task = self.flow.begin()
        source = Path(task.data['items'][0]['media_progress']['items'][0]['path'])
        retained = self.root/'retained.png'
        retained.write_bytes(RED)
        digest = jobs.digest
        replaced = []
        def attack(path, **kwargs):
            value = digest(path, **kwargs)
            if Path(path).resolve() == retained.resolve() and not replaced:
                replace(retained)
                replaced.append(retained)
            return value
        with jobs.item(task, 1), patch.object(jobs, 'digest', side_effect=attack), \
                patch.object(destinations.subprocess, 'run') as importer:
            with self.assertRaises(RuntimeError):
                destinations.import_photos([source], False, None, lambda files: [retained], lambda *args: None)
            importer.assert_not_called()
        self.assertEqual(retained.read_bytes(), BLUE)
        self.assertEqual(source.read_bytes(), RED)

    def test_real_retention_writer_proof_is_not_reset_when_returned(self):
        source = self.root/'source.png'
        source.write_bytes(RED)
        retained = []
        def producer(files):
            result = photos.retain_originals(files)
            replace(result[0])
            retained.extend(result)
            return result
        with patch.object(destinations.subprocess, 'run') as importer:
            with self.assertRaises(RuntimeError):
                destinations.import_photos([source], False, None, producer, lambda *args: None)
            importer.assert_not_called()
        self.assertEqual(retained[0].read_bytes(), BLUE)
        self.assertEqual(source.read_bytes(), RED)

    def test_existing_shared_retention_is_readable_but_not_newly_owned(self):
        source = self.root/'source.png'
        source.write_bytes(RED)
        retained = photos.retain_originals([source])[0]
        again = photos.retain_originals([source])
        self.assertFalse(again.proof(retained).created)
        with patch.object(destinations.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '1:0:1\n', '')):
            destinations.import_photos([source], False, None, photos.retain_originals, lambda *args: None)
        self.assertEqual(retained.read_bytes(), RED)

    def test_direct_reset_keeps_omitted_completed_original_without_prior_refusal(self):
        task = self.flow.begin()
        third = Path(task.data['items'][0]['media_progress']['items'][2]['path'])
        self.flow.names = ('one',)
        self.flow.failed_urls.clear()
        code, output = self.flow.command('retry', task.identifier, '--redownload')
        self.assertEqual(code, 0, output)
        self.assertEqual(third.read_bytes(), RED)
        self.assertEqual(len(list((self.root/'saved').iterdir())), 1)

    def test_file_added_before_first_inventory_is_never_adopted_for_cleanup(self):
        save = cli.save_folder
        added = []
        def attack(files, folder):
            personal = files[0].parent.parent/'user-note.txt'
            personal.write_bytes(b'keep user note')
            added.append(personal)
            return save(files, folder)
        with patch.object(cli, 'save_folder', side_effect=attack):
            code, output = self.single()
        self.assertEqual(code, 0, output)
        self.assertEqual(added[0].read_bytes(), b'keep user note')
        self.assertEqual(list((self.root/'saved').iterdir())[0].read_bytes(), RED)

    def test_empty_directory_added_before_first_inventory_is_kept(self):
        save = cli.save_folder
        added = []
        def attack(files, folder):
            personal = files[0].parent.parent/'user-empty-folder'
            personal.mkdir()
            added.append(personal)
            return save(files, folder)
        with patch.object(cli, 'save_folder', side_effect=attack):
            code, output = self.single()
        self.assertEqual(code, 0, output)
        self.assertTrue(added[0].is_dir())

    def test_legacy_hash_only_receipt_does_not_grant_delete_authority(self):
        path = self.root/'legacy.png'
        path.write_bytes(RED)
        record = {'path': str(path), 'sha256': jobs.digest(path, fresh=True)}
        recovery.remove_verified([record], self.root)
        self.assertEqual(path.read_bytes(), RED)

    def test_managed_library_hash_race_does_not_delete_retained_original(self):
        source = self.root/'source.png'
        source.write_bytes(RED)
        asset = '90FA4F22-C80E-43F2-A2AF-FE5BA03A989E'
        library = self.root/'Synthetic.photoslibrary'
        target = library/'originals/9'/f'{asset}.png'
        target.parent.mkdir(parents=True)
        target.write_bytes(RED)
        digest = jobs.digest
        replaced = []
        def attack(path, **kwargs):
            value = digest(path, **kwargs)
            if Path(path).resolve() == target.resolve() and not replaced:
                replace(target)
                replaced.append(target)
            return value
        with patch.object(photos, 'library_directory', return_value=library), \
                patch.object(jobs, 'digest', side_effect=attack), \
                patch.object(destinations.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '1:0:1\n'+asset+'/L0/001', '')):
            with self.assertRaises(RuntimeError):
                cli.import_photos([source], False)
        self.assertEqual(target.read_bytes(), BLUE)
        self.assertEqual(next(photos.originals_directory().iterdir()).read_bytes(), RED)
        self.assertEqual(source.read_bytes(), RED)

    def test_existing_retention_empty_staged_receipt_can_be_verified_without_deletion(self):
        self.flow.failed_urls.clear()
        self.flow.names = ('one',)
        with patch.object(cli, 'save_folder', side_effect=OSError('synthetic disk full')):
            self.single()
        task, source = self.task_source()
        retained = photos.retain_originals([source])[0]
        def not_managed(*args):
            raise RuntimeError('synthetic managed verification pending')
        with jobs.item(task, 1), patch.object(destinations.subprocess, 'run',
                return_value=subprocess.CompletedProcess([], 0, '1:0:1\nsynthetic-identifier', '')):
            with self.assertRaises(RuntimeError):
                destinations.import_photos([source], False, None, photos.retain_originals, not_managed)
        task = jobs.Journal.load(task.identifier)
        self.assertEqual(task.data['items'][0]['receipt']['staged'], [])
        managed = self.root/'managed.png'
        managed.write_bytes(RED)
        checked = ownership.FileList([managed], [ownership.FileProof.observe(managed)])
        with patch.object(recovery, 'verify_library_copies', return_value=checked):
            code, output = self.flow.command('recover', task.identifier, '--verify')
        self.assertEqual(code, 0, output)
        self.assertEqual(retained.read_bytes(), RED)
        self.assertEqual(managed.read_bytes(), RED)

    def test_reading_retry_keeps_local_log_proof_without_a_task_receipt(self):
        calls = []
        def download(url, folder, cookies):
            calls.append(folder)
            if len(calls) == 1:
                raise cli.PixivBrowserRequired('synthetic login required')
            (folder/'read.png').write_bytes(RED)
        args = argparse.Namespace(link='https://www.pixiv.net/artworks/123', folder=self.root/'read-output',
                browser='auto', profile=None, date_now=False, report=None, live_photos=False,
                redownload=False, page_data=None)
        # The reader always supplies its output folder; this is not a Photos request.
        consumed = []
        with patch.object(cli.sys, 'platform', 'linux'), \
                patch.object(cli, 'download_pixiv', side_effect=download), \
                patch.object(cli.accounts, 'copy_saved', return_value=True), \
                patch.object(cli, 'recover_login', return_value=True), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cli.download(args, {}, media_consumer=lambda *values: consumed.append(values[0]))
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(consumed), 1)
        self.assertTrue((calls[-1].parent/'download.log').exists())

    def test_reading_never_recursively_deletes_an_unrecorded_file(self):
        added = []
        def download(url, folder, cookies):
            (folder/'read.png').write_bytes(RED)
        def consume(files, *args):
            personal = files[0].parent.parent/'user-note.txt'
            personal.write_bytes(b'keep user note')
            added.append(personal)
        args = argparse.Namespace(link='https://www.pixiv.net/artworks/123', folder=self.root/'read-output',
                browser='none', profile=None, date_now=False, report=None, live_photos=False,
                redownload=False, page_data=None)
        with patch.object(cli.sys, 'platform', 'linux'), \
                patch.object(cli, 'download_pixiv', side_effect=download), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.download(args, {}, media_consumer=consume), 0)
        self.assertEqual(added[0].read_bytes(), b'keep user note')

    def test_recovery_keeps_prepared_pair_proof_instead_of_reobserving_paths(self):
        page = self.root/'page.json'
        item = {'aweme_id': '123', 'images': [{'url_list': ['https://cdn.example/still'],
                'video': {'width': 16, 'height': 16, 'play_addr': {'url_list': ['https://cdn.example/motion']}}}]}
        page.write_text(json.dumps(item))
        motion = b'\x00\x00\x00\x18ftypmp42' + b'generated motion fixture' * 8
        def request(value, **kwargs):
            return Response(motion if value.full_url.endswith('/motion') else RED)
        with patch.object(douyin, 'build_opener') as opener, patch.object(cli, 'save_folder', side_effect=OSError('synthetic disk full')):
            opener.return_value.open.side_effect = request
            code, output = self.flow.command('https://www.douyin.com/note/123', '--page-data', str(page),
                    '--browser', 'none', '--folder', str(self.root/'saved'))
        self.assertEqual(code, 1, output)
        task = jobs.all_journals()[0]
        row = task.data['items'][0]
        directory = Path(row['stage'])/'prepared'
        directory.mkdir()
        image, video = directory/'paired.png', directory/'paired.mov'
        image.write_bytes(RED)
        video.write_bytes(motion)
        pair = ownership.FileList([image, video], [ownership.FileProof.observe(image), ownership.FileProof.observe(video)])
        row['ready']['prepared'] = [jobs.snapshot(pair)]
        row['ready']['evidence']['motion_metadata_complete'] = True
        task.write()
        verified = jobs.verified_files
        replaced = []
        def attack(records, root=None):
            result = verified(records, root)
            if result and Path(result[0]).resolve() == image.resolve() and not replaced:
                replace(image)
                replaced.append(image)
            return result
        with patch.object(recovery.jobs, 'verified_files', side_effect=attack), \
                patch.object(recovery, 'inspect_files', side_effect=gallery_fixture.uninspected), \
                patch.object(cli.preflight, 'destination'):
            code, output = self.flow.command('recover', task.identifier, '--live-photos')
        self.assertEqual(code, 1, output)
        self.assertTrue(replaced, output)
        self.assertEqual(image.read_bytes(), BLUE)
        for record in row['ready']['files']:
            self.assertTrue(Path(record['path']).exists())

    def test_writer_seal_refuses_replacement_even_with_identical_bytes(self):
        for content in (RED, BLUE):
            path = self.root/('same.png' if content == RED else 'different.png')
            with ownership.Writer(path) as writer:
                writer.write(RED)
                replace(path, content)
                with self.assertRaises(RuntimeError):
                    writer.seal()
            self.assertEqual(path.read_bytes(), content)

    def test_observation_and_missing_writer_proof_cannot_authorize_deletion(self):
        path = self.root/'observed.png'
        path.write_bytes(RED)
        observed = ownership.FileProof.observe(path)
        with self.assertRaises(ValueError):
            observed.remove()
        with self.assertRaises(TypeError):
            transfers.TransferSet(self.root/'media', 'xiaohongshu', NOTE, [('image', 'synthetic')]).complete(1, path)
        self.assertEqual(path.read_bytes(), RED)
