"""Prefer clean sources while preserving complete media when unavailable."""
import io
import json
from pathlib import Path
import tempfile
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

    def test_clean_rendition_is_used_and_size_tradeoff_reported(self):
        clean = jpeg(690, 920)
        with patch.object(weibo, 'urlopen', return_value=Response(clean)) as network:
            notes = weibo.prefer_clean_images(self.root)
        self.assertEqual(network.call_args.args[0].full_url, 'https://wx1.sinaimg.cn/oslarge/example.jpg')
        self.assertEqual((self.root/'weibo_123_001_preferred.jpg').read_bytes(), clean)
        self.assertFalse(self.source.exists())
        self.assertFalse(self.metadata.exists())
        self.assertIn('lower resolution: 1', notes[0])

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

    def test_xhs_fallback_selects_best_exposed_stream(self):
        note={'type':'video','video':{'media':{'stream':{'h264':[{'height':720,'width':1280,'masterUrl':'https://sns-video-bd.xhscdn.com/low'}],
                                                      'h265':[{'height':1080,'width':1920,'masterUrl':'https://sns-video-bd.xhscdn.com/high'}]}}}}
        self.assertEqual(xiaohongshu.note_media(note, prefer_original=False), [('video','https://sns-video-bd.xhscdn.com/high')])
