import copy
import json
from pathlib import Path
import re
import tempfile
import unittest
from types import SimpleNamespace

from linkget import engines, media

try:
    from gallery_dl import config, exception
    from gallery_dl.extractor.message import Message
    from linkget.instagram_visual import LinkgetInstagramPostExtractor
except ImportError:
    LinkgetInstagramPostExtractor = None


class InstagramCommandTests(unittest.TestCase):
    def test_external_extractor_is_scoped_to_instagram(self):
        args = SimpleNamespace(browser="none", profile=None)
        for domain in ("instagram.com", "x.com"):
            command = engines.command_for(args, "https://example.test/post", "gallery-dl", domain,
                                          Path("media"), {"gallery-dl": "gallery-dl"})
            sources = [arg.split("=", 1)[1] for arg in command if arg.startswith("extractor.module-sources=")]
            if domain == "instagram.com":
                source, fallback = json.loads(sources[0])
                self.assertTrue((Path(source["from"]) / "instagram_visual.py").is_file())
                self.assertEqual(source["import"], ["instagram_visual"])
                self.assertIsNone(fallback)
                self.assertIn("extractor.instagram.audio=false", command)
            else:
                self.assertEqual(sources, [])


@unittest.skipIf(LinkgetInstagramPostExtractor is None, "Run with gallery-dl's Python for extractor integration")
class InstagramExtractorTests(unittest.TestCase):
    def setUp(self):
        config.clear()
        config.set(("extractor", "instagram"), "videos", "merged")

    def post(self, *, music=True, carousel=False):
        photo = {"pk": "1", "code": "abc", "caption": None,
                 "user": {"pk": "2", "username": "fixture", "full_name": "Fixture"},
                 "taken_at": 1700000000, "original_width": 720, "original_height": 1280,
                 "image_versions2": {"candidates": [{"url": "https://example.test/image.jpg", "width": 720, "height": 1280}]}}
        video = copy.deepcopy(photo)
        video["video_versions"] = [{"url": "https://example.test/video.mp4", "width": 720, "height": 1280}]
        if music:
            video["clips_metadata"] = {"original_sound_info": {"audio_asset_id": "3", "original_audio_title": "Original audio",
                                      "duration_in_ms": 13440, "progressive_download_url": "https://example.test/audio.m4a"}}
        if carousel:
            video["carousel_media"] = [copy.deepcopy(photo), copy.deepcopy(video)]
        return video

    def messages(self, post):
        url = "https://www.instagram.com/reel/abc/"
        extractor = LinkgetInstagramPostExtractor(re.match(LinkgetInstagramPostExtractor.pattern, url))
        extractor.initialize()
        extractor.login = lambda: None
        extractor.posts = lambda: iter([post])
        return list(extractor.items())

    def save(self, messages, root, omit=None):
        for index, (kind, url, data) in enumerate(row for row in messages if row[0] == Message.Url):
            if index == omit:
                continue
            name = f"{index}.{data['extension']}"
            (root / name).write_bytes(b"media")
            (root / (name + ".json")).write_text(json.dumps(data, default=str))

    def test_single_reel_music_does_not_require_an_extra_file(self):
        for music in (True, False):
            with self.subTest(music=music), tempfile.TemporaryDirectory() as directory:
                messages = self.messages(self.post(music=music))
                urls = [row for row in messages if row[0] == Message.Url]
                self.assertEqual(len(urls), 1)
                self.assertEqual((urls[0][2]["num"], urls[0][2]["count"]), (1, 1))
                self.save(messages, Path(directory))
                self.assertEqual(media.gallery_evidence(Path(directory), "instagram.com").expected_count, 1)

    def test_mixed_gallery_music_keeps_visual_order_and_count(self):
        with tempfile.TemporaryDirectory() as directory:
            messages = self.messages(self.post(carousel=True))
            urls = [row for row in messages if row[0] == Message.Url]
            self.assertEqual([(row[2]["num"], row[2]["count"], row[2]["extension"]) for row in urls],
                             [(1, 2, "jpg"), (2, 2, "mp4")])
            self.save(messages, Path(directory))
            self.assertEqual(media.gallery_evidence(Path(directory), "instagram.com").expected_count, 2)

    def test_missing_downloaded_photo_or_video_is_still_rejected(self):
        for omit in (0, 1):
            with self.subTest(omit=omit), tempfile.TemporaryDirectory() as directory:
                self.save(self.messages(self.post(carousel=True)), Path(directory), omit=omit)
                with self.assertRaisesRegex(RuntimeError, "incomplete"):
                    media.gallery_evidence(Path(directory), "instagram.com")
                self.assertTrue(list(Path(directory).glob("*.json")))

    def test_source_media_silently_skipped_by_upstream_is_rejected(self):
        post = self.post(carousel=True)
        del post["carousel_media"][0]["image_versions2"]
        with self.assertRaisesRegex(exception.AbortExtraction, "every photo/video"):
            self.messages(post)

    def test_story_music_with_a_shared_number_is_excluded(self):
        post = self.post(music=False, carousel=True)
        post["carousel_media"][0]["story_music_stickers"] = [{"music_asset_info": {
            "id": "3", "title": "Music", "progressive_download_url": "https://example.test/music.m4a"}}]
        urls = [row for row in self.messages(post) if row[0] == Message.Url]
        self.assertEqual([(row[2]["num"], row[2]["count"]) for row in urls], [(1, 2), (2, 2)])


if __name__ == "__main__":
    unittest.main()
