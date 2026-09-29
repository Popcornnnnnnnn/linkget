"""Short-video paths: no browser, Photos, or external network in these tests."""

import contextlib
from http.cookiejar import Cookie
import io
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from linkget import accounts, cli, douyin, links, session


class Response(io.BytesIO):
    def __init__(self, data=b"", code=200, headers=None):
        super().__init__(data)
        self.code = code
        self.headers = headers or {}


def page(item):
    return ("<script>window._ROUTER_DATA = " + json.dumps({"loaderData": {
        "video_layout": None, "video_(id)/page": {"videoInfoRes": {"item_list": [item]}}
    }}) + ";</script>").encode()


class ShortVideoTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "sessions"
        mocked = patch.object(accounts, "directory", return_value=root)
        mocked.start()
        self.addCleanup(mocked.stop)
    def test_full_sharing_text_as_words_quoted_text_or_prompt(self):
        text = "3.53 复制打开抖音，看看【笛笛的坐标系的作品】还有比pro20x更有性价比的消费吗？ # cod... https://v.douyin.com/lnufhXLTfmU/ ban:/ :5pm M@J.Iv 01/01"

        def download(url, directory, cookies):
            self.assertEqual(url, "https://www.douyin.com/video/7640490555692076303")
            (directory / "sample.mp4").write_bytes(b"video")

        for mode in ("words", "quoted", "intermixed", "prompt"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as t, patch.object(links, "build_opener") as build, patch.object(cli, "download_douyin", side_effect=download), patch.object(cli, "import_photos") as photos, patch.object(cli.sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value=text), contextlib.redirect_stdout(io.StringIO()):
                build.return_value.open.return_value = Response(code=302, headers={"Location": "https://www.douyin.com/video/7640490555692076303"})
                options = ["--folder", t, "--browser", "none"]
                words = text.split()
                args = {"words": words + options, "quoted": [text] + options,
                        "intermixed": words[:2] + options + words[2:], "prompt": options}[mode]
                self.assertEqual(cli.main(args), 0)
                self.assertEqual((Path(t) / "sample.mp4").read_bytes(), b"video")
                photos.assert_not_called()

    def test_sharing_text_and_canonical_routes(self):
        cases = [
            ("复制打开抖音 https://www.douyin.com/note/123/ 来看", "https://www.douyin.com/note/123", "douyin"),
            ("https://www.douyin.com/?modal_id=123", "https://www.douyin.com/video/123", "douyin"),
            ("https://www.iesdouyin.com/share/slides/123/", "https://www.douyin.com/note/123", "douyin"),
            ("https://www.tiktok.com/@sample/photo/123?_r=1", "https://www.tiktok.com/@sample/photo/123", "gallery-dl"),
        ]
        for source, expected, engine in cases:
            normalized = links.normalize_link(source)
            self.assertEqual(normalized, expected)
            self.assertEqual(cli.route(normalized)[1], engine)
        for source in ("https://v.douyin.com:999/a", "https://user:pass@v.douyin.com/a", "https://x.com/a https://x.com/b"):
            with self.assertRaises(ValueError):
                links.normalize_link(source)
        with self.assertRaises(ValueError):
            cli.route(links.normalize_link("https://www.tiktok.com/@sample"))

    def test_short_redirect_resolves_before_fetching_post_and_rejects_other_hosts(self):
        with patch.object(links, "build_opener") as build:
            build.return_value.open.return_value = Response(code=302, headers={"Location": "https://www.douyin.com/note/123?x=1"})
            self.assertEqual(links.normalize_link("看看 https://v.douyin.com/abc/"), "https://www.douyin.com/note/123")
            self.assertEqual(build.return_value.open.call_count, 1)
            build.return_value.open.return_value = Response(code=302, headers={"Location": "https://evil.test/video/123"})
            with self.assertRaises(ValueError):
                links.normalize_link("https://v.douyin.com/abc/")

    def test_new_platform_sessions_are_never_falsely_valid(self):
        for domain in ("douyin.com", "tiktok.com"):
            jar = session.MozillaCookieJar()
            self.assertEqual(session.check_session(jar, domain).state, "missing")
            for name in ("ttwid", "sessionid"):
                jar.set_cookie(Cookie(0, name, "synthetic", None, False, "." + domain, True, True,
                                     "/", True, True, None, True, None, None, {}))
                with patch.object(session, "build_opener") as network:
                    network.return_value.open.return_value = Response(b"{}")
                    expected = "missing" if name == "ttwid" else "unverified"
                    self.assertEqual(session.check_session(jar, domain).state, expected)
                    if name == "sessionid":
                        network.assert_called_once()
                    else:
                        network.assert_not_called()

    def test_successful_download_hides_authentication_details(self):
        item = {"aweme_id": "123", "images": [{"url_list": ["https://p3.douyinpic.com/image~tplv-dy-lqen-new:4510:1626:q80.webp"]}]}
        jpg = b"\xff\xd8\xff" + b"image" * 30

        def prepare(tool, spec, path, domain):
            jar = session.MozillaCookieJar(str(path))
            jar.set_cookie(Cookie(0, "sessionid", "synthetic", None, False, ".douyin.com", True, True,
                                 "/", True, True, None, True, None, None, {}))
            jar.save(ignore_discard=True)
            return session.Session("valid", "Valid")

        for mode in ("api", "fallback", "unsafe_api", "anonymous"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as t, patch.object(cli, "find_tool", return_value="gallery-dl"), patch.object(cli, "prepare_session", side_effect=prepare), patch.object(douyin, "build_opener") as build, patch.object(cli, "import_photos") as photos, contextlib.redirect_stdout(io.StringIO()) as out:
                responses = [Response(jpg)]
                if mode == "api":
                    responses.insert(0, Response(json.dumps({"aweme_detail": item}).encode()))
                else:
                    responses.insert(0, Response(page(item)))
                    if mode == "fallback":
                        responses.insert(0, Response(b"{}"))
                    elif mode == "unsafe_api":
                        unsafe = {"aweme_id": "123", "images": [{"url_list": ["https://p3.douyinpic.com/image~tplv-dy-lqen-new-water:q80.webp"]}]}
                        responses.insert(0, Response(json.dumps({"aweme_detail": unsafe}).encode()))
                build.return_value.open.side_effect = responses
                code = cli.main(["https://www.douyin.com/note/123", "--browser", "none" if mode == "anonymous" else "chrome", "--folder", t])
                self.assertEqual(code, 0)
                text = out.getvalue()
                self.assertNotIn("login validity", text)
                self.assertNotIn("Session", text)
                self.assertNotIn("Browser", text)
                self.assertNotIn("Continuing", text)
                self.assertNotIn("Public share page", text)
                self.assertNotIn("browser cookies", text)
                photos.assert_not_called()

    def test_douyin_ignores_other_posts_and_uses_all_images_in_order(self):
        item = {"aweme_id": "123", "images": [{"url_list": ["https://p3.douyinpic.com/1~tplv-dy-aweme-images:q75.webp"]}, {"url_list": ["https://p3.douyinpic.com/2~tplv-dy-aweme-images:q75.webp"]}],
                "video": {"play_addr": {"url_list": ["https://cdn.test/slideshow"]}}}
        self.assertIsNone(douyin.share_item(page(item).decode(), "456"))
        self.assertEqual(douyin.media_urls(douyin.share_item(page(item).decode(), "123")),
                         [("image", "https://p3.douyinpic.com/1~tplv-dy-aweme-images:q75.webp"), ("image", "https://p3.douyinpic.com/2~tplv-dy-aweme-images:q75.webp")])
        item["images"].append({})
        with self.assertRaises(RuntimeError):
            douyin.media_urls(item)

    def test_douyin_prefers_playback_variant_and_rejects_unknown_sources(self):
        original = "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=example&ratio=720p&line=0"
        item = {"aweme_id": "123", "video": {"play_addr": {"url_list": [original]}}}
        expected = original.replace("/playwm/", "/play/")
        self.assertEqual(douyin.media_urls(item), [("video", expected)])
        mp4 = b"\x00\x00\x00\x18ftypmp42" + b"x" * 100
        with tempfile.TemporaryDirectory() as t, patch.object(douyin, "build_opener") as build:
            build.return_value.open.side_effect = [Response(page(item)), Response(mp4)]
            # An existing download must not make the upgraded variant look duplicated.
            old = Path(t) / "douyin_123_001.mp4"
            old.write_bytes(b"old-watermarked-version")
            douyin.download("https://www.douyin.com/video/123", Path(t))
            self.assertEqual(build.return_value.open.call_args.args[0].full_url, expected)
            self.assertEqual(old.read_bytes(), b"old-watermarked-version")
            self.assertEqual((Path(t) / "douyin_123_001_clean.mp4").read_bytes(), mp4)
        for url in ("https://cdn.test/video.mp4?note=playwm", "https://other.test/aweme/v1/playwm/"):
            item["video"]["play_addr"]["url_list"] = [url]
            with self.assertRaises(RuntimeError):
                douyin.media_urls(item)
        item["video"]["has_watermark"] = False
        self.assertEqual(douyin.media_urls(item), [("video", url)])

    def test_rejected_douyin_clean_source_keeps_available_native_media(self):
        native_video = "https://aweme.snssdk.com/aweme/v1/playwm/?video_id=example"
        clean_image = "https://p3.douyinpic.com/1~tplv-dy-aweme-images:q75.webp"
        native_image = "https://p3.douyinpic.com/1~tplv-dy-water:q75.webp"
        for item, native, content in [
            ({"aweme_id": "123", "video": {"play_addr": {"url_list": [native_video]}}}, native_video, b"\x00\x00\x00\x18ftypmp42" + b"x" * 100),
            ({"aweme_id": "123", "images": [{"url_list": [clean_image], "download_url_list": [native_image]}]}, native_image, b"\xff\xd8\xff" + b"x" * 100)]:
            with tempfile.TemporaryDirectory() as temp, patch.object(douyin, "build_opener") as network:
                network.return_value.open.side_effect = [Response(page(item)),
                    HTTPError("https://cdn.test/missing", 404, "missing", {}, None), Response(content)]
                notes = douyin.download("https://www.douyin.com/video/123", Path(temp))
                self.assertIn("may contain watermarks", notes[0])
                self.assertEqual(network.return_value.open.call_args.args[0].full_url, native)
                self.assertEqual(next(Path(temp).iterdir()).read_bytes(), content)

    def test_douyin_bootstraps_visitor_cookie_once_and_downloads_all_photos(self):
        item = {"aweme_id": "123", "images": [{"url_list": ["https://p3.douyinpic.com/1~tplv-dy-aweme-images:q75.webp"]}, {"url_list": ["https://p3.douyinpic.com/2~tplv-dy-aweme-images:q75.webp"]}]}
        jpg = b"\xff\xd8\xff" + b"image" * 30
        calls = []

        def build(processor):
            class Opener:
                def open(self, req, **kwargs):
                    calls.append(req.full_url)
                    if len(calls) == 1:
                        processor.cookiejar.set_cookie(Cookie(0, "ttwid", "visitor", None, False, ".iesdouyin.com", True, True,
                                                             "/", True, True, None, True, None, None, {}))
                        return Response(b"<html>Open App</html>")
                    if len(calls) == 2:
                        return Response(page(item))
                    return Response(jpg, headers={"Content-Length": str(len(jpg))})
            return Opener()

        with tempfile.TemporaryDirectory() as t, patch.object(douyin, "build_opener", side_effect=build):
            douyin.download("https://www.douyin.com/note/123", Path(t))
            files = sorted(Path(t).iterdir())
            self.assertEqual([p.name for p in files], ["douyin_123_001.jpg", "douyin_123_002.jpg"])
            self.assertEqual([p.read_bytes() for p in files], [jpg, jpg])
        self.assertEqual(len(calls), 4)

    def test_douyin_requires_clean_source_for_every_image_before_downloading(self):
        clean = "https://p3.douyinpic.com/image~tplv-dy-lqen-new:4510:1626:q80.webp"
        marked = clean.replace("lqen-new:", "lqen-new-water:")
        item = {"aweme_id": "123", "images": [{"url_list": [marked, clean], "download_url_list": [marked]}]}
        self.assertEqual(douyin.media_urls(item), [("image", clean)])
        # Prefer the clean first image, retain the marked second only when needed.
        item["images"].append({"url_list": [marked], "download_url_list": [marked]})
        with tempfile.TemporaryDirectory() as t, patch.object(douyin, "build_opener") as build:
            jpg = b"\xff\xd8\xff" + b"image" * 20
            build.return_value.open.side_effect = [Response(page(item)), Response(jpg), Response(jpg)]
            notes = douyin.download("https://www.douyin.com/note/123", Path(t))
            self.assertIn("may contain watermarks", notes[0])
            self.assertEqual(build.return_value.open.call_count, 3)
            self.assertEqual(len(list(Path(t).glob('*.jpg'))), 2)
        for url in ("https://p3.douyinpic.com/image~unknown:q80.webp", "https://other.test/image~tplv-dy-lqen-new:q80.webp"):
            item["images"] = [{"url_list": [url]}]
            with self.assertRaises(RuntimeError):
                douyin.media_urls(item)

    def test_douyin_challenge_or_incomplete_media_never_imports(self):
        item = {"aweme_id": "123", "video": {"play_addr": {"url_list": ["https://aweme.snssdk.com/aweme/v1/play/?video_id=test"]}}}
        for media in (Response(b"<html>Verification</html>"), Response(b"\x00\x00\x00\x18ftypmp42" + b"x" * 100, headers={"Content-Length": "1000"})):
            with tempfile.TemporaryDirectory() as t, patch.object(douyin, "build_opener") as build, patch.object(cli, "import_photos") as photos, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                build.return_value.open.side_effect = [Response(page(item)), media]
                with patch.object(cli.tempfile, "tempdir", t):
                    self.assertEqual(cli.main(["https://www.douyin.com/video/123", "--browser", "none"]), 1)
                photos.assert_not_called()
                self.assertFalse(list(Path(t).rglob("*.mp4")))

    def test_tiktok_excludes_audio_sidecars_and_douyin_uses_existing_save_flow(self):
        def download(command, **kwargs):
            self.assertIn("extractor.tiktok.audio=false", command)
            directory = Path(command[command.index("--directory") + 1])
            (directory / "tiktok_123_001.jpg").write_bytes(b"image")
            return CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as t, patch.object(cli, "find_tool", return_value="gallery-dl"), patch.object(cli.subprocess, "run", side_effect=download), patch.object(cli, "import_photos") as photos, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(["https://www.tiktok.com/@sample/photo/123", "--browser", "none", "--folder", t]), 0)
            photos.assert_not_called()
        def native(url, directory, cookies):
            (directory / "douyin_123_001.jpg").write_bytes(b"image")
        with tempfile.TemporaryDirectory() as t, patch.object(cli, "download_douyin", side_effect=native), patch.object(cli, "import_photos") as photos, contextlib.redirect_stdout(io.StringIO()) as out:
            args = ["https://www.douyin.com/note/123", "--browser", "none", "--folder", t]
            self.assertEqual(cli.main(args), 0)
            self.assertEqual(cli.main(args), 0)
            self.assertEqual(len(list(Path(t).iterdir())), 1)
            self.assertIn("1 duplicate", out.getvalue())
            photos.assert_not_called()


if __name__ == "__main__":
    unittest.main()
