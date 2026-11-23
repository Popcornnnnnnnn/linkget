"""Tests for Phase 2 richer reading: video keyframes, general web articles, and WeChat Official Accounts."""
import contextlib
from email.message import Message
import gzip
import http.client
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import Request

from linkget import (
    accounts,
    cli,
    content,
    neo_browser,
    read_formats,
    read_frames,
    read_tasks,
    rendered_page,
    web_article,
)
from linkget.media import DownloadResult


class FakeHTTPResponse:
    def __init__(self, body_bytes, *, url, content_type='text/html; charset=utf-8', content_encoding=''):
        self._buf = io.BytesIO(body_bytes)
        self._url = url
        self.headers = Message()
        if content_type:
            self.headers['Content-Type'] = content_type
        if content_encoding:
            self.headers['Content-Encoding'] = content_encoding

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n=-1):
        return self._buf.read(n)

    def geturl(self):
        return self._url


class FakeOpener:
    def __init__(self, responder):
        self._responder = responder
        self.requests = []

    def open(self, req, timeout=15):
        self.requests.append((req, timeout))
        return self._responder(req)


class RicherReadingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        patcher = patch.object(accounts, 'directory', return_value=self.root / 'settings/sessions')
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'ffmpeg and ffprobe are required')
    def test_keyframes_scene_fallback_audio_only_and_cli_silent_video(self):
        ffmpeg = shutil.which('ffmpeg')
        ffprobe = shutil.which('ffprobe')

        # 1) Multi-scene silent synthetic video (black/white alternating hard cuts) -> scene frames + cap
        multi = self.root / 'scenes.mp4'
        subprocess.run(
            [
                ffmpeg, '-y', '-v', 'error',
                '-f', 'lavfi', '-i', 'color=c=black:s=64x64:d=1',
                '-f', 'lavfi', '-i', 'color=c=white:s=64x64:d=1',
                '-f', 'lavfi', '-i', 'color=c=black:s=64x64:d=1',
                '-f', 'lavfi', '-i', 'color=c=white:s=64x64:d=1',
                '-filter_complex', '[0:v][1:v][2:v][3:v]concat=n=4:v=1:a=0',
                '-pix_fmt', 'yuv420p',
                str(multi),
            ],
            check=True,
        )
        frames_all = read_frames.extract_keyframes(
            multi, self.root / 'f-all', ffmpeg=ffmpeg, ffprobe=ffprobe, max_frames=12
        )
        self.assertGreaterEqual(len(frames_all), 3)
        self.assertEqual(frames_all[0]['method'], 'scene')
        for frame in frames_all:
            self.assertTrue(Path(frame['path']).is_file())
            self.assertTrue(frame['relative_path'].startswith('frames/v1-frame-'))
            self.assertGreater(frame['bytes'], 0)

        frames_capped = read_frames.extract_keyframes(
            multi, self.root / 'f-cap', ffmpeg=ffmpeg, ffprobe=ffprobe, max_frames=2
        )
        self.assertEqual(len(frames_capped), 2)

        # 2) Static single-color video -> uniform fallback
        static_vid = self.root / 'static.mp4'
        subprocess.run(
            [
                ffmpeg, '-y', '-v', 'error',
                '-f', 'lavfi', '-i', 'color=c=black:s=64x64:d=4',
                '-pix_fmt', 'yuv420p',
                str(static_vid),
            ],
            check=True,
        )
        frames_static = read_frames.extract_keyframes(
            static_vid, self.root / 'f-static', ffmpeg=ffmpeg, ffprobe=ffprobe, max_frames=4
        )
        self.assertEqual(len(frames_static), 3)
        self.assertEqual(frames_static[0]['method'], 'uniform')

        # 3) Audio-only local file returns empty keyframe list gracefully
        audio_only = self.root / 'tone.wav'
        subprocess.run(
            [ffmpeg, '-y', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1', str(audio_only)],
            check=True,
        )
        self.assertEqual(read_frames.extract_keyframes(audio_only, self.root / 'f-aud', ffmpeg=ffmpeg, ffprobe=ffprobe), [])

        # 4) End-to-end local silent video read with --frames and --frame-ocr -> Markdown + STDOUT + frames/
        out_dir = self.root / 'read-silent'
        with patch('linkget.read_ocr.images', side_effect=lambda paths: [{'text': f'Slide {i} text'} for i, _ in enumerate(paths, 1)]):
            code, out, err = self.run_cli(
                'read', str(multi), '--folder', str(out_dir), '--frames', '--max-frames', '2', '--frame-ocr', '--stdout'
            )
        self.assertEqual(code, 0, out + err)
        self.assertIn('## Keyframes', out)
        self.assertIn('![Frame 1', out)
        self.assertIn('Slide 1 text', out)
        saved = json.loads((out_dir / 'content.json').read_text())
        self.assertEqual(len(saved['frames']), 2)
        self.assertTrue((out_dir / 'frames' / 'v1-frame-001.jpg').is_file())
        self.assertEqual(saved['frames'][0]['ocr_text'], 'Slide 1 text')

        # 5) Subsequent read WITHOUT --frames must NOT inherit cached --frames result from out_dir
        no_frames_dir = self.root / 'read-no-frames'
        code, out, _ = self.run_cli('read', str(multi), '--folder', str(no_frames_dir), '--foreground', '--json')
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)['ok'])
        self.assertFalse((no_frames_dir / 'frames').exists())

        # 6) Silent .webm video works with --frames cleanly across all content.py checks
        silent_webm = self.root / 'silent.webm'
        silent_webm.write_bytes(b'webm-fixture')
        webm_dir = self.root / 'read-silent-webm'
        fake_webm_frame = [{
            'index': 1, 'timestamp': 0.5, 'timestamp_label': '00:00:00',
            'path': str(webm_dir / 'frames/v1-frame-001.jpg'),
            'relative_path': 'frames/v1-frame-001.jpg',
            'method': 'uniform', 'sha256': 'def', 'bytes': 50,
        }]
        with patch.object(cli, 'find_tool', return_value='/usr/bin/tool'), \
                patch.object(read_frames, 'probe_video', return_value={'has_video': True, 'duration': 2.0, 'width': 64, 'height': 64}), \
                patch('linkget.read_asr.probe', return_value=(2.0, False)), \
                patch.object(read_frames, 'extract_keyframes', return_value=fake_webm_frame):
            code, out, err = self.run_cli('read', str(silent_webm), '--folder', str(webm_dir), '--frames', '--foreground', '--json')
        self.assertEqual(code, 0, out + err)
        webm_payload = json.loads(out)
        self.assertTrue(webm_payload['ok'])
        self.assertEqual(len(webm_payload['result']['frames']), 1)

    def test_video_url_with_frames_downloads_video_and_reuses_subtitles(self):
        out_dir = self.root / 'url-frames'
        subs = {
            'text': 'Hello keyframe world',
            'segments': [{'start': 0.0, 'end': 3.0, 'text': 'Hello keyframe world'}],
            'content_complete': True,
            'content_basis': 'publisher_subtitles',
            'recognition_verified': False,
        }
        vid_src = self.root / 'dl.mp4'
        vid_src.write_bytes(b'fake-mp4')

        def fake_download_clean(d_args, setup, records=None, media_consumer=None):
            self.assertFalse(d_args.read_audio)
            consumed_early = d_args.read_metadata_consumer({'title': 'Talk', 'duration': 10}, d_args.link, 'YouTube')
            self.assertFalse(consumed_early)
            media_consumer(
                [vid_src],
                [{'kind': 'video', 'duration': 10.0, 'audio': True, 'probe_ok': True}],
                DownloadResult(expected_count=1),
                d_args.link,
                'YouTube',
                {'title': 'Talk', 'duration': 10.0},
            )
            return 0

        fake_frames = [{
            'index': 1, 'timestamp': 1.5, 'timestamp_label': '00:00:01',
            'path': str(out_dir / 'frames/v1-frame-001.jpg'),
            'relative_path': 'frames/v1-frame-001.jpg',
            'method': 'scene', 'sha256': 'abc', 'bytes': 100,
        }]
        with patch.object(cli, 'find_tool', return_value='/usr/bin/tool'), \
                patch.object(cli, 'download', side_effect=fake_download_clean), \
                patch.object(read_formats, 'subtitles', return_value=subs), \
                patch.object(read_frames, 'extract_keyframes', return_value=fake_frames):
            code, out, err = self.run_cli(
                'read', 'https://www.youtube.com/watch?v=BaW_jenozKc',
                '--folder', str(out_dir), '--frames', '--foreground', '--json'
            )
        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertTrue(payload['ok'])
        self.assertEqual(payload['result']['videos'][0]['content_basis'], 'publisher_subtitles')
        self.assertEqual(len(payload['result']['frames']), 1)

    def test_max_frames_validation_rejects_out_of_range_before_detaching(self):
        for bad in ('0', '61'):
            with patch.object(read_tasks, 'submit') as submit, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as exc:
                    read_tasks.main(['https://example.org/post', '--max-frames', bad])
            self.assertEqual(exc.exception.code, 2)
            submit.assert_not_called()

    def test_general_web_html_markdown_plain_gzip_and_ssrf_safety(self):
        sample_html = '''<!doctype html>
        <html>
          <head>
            <meta charset="utf-8">
            <title>Ignored Site Title</title>
            <meta property="og:title" content="Deep Dive into Protocols">
            <meta name="author" content="Alice Dev">
            <script type="application/ld+json">{"datePublished": "2026-05-01"}</script>
          </head>
          <body>
            <header><nav>Home | Pricing | Login</nav></header>
            <main>
              <article class="post-body">
                <h1>Deep Dive into Protocols</h1>
                <p>Modern protocols rely on clean framing, explicit timeouts, and bounded buffers for safety.</p>
                <h2>Key Design</h2>
                <ul>
                  <li>Validate URLs before connecting.</li>
                  <li>Bound response bytes strictly.</li>
                </ul>
                <pre><code>def hello():
    return "world"</code></pre>
                <p>See the <a href="/docs/spec">spec</a> for full byte-level details and invariants.</p>
              </article>
              <aside class="sidebar">Related links cookie banner subscribe now</aside>
            </main>
            <footer>Copyright 2026</footer>
          </body>
        </html>'''

        public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        opener = FakeOpener(lambda req: FakeHTTPResponse(gzip.compress(sample_html.encode('utf-8')), url='https://blog.example.com/posts/protocols', content_encoding='gzip'))
        with patch.object(web_article, 'build_opener', return_value=opener), \
                patch.object(web_article.socket, 'getaddrinfo', return_value=public_dns):
            out_dir = self.root / 'web-read'
            code, out, err = self.run_cli('read', 'https://blog.example.com/posts/protocols', '--folder', str(out_dir), '--foreground', '--json')
        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertTrue(payload['ok'])
        res = payload['result']
        self.assertEqual(res['kind'], 'article')
        self.assertEqual(res['title'], 'Deep Dive into Protocols')
        self.assertEqual(res['author'], 'Alice Dev')
        self.assertEqual(res['published_at'], '2026-05-01')
        self.assertIn('bounded buffers for safety', res['text'])
        self.assertNotIn('cookie banner', res['text'])
        self.assertIn('[spec](https://blog.example.com/docs/spec)', res['markdown'])
        self.assertIn('```', (out_dir / 'content.md').read_text())

        # Markdown & plain text endpoints
        md_doc = '# Notes Title\n\nSome clean markdown paragraph with more than ten characters.'
        md_opener = FakeOpener(lambda req: FakeHTTPResponse(md_doc.encode(), url='https://docs.example.com/readme.md', content_type='text/markdown; charset=utf-8'))
        with patch.object(web_article, 'build_opener', return_value=md_opener), \
                patch.object(web_article.socket, 'getaddrinfo', return_value=public_dns):
            code, out, err = self.run_cli('read', 'https://docs.example.com/readme.md', '--folder', str(self.root / 'md-read'), '--stdout')
        self.assertEqual(code, 0, err)
        self.assertIn('# Notes Title', out)

        # SSRF & decompression bomb & invalid content-type rejections
        for bad_url in (
            'http://127.0.0.1/admin',
            'http://169.254.169.254/latest/meta-data/',
            'http://10.0.0.1/secret',
            'http://[::1]/status',
            'http://localhost:8080/health',
            'https://service.local/internal',
            'https://user:secret@example.com/article',
        ):
            with self.assertRaises(ValueError):
                web_article.validate_public_web_url(bad_url, resolve_dns=False)

        with patch.object(web_article.socket, 'getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.50', 443))]):
            with self.assertRaises(ValueError):
                web_article.validate_public_web_url('https://rebind.example.com/post', resolve_dns=True)

        handler = web_article._SafeRedirectHandler(resolve_dns=False)
        with self.assertRaises(ValueError):
            handler.redirect_request(Request('https://example.com/a'), None, 302, 'Found', {}, 'http://127.0.0.1/internal')

        # Decompression bomb protection
        huge_zeros = gzip.compress(b'A' * 1024)
        with self.assertRaises(ValueError):
            web_article._decompress_bounded(huge_zeros, 'gzip', max_bytes=128)

        # Reject binary PDF or JS challenge shell
        pdf_opener = FakeOpener(lambda req: FakeHTTPResponse(b'%PDF-1.7', url=req.full_url, content_type='application/pdf'))
        with self.assertRaises(ValueError):
            web_article.read_article('https://example.com/file.pdf', opener=pdf_opener)

        shell_html = '<html><body><p>Please enable JavaScript and cookies to continue.</p></body></html>'
        shell_opener = FakeOpener(lambda req: FakeHTTPResponse(shell_html.encode(), url=req.full_url))
        with self.assertRaises(ValueError):
            web_article.read_article('https://example.com/cf', opener=shell_opener)

        # Non-canonical shorthand/octal IPv4 SSRF and known mobile media hosts
        for bad_short in ('http://127.1/secret', 'http://0177.0.0.1/secret'):
            with self.assertRaises(ValueError):
                web_article.validate_public_web_url(bad_short, resolve_dns=False)
        self.assertTrue(web_article.is_known_media_host('m.weibo.cn'))
        self.assertTrue(web_article.is_known_media_host('www.iesdouyin.com'))

        # Hostile HTTP payloads (charset=base64, corrupt gzip, IncompleteRead) fail as clean JSON errors
        base64_opener = FakeOpener(lambda req: FakeHTTPResponse(
            b'<html><body><article><p>Fallback UTF-8 article body that is long enough to extract cleanly.</p></article></body></html>',
            url=req.full_url, content_type='text/html; charset=base64',
        ))
        with patch.object(web_article, 'build_opener', return_value=base64_opener), \
                patch.object(web_article.socket, 'getaddrinfo', return_value=public_dns):
            code, out, err = self.run_cli('read', 'https://example.com/b64', '--folder', str(self.root / 'web-b64'), '--foreground', '--json')
        self.assertEqual(code, 0, out + err)
        self.assertTrue(json.loads(out)['ok'])

        bad_gzip_opener = FakeOpener(lambda req: FakeHTTPResponse(b'not-gzip', url=req.full_url, content_encoding='gzip'))
        with patch.object(web_article, 'build_opener', return_value=bad_gzip_opener), \
                patch.object(web_article.socket, 'getaddrinfo', return_value=public_dns):
            code, out, _ = self.run_cli('read', 'https://example.com/badgz', '--folder', str(self.root / 'web-badgz'), '--foreground', '--json')
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)['ok'])

        inc_opener = FakeOpener(lambda req: (_ for _ in ()).throw(http.client.IncompleteRead(b'part', 100)))
        with patch.object(web_article, 'build_opener', return_value=inc_opener), \
                patch.object(web_article.socket, 'getaddrinfo', return_value=public_dns):
            code, out, _ = self.run_cli('read', 'https://example.com/inc', '--folder', str(self.root / 'web-inc'), '--foreground', '--json')
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)['ok'])

    def test_wechat_canonical_http_gate_browser_fallback_and_js_scope(self):
        # Canonicalization & rejection
        url_path, eligible_path = web_article.canonical_wechat_url('https://mp.weixin.qq.com/s/AbCdEf123456?scene=21#rd')
        self.assertEqual(url_path, 'https://mp.weixin.qq.com/s/AbCdEf123456')
        self.assertTrue(eligible_path)

        url_qs, eligible_qs = web_article.canonical_wechat_url(
            'https://mp.weixin.qq.com/s?__biz=MzA1234==&mid=1001&idx=1&sn=abc12345def&chksm=junk&scene=126'
        )
        self.assertEqual(url_qs, 'https://mp.weixin.qq.com/s?__biz=MzA1234%3D%3D&mid=1001&idx=1&sn=abc12345def')
        self.assertFalse(eligible_qs)

        with self.assertRaises(ValueError):
            web_article.canonical_wechat_url('https://mp.weixin.qq.com/mp/profile_ext?action=home')

        # Direct HTTP WeChat article extraction
        public_dns = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', 443))]
        wx_html = '''<html><head><title>Wx</title></head><body>
          <h1 id="activity-name">Official Engineering Note</h1>
          <a id="js_name">Tech Team</a>
          <em id="publish_time">2026-10-01</em>
          <div id="js_content">
            <p>First paragraph of the official WeChat article describing architecture.</p>
            <p>Second paragraph with concrete latency numbers and lessons learned.</p>
          </div>
        </body></html>'''
        wx_opener = FakeOpener(lambda req: FakeHTTPResponse(wx_html.encode('utf-8'), url=url_path))
        with patch.object(web_article, 'build_opener', return_value=wx_opener), \
                patch.object(web_article.socket, 'getaddrinfo', return_value=public_dns):
            code, out, err = self.run_cli('read', url_path, '--folder', str(self.root / 'wx-http'), '--browser', 'none', '--stdout')
        self.assertEqual(code, 0, err)
        self.assertIn('# Official Engineering Note', out)
        self.assertIn('Tech Team · 2026-10-01', out)
        self.assertIn('Second paragraph with concrete latency numbers', out)

        # Gate triggers browser fallback for /s/<id> (distinct article ID to bypass 24h cache)
        gate_url = 'https://mp.weixin.qq.com/s/GateArticle1234'
        gate_html = '<html><body><div>环境异常，当前环境存在异常，请完成验证后继续访问。</div></body></html>'
        gate_opener = FakeOpener(lambda req: FakeHTTPResponse(gate_html.encode('utf-8'), url=gate_url))
        tail = 'Body from browser DOM after WeChat verification passed cleanly.'
        wx_dom = {
            'url': gate_url, 'final_url': gate_url, 'title': 'Browser WeChat Article',
            'author': 'Account Name', 'published_at': '2026-10-02', 'kind': 'article',
            'text': 'Browser WeChat Article\n\n' + tail,
            'content_complete': True,
            'dom_proof': {
                'selector': '#js_content', 'reader_revision': 1,
                'basis': 'bounded_wechat_article_dom', 'http_transfer_verified': False,
                'end_excerpt': tail,
            },
            'browser_controller': {
                'kind': 'browseros_neo_mcp', 'reader_revision': 1,
                'temporary_tab_cleanup': 'closed', 'credentials_exported': False, 'server_version': '0.0.69',
            },
        }
        with patch.object(web_article, 'build_opener', return_value=gate_opener), \
                patch.object(web_article.socket, 'getaddrinfo', return_value=public_dns), \
                patch.object(rendered_page, 'selection', return_value={'browser': 'neo', 'source': 'test', 'supported': True, 'enabled': True}), \
                patch.object(rendered_page, 'read_wechat', return_value=rendered_page.validate_wechat_page(wx_dom, gate_url)) as r_wx:
            code, out, err = self.run_cli('read', gate_url, '--folder', str(self.root / 'wx-browser'), '--foreground', '--json')
        self.assertEqual(code, 0, out + err)
        r_wx.assert_called_once()
        wx_res = json.loads(out)['result']
        self.assertEqual(wx_res['title'], 'Browser WeChat Article')
        self.assertEqual(wx_res['content_basis'], 'complete_browseros_neo_wechat_dom')

        # Gate with --browser none does NOT call rendered_page
        nb_url = 'https://mp.weixin.qq.com/s/NoBrowserGate12'
        nb_opener = FakeOpener(lambda req: FakeHTTPResponse(gate_html.encode('utf-8'), url=nb_url))
        with patch.object(web_article, 'build_opener', return_value=nb_opener), \
                patch.object(web_article.socket, 'getaddrinfo', return_value=public_dns), \
                patch.object(rendered_page, 'read_wechat') as r_none:
            code, out, _ = self.run_cli('read', nb_url, '--folder', str(self.root / 'wx-nobrowser'), '--browser', 'none', '--foreground', '--json')
        self.assertEqual(code, 1)
        r_none.assert_not_called()
        self.assertFalse(json.loads(out)['ok'])

        # Deleted WeChat article raises clear RuntimeError without invoking browser reader
        del_url = 'https://mp.weixin.qq.com/s/DeletedArticle12'
        deleted_html = '<html><body><div>该内容已被发布者删除</div></body></html>'
        deleted_opener = FakeOpener(lambda req: FakeHTTPResponse(deleted_html.encode('utf-8'), url=del_url))
        with patch.object(web_article, 'build_opener', return_value=deleted_opener), \
                patch.object(web_article.socket, 'getaddrinfo', return_value=public_dns), \
                patch.object(rendered_page, 'read_wechat') as r_del:
            code, out, _ = self.run_cli('read', del_url, '--folder', str(self.root / 'wx-deleted'), '--foreground', '--json')
        self.assertEqual(code, 1)
        r_del.assert_not_called()
        self.assertIn('deleted', json.loads(out)['error']['message'])

        # neo_browser scope check and _read_script selector check for WeChat
        self.assertEqual(neo_browser._content_url(url_path), url_path)
        self.assertIn('#js_content, #activity-name, .weui-msg', neo_browser._read_script(url_path, 'function(){}', 'tok', 1000, 1))
        for bad in (url_qs, url_path + '#frag', 'https://example.org/s/AbCdEf123456'):
            with self.assertRaises(ValueError):
                neo_browser._content_url(bad)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for JS extractor sanity check')
    def test_wechat_browser_js_normalization(self):
        js = (Path(rendered_page.__file__).with_name('wechat_browser.js')).read_text()
        expected = 'https://mp.weixin.qq.com/s/AbCdEf123456'
        script = (
            'global.location={href:"https://example.org/",pathname:"/",search:""};'
            'global.document={readyState:"complete",body:{textContent:""},querySelector:()=>null};'
            'const fn=(' + js + ');'
            'process.stdout.write(JSON.stringify(fn(' + json.dumps(expected) + ')));'
        )
        res = subprocess.run([shutil.which('node'), '-e', script], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(res.stdout), {'diagnostic': {'reason': 'public_article_mismatch'}})

    def test_inspect_and_sites_cover_web_and_wechat_and_keep_media_rules_intact(self):
        for target, exp_site in (
            ('https://example.com/posts/2026', 'example.com'),
            ('https://mp.weixin.qq.com/s/AbCdEf123456?scene=21', 'WeChat'),
            ('https://mp.weixin.qq.com/s?__biz=MzA1234==&mid=1001&idx=1&sn=abc12345def&chksm=junk', 'WeChat'),
        ):
            code, out, err = self.run_cli('inspect', target, '--no-network', '--json')
            self.assertEqual(code, 0, out + err)
            data = json.loads(out)
            self.assertTrue(data['ok'])
            self.assertEqual(data['site'], exp_site)
            self.assertEqual(data['engine'], 'web_article')
            self.assertTrue(data['supports_read'])

            # --audio on articles returns ok: False cleanly
            code, out, _ = self.run_cli('inspect', target, '--no-network', '--audio', '--json')
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(out)['ok'])

        # Private IP and out-of-scope Raspberry Pi URLs remain rejected
        for bad in ('http://127.0.0.1/post', 'https://www.raspberrypi.com/news/post/', 'https://soundcloud.com/a/sets/b'):
            code, out, _ = self.run_cli('inspect', bad, '--no-network', '--json')
            self.assertEqual(code, 1)
            self.assertFalse(json.loads(out)['ok'])

        code, out, err = self.run_cli('sites', '--json')
        self.assertEqual(code, 0, err)
        names = {row['name'] for row in json.loads(out)['sites']}
        self.assertIn('WeChat Official Accounts', names)
        self.assertIn('Web articles', names)


if __name__ == '__main__':
    unittest.main()
