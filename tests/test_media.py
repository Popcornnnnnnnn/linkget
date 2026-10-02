import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from linkget import cli, media, accounts


class MediaEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def metadata(self, name, item):
        (self.root / name).write_bytes(b"media")
        (self.root / (name + '.json')).write_text(json.dumps(item))

    def test_incomplete_gallery_count_prevents_a_successful_partial_save(self):
        self.metadata('photo.jpg', {'tweet_id': '123', 'num': 1, 'count': 2})
        with self.assertRaisesRegex(RuntimeError, 'incomplete'):
            media.gallery_evidence(self.root, 'x.com')
        self.assertTrue((self.root / 'photo.jpg.json').exists())

    def test_bilibili_pairs_use_item_identity_and_reject_missing_motion(self):
        self.metadata('one.jpg', {'num': 1, 'count': 2, 'suffix': '', 'live_url': 'private'})
        self.metadata('one.mov', {'num': 1, 'count': 2, 'suffix': 'l', 'live_url': 'private'})
        self.metadata('two.jpg', {'num': 2, 'count': 2, 'suffix': '', 'live_url': 'private'})
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            media.gallery_evidence(self.root, 'bilibili.com')
        self.metadata('two.mov', {'num': 2, 'count': 2, 'suffix': 'l', 'live_url': 'private'})
        evidence = media.gallery_evidence(self.root, 'bilibili.com')
        self.assertEqual(evidence.expected_count, 4)
        self.assertEqual(evidence.live_pairs, [('one.jpg', 'one.mov'), ('two.jpg', 'two.mov')])
        self.assertFalse(list(self.root.glob('*.json')))

    def test_weibo_pair_does_not_take_an_unrelated_video(self):
        self.metadata('one.jpg', {'num': 1, 'type': 'livephoto', 'status': {'id': 1, 'count': 2}})
        self.metadata('other.mp4', {'num': 2, 'type': 'video', 'status': {'id': 1, 'count': 2}})
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            media.gallery_evidence(self.root, 'weibo.com')

    def test_reports_keep_tokens_out_and_identical_bytes_are_not_a_quality_guarantee(self):
        item = {'reference': media.reference('https://www.xiaohongshu.com/explore/abc?xsec_token=private'),
                'status': 'completed', 'files': [{'sha256': 'a', 'width': 100, 'height': 200}]}
        report = self.root / 'report.json'
        media.write_report(report, 'dev', [item], 0)
        saved = json.loads(report.read_text())
        self.assertNotIn('private', report.read_text())
        self.assertEqual(saved['highest_quality'], 'not_verified')
        self.assertEqual(saved['watermark'], 'not_verified')
        self.assertTrue(media.compare_reports(saved, saved)[0]['same_bytes'])
        with self.assertRaises(FileExistsError):
            media.write_report(report, 'dev', [], 1)

    def test_different_codec_at_equal_dimensions_remains_a_visual_comparison(self):
        a = {'schema': 1, 'items': [{'reference': 'same', 'status': 'completed',
                                   'files': [{'sha256': 'a', 'width': 100, 'height': 200, 'codec': 'hevc'}]}]}
        b = copy.deepcopy(a)
        b['items'][0]['files'][0].update(sha256='b', codec='h264')
        result = media.compare_reports(a, b)[0]
        self.assertFalse(result['same_bytes'])
        self.assertIn('visual comparison', result['result'])
        b['items'][0]['reference'] = 'different post'
        with self.assertRaisesRegex(ValueError, 'same links'):
            media.compare_reports(a, b)

    def test_non_report_json_produces_guidance_instead_of_a_traceback(self):
        path = self.root / 'other.json'
        path.write_text('[]')
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as output:
            self.assertEqual(cli.main(['compare', str(path), str(path)]), 1)
        self.assertIn('Use two linkget --report JSON files', output.getvalue())

    def test_invalid_media_is_not_saved_or_imported_as_a_completed_download(self):
        def download(url, folder, cookies):
            (folder / 'bad.jpg').write_bytes(b'<html>verification</html>')
            return media.DownloadResult(expected_count=1)

        with patch.object(accounts, 'directory', return_value=self.root / 'settings/sessions'), patch.object(cli.tempfile, 'tempdir', str(self.root)), patch.object(cli, 'download_pixiv', side_effect=download), patch.object(cli, 'inspect_files', return_value=[{'file': 'bad.jpg', 'source': 'web_original', 'probe_ok': False}]), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['https://www.pixiv.net/artworks/123', '--folder', str(self.root / 'output'), '--browser', 'none']), 1)
        self.assertFalse((self.root / 'output/bad.jpg').exists())
        self.assertTrue(list(self.root.glob('linkget-*/media/bad.jpg')))

    def test_baseline_gate_flags_new_regressions_without_requiring_old_failures_to_pass(self):
        path = Path(__file__).resolve().parents[1] / 'scripts/validate_media.py'
        spec = importlib.util.spec_from_file_location('corpus_gate', path)
        gate = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gate)
        result = {'mode': 'public', 'status': 'downloaded', 'files': [{'kind': 'image', 'probe_ok': True, 'width': 100, 'height': 200}]}
        baseline = [{'id': 'ok', 'results': [result]}, {'id': 'old-failure', 'results': [{'mode': 'public', 'status': 'failed'}]}]
        current = copy.deepcopy(baseline)
        self.assertEqual(gate.regressions(current, baseline), [])
        current[0]['results'][0]['files'][0]['height'] = 100
        self.assertEqual(len(gate.regressions(current, baseline)), 1)
        current[0]['results'] = []
        self.assertEqual(len(gate.regressions(current, baseline)), 1)
        self.assertEqual(gate.regressions([current[1]], baseline), [])


if __name__ == '__main__':
    unittest.main()
