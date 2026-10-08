"""Toutiao identity/completeness boundaries; no browser, accounts or Photos access."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from linkget import cli, toutiao, read_store

URL = 'https://www.toutiao.com/video/7693844463388655644/'
MEDIA = 'https://v9-web.toutiaovod.com/fixture/video.mp4?signature=synthetic'


def page():
    return {'url': URL, 'kind': 'video', 'title': 'A shared video', 'duration': 38.4,
            'media_url': MEDIA, 'captions': []}


class ToutiaoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = patch.object(read_store, 'root', return_value=Path(temporary.name))
        root.start()
        self.addCleanup(root.stop)

    def test_mobile_tracking_and_desktop_have_same_post_identity(self):
        self.assertEqual(toutiao.canonical('https://m.toutiao.com/video/7693844463388655644/?utm_source=wechat'), URL)
        for url in ('https://toutiao.com.evil.test/video/1/', 'https://user@www.toutiao.com/video/1/',
                    'http://www.toutiao.com/video/1/', 'https://www.toutiao.com:8123/video/1/',
                    'https://www.toutiao.com/c/user/123/', 'https://www.toutiao.com/video/1/2/'):
            with self.assertRaises(ValueError):toutiao.canonical(url)

    def test_autoplay_another_post_is_rejected_before_any_files_or_network(self):
        wrong = dict(page(), url='https://www.toutiao.com/video/2/')
        with tempfile.TemporaryDirectory() as tmp, patch.object(toutiao, 'download_video') as download:
            destination = Path(tmp) / 'new'
            with self.assertRaises(ValueError):toutiao.read(URL, destination, wrong)
            self.assertFalse(destination.exists())
            download.assert_not_called()

    def test_title_is_never_complete_video_or_article_content(self):
        item = toutiao.validate_page(URL, page())
        self.assertFalse(item['content_complete'])
        with tempfile.TemporaryDirectory() as tmp, patch.object(toutiao, 'download_video') as download:
            with self.assertRaises(ValueError):toutiao.read(URL, Path(tmp) / 'new', page())
            download.assert_not_called()
        article = {'url': 'https://www.toutiao.com/article/123/', 'kind': 'article', 'title': 'Title',
                   'text': 'Title', 'content_complete': True}
        with self.assertRaises(ValueError):toutiao.validate_page(article['url'], article)

    def test_page_payload_selects_only_content_and_does_not_persist_account_fields(self):
        item = toutiao.validate_page(URL, {**page(), 'cookies': 'synthetic', 'account': {'name': 'synthetic'}})
        self.assertNotIn('cookies', item)
        self.assertNotIn('account', item)
        for url in ('https://toutiaovod.com.evil.test/video.mp4', 'https://127.0.0.1/video.mp4',
                    'http://v9-web.toutiaovod.com/video.mp4', 'https://user@v9-web.toutiaovod.com/video.mp4'):
            with self.assertRaises(ValueError):toutiao.validate_page(URL, dict(page(), media_url=url))

    def test_invalid_duration_and_caption_ranges_are_rejected(self):
        for duration in (0, -1, float('nan'), float('inf'), True):
                with self.assertRaises(ValueError):toutiao.validate_page(URL, dict(page(), duration=duration))
        self.assertEqual(toutiao.validate_page(URL, dict(page(), duration=1801))['duration'], 1801)
        with self.assertRaises(ValueError):
            toutiao.validate_page(URL, dict(page(), captions=[{'text': 'wrong', 'start': 9, 'end': 1}]))

    def test_missing_player_duration_requires_direct_bound_source_and_download_probe(self):
        pending={**page(),'duration':None,'duration_pending':True,'media_binding':'player_current_src'}
        self.assertFalse(toutiao.validate_page(URL,pending)['content_complete'])
        for bad in ({**pending,'duration_pending':False},{**pending,'media_binding':'single_page_vod_resource'},
                    {**pending,'captions':[{'text':'unbound','start':0,'end':2}]}):
            with self.assertRaises(ValueError):toutiao.validate_page(URL,bad)
        with tempfile.TemporaryDirectory() as tmp, patch.object(toutiao.shutil,'which',return_value='tool'):
            root=Path(tmp);model=root/'model.bin';model.touch()
            with patch.object(toutiao,'download_video',side_effect=lambda u,p:p.write_bytes(b'complete')), \
                    patch('linkget.read_asr.probe',return_value=(123.4,True)), \
                    patch.object(toutiao,'transcribe',return_value={'text':'full speech','content_complete':True}) as asr:
                output=toutiao.read(URL,root/'out',pending,model)
                self.assertEqual(asr.call_args.args[3],123.4)
                self.assertEqual(json.loads(output.read_text())['duration_basis'],'complete_download_ffprobe')
                self.assertFalse((root/'out/source.mp4').exists())

    def test_partial_http_media_response_is_never_full_content(self):
        class Response(io.BytesIO):
            def geturl(self):return MEDIA
        for status,headers,data in [(206,{'Content-Range':'bytes 0-2/10'},b'abc'),
                                    (200,{'Content-Length':'10'},b'abc'),(200,{'Content-Length':'3'},b'abc')]:
            with tempfile.TemporaryDirectory() as tmp:
                response=Response(data);response.status=status;response.headers=headers
                with patch.object(toutiao,'urlopen',return_value=response):
                    if status==200 and headers['Content-Length']=='3':toutiao.download_video(MEDIA,Path(tmp)/'video.mp4')
                    else:
                        with self.assertRaises(RuntimeError):toutiao.download_video(MEDIA,Path(tmp)/'video.mp4')

    def test_repeated_run_preserves_evidence_without_browser_or_download(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(toutiao, 'read_browser') as browser:
            with self.assertRaises(ValueError):toutiao.read(URL, Path(tmp))
            browser.assert_not_called()

    def test_truncated_media_or_no_audio_never_runs_recognition(self):
        for data in ({'format': {'duration': '12'}, 'streams': [{'codec_type': 'audio'}]},
                     {'format': {'duration': '38.4'}, 'streams': [{'codec_type': 'video'}]}):
            with tempfile.TemporaryDirectory() as tmp, patch.object(toutiao.shutil, 'which', side_effect=lambda n: n), \
                    patch.object(toutiao.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, json.dumps(data))) as run:
                root = Path(tmp); model = root / 'model.bin'; model.touch()
                with self.assertRaises(RuntimeError):toutiao.transcribe(root / 'source.mp4', root, model, 38.4)
                self.assertEqual(run.call_count, 1)

    def test_read_command_keeps_browser_credentials_and_photos_out_of_the_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); data = root / 'page.json'; data.write_text(json.dumps(page()))
            model = root / 'model.bin'; model.touch()
            def download(url, path):
                self.assertEqual(url, MEDIA);path.write_bytes(b'video')
            def recognized(video, folder, model, duration):
                self.assertEqual(duration, 38.4)
                return {'text': 'Complete recognized speech', 'segments': [{'start': 0, 'end': 38.4, 'text': 'Complete recognized speech'}], 'content_complete': True,
                        'content_basis': 'full_video_local_asr', 'recognition_verified': False}
            with patch.object(toutiao.shutil, 'which', return_value='tool'), \
                    patch.object(toutiao, 'download_video', side_effect=download), \
                    patch.object(toutiao, 'transcribe', side_effect=recognized), \
                    patch.object(cli, 'inspect_files', return_value=[{'duration': 38.4, 'audio': True, 'probe_ok': True}]), \
                    patch.object(cli, 'first_run') as setup, patch.object(cli, 'prepare_session') as credentials, \
                    patch.object(cli, 'import_photos') as photos, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['read', '--foreground', URL, '--folder', str(root / 'out'), '--page-data', str(data),
                                           '--transcribe', '--whisper-model', str(model)]), 0)
                setup.assert_not_called();credentials.assert_not_called();photos.assert_not_called()
            result = json.loads((root / 'out/content.json').read_text())
            self.assertEqual(result['url'], URL)
            self.assertNotIn('media_url', result)
            self.assertFalse(result['send_enabled'])
            self.assertFalse(result['recognition_verified'])
            self.assertFalse((root / 'out/source.mp4').exists())

    def test_cleanup_preserves_content_and_summary_and_refuses_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in (*toutiao.INTERMEDIATES, 'content.json', 'summary.md', 'model.bin'):
                (root / name).write_text('evidence')
            self.assertEqual(set(toutiao.cleanup_media(root)), set(toutiao.INTERMEDIATES))
            for name in ('content.json', 'summary.md', 'model.bin'):
                self.assertEqual((root / name).read_text(), 'evidence')
            (root / 'source.mp4').symlink_to(root / 'model.bin')
            with self.assertRaises(ValueError):toutiao.cleanup_media(root)
            self.assertEqual((root / 'model.bin').read_text(), 'evidence')

    def test_failed_download_cleans_partial_media_without_ready_content(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(toutiao.shutil, 'which', return_value='tool'):
            root = Path(tmp); model = root / 'model.bin'; model.touch()
            def fail(url, path):
                path.write_bytes(b'partial');raise RuntimeError('interrupted')
            with patch.object(toutiao, 'download_video', side_effect=fail):
                with self.assertRaises(RuntimeError):toutiao.read(URL, root / 'out', page(), model)
            self.assertEqual(list((root / 'out').iterdir()), [])
            self.assertTrue(model.exists())

    def test_manual_review_retains_media_until_explicit_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(toutiao.shutil, 'which', return_value='tool'):
            root = Path(tmp); model = root / 'model.bin'; model.touch()
            with patch.object(toutiao, 'download_video', side_effect=lambda u,p:p.write_bytes(b'video')), \
                    patch.object(toutiao, 'transcribe', return_value={'text':'speech'}):
                toutiao.read(URL, root / 'out', page(), model, keep_media=True)
            self.assertTrue((root / 'out/source.mp4').exists())
            toutiao.cleanup_media(root / 'out')
            self.assertTrue((root / 'out/content.json').exists())

    def test_rendered_reader_reads_only_matching_dom_content(self):
        script = (Path(toutiao.__file__).parent / 'toutiao_browser.js').read_text()
        cases = [('video', 'https://v9-web.toutiaovod.com/video.mp4', 38.4), ('video', '', 38.4)]
        for kind, src, duration in cases:
            code = 'const location={hostname:"www.toutiao.com",pathname:"/video/123/"};\n'
            code += 'const document={querySelector:s=>s==="h1"?{innerText:"展开\\nTitle"}:s==="video"?'
            code += json.dumps({'currentSrc': src, 'duration': duration, 'textTracks': []}) + ':null};\n'
            code += 'process.stdout.write(JSON.stringify((' + script + ')()));'
            result = subprocess.run(['/opt/homebrew/bin/node', '-'], input=code, text=True, capture_output=True, check=True)
            data = json.loads(result.stdout)
            self.assertEqual(data['title'], 'Title') if src else self.assertIsNone(data)


if __name__ == '__main__':unittest.main()
