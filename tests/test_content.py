"""Read routes reuse downloads and commit only complete speech results."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, cli, content, toutiao
from linkget.media import DownloadResult


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.model = self.root / 'model.bin'
        self.model.touch()
        self.stages = []
        self.names = ['clip.mp4']
        self.evidence = DownloadResult()
        self.recognized = {'text': 'Recognized speech', 'segments': [{'text': 'Recognized speech', 'start': 0, 'end': 3}],
                           'content_complete': True, 'content_basis': 'full_video_local_asr',
                           'recognition_verified': False, 'media_evidence': {'duration': 3, 'audio': True}}
        for target, field, value in ((accounts, 'preferences', {}), (accounts, 'directory', self.root / 'sessions'),
                                     (cli, 'find_tool', 'tool')):
            p = patch.object(target, field, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def download(self, url, directory, *args):
        self.stages.append(directory.parent)
        for name in self.names:
            (directory / name).write_bytes(b'fixture')
        return self.evidence

    def engine(self, command, **kwargs):
        if '--directory' in command:
            directory = Path(command[command.index('--directory') + 1])
        else:
            directory = Path(command[command.index('-o') + 1]).parent
        self.download(command[-1], directory)
        return subprocess.CompletedProcess(command, 0)

    def prepare(self, command, info, log, domain):
        info.write_text(json.dumps({'title': 'Publisher title', 'description': 'Publisher description',
                                    'duration': 3, 'url': 'https://signed.invalid/?secret=fixture'}))
        return command

    def inspect(self, files, variants=None):
        return [{'file': p.name, 'kind': 'video' if p.suffix in cli.VIDEO else 'image',
                 'probe_ok': True, 'audio': True, 'duration': '3', 'source': 'engine_selected'} for p in files]

    def run_read(self, url, folder='out', extra=(), recognized=None):
        with patch.object(cli, 'download_douyin', side_effect=self.download), \
             patch.object(cli, 'download_xiaohongshu', side_effect=self.download), \
             patch.object(cli, 'download_kuaishou', side_effect=self.download), \
             patch.object(cli, 'run_process', side_effect=self.engine), \
             patch.object(cli, 'prepare_video', side_effect=self.prepare), \
             patch.object(cli, 'gallery_evidence', return_value=self.evidence), \
             patch.object(cli, 'inspect_files', side_effect=self.inspect), \
             patch.object(toutiao, 'transcribe', side_effect=recognized, return_value=self.recognized) as asr, \
             patch.object(cli, 'first_run') as setup, patch.object(cli, 'save_folder') as save, \
             patch.object(cli, 'import_photos') as photos, contextlib.redirect_stdout(io.StringIO()), \
             contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(['read', url, '--folder', str(self.root / folder), '--transcribe',
                             '--whisper-model', str(self.model), '--browser', 'none', *extra])
            setup.assert_not_called()
            save.assert_not_called()
            photos.assert_not_called()
            return code, asr

    def test_all_existing_video_routes_use_the_read_consumer(self):
        urls = ['https://www.bilibili.com/video/BV1bK411W797?p=2',
                'https://youtu.be/abcdefghijk', 'https://www.douyin.com/video/123',
                'https://www.kuaishou.com/short-video/123',
                'https://www.xiaohongshu.com/explore/' + 'a' * 24 + '?xsec_token=private',
                'https://www.tiktok.com/@user/video/123', 'https://x.com/user/status/123',
                'https://www.instagram.com/reel/abc/', 'https://weibo.com/tv/show/123:abc',
                'https://weibo.com/detail/abc', 'https://www.reddit.com/r/test/comments/abc/post/',
                'https://user.lofter.com/post/abc', 'https://www.pinterest.com/pin/123/',
                'https://www.bilibili.com/opus/123']
        for i, url in enumerate(urls):
            with self.subTest(url=url):
                folder = f'out-{i}'
                code, asr = self.run_read(url, folder)
                self.assertEqual(code, 0)
                data = json.loads((self.root / folder / 'content.json').read_text())
                self.assertEqual(data['text'], 'Recognized speech')
                self.assertEqual(data['video_count'], 1)
                self.assertFalse(data['recognition_verified'])
                self.assertNotIn('private', json.dumps(data))
                self.assertNotIn('signed.invalid', json.dumps(data))
                self.assertFalse((self.root / folder / 'media').exists())
                self.assertEqual(asr.call_args.kwargs['language'], 'auto')
                if 'bilibili.com/video/' in url:
                    self.assertEqual(data['title'], 'Publisher title')
                    self.assertIn('?p=2', data['reference'])
                    self.assertEqual(asr.call_args.args[3], 3)
        self.assertTrue(all(not stage.exists() for stage in self.stages))

    def test_multiple_videos_have_independent_time_segments_and_ignore_images_and_motion(self):
        self.names = ['001.jpg', '002.mp4', '003.mp4', '004.mp4']
        self.evidence.live_pairs = [('001.jpg', '002.mp4')]
        code, asr = self.run_read('https://x.com/user/status/123', extra=['--language', 'en', '--keep-media'])
        self.assertEqual(code, 0)
        data = json.loads((self.root / 'out/content.json').read_text())
        self.assertEqual(data['video_count'], 2)
        self.assertEqual(data['ignored_motion_count'], 1)
        self.assertEqual([v['source_file'] for v in data['videos']], ['003.mp4', '004.mp4'])
        self.assertEqual(asr.call_count, 2)
        self.assertEqual(asr.call_args.kwargs['language'], 'en')
        self.assertTrue((self.root / 'out/media/video-002/source.mp4').exists())

    def test_failed_second_video_never_commits_partial_content(self):
        self.names = ['001.mp4', '002.mp4']
        code, _ = self.run_read('https://x.com/user/status/123', recognized=[self.recognized, RuntimeError('No speech')])
        self.assertEqual(code, 1)
        self.assertFalse((self.root / 'out/content.json').exists())
        self.assertTrue((self.root / 'out/media').exists())
        self.assertEqual(json.loads((self.root / 'out/.read-job.json').read_text())['status'], 'failed')
        self.assertTrue(all(not stage.exists() for stage in self.stages))

    def test_image_only_and_no_audio_fail_without_recognition(self):
        self.names = ['image.jpg']
        code, asr = self.run_read('https://x.com/user/status/123')
        self.assertEqual(code, 1)
        asr.assert_not_called()
        self.names = ['clip.mp4']
        original = self.inspect
        self.inspect = lambda files, variants=None: [{**r, 'audio': False} for r in original(files, variants)]
        code, asr = self.run_read('https://x.com/user/status/123', 'silent')
        self.assertEqual(code, 1)
        asr.assert_not_called()

    def test_existing_folder_and_missing_model_stop_before_network(self):
        (self.root / 'out').mkdir()
        (self.root / 'out/content.json').write_text('previous evidence')
        code, _ = self.run_read('https://x.com/user/status/123')
        self.assertEqual(code, 1)
        self.assertEqual((self.root / 'out/content.json').read_text(), 'previous evidence')
        self.model.unlink()
        code, _ = self.run_read('https://x.com/user/status/123', 'missing')
        self.assertEqual(code, 1)
        self.assertEqual(self.stages, [])

    def test_pixiv_and_boards_are_rejected(self):
        for i, url in enumerate(['https://www.pixiv.net/artworks/123/', 'https://www.pinterest.com/user/board/',
                                 'https://user.lofter.com/']):
            code, _ = self.run_read(url, f'unsupported-{i}')
            self.assertEqual(code, 1)
        self.assertEqual(self.stages, [])

    def test_transcription_rejects_non_speech_labels_and_invalid_timestamps(self):
        model = self.model
        for text, end in [('(歌詞)', 3000), ('[Music]', 3000), ('Actual words', 8000)]:
            with self.subTest(text=text, end=end):
                working = self.root / f'work-{end}-{len(text)}'
                working.mkdir()
                video = working / 'source.mp4'
                video.touch()
                def process(command, **kwargs):
                    if command[0] == 'ffprobe':
                        return subprocess.CompletedProcess(command, 0, json.dumps({'format': {'duration': '3'}, 'streams': [{'codec_type': 'audio'}]}))
                    if command[0] == 'whisper-cli':
                        (working / 'transcript.json').write_text(json.dumps({'transcription': [{'text': text, 'offsets': {'from': 0, 'to': end}}]}))
                    return subprocess.CompletedProcess(command, 0)
                with patch.object(toutiao.shutil, 'which', side_effect=lambda name: name), \
                     patch.object(toutiao.subprocess, 'run', side_effect=process):
                    with self.assertRaises(RuntimeError):
                        toutiao.transcribe(video, working, model, language='auto')


if __name__ == '__main__':
    unittest.main()
