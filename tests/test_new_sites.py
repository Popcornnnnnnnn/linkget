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
from urllib.error import HTTPError

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

    def test_xhs_empty_map_store_does_not_hide_authenticated_viewer(self):
        for user, expected in [({"loggedIn": True, "userInfo": {"userId": "123"}}, "valid"),
                               ({"loggedIn": False}, "invalid"), ({}, "unverified")]:
            html = ('window.__INITIAL_STATE__={"AiNoteDetailStore":{"noteDetailMap":new Map([])},'
                    '"missing":undefined,"text":"new Map([])","user":' + json.dumps(user) + '};')
            self.assertEqual(xhs.initial_state(html)["text"], "new Map([])")
            self.assertEqual(session.classify_response("xiaohongshu.com", 200,
                             session.webpage_login("xiaohongshu.com", html)).state, expected)
        self.assertEqual(xhs.initial_state('window.__INITIAL_STATE__={"map":new Map( [ ] )}'), {"map": {}})
        self.assertEqual(xhs.initial_state('window.__INITIAL_STATE__={"map":new Map(evil())}'), {})

    def test_xhs_cn_share_text_resolves_across_short_domains_and_keeps_token(self):
        shared = "看看这篇图文笔记 https://xhslink.cn/o/example Copy and open rednote to view the note"
        final = NOTE_URL + "?xsec_token=synthetic%2Btoken%3D&xsec_source=app_share"
        with patch.object(links, "build_opener") as network:
            network.return_value.open.side_effect = [
                Response(code=302, headers={"Location": "https://xhslink.com/a/example"}),
                Response(code=302, headers={"Location": final}),
            ]
            self.assertEqual(links.normalize_link(shared), final)
            self.assertEqual(cli.route(final)[1], "xiaohongshu")
            self.assertEqual(network.return_value.open.call_count, 2)
        with patch.object(links, "build_opener") as network:
            network.return_value.open.return_value = Response(code=302, headers={"Location": "https://xhslink.cn.evil.test/o/example"})
            with self.assertRaisesRegex(ValueError, "outside its platform"):
                links.normalize_link(shared)

    def test_xhs_saves_every_image_in_order_and_skips_preview_urls(self):
        note = {"type": "normal", "noteId": NOTE_ID, "imageList": [
            {"fileId": "notes_pre_post/one", "urlPre": "https://sns-webpic-qc.xhscdn.com/thumb", "urlDefault": "https://sns-webpic-qc.xhscdn.com/one"},
            {"fileId": "notes_pre_post/two", "infoList": [{"imageScene": "WB_DFT", "url": "https://sns-webpic-qc.xhscdn.com/two"}]}]}
        image = b"\xff\xd8\xff" + b"image" * 20
        with patch.object(xhs, "build_opener") as network, contextlib.redirect_stdout(io.StringIO()):
            network.return_value.open.side_effect = [Response(page(note)), Response(image), Response(image)]
            self.assertEqual(cli.main([NOTE_URL, "--browser", "none", "--folder", str(self.root / "out")]), 0)
            names = sorted(path.name for path in (self.root / "out").iterdir())
            self.assertEqual(names, [f"xiaohongshu_{NOTE_ID}_001_original.jpg", f"xiaohongshu_{NOTE_ID}_002_original.jpg"])
            self.assertNotIn("thumb", str(network.return_value.open.call_args_list))

    def test_xhs_video_uses_original_instead_of_display_stream_or_cover(self):
        note = {"type": "video", "imageList": [{"urlDefault": "https://sns-webpic-qc.xhscdn.com/cover"}],
                "video": {"consumer": {"originVideoKey": "original/video.mp4"}, "media": {"stream": {"h264": [
                    {"height": 720, "width": 1280, "masterUrl": "https://sns-video-bd.xhscdn.com/low"}],
                    "h265": [{"height": 1080, "width": 1920, "masterUrl": "https://sns-video-bd.xhscdn.com/high"}]}}}}
        self.assertEqual(xhs.note_media(note), [("video", "https://sns-video-bd.xhscdn.com/original/video.mp4")])
        with patch.object(xhs, "build_opener") as network:
            network.return_value.open.side_effect = [Response(page(note)), Response(b"\x00\x00\x00\x18ftypmp42" + b"x" * 100)]
            xhs.download(NOTE_URL, self.root)
            self.assertTrue((self.root / f"xiaohongshu_{NOTE_ID}_001_original.mp4").is_file())

    def test_xhs_mobile_share_downloads_full_gallery_without_login(self):
        note = {"noteId": NOTE_ID, "type": "normal", "imageList": [
            {"fileId": f"notes_pre_post/full-{index}", "infoList": [{"imageScene": "H5_PRV", "url": "https://sns-webpic-qc.xhscdn.com/preview"},
                          {"imageScene": "H5_DTL", "url": f"https://sns-webpic-qc.xhscdn.com/full-{index}"}]}
            for index in range(3)]}
        mobile = ('window.__INITIAL_STATE__=' + json.dumps({"noteData": {"data": {"noteData": note}}})).encode()
        image = b"\xff\xd8\xff" + b"image" * 20
        with patch.object(xhs, "build_opener") as network:
            network.return_value.open.side_effect = [
                Response(b'window.__INITIAL_STATE__={"user":{"loggedIn":false}}'),
                Response(mobile), Response(image), Response(image), Response(image)]
            xhs.download(NOTE_URL + "?xsec_token=synthetic", self.root)
            self.assertEqual(len(list(self.root.glob('*.jpg'))), 3)
            request = network.return_value.open.call_args_list[1].args[0]
            self.assertIn('/discovery/item/' + NOTE_ID + '?xsec_token=synthetic', request.full_url)
            self.assertIn('iPhone', request.get_header('User-agent'))
            self.assertNotIn('/preview', str(network.return_value.open.call_args_list))

    def test_xhs_incomplete_gallery_challenge_and_truncation_do_not_import(self):
        note = {"type": "normal", "imageList": [{"fileId": "notes_pre_post/one", "urlDefault": "https://sns-webpic-qc.xhscdn.com/one"}, {}]}
        with patch.object(xhs, "build_opener") as network:
            network.return_value.open.return_value = Response(page(note))
            with self.assertRaisesRegex(RuntimeError, "incomplete original image set"):
                xhs.download(NOTE_URL, self.root)
            self.assertEqual(network.return_value.open.call_count, 1)
        note["imageList"].pop()
        for body, headers in [(b"<html>verify</html>", {}), (b"\xff\xd8\xffshort", {"Content-Length": "1000"})]:
            with tempfile.TemporaryDirectory() as temp, patch.object(xhs, "build_opener") as network, patch.object(cli, "import_photos") as photos, patch.object(cli.tempfile, "tempdir", temp), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                network.return_value.open.side_effect = [Response(page(note)), Response(body, headers=headers)]
                self.assertEqual(cli.main([NOTE_URL, "--browser", "none", "--folder", str(Path(temp) / "out")]), 1)
                self.assertEqual(network.return_value.open.call_count, 2)
                photos.assert_not_called()

    def test_xhs_original_image_recovers_file_id_and_preserves_heic_format(self):
        url = "https://sns-webpic-qc.xhscdn.com/202609292111/" + "a" * 32 + "/notes_pre_post/sample!h5_1080jpg"
        note = {"type": "normal", "imageList": [{"url": url}]}
        self.assertEqual(xhs.note_media(note), [("image", "https://sns-img-bd.xhscdn.com/notes_pre_post/sample")])
        heic = b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00mif1heic" + b"x" * 100
        with patch.object(xhs, "build_opener") as network:
            network.return_value.open.side_effect = [Response(page(note)), Response(heic)]
            xhs.download(NOTE_URL, self.root)
        self.assertEqual((self.root / f"xiaohongshu_{NOTE_ID}_001_original.heic").read_bytes(), heic)
        for key in ("../secret", "https://other.test/file", "notes/../../secret", "/file"):
            with self.assertRaises(RuntimeError):
                xhs.original_url(key, "image")

    def test_xhs_missing_or_rejected_original_never_falls_back_to_marked_stream(self):
        note = {"type": "video", "video": {"media": {"stream": {"h264": [{"masterUrl": "https://sns-video-bd.xhscdn.com/display"}]}}}}
        with self.assertRaisesRegex(RuntimeError, "original video source"):
            xhs.note_media(note)
        note["video"]["consumer"] = {"originVideoKey": "original/video.mp4"}
        with patch.object(xhs, "build_opener") as network:
            network.return_value.open.side_effect = [Response(page(note)), HTTPError("https://sns-video-bd.xhscdn.com/original/video.mp4", 404, "not found", {}, None)]
            with self.assertRaisesRegex(RuntimeError, "no display-version fallback"):
                xhs.download(NOTE_URL, self.root)
            self.assertEqual(network.return_value.open.call_count, 2)
            self.assertFalse(list(self.root.glob('*.mp4')))

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

    def test_weibo_current_homepage_response_verifies_viewer_not_feature_config(self):
        # Shape captured from a live homepage; all identity fields are synthetic.
        viewer = {"serverTime": 0, "enablePopLogin": False, "uid": 123,
                  "user": {"id": 123, "idstr": "123", "screen_name": "Example"},
                  "flags": {}, "loginHeader": {}}
        html = '<script>window.$CONFIG = ' + json.dumps(viewer) + '; window.$CONFIG = {};</script>'
        jar = MozillaCookieJar()
        jar.set_cookie(Cookie(0, "SUB", "synthetic", None, False, ".weibo.com", True, True, "/", True, True, None, True, None, None, {}))
        with patch.object(session, "build_opener") as network:
            network.return_value.open.return_value = Response(html.encode())
            self.assertEqual(session.check_session(jar, "weibo.com").state, "valid")
            request = network.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "https://weibo.com/")
            self.assertEqual(request.get_header("Accept"), "text/html")
        for body in [json.dumps({"ok": 1, "data": {"ab_test": {}}}),
                     json.dumps({"ok": 0, "message": "404"}),
                     json.dumps({"data": {"login": True, "uid": "123"}}),
                     'window.$CONFIG = {"user":{"id":123,"screen_name":"Public author"}};',
                     'window.$CONFIG = {"uid":0,"user":{"id":123,"screen_name":"Public author"}};',
                     'window.$CONFIG = {"uid":456,"user":{"id":123,"screen_name":"Other user"}};',
                     'window.$CONFIG = {"uid":123,"user":null};',
                     'window.$CONFIG = evil();', '<html>Security verification required</html>']:
            with self.subTest(body=body), patch.object(session, "build_opener") as network:
                network.return_value.open.return_value = Response(body.encode())
                self.assertEqual(session.check_session(jar, "weibo.com").state, "unverified")
        for location, expected in [("https://passport.weibo.com/visitor/visitor", "invalid"),
                                   ("/login.php", "invalid"),
                                   ("https://passport.weibo.com/sso/signin", "invalid"),
                                   ("https://elsewhere.test/login.php", "unverified")]:
            with patch.object(session, "build_opener") as network:
                network.return_value.open.side_effect = HTTPError("https://weibo.com/", 302, "redirect", {"Location": location}, io.BytesIO())
                self.assertEqual(session.check_session(jar, "weibo.com").state, expected)

    def test_weibo_profiles_albums_and_profile_shortlinks_never_start_downloader(self):
        urls = ["https://weibo.com/u/1234567890", "https://weibo.com/1234567890",
                "https://weibo.com/example", "https://weibo.com/n/example", "https://weibo.com/p/1005051234567890/home",
                "https://weibo.com/u/1234567890?tabtype=album", "https://weibo.com/1234567890?tabtype=video",
                "https://m.weibo.cn/u/1234567890", "https://m.weibo.cn/profile/1234567890", "https://t.cn/example"]
        for url in urls:
            with self.subTest(url=url), patch.object(links, "build_opener") as network, patch.object(cli.subprocess, "run") as download, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                network.return_value.open.side_effect = [Response(code=302, headers={"Location": urls[0]}), Response()]
                self.assertEqual(cli.main([url, "--browser", "none", "--folder", str(self.root / "out")]), 1)
                download.assert_not_called()
                self.assertFalse((self.root / "out").exists())


if __name__ == "__main__":
    unittest.main()
