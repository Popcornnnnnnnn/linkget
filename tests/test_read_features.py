"""Acceptance boundaries for subtitle priority, chunks, caches and AI source references."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from linkget import cli, content, engines, read_ai, read_asr, read_formats, read_store


class ReadingFeatures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        p = patch.object(read_store, 'root', return_value=self.root / 'private')
        p.start()
        self.addCleanup(p.stop)

    def test_settings_clear_keeps_other_preferences(self):
        read_store.settings({'model': '/model', 'language': 'en', 'output': '/output'})
        read_store.settings({'model': None})
        self.assertEqual(read_store.settings(), {'language': 'en', 'output': '/output'})

    def test_subtitle_parsers_check_source_duration_and_full_coverage(self):
        for text in ['WEBVTT\n\n00:00.000 --> 00:10.000\nHello <b>world</b>\n',
                     '1\n00:00:00,000 --> 00:00:10,000\nHello world\n',
                     json.dumps({'body': [{'from': 0, 'to': 10, 'content': 'Hello world'}]}),
                     json.dumps({'events': [{'tStartMs': 0, 'dDurationMs': 10000, 'segs': [{'utf8': 'Hello world'}]}]})]:
            self.assertEqual(read_formats.parse_subtitles(text, 10)[0]['text'], 'Hello world')
        with self.assertRaises(ValueError):
            read_formats.parse_subtitles('1\n00:00:00,000 --> 00:00:10,000\nPreview\n', 100)
        with self.assertRaises(ValueError):
            read_formats.parse_subtitles(json.dumps({'body': [{'from': 10, 'to': 0, 'content': 'bad'}]}), 10)

    def test_metadata_subtitles_finish_before_any_media_download_or_model(self):
        recognized = {'text': 'Publisher speech', 'segments': [{'start': 0, 'end': 3, 'text': 'Publisher speech'}],
                      'content_complete': True, 'content_basis': 'publisher_subtitles', 'recognition_verified': False}
        def downloader(args, setup, records=None, media_consumer=None):
            self.assertTrue(args.read_audio)
            self.assertTrue(args.read_metadata_consumer({'title': 'Title'}, args.link, 'Bilibili'))
            return 0
        with patch.object(read_store, 'model_default', return_value=None), \
             patch.object(cli, 'download', side_effect=downloader), patch.object(read_formats, 'subtitles', return_value=recognized), \
             patch.object(content.toutiao, 'transcribe') as asr, contextlib.redirect_stdout(io.StringIO()):
            code = content.main(['https://www.bilibili.com/video/BV123?p=2', '--folder', str(self.root / 'out')])
        self.assertEqual(code, 0)
        asr.assert_not_called()
        result = json.loads((self.root / 'out/content.json').read_text())
        self.assertEqual(result['content_basis'], 'publisher_subtitles')
        self.assertTrue((self.root / 'out/content.srt').exists())
        self.assertIn('p=2', result['reference'])

    def test_read_audio_format_does_not_change_normal_download_format(self):
        args = argparse.Namespace(read_audio=True)
        tools = {'yt-dlp': 'yt-dlp', 'ffmpeg': 'ffmpeg'}
        command = engines.command_for(args, 'https://www.bilibili.com/video/BV123', 'yt-dlp', 'bilibili.com', self.root, tools)
        self.assertEqual(command[command.index('-f') + 1], 'ba/b')
        args.read_audio = False
        command = engines.command_for(args, 'https://www.bilibili.com/video/BV123', 'yt-dlp', 'bilibili.com', self.root, tools)
        self.assertIn('bv*', command[command.index('-f') + 1])

    def test_long_audio_resumes_checked_chunks_with_global_timestamps(self):
        video, model = self.root / 'video.mp4', self.root / 'model.bin'
        video.write_bytes(b'input')
        model.write_bytes(b'model')
        working = self.root / 'working'
        working.mkdir()
        calls, fail = [], [True]
        def process(command, **kwargs):
            if command[0] == 'ffprobe':
                return subprocess.CompletedProcess(command, 0, json.dumps({'format': {'duration': '1820'}, 'streams': [{'codec_type': 'audio'}]}))
            if command[0] == 'whisper-cli':
                calls.append(command)
                if len(calls) == 2 and fail[0]:
                    raise subprocess.CalledProcessError(1, command)
                base = Path(command[command.index('-of') + 1])
                base.with_suffix('.json').write_text(json.dumps({'result': {'language': 'en'}, 'transcription': [{'text': 'Actual speech', 'offsets': {'from': 0, 'to': 10000}}]}))
            return subprocess.CompletedProcess(command, 0)
        with patch.object(read_asr.shutil, 'which', side_effect=lambda n: n), patch.object(read_asr.subprocess, 'run', side_effect=process):
            with self.assertRaises(subprocess.CalledProcessError):
                read_asr.transcribe(video, working, model, language='auto')
            self.assertEqual(len(list((working / 'chunks').glob('*.json'))), 1)
            fail[0] = False
            result = read_asr.transcribe(video, working, model, language='auto')
            self.assertEqual(len(calls), 5)
            self.assertEqual([s['start'] for s in result['segments']], [0, 600, 1200, 1800])
            self.assertEqual(result['chunks_processed'], 4)
            read_asr.transcribe(video, working, model, language='auto')
            self.assertEqual(len(calls), 5)
            video.write_bytes(b'changed input')
            read_asr.transcribe(video, working, model, language='auto')
            self.assertEqual(len(calls), 9)

    def test_complete_cache_checks_model_language_source_and_age(self):
        model = self.root / 'model'
        model.write_bytes(b'model')
        key = read_store.cache_key('source?p=1', 'en', model, 'auto', False)
        read_store.cache(key, {'text': 'speech', 'content_complete': True})
        self.assertEqual(read_store.cached(key)['text'], 'speech')
        self.assertIsNone(read_store.cached(key, max_age=-1))
        self.assertNotEqual(key, read_store.cache_key('source?p=2', 'en', model, 'auto', False))
        self.assertNotEqual(key, read_store.cache_key('source?p=1', 'zh', model, 'auto', False))
        model.write_bytes(b'new model')
        self.assertNotEqual(key, read_store.cache_key('source?p=1', 'en', model, 'auto', False))

    def test_exports_preserve_independent_video_timelines_and_refuse_symlinks(self):
        folder = self.root / 'output'
        folder.mkdir()
        video = {'segments': [{'start': 2.5, 'end': 4, 'text': 'speech'}]}
        result = {'title': 'Title', 'reference': 'example.com/post', 'text': 'speech', 'videos': [video, video]}
        read_formats.export(folder, result, ['md', 'txt', 'srt', 'vtt'])
        self.assertEqual((folder / 'video-001.srt').read_text(), (folder / 'video-002.srt').read_text())
        self.assertIn('00:00:02,500', (folder / 'video-001.srt').read_text())
        (folder / 'content.txt').unlink()
        outside = self.root / 'outside'
        outside.write_text('preserve')
        (folder / 'content.txt').symlink_to(outside)
        with self.assertRaises(ValueError):
            read_formats.export(folder, result, ['txt'])
        self.assertEqual(outside.read_text(), 'preserve')

    def test_ai_summary_rejects_invented_source_ids_and_preserves_original(self):
        folder = self.root / 'output'
        folder.mkdir()
        original = {'content_complete': True, 'text': 'Source', 'videos': [{'segments': [{'start': 0, 'end': 1, 'text': 'Source'}]}]}
        read_store.write_json(folder / 'content.json', original)
        config = {'ai_provider': 'compatible', 'ai_model': 'configured-model'}
        with patch.object(read_ai, 'request', return_value={'points': [{'text': 'invented', 'source_ids': ['wrong']}]}):
            with self.assertRaises(ValueError):
                read_ai.generate(folder, 'summarize', 'zh', config)
        self.assertFalse((folder / 'summary.md').exists())
        self.assertEqual(read_store.read_json(folder / 'content.json'), original)
        with patch.object(read_ai, 'request', return_value={'points': [{'text': 'Summary', 'source_ids': ['v1-s1']}]}):
            output = read_ai.generate(folder, 'summarize', 'zh', config)
        self.assertIn('content.md#v1-s1', output.read_text())

    def test_ai_translation_requires_every_source_unit_exactly_once(self):
        folder = self.root / 'output'
        folder.mkdir()
        read_store.write_json(folder / 'content.json', {'content_complete': True, 'text': 'Source'})
        with patch.object(read_ai, 'request', return_value={'units': []}):
            with self.assertRaises(ValueError):
                read_ai.generate(folder, 'translate', 'en', {'ai_model': 'configured-model'})
        self.assertFalse((folder / 'translation-en.md').exists())

    def test_failed_local_read_retries_retained_media_without_fetch(self):
        local, model = self.root / 'local.wav', self.root / 'model'
        local.write_bytes(b'fixture'); model.write_bytes(b'model')
        folder = self.root / 'retry'
        recognized = {'text': 'Recovered', 'segments': [{'text': 'Recovered', 'start': 0, 'end': 3}],
                      'content_complete': True, 'content_basis': 'full_video_local_asr'}
        arguments = [str(local), '--folder', str(folder), '--whisper-model', str(model)]
        with patch.object(read_asr, 'probe', return_value=(3, True)), patch.object(cli, 'find_tool', return_value='tool'), \
             patch.object(content.toutiao, 'transcribe', side_effect=RuntimeError('interrupted')), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(content.main(arguments), 1)
        retained = folder / 'media/video-001/source.wav'
        self.assertEqual(retained.read_bytes(), b'fixture')
        local.unlink()  # Recovery must not depend on the original source still existing.
        with patch.object(cli, 'find_tool', return_value='tool'), patch.object(cli, 'download') as download, \
             patch.object(content.toutiao, 'transcribe', return_value=recognized), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(content.main(['retry', str(folder)]), 0)
        download.assert_not_called()
        self.assertEqual(read_store.read_json(folder / 'content.json')['text'], 'Recovered')
        self.assertFalse((folder / 'media').exists())

    def test_read_batch_keeps_going_after_one_item_fails(self):
        with patch.object(content, 'read_videos', side_effect=[1, 0, 0]) as reads, contextlib.redirect_stdout(io.StringIO()):
            code = content.main(['batch', '/first', '/second', '/third', '--folder', str(self.root / 'batch')])
        self.assertEqual(code, 1)
        self.assertEqual([c.args[0].url for c in reads.call_args_list], ['/first', '/second', '/third'])
        self.assertEqual(reads.call_args_list[2].args[0].folder.name, 'item-003')

    def test_changed_retained_media_is_rejected_before_asr(self):
        folder = self.root / 'retained'; folder.mkdir()
        original = self.root / 'original.wav'; original.write_bytes(b'original')
        saved = folder / 'media/source.wav'; saved.parent.mkdir(); saved.write_bytes(b'changed')
        args = {'whisper_model': None, 'language': 'auto', 'formats': 'txt', 'subtitles': 'auto', 'ocr': False,
                'chunk_seconds': 600, 'vad_model': None, 'browser': 'none', 'profile': None, 'keep_media': False, 'page_data': None}
        read_store.checkpoint(folder, str(original), args)
        read_store.write_json(folder / '.read-inputs.json', {'audio': [{'path': 'media/source.wav', 'sha256': 'expected'}], 'images': []})
        with patch.object(content.toutiao, 'transcribe') as asr, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(content.main(['retry', str(folder)]), 1)
        asr.assert_not_called()
        self.assertFalse((folder / 'content.json').exists())

    def test_ai_includes_publisher_and_ocr_text_with_source_links(self):
        source = {'text': 'speech\nimage', 'content_complete': True, 'description': 'Publisher note',
                  'videos': [{'segments': [{'start': 0, 'end': 2, 'text': 'speech'}]}], 'images': [{'text': 'image'}]}
        self.assertEqual([u['id'] for u in read_ai.units(source)], ['v1-s1', 'description', 'image-1'])
        folder = self.root / 'all-source'; folder.mkdir(); read_store.write_json(folder / 'content.json', source)
        response = {'points': [{'text': 'OCR summary', 'source_ids': ['image-1', 'description']}]}
        with patch.object(read_ai, 'request', return_value=response):
            output = read_ai.generate(folder, 'summarize', 'en', {'ai_model': 'fixture'})
        self.assertIn('content.md#image-1', output.read_text())
        self.assertIn('id="image-1"', (folder / 'content.md').read_text())

    def test_ai_http_providers_parse_output_and_reject_redirects(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        requests = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                requests.append((self.path, body))
                if self.path.startswith('/redirect'):
                    self.send_response(307); self.send_header('Location', '/chat/completions'); self.end_headers(); return
                text = '{"points":[{"text":"Result","source_ids":["p1"]}]}'
                value = {'message': {'content': text}} if self.path == '/api/chat' else {'choices': [{'message': {'content': text}}]} if self.path == '/chat/completions' else {'status': 'completed', 'output': [{'type': 'reasoning'}, {'content': [{'type': 'output_text', 'text': text}]}]}
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(value).encode())
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
        endpoint = f'http://127.0.0.1:{server.server_port}'
        for provider in ('openai', 'compatible', 'ollama'):
            result = read_ai.request({'ai_provider': provider, 'ai_model': 'fixture', 'ai_endpoint': endpoint}, 'instruction', [{'id': 'p1', 'text': 'source'}])
            self.assertEqual(result['points'][0]['text'], 'Result')
        self.assertEqual([r[0] for r in requests], ['/responses', '/chat/completions', '/api/chat'])
        self.assertIs(requests[0][1]['store'], False)
        with self.assertRaises(RuntimeError):
            read_ai.request({'ai_provider': 'compatible', 'ai_model': 'fixture', 'ai_endpoint': endpoint + '/redirect'}, '', [])
        self.assertEqual(len(requests), 4)
        with self.assertRaises(ValueError):
            read_ai.request({'ai_provider': 'compatible', 'ai_model': 'fixture', 'ai_endpoint': 'http://external.invalid'}, '', [])

    def test_ai_preserves_older_or_edited_exports_and_cites_matching_snapshot(self):
        folder = self.root / 'older'; folder.mkdir()
        source = {'content_complete': True, 'text': 'Original speech',
                  'videos': [{'segments': [{'start': 3, 'end': 5, 'text': 'Original speech'}]}]}
        read_store.write_json(folder / 'content.json', source)
        (folder / 'content.md').write_text('Older user-edited Markdown')
        (folder / 'content.txt').write_text('User edited text')
        with patch.object(read_ai, 'request', return_value={'units': [{'id': 'v1-s1', 'text': '原始讲话'}]}):
            output = read_ai.generate(folder, 'translate', 'zh', {'ai_model': 'fixture'})
        self.assertEqual((folder / 'content.md').read_text(), 'Older user-edited Markdown')
        self.assertEqual((folder / 'content.txt').read_text(), 'User edited text')
        import re
        target = re.search(r'\]\((source-[a-f0-9]+\.md)#v1-s1\)', output.read_text())[1]
        self.assertIn('Original speech', (folder / target).read_text())
        self.assertIn('id="v1-s1"', (folder / target).read_text())
        self.assertEqual(read_store.read_json(folder / 'content.json'), source)

    def test_cpa_authentication_is_explicit_bound_to_loopback_and_not_logged(self):
        from urllib.parse import urlsplit
        config_file = self.root / 'cpa.conf'
        config_file.write_text('api-keys:\n  - "local-client-key" # configured client\nother-key: "not-a-client-key"\n')
        config = {'ai_auth': 'cpa', 'ai_auth_file': str(config_file)}
        self.assertEqual(read_ai.authentication(config, urlsplit('http://127.0.0.1:8317/v1')), 'local-client-key')
        for endpoint in ['https://remote.example/v1', 'http://127.0.0.1:9999/v1', 'http://127.0.0.1:8317/other', 'http://127.0.0.1:8317/v1?key=1']:
            with self.assertRaises(ValueError):
                read_ai.authentication(config, urlsplit(endpoint))
        config_file.write_text('api-keys:\n  - "secret\\nheader-injection"\n')
        with self.assertRaises(ValueError) as result:
            read_ai.authentication(config, urlsplit('http://localhost:8317/v1'))
        self.assertNotIn('secret', str(result.exception))
        with patch.dict(read_ai.os.environ, {'TEST_LINKGET_AUTH': 'environment-key'}):
            self.assertEqual(read_ai.authentication({'ai_key_env': 'TEST_LINKGET_AUTH'}, urlsplit('https://api.example/v1')), 'environment-key')

    def test_translation_keeps_source_timeline_when_service_reorders_units(self):
        folder = self.root / 'reordered'; folder.mkdir()
        read_store.write_json(folder / 'content.json', {'content_complete': True, 'text': 'First. Second.',
            'videos': [{'segments': [{'start': 0, 'end': 1, 'text': 'First.'}, {'start': 5, 'end': 6, 'text': 'Second.'}]}]})
        response = {'units': [{'id': 'v1-s2', 'text': '第二句'}, {'id': 'v1-s1', 'text': '第一句'}]}
        with patch.object(read_ai, 'request', return_value=response):
            output = read_ai.generate(folder, 'translate', 'zh', {'ai_model': 'fixture'})
        self.assertLess(output.read_text().index('第一句'), output.read_text().index('第二句'))
        self.assertEqual([item['id'] for item in read_store.read_json(folder / 'translation-zh.json')['items']], ['v1-s1', 'v1-s2'])


if __name__ == '__main__':
    unittest.main()
