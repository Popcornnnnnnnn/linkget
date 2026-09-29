"""New sites: complete posts, scoped sessions, and no personal apps or browsers."""
import contextlib
from http.cookiejar import Cookie, MozillaCookieJar
import io
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, cli, links, session, xiaohongshu as xhs

NOTE_ID = "674051740000000007027a15"
NOTE_URL = "https://www.xiaohongshu.com/explore/" + NOTE_ID
YT_URL = "https://www.youtube.com/watch?v=BaW_jenozKc"


class Response(io.BytesIO):
    def __init__(self, body=b"", code=200, headers=None):
        super().__init__(body)
        self.code = code
        self.headers = headers or {}


def page(note):
    return ('<script>window.__INITIAL_STATE__=' + json.dumps({
        "note": {"noteDetailMap": {NOTE_ID: {"note": note}}}}) + ';</script>').encode()


class NewSiteTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for obj, name, value in [(accounts, "directory", self.root / "sessions"),
                                 (cli.sys.stdin, "isatty", False)]:
            mocked = patch.object(obj, name, return_value=value)
            mocked.start()
            self.addCleanup(mocked.stop)

    def test_share_links_keep_xhs_access_token_and_route_single_posts(self):
        query = "?xsec_token=synthetic%2Btoken%3D&xsec_source=pc_share"
        cases = [
            ("分享 " + NOTE_URL + query, NOTE_URL + query, "xiaohongshu"),
            ("https://m.weibo.cn/status/AbCd123?from=share", "https://weibo.com/detail/AbCd123", "gallery-dl"),
            ("https://weibo.com/1234567890/AbCd123", "https://weibo.com/detail/AbCd123", "gallery-dl"),
            ("https://video.weibo.com/show?fid=1034:123", "https://weibo.com/tv/show/1034:123", "yt-dlp"),
            ("https://youtu.be/BaW_jenozKc?si=share", YT_URL, "yt-dlp"),
            ("https://m.youtube.com/shorts/BaW_jenozKc", YT_URL, "yt-dlp"),
            (YT_URL + "&list=playlist&index=2", YT_URL, "yt-dlp"),
        ]
        for source, expected, engine in cases:
            self.assertEqual(links.normalize_link(source), expected)
            self.assertEqual(cli.route(expected)[1], engine)
        for source in ("https://www.youtube.com/playlist?list=test", "https://www.youtube.com/@someone",
                       "https://weibo.com/u/1234567890", "https://www.xiaohongshu.com/user/profile/" + NOTE_ID):
            with self.assertRaises(ValueError):
                cli.route(links.normalize_link(source))
        with patch.object(links, "build_opener") as network:
            network.return_value.open.return_value = Response(code=302, headers={"Location": NOTE_URL + query})
            self.assertEqual(links.normalize_link("分享 http://xhslink.com/a/test"), NOTE_URL + query)
            network.return_value.open.return_value = Response(code=302, headers={"Location": "https://other.test/"})
            with self.assertRaises(ValueError):
                links.normalize_link("https://t.cn/test")

    def test_xhs_js_state_parser_never_evaluates_code_or_rewrites_strings(self):
        self.assertEqual(xhs.initial_state('window.__INITIAL_STATE__={"missing":undefined,"text":"undefined"};evil()'),
                         {"missing": None, "text": "undefined"})
        self.assertEqual(xhs.initial_state('window.__INITIAL_STATE__=evil()'), {})

    def test_xhs_saves_every_image_in_order_and_skips_preview_urls(self):
        note = {"type": "normal", "noteId": NOTE_ID, "imageList": [
            {"urlPre": "https://sns-webpic-qc.xhscdn.com/thumb", "urlDefault": "https://sns-webpic-qc.xhscdn.com/one"},
            {"infoList": [{"imageScene": "WB_DFT", "url": "https://sns-webpic-qc.xhscdn.com/two"}]}]}
        image = b"\xff\xd8\xff" + b"image" * 20
        with patch.object(xhs, "build_opener") as network, contextlib.redirect_stdout(io.StringIO()):
            network.return_value.open.side_effect = [Response(page(note)), Response(image), Response(image)]
            self.assertEqual(cli.main([NOTE_URL, "--browser", "none", "--folder", str(self.root / "out")]), 0)
            names = sorted(path.name for path in (self.root / "out").iterdir())
            self.assertEqual(names, [f"xiaohongshu_{NOTE_ID}_001.jpg", f"xiaohongshu_{NOTE_ID}_002.jpg"])
            self.assertNotIn("thumb", str(network.return_value.open.call_args_list))

    def test_xhs_video_uses_highest_resolution_and_not_its_cover(self):
        note = {"type": "video", "imageList": [{"urlDefault": "https://sns-webpic-qc.xhscdn.com/cover"}],
                "video": {"media": {"stream": {"h264": [
                    {"height": 720, "width": 1280, "masterUrl": "https://sns-video-bd.xhscdn.com/low"}],
                    "h265": [{"height": 1080, "width": 1920, "masterUrl": "https://sns-video-bd.xhscdn.com/high"}]}}}}
        self.assertEqual(xhs.note_media(note), [("video", "https://sns-video-bd.xhscdn.com/high")])
        with patch.object(xhs, "build_opener") as network:
            network.return_value.open.side_effect = [Response(page(note)), Response(b"\x00\x00\x00\x18ftypmp42" + b"x" * 100)]
            xhs.download(NOTE_URL, self.root)
            self.assertTrue((self.root / f"xiaohongshu_{NOTE_ID}_001.mp4").is_file())

    def test_xhs_incomplete_gallery_challenge_and_truncation_do_not_import(self):
        note = {"type": "normal", "imageList": [{"urlDefault": "https://sns-webpic-qc.xhscdn.com/one"}, {}]}
        with patch.object(xhs, "build_opener") as network:
            network.return_value.open.return_value = Response(page(note))
            with self.assertRaisesRegex(RuntimeError, "incomplete image set"):
                xhs.download(NOTE_URL, self.root)
            self.assertEqual(network.return_value.open.call_count, 1)
        note["imageList"].pop()
        for body, headers in [(b"<html>verify</html>", {}), (b"\xff\xd8\xffshort", {"Content-Length": "1000"})]:
            with tempfile.TemporaryDirectory() as temp, patch.object(xhs, "build_opener") as network, patch.object(cli, "import_photos") as photos, patch.object(cli.tempfile, "tempdir", temp), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                network.return_value.open.side_effect = [Response(page(note)), Response(body, headers=headers)]
                self.assertEqual(cli.main([NOTE_URL, "--browser", "none", "--folder", str(Path(temp) / "out")]), 1)
                self.assertEqual(network.return_value.open.call_count, 2)
                photos.assert_not_called()

    def test_youtube_preflight_reuses_metadata_shows_quality_and_merges_audio(self):
        metadata_paths = []
        def run(command, **kwargs):
            self.assertIn("--no-playlist", command)
            self.assertIn("--js-runtimes", command)
            self.assertNotIn("--cookies", command)
            self.assertIn("mp4", command)
            if "--dump-single-json" in command:
                return CompletedProcess(command, 0, json.dumps({"id": "BaW_jenozKc", "requested_formats": [
                    {"vcodec": "av01.0", "width": 3840, "height": 2160, "fps": 60}, {"vcodec": "none"}]}) + "\n", "")
            info = Path(command[command.index("--load-info-json") + 1])
            metadata_paths.append(info)
            self.assertEqual(info.stat().st_mode & 0o777, 0o600)
            output = Path(command[command.index("-o") + 1]).parent
            (output / "youtube_BaW_jenozKc.mp4").write_bytes(b"video")
            return CompletedProcess(command, 0)
        with patch.object(cli, "find_tool", return_value="tool"), patch.object(cli.subprocess, "run", side_effect=run) as calls, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main([YT_URL, "--folder", str(self.root / "out")]), 0)
            self.assertEqual(calls.call_count, 2)
            self.assertIn("3840×2160", out.getvalue())
        self.assertFalse(metadata_paths[0].exists())

    def test_youtube_live_and_playlists_are_rejected_before_downloading(self):
        for metadata in ({"is_live": True}, {"live_status": "is_upcoming"}, {"_type": "playlist", "entries": []}):
            with patch.object(cli, "find_tool", return_value="tool"), patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, json.dumps(metadata), "")) as run, patch.object(cli, "import_photos") as photos, patch.object(cli.tempfile, "tempdir", str(self.root)), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main([YT_URL, "--browser", "none", "--folder", str(self.root / "out")]), 1)
                self.assertEqual(run.call_count, 1)
                photos.assert_not_called()

    def test_missing_youtube_runtime_guides_install_before_network(self):
        with patch.object(cli, "find_tool", side_effect=lambda name: None if name == "deno" else "tool"), patch.object(cli.subprocess, "run") as run, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cli.main([YT_URL, "--folder", str(self.root)]), 1)
            self.assertIn("brew install deno", err.getvalue())
            run.assert_not_called()

    def test_weibo_gallery_routes_all_media_to_existing_save_flow(self):
        def download(command, **kwargs):
            self.assertIn("extractor.weibo.videos=true", command)
            folder = Path(command[command.index("--directory") + 1])
            (folder / "weibo_123_001.jpg").write_bytes(b"image")
            (folder / "weibo_123_002.mp4").write_bytes(b"video")
            return CompletedProcess(command, 0)
        with patch.object(cli, "find_tool", return_value="gallery-dl"), patch.object(cli.subprocess, "run", side_effect=download), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["https://m.weibo.cn/detail/123", "--folder", str(self.root / "out")]), 0)
            self.assertIn("1 photo, 1 video saved", out.getvalue())

    def test_new_account_checks_require_current_viewer_not_public_author(self):
        true_user = 'window.__INITIAL_STATE__={"user":{"loggedIn":true,"userInfo":{"userId":"123"}}}'
        false_user = 'window.__INITIAL_STATE__={"user":{"loggedIn":false},"note":{"user":{"userId":"123"}}}'
        cases = [("xiaohongshu.com", true_user, "valid"), ("xiaohongshu.com", false_user, "invalid"),
                 ("youtube.com", 'ytcfg.set({"LOGGED_IN":true});', "valid"),
                 ("youtube.com", 'ytcfg.set({"LOGGED_IN":false});', "invalid"),
                 ("youtube.com", 'var ytInitialData = {"responseContext":{"mainAppWebResponseContext":{"loggedOut":false}}};', "valid")]
        for domain, html, expected in cases:
            self.assertEqual(session.classify_response(domain, 200, session.webpage_login(domain, html)).state, expected)
            self.assertEqual(session.classify_response(domain, 403, session.webpage_login(domain, html)).state, "unverified")
        for value, expected in [({"data": {"login": True, "uid": "123"}}, "valid"), ({"data": {"login": False}}, "invalid"), ({"data": {"uid": "123"}}, "unverified")]:
            self.assertEqual(session.classify_response("weibo.com", 200, value).state, expected)
        self.assertTrue(cli.needs_login("Sign in to confirm you're not a bot"))
        self.assertTrue(cli.needs_login("HTTP redirect to login page"))
        self.assertFalse(cli.needs_login("HTTP Error 403: Forbidden"))

    def test_youtube_alternate_cookie_is_saved_but_google_cookies_are_excluded(self):
        jar = MozillaCookieJar()
        for domain in (".youtube.com", ".google.com"):
            jar.set_cookie(Cookie(0, "__Secure-3PAPISID", "synthetic", None, False, domain, True, True, "/", True, True, None, True, None, None, {}))
        source = self.root / "youtube.txt"
        filtered = session.site_cookies(jar, "youtube.com", source)
        filtered.save(ignore_discard=True)
        self.assertEqual(accounts.save(source, "youtube.com").state, "unverified")
        self.assertNotIn("google.com", accounts.path_for("youtube.com").read_text())
        accounts.logout("youtube")
        self.assertFalse(accounts.copy_saved("youtube.com", self.root / "copy.txt"))
        self.assertIn("youtube.com", accounts.preferences()["blocked"])


if __name__ == "__main__":
    unittest.main()
