"""Offline task scheduling/retry/view boundaries; no account or media access."""
import argparse
import contextlib
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from linkget import batch, cli, jobs, read_tasks, tasks, pixiv, kuaishou
from linkget.failures import classify, retry_delay


class QueueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        mock = patch.object(jobs, 'directory', return_value=self.root / 'jobs')
        mock.start(); self.addCleanup(mock.stop)

    def run_queue(self, links, callback, jobs_count=None):
        args = argparse.Namespace(folder=self.root, jobs=jobs_count, redownload=False)
        journal = jobs.Journal.create(links, args)
        with patch.object(batch.sys.stdout, 'isatty', return_value=False), patch.object(batch, 'SITE_INTERVAL', 0), patch.object(batch, 'retry_wait'):
            result = batch._run(args, list(enumerate(links, 1)), [], callback, lambda a: {}, lambda *a: None, journal)
        return result, journal

    def test_defaults_cap_workers_and_same_site_never_overlaps(self):
        lock = threading.Lock(); active = set(); maximum = [0]; collisions = []
        def download(args, setup, records):
            key = batch.site_key(args.link)
            with lock:
                if key in active: collisions.append(key)
                active.add(key); maximum[0] = max(maximum[0], len(active))
            time.sleep(.02)
            with lock: active.remove(key)
            return 0
        code, _ = self.run_queue(['https://a.example/a', 'https://b.other/a', 'https://c.third/a', 'https://a.example/b', 'https://d.fourth/a'], download)
        self.assertEqual(code, 0); self.assertEqual(collisions, [])
        self.assertLessEqual(maximum[0], 3)

    def test_known_short_and_mobile_aliases_share_gate(self):
        for short, full in [('v.douyin.com', 'www.douyin.com'), ('xhslink.cn', 'www.xiaohongshu.com'), ('b23.tv', 'm.bilibili.com'), ('t.cn', 'm.weibo.cn'), ('youtu.be', 'www.youtube.com'), ('twitter.com', 'x.com')]:
            self.assertEqual(batch.site_key('share https://' + short + '/a'), batch.site_key('https://' + full + '/b'))

    def test_network_retries_are_finite_and_final_report_is_one_item(self):
        calls = []
        def download(args, setup, records):
            calls.append(args.redownload)
            records.append({'status': 'failed'})
            args._retry_failure = classify(TimeoutError('timed out'))
            return 1
        code, journal = self.run_queue(['https://example.com/a'], download)
        self.assertEqual((code, len(calls)), (1, 3))
        self.assertEqual(journal.data['items'][0]['attempt'], 3)

    def test_access_failure_does_not_retry(self):
        calls = []
        def download(args, setup, records):
            calls.append(1); args._retry_failure = classify('HTTP 403 forbidden'); return 1
        code, _ = self.run_queue(['https://example.com/a'], download)
        self.assertEqual((code, len(calls)), (1, 1))

    def test_save_and_import_failures_cannot_enable_read_retry(self):
        args = argparse.Namespace()
        for phase in ['save', 'import', 'pair', 'postprocess']:
            cli.record_failure(args, TimeoutError('timed out'), phase)
            self.assertIsNone(getattr(args, '_retry_failure', None))
        error = HTTPError('https://example.com/private?token=secret', 429, 'limit', {'Retry-After': '31'}, None)
        cli.record_failure(args, error, 'download')
        self.assertIsNone(retry_delay(args._retry_failure, 1, operation='read', retry_after=args._retry_after))
        self.assertIsNone(retry_delay(classify('CERTIFICATE_VERIFY_FAILED'), 1, operation='read'))

    def test_native_network_wrappers_preserve_retry_and_header_evidence(self):
        for adapter, url in [(pixiv, 'https://www.pixiv.net/artworks/123'),
                             (kuaishou, 'https://www.kuaishou.com/short-video/abc')]:
            for failure in [URLError(TimeoutError('timed out')),
                            HTTPError(url, 429, 'limit', {'Retry-After': '31'}, None)]:
                with patch.object(adapter, 'build_opener') as opener:
                    opener.return_value.open.side_effect = failure
                    with self.assertRaises(RuntimeError) as caught:
                        adapter.download(url, self.root)
                args = argparse.Namespace()
                cli.record_failure(args, caught.exception, 'download')
                expected = 'rate_limit' if isinstance(failure, HTTPError) else 'network'
                self.assertEqual(args._retry_failure.category, expected)
                if expected == 'rate_limit':
                    self.assertEqual(args._retry_after, '31')

    def test_site_gap_and_independent_sites(self):
        gate = batch.SiteGate()
        with patch.object(batch.time, 'monotonic', return_value=10):
            self.assertTrue(gate.enter('one'))
            self.assertFalse(gate.enter('one'))
            gate.leave('one')
            self.assertFalse(gate.enter('one'))
            self.assertTrue(gate.enter('two'))
        with patch.object(batch.time, 'monotonic', return_value=11):
            self.assertTrue(gate.enter('one'))
        self.assertEqual(batch.site_key('https://[bad'), '')

    def test_gate_delay_and_wait_are_cancellable(self):
        state = {'cancelled': threading.Event()}
        state['cancelled'].set()
        with self.assertRaises(KeyboardInterrupt): batch.retry_wait(30, state)


