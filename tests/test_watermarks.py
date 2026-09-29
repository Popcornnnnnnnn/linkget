"""Prefer clean sources while preserving complete media when unavailable."""
import io
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from linkget import weibo, xiaohongshu


def jpeg(width, height):
    return b'\xff\xd8\xff\xc0\x00\x11\x08' + height.to_bytes(2, 'big') + width.to_bytes(2, 'big') + b'\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00\xff\xd9'


class Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}


class WatermarkTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source = self.root / 'weibo_123_001.jpg'
        self.original = jpeg(1200, 1600)
        self.source.write_bytes(self.original)
        self.metadata = self.root / 'weibo_123_001.jpg.json'
        self.metadata.write_text(json.dumps({'url':'https://wx1.sinaimg.cn/large/example.jpg'}))
        decoder = patch.object(weibo.shutil, 'which', return_value='/test/ffmpeg')
        decoder.start()
        self.addCleanup(decoder.stop)

    def test_same_size_clean_rendition_is_used_without_lowering_resolution(self):
        clean = jpeg(1200, 1600) + b'\xff\xd9'
        with patch.object(weibo, 'urlopen', side_effect=[Response(clean), Response(clean)]) as network, patch.object(weibo, 'compare_sources', return_value=True):
            notes = weibo.prefer_clean_images(self.root)
        self.assertEqual(network.call_args_list[0].args[0].full_url, 'https://wx1.sinaimg.cn/oslarge/example.jpg')
        self.assertEqual(network.call_args_list[1].args[0].full_url, 'https://wx1.sinaimg.cn/mw690/example.jpg')
        self.assertEqual((self.root/'weibo_123_001_preferred.jpg').read_bytes(), clean)
        self.assertFalse(self.source.exists())
        self.assertFalse(self.metadata.exists())
        self.assertIn('original resolution preserved', notes[0])

    def test_watermark_improvement_never_allows_either_dimension_to_shrink(self):
        for width, height in ((690, 920), (1600, 1200), (2400, 800), (800, 2400)):
            self.metadata.write_text(json.dumps({'url':'https://wx1.sinaimg.cn/large/example.jpg'}))
            candidate = jpeg(width, height)
            with patch.object(weibo, 'urlopen', side_effect=[Response(candidate), Response(candidate)]), patch.object(weibo, 'compare_sources', return_value=True):
                notes = weibo.prefer_clean_images(self.root)
            self.assertEqual(self.source.read_bytes(), self.original)
            self.assertFalse((self.root / 'weibo_123_001_preferred.jpg').exists())
            self.assertFalse(self.metadata.exists())
            self.assertIn('watermark removal unavailable at this resolution', ' '.join(notes))
            self.assertNotIn('selected alternate', ' '.join(notes))

    def test_failed_or_invalid_alternative_keeps_original_and_cleans_sidecars(self):
        for response in [HTTPError('https://wx1.sinaimg.cn/oslarge/example.jpg', 404, 'missing', {}, None),
                         Response(b'<html>error</html>'), Response(jpeg(1,1)),
                         Response(jpeg(690,920), {'Content-Length':'9999'}), Response(jpeg(690,920)[:-2])]:
            self.metadata.write_text(json.dumps({'url':'https://wx1.sinaimg.cn/large/example.jpg'}))
            with patch.object(weibo, 'urlopen', side_effect=response if isinstance(response,Exception) else None,
                              return_value=response):
                notes = weibo.prefer_clean_images(self.root)
            self.assertEqual(self.source.read_bytes(), self.original)
            self.assertFalse(self.metadata.exists())
            self.assertFalse(list(self.root.glob('*.part')))
            self.assertIn('may contain watermarks', notes[0])

    def test_interrupt_preserves_media_and_removes_active_metadata(self):
        with patch.object(weibo, 'urlopen', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                weibo.prefer_clean_images(self.root)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(self.metadata.exists())

    def test_other_hosts_and_animated_images_are_not_rewritten(self):
        for url in ['https://other.test/large/a.jpg', 'https://sinaimg.cn.other.test/large/a.jpg',
                    'https://wx1.sinaimg.cn/large/a.gif', 'https://user@wx1.sinaimg.cn/large/a.jpg']:
            self.assertIsNone(weibo.clean_url(url))

    def test_clean_or_uncertain_comparison_preserves_high_resolution_bytes(self):
        for decision in (False, None):
            self.metadata.write_text(json.dumps({'url': 'https://wx1.sinaimg.cn/large/example.jpg'}))
            with patch.object(weibo, 'urlopen', side_effect=[Response(jpeg(690, 920)), Response(jpeg(690, 920))]), patch.object(weibo, 'compare_sources', return_value=decision):
                notes = weibo.prefer_clean_images(self.root)
            self.assertEqual(self.source.read_bytes(), self.original)
            self.assertFalse((self.root / 'weibo_123_001_preferred.jpg').exists())
            self.assertFalse(self.metadata.exists())
            self.assertIn('original quality', ' '.join(notes))
            self.assertNotIn('lower resolution', ' '.join(notes))

    def test_missing_decoder_and_comparison_timeout_preserve_original(self):
        with patch.object(weibo.shutil, 'which', return_value=None), patch.object(weibo, 'urlopen') as network:
            notes = weibo.prefer_clean_images(self.root)
        network.assert_not_called()
        self.assertIn('Install ffmpeg', ' '.join(notes))
        self.metadata.write_text(json.dumps({'url': 'https://wx1.sinaimg.cn/large/example.jpg'}))
        with patch.object(weibo, 'urlopen', side_effect=[Response(jpeg(690, 920)), Response(jpeg(690, 920))]), patch.object(weibo, 'compare_sources', side_effect=subprocess.TimeoutExpired('ffmpeg', 12)):
            weibo.prefer_clean_images(self.root)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(self.metadata.exists())
        self.assertFalse(list(self.root.glob('*.part')))

    def test_comparison_requires_matching_reference_dimensions(self):
        with patch.object(weibo.subprocess, 'run') as decoder:
            self.assertIsNone(weibo.compare_sources(jpeg(1200, 1600), jpeg(690, 919), jpeg(690, 920), '/test/ffmpeg'))
        decoder.assert_not_called()

    def test_visible_overlay_changes_select_alternate_but_encoding_noise_does_not(self):
        width, height = 640, 320
        alternate = bytes(80 + index % 100 for index in range(width * height))
        noise = bytes(value + (index % 5 - 2) for index, value in enumerate(alternate))
        self.assertFalse(weibo.watermark_difference(alternate, noise, alternate, width, height))
        for top in (150, 280):  # Both centered and bottom watermarks.
            marked = bytearray(alternate)
            for y in range(top, top + 20):
                for x in range(400, 520):
                    marked[y * width + x] = 255
            self.assertTrue(weibo.watermark_difference(marked, marked, alternate, width, height))
            # A marked thumbnail alone must never downgrade a clean large image.
            self.assertIsNone(weibo.watermark_difference(alternate, marked, alternate, width, height))

    def test_distributed_changes_and_undecodable_pixels_never_trigger_downgrade(self):
        width, height = 640, 320
        alternate = bytes([80]) * (width * height)
        marked = bytes(140 if index % 40 == 0 else 80 for index in range(width * height))
        self.assertIsNone(weibo.watermark_difference(marked, marked, alternate, width, height))
        self.assertIsNone(weibo.watermark_difference(b'', marked, alternate, width, height))

    def test_xhs_fallback_selects_best_exposed_stream(self):
        note={'type':'video','video':{'media':{'stream':{'h264':[{'height':720,'width':1280,'masterUrl':'https://sns-video-bd.xhscdn.com/low'}],
                                                      'h265':[{'height':1080,'width':1920,'masterUrl':'https://sns-video-bd.xhscdn.com/high'}]}}}}
        self.assertEqual(xiaohongshu.note_media(note, prefer_original=False), [('video','https://sns-video-bd.xhscdn.com/high')])
