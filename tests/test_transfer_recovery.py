"""Native gallery gap recovery using synthetic HTTP responses and isolated tasks."""
import contextlib
import errno
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import URLError

from linkget import accounts, cli, douyin, jobs, transfers, xiaohongshu
from media_stub import uninspected

NOTE = 'a' * 24
URL = 'https://www.xiaohongshu.com/explore/' + NOTE
IMAGE = b'\xff\xd8\xff' + b'generated image fixture' * 8


class Response(io.BytesIO):
    def __init__(self, value):
        super().__init__(value)
        self.headers = {'Content-Length': str(len(value))}


class GalleryRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.calls = []
        self.fail = {'notes/two'}
        self.cancel = set()
        self.lock = threading.Lock()
        self.names = ('one', 'two', 'three')
        for obj, name, options in (
            (accounts, 'directory', {'return_value': self.root/'settings/sessions'}),
            (cli.tempfile, 'tempdir', {'new': str(self.root)}),
            (cli, 'inspect_files', {'side_effect': uninspected}),
            (cli, 'first_run', {'return_value': {}}),
            (xiaohongshu, 'build_opener', {}),
        ):
            mocked = patch.object(obj, name, **options)
            value = mocked.start()
            if obj is xiaohongshu:
                value.return_value.open.side_effect = self.request
            self.addCleanup(mocked.stop)

    def request(self, request, **kwargs):
        target = request.full_url
        with self.lock:
            self.calls.append(target)
        if '/explore/' in target:
            note = {'noteId': NOTE, 'type': 'normal', 'imageList': [{'fileId': 'notes/' + key} for key in self.names]}
            state = {'note': {'noteDetailMap': {NOTE: {'note': note}}}}
            return Response(('window.__INITIAL_STATE__=' + json.dumps(state)).encode())
        if any(target.endswith(key) for key in self.cancel):
            raise KeyboardInterrupt()
        if any(target.endswith(key) for key in self.fail):
            raise URLError('connection reset')
        return Response(IMAGE + target.rsplit('/', 1)[-1].encode())

    def command(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            result = cli.main(list(args))
        return result, out.getvalue() + err.getvalue()

    def begin(self):
        code, output = self.command(URL, '--browser', 'none', '--folder', str(self.root/'saved'))
        self.assertEqual(code, 1)
        task = jobs.all_journals()[0]
        progress = task.data['items'][0]['media_progress']
        self.assertEqual([row['state'] for row in progress['items']], ['complete', 'failed', 'complete'])
        self.assertEqual(progress['planned'], 3)
        self.assertIsNone(progress['source_total'])
        self.assertIn('2 / 3 exposed resources recorded complete; total source count unknown', output)
        self.assertFalse(list((self.root/'saved').iterdir()))
        return task

    def test_retry_fetches_only_gap_and_cleanup_follows_full_verified_save(self):
        task = self.begin()
        first = Path(task.data['items'][0]['stage'])
        self.calls.clear()
        self.fail.clear()
        self.assertEqual(self.command('retry', task.identifier)[0], 0)
        self.assertEqual([url.rsplit('/', 1)[-1] for url in self.calls], [NOTE, 'two'])
        self.assertEqual(len(list((self.root/'saved').iterdir())), 3)
        self.assertFalse(first.exists())
        self.assertEqual(jobs.Journal.load(task.identifier).incomplete(), [])

    def test_repeated_failure_reuses_latest_checked_copies_until_complete(self):
        task = self.begin()
        old = Path(task.data['items'][0]['stage'])
        self.calls.clear()
        self.assertEqual(self.command('retry', task.identifier)[0], 1)
        self.assertEqual([url.rsplit('/', 1)[-1] for url in self.calls], [NOTE, 'two'])
        self.assertTrue(old.exists())
        self.calls.clear()
        self.fail.clear()
        self.assertEqual(self.command('retry', task.identifier)[0], 0)
        self.assertEqual([url.rsplit('/', 1)[-1] for url in self.calls], [NOTE, 'two'])
        self.assertFalse(old.exists())

    def test_failed_format_probe_redownloads_only_rejected_resource(self):
        self.fail.clear()
        reject = True
        def probe(files, variants=None):
            rows = uninspected(files, variants)
            for row in rows:
                row['probe_ok'] = not (reject and '_002_' in row['file'])
            return rows
        with patch.object(cli, 'inspect_files', side_effect=probe):
            self.assertEqual(self.command(URL, '--browser', 'none', '--folder', str(self.root/'saved'))[0], 1)
            task = jobs.all_journals()[0]
            self.assertEqual([r['state'] for r in task.data['items'][0]['media_progress']['items']], ['complete', 'failed', 'complete'])
            self.calls.clear()
            reject = False
            self.assertEqual(self.command('retry', task.identifier)[0], 0)
        self.assertEqual([url.rsplit('/', 1)[-1] for url in self.calls], [NOTE, 'two'])

    def test_changed_source_url_is_new_resource_instead_of_reusing_stale_bytes(self):
        task = self.begin()
        self.names = ('one-updated', 'two', 'three')
        self.fail.clear()
        self.calls.clear()
        self.assertEqual(self.command('retry', task.identifier)[0], 0)
        self.assertCountEqual([url.rsplit('/', 1)[-1] for url in self.calls], [NOTE, 'one-updated', 'two'])
        first = sorted((self.root/'saved').iterdir())[0]
        self.assertEqual(first.read_bytes(), IMAGE + b'one-updated')

    def test_manifest_keeps_only_opaque_resource_identity_and_failure_type(self):
        task = self.begin()
        serialized = json.dumps(task.data['items'][0]['media_progress'])
        for value in ('https://', 'connection reset', 'notes/two', 'xsec_token'):
            self.assertNotIn(value, serialized)
        self.assertIn('URLError', serialized)

    def test_cancellation_keeps_other_successes_and_retry_fetches_only_gap(self):
        self.fail.clear()
        self.cancel.add('notes/two')
        self.assertEqual(self.command(URL, '--browser', 'none', '--folder', str(self.root/'saved'))[0], 130)
        task = jobs.all_journals()[0]
        self.assertEqual([r['state'] for r in task.data['items'][0]['media_progress']['items']], ['complete', 'cancelled', 'complete'])
        self.cancel.clear()
        self.calls.clear()
        self.assertEqual(self.command('retry', task.identifier)[0], 0)
        self.assertEqual([url.rsplit('/', 1)[-1] for url in self.calls], [NOTE, 'two'])

    def test_changed_retained_file_is_fetched_again_and_edited_old_file_is_kept(self):
        task = self.begin()
        source = Path(task.data['items'][0]['media_progress']['items'][0]['path'])
        source.write_bytes(b'edited retained file')
        self.fail.clear()
        self.calls.clear()
        self.assertEqual(self.command('retry', task.identifier)[0], 0)
        self.assertCountEqual([url.rsplit('/', 1)[-1] for url in self.calls], [NOTE, 'one', 'two'])
        self.assertEqual(source.read_bytes(), b'edited retained file')

    def test_corrupt_manifest_refused_and_explicit_redownload_preserves_untracked_files(self):
        task = self.begin()
        row = task.data['items'][0]
        extra = Path(row['stage'])/'user-note.txt'
        extra.write_bytes(b'keep user note')
        row['media_progress']['items'][0]['key'] = 'invalid'
        task.write()
        self.fail.clear()
        code, output = self.command('retry', task.identifier)
        self.assertEqual(code, 1)
        self.assertIn('manifest is invalid', output)
        self.assertEqual(extra.read_bytes(), b'keep user note')
        self.assertEqual(self.command('retry', task.identifier, '--redownload')[0], 0)
        self.assertEqual(extra.read_bytes(), b'keep user note')

    def test_manifest_cannot_reuse_outside_or_symlinked_source(self):
        for unsafe in ('outside', 'symlink'):
            with self.subTest(unsafe=unsafe):
                task = self.begin()
                record = task.data['items'][0]['media_progress']['items'][0]
                source = Path(record['path'])
                outside = self.root/'unrelated.jpg'
                outside.write_bytes(IMAGE)
                if unsafe == 'outside':
                    record['path'] = str(outside)
                else:
                    source.unlink()
                    source.symlink_to(outside)
                task.write()
                self.fail.clear()
                self.assertEqual(self.command('retry', task.identifier)[0], 1)
                self.assertEqual(outside.read_bytes(), IMAGE)
                self.fail.add('notes/two')

    def test_malformed_nested_ownership_records_fail_safely_without_traceback(self):
        for value in ([], {'invalid': []}):
            with self.subTest(value=value):
                task = self.begin()
                row = task.data['items'][0]
                source = Path(row['media_progress']['items'][0]['path'])
                before = source.read_bytes()
                row['stage_snapshots'] = [] if value == [] else {str(source.parent.parent.resolve()): []}
                task.write()
                self.fail.clear()
                code, output = self.command('retry', task.identifier)
                self.assertEqual(code, 1)
                self.assertIn('ownership', output)
                self.assertNotIn('Traceback', output)
                self.assertEqual(source.read_bytes(), before)
                self.fail.add('notes/two')

    def test_concurrently_replaced_reuse_target_survives_failure_and_later_cleanup(self):
        task = self.begin()
        source = Path(task.data['items'][0]['media_progress']['items'][0]['path'])
        replaced = []
        def replace_target(original, output):
            target = Path(output.name)
            target.unlink()
            target.write_bytes(b'concurrent user replacement')
            replaced.append(target)
            raise OSError(errno.ENOSPC, 'disk full')
        with patch.object(transfers.shutil, 'copyfileobj', side_effect=replace_target):
            self.assertEqual(self.command('retry', task.identifier)[0], 1)
        self.assertTrue(replaced)
        self.assertTrue(source.exists())
        for path in replaced:
            self.assertEqual(path.read_bytes(), b'concurrent user replacement')
        self.fail.clear()
        self.assertEqual(self.command('retry', task.identifier)[0], 0)
        for path in replaced:
            self.assertEqual(path.read_bytes(), b'concurrent user replacement')

    def test_disk_full_during_reuse_keeps_sources_and_prior_outputs(self):
        task = self.begin()
        source = Path(task.data['items'][0]['media_progress']['items'][0]['path'])
        before = source.read_bytes()
        saved = self.root/'saved/user.txt'
        saved.write_bytes(b'user output')
        def broken(original, output):
            output.write(b'partial')
            raise OSError(errno.ENOSPC, 'disk full')
        with patch.object(transfers.shutil, 'copyfileobj', side_effect=broken):
            code, output = self.command('retry', task.identifier)
            self.assertEqual(code, 1)
            self.assertIn('Save/processing', output)
            self.assertIn('Free space', output)
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual(saved.read_bytes(), b'user output')
        self.fail.clear()
        self.assertEqual(self.command('retry', task.identifier)[0], 0)

    def test_douyin_motion_gap_reuses_checked_still_and_keeps_pair_mapping(self):
        url = 'https://www.douyin.com/note/123'
        page = self.root/'page.json'
        item = {'aweme_id': '123', 'images': [{'url_list': ['https://cdn.example/still'],
                'video': {'width': 64, 'height': 64, 'play_addr': {'url_list': ['https://cdn.example/motion']}}}]}
        page.write_text(json.dumps(item))
        video = b'\x00\x00\x00\x18ftypmp42' + b'generated video fixture' * 8
        calls = []
        failing = True
        def request(request, **kwargs):
            target = request.full_url
            calls.append(target)
            if target.endswith('/motion'):
                if failing:
                    raise URLError('connection reset')
                return Response(video)
            return Response(IMAGE)
        with patch.object(douyin, 'build_opener') as opener:
            opener.return_value.open.side_effect = request
            self.assertEqual(self.command(url, '--page-data', str(page), '--browser', 'none', '--folder', str(self.root/'motion'))[0], 1)
            task = jobs.all_journals()[0]
            calls.clear()
            failing = False
            code, output = self.command('retry', task.identifier)
            self.assertEqual(code, 0, output)
            self.assertEqual(calls, ['https://cdn.example/motion'])
            self.assertEqual(len(list((self.root/'motion').iterdir())), 2)


if __name__ == '__main__':
    unittest.main()