class TaskViewTests(unittest.TestCase):
    def test_download_and_read_share_view_without_tokens_or_false_completion(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root); downloads = root/'jobs'; reads = root/'read-tasks'
            stage = root/'linkget-stage'; stage.mkdir(); (stage/'kept.bin').write_bytes(b'x'*123)
            with patch.object(jobs, 'directory', return_value=downloads), patch.object(read_tasks, 'directory', return_value=reads):
                journal = jobs.Journal.create(['https://example.com/post?token=private-token'], argparse.Namespace(folder=root/'output'))
                journal.update(1, status='running', stage=str(stage), failure={'message':'HTTP 429 https://example.com/post?token=private-token','next_step':'Wait.'})
                value = {'schema':1, 'id':'20261022T010101-'+'a'*12, 'kind':'read','created':1,'status':'queued','phase':'queued','outputs':[],'argv':['https://example.com/?token=private-token']}
                reads.mkdir()
                (reads/(value['id']+'.json')).write_text('{}')
                output = io.StringIO()
                with patch.object(read_tasks, 'load', return_value=value), contextlib.redirect_stdout(output):
                    self.assertEqual(tasks.main([]), 0)
                text=output.getvalue()
                self.assertIn('interrupted (recorded running',text); self.assertIn('queued',text)
                self.assertIn('worker unconfirmed',text)
                self.assertEqual(value['status'], 'queued')
                self.assertIn('123 B',text); self.assertIn('linkget retry',text)
                self.assertNotIn('private-token',text)
                self.assertNotIn('https://',text)
                self.assertEqual(jobs.Journal.load(journal.identifier).data['items'][0]['status'],'running')

    def test_completed_download_does_not_present_an_old_failure_as_current(self):
        with tempfile.TemporaryDirectory() as root, patch.object(jobs, 'directory', return_value=Path(root)):
            journal = jobs.Journal.create(['https://example.com/a'], argparse.Namespace(folder=Path(root)))
            journal.update(1, status='completed', failure={'message':'historical failure', 'next_step':'historical retry'})
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                tasks.show_download(journal)
            self.assertIn('completed', output.getvalue())
            self.assertNotIn('historical', output.getvalue())

    def test_size_deduplicates_paths_and_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as root:
            root=Path(root); folder=root/'data'; folder.mkdir(); (folder/'a').write_bytes(b'abc')
            outside=root/'outside'; outside.write_bytes(b'secret'*100)
            (folder/'link').symlink_to(outside)
            self.assertEqual(tasks.retained_size([folder,folder/'a']),'3 B')
