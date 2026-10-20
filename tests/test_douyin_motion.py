"""Sanitized Douyin motion fixtures; no live network, browser or Photos access."""
import contextlib
from http.client import IncompleteRead
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from linkget import accounts, cli, douyin
from media_stub import uninspected


class Response(io.BytesIO):
    headers = {}


def image(number, motion=False):
    result = {"url_list": [f"https://p3.douyinpic.com/{number}~tplv-dy-aweme-images:q75.webp"],
              "live_photo_type": int(motion)}
    if motion:
        result["video"] = {"width": 720, "height": 1280, "has_watermark": False,
                           "play_addr": {"url_list": [f"https://cdn.example/{number}.mp4"]}}
    return result


class DouyinMotionTests(unittest.TestCase):
    def test_image_scoped_motion_keeps_pairs_and_excludes_top_level_music(self):
        item = {"aweme_id": "123", "images": [image(1, True), image(2), image(3, True)],
                "video": {"play_addr": {"url_list": ["https://cdn.example/music.mp3"]}}}
        requested = []

        def respond(request, **kwargs):
            requested.append(request.full_url)
            return Response(b"\x00\x00\x00\x18ftypmp42" if request.full_url.endswith(".mp4") else b"\xff\xd8\xffstill")

        with tempfile.TemporaryDirectory() as temporary, patch.object(douyin, "build_opener") as network:
            network.return_value.open.side_effect = respond
            result = douyin.download("https://www.douyin.com/note/123", Path(temporary),
                                     browser_item=item, require_motion=True)
            self.assertEqual(result.expected_count, 5)
            self.assertEqual(result.live_pairs, [("douyin_123_001.jpg", "douyin_123_004.mp4"),
                                                 ("douyin_123_003.jpg", "douyin_123_005.mp4")])
            self.assertEqual(len(list(Path(temporary).iterdir())), 5)
        self.assertEqual(len(requested), 5)
        self.assertNotIn("https://cdn.example/music.mp3", requested)
        self.assertTrue(result.metadata["motion_metadata_complete"])

    def test_motion_fallback_uses_actual_downloaded_filename_in_pair(self):
        still = image(1, True)
        native = "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=motion"
        still["video"]["play_addr"]["url_list"] = [native]
        requested = []

        def respond(request, **kwargs):
            target = request.full_url
            requested.append(target)
            if "/play/?" in target:
                raise HTTPError(target, 404, "synthetic unavailable clean source", {}, None)
            return Response(b"\x00\x00\x00\x18ftypmp42" if target == native else b"\xff\xd8\xffstill")

        with tempfile.TemporaryDirectory() as temporary, patch.object(douyin, "build_opener") as network:
            network.return_value.open.side_effect = respond
            result = douyin.download("https://www.douyin.com/note/123", Path(temporary),
                                     browser_item={"aweme_id": "123", "images": [still]})
            self.assertEqual(result.live_pairs, [("douyin_123_001.jpg", "douyin_123_002.mp4")])
            self.assertEqual(result.variants["douyin_123_002.mp4"], "fallback")
        self.assertIn(native, requested)

    def test_missing_declared_motion_stops_before_download(self):
        for missing in (None, {}, {"width": 720, "height": 1280}):
            still = image(1, True)
            still["video"] = missing
            with self.subTest(video=missing), tempfile.TemporaryDirectory() as temporary, patch.object(douyin, "build_opener") as network:
                with self.assertRaisesRegex(douyin.MediaUnavailable, "image motion component"):
                    douyin.download("https://www.douyin.com/note/123", Path(temporary),
                                    browser_item={"aweme_id": "123", "images": [still]})
                network.return_value.open.assert_not_called()
                self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_interrupted_motion_never_returns_complete_pairs(self):
        def respond(request, **kwargs):
            if request.full_url.endswith(".mp4"):
                raise IncompleteRead(b"partial", 100)
            return Response(b"\xff\xd8\xffstill")
        with tempfile.TemporaryDirectory() as temporary, patch.object(douyin, "build_opener") as network:
            network.return_value.open.side_effect = respond
            with self.assertRaises(douyin.DownloadInterrupted):
                douyin.download("https://www.douyin.com/note/123", Path(temporary),
                                browser_item={"aweme_id": "123", "images": [image(1, True)]})

    def test_public_metadata_without_motion_fields_is_explicitly_unverified(self):
        still = image(1)
        del still["live_photo_type"]
        with tempfile.TemporaryDirectory() as temporary, patch.object(douyin, "build_opener") as network:
            network.return_value.open.return_value = Response(b"\xff\xd8\xffstill")
            result = douyin.download("https://www.douyin.com/note/123", Path(temporary),
                                     browser_item={"aweme_id": "123", "images": [still]})
        self.assertFalse(result.metadata["motion_metadata_complete"])
        self.assertIn("does not verify motion completeness", result.notes[-1])

    def test_live_photos_refuses_stripped_public_metadata_without_browser(self):
        # The observed mobile response has stills plus a zero-dimension music
        # video, and omits the desktop image's live_photo_type/video fields.
        still = image(1)
        del still["live_photo_type"]
        item = {"aweme_id": "123", "images": [still], "video": {
            "width": 0, "height": 0, "duration": 0,
            "play_addr": {"url_list": ["https://cdn.example/music.mp3"]}}}
        content = ("window._ROUTER_DATA = " + json.dumps({"loaderData": {
            "page": {"videoInfoRes": {"item_list": [item]}}}})).encode()
        with tempfile.TemporaryDirectory() as temporary, patch.object(accounts, "directory", return_value=Path(temporary) / "sessions"), patch.object(douyin, "build_opener") as network, patch.object(cli, "read_douyin_browser") as browser, patch.object(cli, "import_photos") as photos, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()) as errors:
            network.return_value.open.side_effect = lambda *args, **kwargs: Response(content)
            code = cli.main(["https://www.douyin.com/note/123", "--photos", "--live-photos", "--browser", "none"])
        self.assertEqual(code, 1)
        self.assertIn("does not establish motion completeness", errors.getvalue())
        self.assertEqual(network.return_value.open.call_count, 1)
        browser.assert_not_called()
        photos.assert_not_called()

    def test_live_photos_uses_existing_browser_fallback_and_packaging(self):
        item = {"aweme_id": "123", "images": [image(1, True)]}
        public_still = {"url_list": image(1)["url_list"]}
        public = ("window._ROUTER_DATA = " + json.dumps({"loaderData": {"page": {
            "videoInfoRes": {"item_list": [{"aweme_id": "123", "images": [public_still]}]}}}})).encode()
        with tempfile.TemporaryDirectory() as temporary, patch.object(accounts, "directory", return_value=Path(temporary) / "sessions"), patch.object(douyin, "build_opener") as network, patch.object(cli, "read_douyin_browser", return_value=item) as browser, patch.object(cli, "prepare_pairs", side_effect=lambda files, pairs, folder: [(files[0], files[1])]) as package, patch.object(cli, "inspect_files", side_effect=uninspected), patch.object(cli, "import_photos") as photos, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            def respond(request, **kwargs):
                if "iesdouyin.com" in request.full_url:
                    return Response(public)
                return Response(b"\x00\x00\x00\x18ftypmp42" if request.full_url.endswith(".mp4") else b"\xff\xd8\xffstill")
            network.return_value.open.side_effect = respond
            report = Path(temporary) / "report.json"
            code = cli.main(["https://www.douyin.com/note/123", "--folder", str(Path(temporary) / "out"),
                             "--live-photos", "--browser", "chrome", "--report", str(report)])
            saved = json.loads(report.read_text())["items"][0]
            self.assertEqual(saved["expected_count"], 2)
            self.assertTrue(saved["motion_metadata_complete"])
            self.assertEqual(saved["paired_exports"], 1)
        self.assertEqual(code, 0)
        browser.assert_called_once()
        self.assertEqual(package.call_args.args[1], [("douyin_123_001.jpg", "douyin_123_002.mp4")])
        photos.assert_not_called()

    def test_static_gallery_with_explicit_motion_flags_needs_no_browser(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(douyin, "build_opener") as network:
            network.return_value.open.side_effect = lambda *args, **kwargs: Response(b"\xff\xd8\xffstill")
            result = douyin.download("https://www.douyin.com/note/123", Path(temporary),
                                     browser_item={"aweme_id": "123", "images": [image(1), image(2)]},
                                     require_motion=True)
        self.assertEqual(result.expected_count, 2)
        self.assertEqual(result.live_pairs, [])
        self.assertTrue(result.metadata["motion_metadata_complete"])


if __name__ == "__main__":
    unittest.main()
