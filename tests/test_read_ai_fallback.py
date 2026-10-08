"""Bounded failover, credential isolation and local startup acceptance."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError, URLError

from linkget import content, read_ai, read_store


class AIFallbackTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.config = {'ai_provider': 'compatible', 'ai_model': 'primary',
                       'ai_endpoint': 'http://127.0.0.1:8317/v1', 'ai_auth': 'cpa',
                       'ai_fallback_provider': 'lmstudio', 'ai_fallback_model': 'qwen/qwen3.5-4b',
                       'ai_fallback_endpoint': 'http://127.0.0.1:1234/v1'}
        self.source = {'content_complete': True, 'text': 'Source', 'videos': [{'segments': [
            {'text': str(i) + 'x' * 2999, 'start': i, 'end': i + 1} for i in range(3)]}]}
        read_store.write_json(self.folder / 'content.json', self.source)

    def test_partial_success_switches_once_and_records_each_batch(self):
        calls = []
        def request(config, instruction, group, **kwargs):
            calls.append((config['ai_model'], [v['id'] for v in group]))
            if len(calls) == 2:
                raise read_ai.AIUnavailable('HTTP 503')
            return {'units': [{'id': v['id'], 'text': '译文 ' + v['id']} for v in reversed(group)]}
        with patch.object(read_ai, 'request', side_effect=request), contextlib.redirect_stderr(io.StringIO()):
            output = read_ai.generate(self.folder, 'translate', 'zh', self.config)
        self.assertEqual([c[0] for c in calls], ['primary', 'primary', 'qwen/qwen3.5-4b', 'qwen/qwen3.5-4b'])
        self.assertEqual(calls[1][1], calls[2][1])
        result = read_store.read_json(self.folder / 'translation-zh.json')
        self.assertEqual(result['model'], 'mixed')
        self.assertEqual(result['models'], ['primary', 'qwen/qwen3.5-4b'])
        self.assertEqual([b['fallback'] for b in result['batches']], [False, True, True])
        self.assertEqual([i['id'] for i in result['items']], ['v1-s1', 'v1-s2', 'v1-s3'])
        self.assertIn('Automatic fallback used (HTTP 503)', output.read_text())
        self.assertEqual(read_store.read_json(self.folder / 'content.json'), self.source)

    def test_two_failures_stop_without_loop_or_partial_output(self):
        with patch.object(read_ai, 'request', side_effect=read_ai.AIUnavailable('connection/timeout')) as request, contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'Primary and fallback'):
                read_ai.generate(self.folder, 'translate', 'zh', self.config)
        self.assertEqual(request.call_count, 2)
        self.assertFalse((self.folder / 'translation-zh.json').exists())
        self.assertFalse((self.folder / 'translation-zh.md').exists())
        self.assertEqual(read_store.read_json(self.folder / 'content.json'), self.source)

    def test_without_opt_in_and_invalid_output_never_fall_back(self):
        for config, response in [({'ai_model': 'primary'}, read_ai.AIUnavailable('HTTP 503')),
                                 (self.config, RuntimeError('invalid structured output')),
                                 (self.config, {'units': []})]:
            with patch.object(read_ai, 'request', side_effect=response if isinstance(response, Exception) else None,
                              return_value=response) as request:
                with self.assertRaises((RuntimeError, ValueError)):
                    read_ai.generate(self.folder, 'translate', 'zh', config)
            self.assertEqual(request.call_count, 1)

    def test_new_endpoint_cannot_inherit_primary_authentication(self):
        cfg = {**self.config, 'ai_key_env': 'PRIMARY_SECRET', 'ai_auth_file': '/primary-secret-config'}
        backup = read_ai.fallback_config(cfg)
        self.assertNotIn('ai_auth', backup)
        self.assertNotIn('ai_auth_file', backup)
        self.assertEqual(backup['ai_key_env'], 'LINKGET_FALLBACK_AI_KEY')
        self.assertEqual(backup['ai_endpoint'], 'http://127.0.0.1:1234/v1')
        cfg['ai_fallback_key_env'] = 'BACKUP_KEY'
        self.assertEqual(read_ai.fallback_config(cfg)['ai_key_env'], 'BACKUP_KEY')
        same = {'ai_provider': 'compatible', 'ai_model': 'a', 'ai_auth': 'cpa', 'ai_fallback_model': 'b'}
        self.assertEqual(read_ai.fallback_config(same)['ai_auth'], 'cpa')
        with self.assertRaises(ValueError):
            read_ai.fallback_config({**same, 'ai_fallback_model': 'a'})

    def test_http_and_transport_failures_are_classified_without_service_details(self):
        cfg = {'ai_provider': 'compatible', 'ai_model': 'fixture', 'ai_endpoint': 'http://127.0.0.1:9999'}
        for code in [400, 401, 403, 404, 408, 429, 500, 502, 503, 504]:
            error = HTTPError(cfg['ai_endpoint'], code, 'private-service-detail', {}, None)
            with patch.object(read_ai, 'build_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(RuntimeError) as caught:
                    read_ai.request(cfg, '', [])
            self.assertEqual(isinstance(caught.exception, read_ai.AIUnavailable), code not in {400, 401, 403})
            self.assertNotIn('private-service-detail', str(caught.exception))
        for error in [URLError('private connection detail'), TimeoutError(), ConnectionResetError()]:
            with patch.object(read_ai, 'build_opener') as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(read_ai.AIUnavailable):
                    read_ai.request(cfg, '', [])

    def test_fallback_config_can_be_cleared_without_changing_primary(self):
        with patch.object(read_store, 'root', return_value=self.folder / 'settings'), contextlib.redirect_stdout(io.StringIO()):
            content.config_command(['--ai-model', 'primary', '--ai-fallback-provider', 'lmstudio', '--ai-fallback-model', 'qwen/qwen3.5-4b'])
            self.assertEqual(read_store.settings()['ai_fallback_model'], 'qwen/qwen3.5-4b')
            content.config_command(['--clear', 'ai_fallback_model'])
            cfg = read_store.settings()
        self.assertEqual(cfg['ai_model'], 'primary')
        self.assertIsNone(read_ai.fallback_config(cfg))

    def test_local_startup_loads_only_downloaded_model_with_idle_unload(self):
        cfg = {'ai_model': 'qwen/qwen3.5-4b'}
        before = {'models': [{'key': cfg['ai_model'], 'loaded_instances': []}]}
        after = {'models': [{'key': cfg['ai_model'], 'loaded_instances': [{'id': 'local-instance'}]}]}
        responses = []
        for payload in (before, after):
            response = MagicMock(); response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            responses.append(response)
        with patch.object(read_ai, 'build_opener') as opener, patch.object(read_ai.shutil, 'which', return_value='/installed/lms'), patch.object(read_ai.subprocess, 'run') as run:
            opener.return_value.open.side_effect = [URLError('offline'), *responses]
            self.assertEqual(read_ai.prepare_lmstudio(cfg, 'http://127.0.0.1:1234/v1'), 'local-instance')
        self.assertEqual(run.call_args_list[0].args[0], ['/installed/lms', 'server', 'start', '--port', '1234', '--bind', '127.0.0.1'])
        self.assertIn('--ttl', run.call_args_list[1].args[0])
        self.assertIn('load', run.call_args_list[1].args[0])
        self.assertFalse(any('get' in c.args[0] for c in run.call_args_list))

    def test_local_model_absence_and_remote_endpoint_are_rejected_without_download(self):
        response = MagicMock(); response.__enter__.return_value.read.return_value = b'{"models":[]}'
        with patch.object(read_ai, 'build_opener') as opener, patch.object(read_ai.subprocess, 'run') as run:
            opener.return_value.open.return_value = response
            with self.assertRaisesRegex(ValueError, 'not downloaded'):
                read_ai.prepare_lmstudio({'ai_model': 'missing'}, 'http://127.0.0.1:1234/v1')
            with self.assertRaises(ValueError):
                read_ai.prepare_lmstudio({'ai_model': 'missing'}, 'https://remote.example/v1')
        run.assert_not_called()

    def test_local_requests_disable_reasoning_and_do_not_inherit_openai_key(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': '{"units":[{"id":"p1","text":"结果"}]}'}}]}).encode()
        with patch.object(read_ai, 'prepare_lmstudio', return_value='local-instance'), patch.object(read_ai, 'build_opener') as opener, patch.dict(read_ai.os.environ, {'OPENAI_API_KEY': 'primary-secret'}):
            opener.return_value.open.return_value = response
            result = read_ai.request({'ai_provider': 'lmstudio', 'ai_model': 'qwen/qwen3.5-4b'}, '', [{'id': 'p1', 'text': 'source'}], task='translate')
        request = opener.return_value.open.call_args.args[0]
        self.assertNotIn('Authorization', request.headers)
        self.assertTrue(any(isinstance(handler, read_ai.ProxyHandler) and handler.proxies == {} for handler in opener.call_args.args))
        self.assertEqual(json.loads(request.data)['reasoning_effort'], 'none')
        schema = json.loads(request.data)['response_format']['json_schema']['schema']['properties']['units']
        self.assertEqual(schema['maxItems'], 1)
        self.assertEqual(schema['items']['properties']['id']['enum'], ['p1'])
        self.assertEqual(result['units'][0]['text'], '结果')


if __name__ == '__main__':
    unittest.main()
