"""Actual detached-worker acceptance using only local HTTP fixtures."""
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from linkget import cli, content, read_ai, read_store, read_tasks


class BackgroundReadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.home = self.root / 'private'
        root = patch.object(read_store, 'root', return_value=self.home)
        root.start(); self.addCleanup(root.stop)
        self.entered, self.release = threading.Event(), threading.Event()
        entered, release = self.entered, self.release
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                entered.set(); release.wait(10)
                if self.path.startswith('/fail'):
                    self.send_response(401); self.end_headers(); return
                units = json.loads(payload['messages'][-1]['content'])['source_units']
                answer = {'units': [{'id': v['id'], 'text': '翻译结果'} for v in units]}
                data = {'choices': [{'message': {'content': json.dumps(answer)}, 'finish_reason': 'stop'}]}
                self.send_response(200); self.end_headers()
                try: self.wfile.write(json.dumps(data).encode())
                except (BrokenPipeError, ConnectionResetError): pass
            def log_message(self, *args): pass
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close); self.addCleanup(self.server.shutdown)
        self.addCleanup(self.release.set)
        self.addCleanup(self.stop_workers)
        self.folder = self.root / 'source'; self.folder.mkdir()
        read_store.write_json(self.folder / 'content.json', {'text': 'Source text', 'content_complete': True})
        self.original = (self.folder / 'content.json').read_bytes()
        read_store.settings({'ai_provider': 'compatible', 'ai_model': 'fixture',
                             'ai_endpoint': f'http://127.0.0.1:{self.server.server_port}/v1', 'ai_key_env': 'LINKGET_TEST_UNSET'})

    def stop_workers(self):
        for path in read_tasks.directory().glob('*.json'):
            value = read_tasks.load(path.stem)
            if read_tasks.active(value):
                try:
                    os.killpg(value['pid'], signal.SIGINT)
                except ProcessLookupError:
                    pass  # The worker can exit after the active-process check.
                self.wait_terminal(value['id'])

    def wait_terminal(self, identity, timeout=5):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            value = read_tasks.load(identity)
            if value['status'] in read_tasks.TERMINAL:
                return value
            time.sleep(0.025)
        self.fail('Detached worker did not finish: ' + str(read_tasks.load(identity)))

    def submit(self):
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cli.main(['read', 'translate', str(self.folder)]), 0)
        identity = output.getvalue().splitlines()[0].removeprefix('Task: ')
        read_tasks.load(identity)
        return identity

    def test_returns_before_request_finishes_and_worker_survives_caller(self):
        start = time.monotonic()
        identity = self.submit()
        self.assertLess(time.monotonic() - start, 2)
        self.assertTrue(self.entered.wait(5))
        self.assertEqual(read_tasks.refresh(identity)['status'], 'running')
        self.assertFalse((self.folder / 'translation-zh.json').exists())
        self.release.set()
        result = self.wait_terminal(identity)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['outputs'], [str((self.folder / 'translation-zh.md').resolve())])
        self.assertEqual((self.folder / 'content.json').read_bytes(), self.original)
        for path in (read_tasks.paths(identity)[0], read_tasks.paths(identity)[3]):
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_cancel_interrupts_only_the_held_worker_and_preserves_source(self):
        identity = self.submit(); self.assertTrue(self.entered.wait(5))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(read_tasks.cancel([identity]), 0)
        self.assertEqual(self.wait_terminal(identity)['status'], 'cancelled')
        self.assertEqual((self.folder / 'content.json').read_bytes(), self.original)
        self.assertFalse((self.folder / 'translation-zh.md').exists())

    def test_failed_worker_reports_error_and_leaves_no_translation(self):
        read_store.settings({'ai_endpoint': f'http://127.0.0.1:{self.server.server_port}/fail/v1'})
        self.release.set()
        identity = self.submit()
        self.assertEqual(self.wait_terminal(identity)['status'], 'failed')
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(read_tasks.status([identity]), 1)
            self.assertEqual(read_tasks.logs([identity]), 0)
        self.assertIn('HTTP 401', output.getvalue())
        self.assertFalse((self.folder / 'translation-zh.md').exists())
        self.assertEqual((self.folder / 'content.json').read_bytes(), self.original)

    def receipt(self, **extra):
        identity = '20261008T000000-abcdef123456'
        value = {'schema': 1, 'id': identity, 'kind': 'translate', 'argv': ['translate', str(self.folder)],
                 'status': 'queued', 'created': time.time(), 'outputs': [], 'pid': None, **extra}
        read_store.write_json(read_tasks.paths(identity)[0], value)
        return identity

    def test_cancel_before_worker_starts_never_calls_model(self):
        identity = self.receipt()
        with contextlib.redirect_stdout(io.StringIO()), patch.object(content, 'main') as processing:
            self.assertEqual(read_tasks.cancel([identity]), 0)
            self.assertEqual(read_tasks.worker(identity), 130)
        processing.assert_not_called()
        self.assertEqual(read_tasks.load(identity)['status'], 'cancelled')

    def test_stale_pid_is_interrupted_and_never_signals_another_process(self):
        identity = self.receipt(status='running', pid=os.getpid())
        with patch.object(read_tasks.os, 'killpg') as kill, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(read_tasks.cancel([identity]), 0)
        kill.assert_not_called()
        self.assertEqual(read_tasks.load(identity)['status'], 'interrupted')

    def test_foreground_and_fast_commands_do_not_spawn_workers(self):
        with patch.object(content, 'main', return_value=0) as processing, patch.object(read_tasks, 'submit') as submit:
            self.assertEqual(cli.main(['read', 'translate', str(self.folder), '--foreground']), 0)
            self.assertEqual(cli.main(['read', 'config']), 0)
        submit.assert_not_called()
        self.assertNotIn('--foreground', processing.call_args_list[0].args[0])

    def test_batch_stdin_is_captured_before_detaching(self):
        with patch.object(read_tasks, 'submit', return_value=0) as submit, patch.object(read_tasks.sys, 'stdin', io.StringIO('/first.wav\n/second.wav\n')):
            self.assertEqual(cli.main(['read', 'batch', '--folder', str(self.root / 'out')]), 0)
        self.assertEqual(submit.call_args.args, (['batch', '/first.wav', '/second.wav', '--folder', str(self.root / 'out')], 'batch'))

    def test_bad_input_is_rejected_before_creating_a_worker(self):
        with patch.object(read_tasks, 'submit') as submit, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['read', 'translate', str(self.root / 'missing')]), 1)
            self.assertEqual(cli.main(['read', 'status', '../../outside']), 1)
        submit.assert_not_called()

    def test_local_startup_is_serialized_for_concurrent_jobs(self):
        entered, release, calls = threading.Event(), threading.Event(), []
        def start(config, base):
            calls.append(config['ai_model']); entered.set(); release.wait(5)
            return 'instance'
        results = []
        with patch.object(read_ai, '_prepare_lmstudio', side_effect=start):
            first = threading.Thread(target=lambda: results.append(read_ai.prepare_lmstudio({'ai_model': 'one'}, 'local')))
            second = threading.Thread(target=lambda: results.append(read_ai.prepare_lmstudio({'ai_model': 'two'}, 'local')))
            first.start(); self.assertTrue(entered.wait(2)); second.start()
            time.sleep(0.05); self.assertEqual(calls, ['one'])
            release.set(); first.join(2); second.join(2)
        self.assertEqual(results, ['instance', 'instance'])
        self.assertEqual(calls, ['one', 'two'])


if __name__ == '__main__':
    unittest.main()
