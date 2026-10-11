"""Regressions for desktop configuration-only and public mobile Kuaishou pages."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from linkget import kuaishou

POST = "3xz5t5ber85wq7g"
URL = "https://www.kuaishou.com/short-video/" + POST + "?authorId=3xsafng3tf9q47w&area=homexxbrilliant"
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"video" * 30


class Response(io.BytesIO):
    def __init__(self, body, headers=None, url=URL):
        super().__init__(body)
        self.headers = headers or {}
        self.url = url

    def geturl(self):
        return self.url


def page(name, state):
    return ("<script>window." + name + " = " + json.dumps(state) + ";</script>").encode()


def representation(codec, width=720, height=1280, bitrate=461):
    return {"videoCodec": codec, "width": width, "height": height,
            "avgBitrate": bitrate, "url": "https://cdn.test/" + codec + ".mp4"}


def mobile_state(post_id=POST):
    # The live mobile page uses a numeric internal ID; share_info identifies
    # the public ID from the URL. Its cache-entry key is opaque and can change.
    return {"opaque-cache-key": {"result": 1, "photo": {
        "photoId": "5221642496231026999", "share_info": "photoId=" + post_id,
        "manifest": {"adaptationSet": [{"representation": [
            representation("avc", bitrate=1000), representation("hevc")
        ]}]}
    }}}


class KuaishouTests(unittest.TestCase):
    def test_desktop_config_only_uses_matching_public_mobile_video(self):
        state = mobile_state()
        state = dict({"unrelated": mobile_state("other")["opaque-cache-key"]}, **state)
        with tempfile.TemporaryDirectory() as temporary, patch.object(kuaishou, "build_opener") as build:
            build.return_value.open.side_effect = [
                Response(page("__APOLLO_STATE__", {"defaultClient": {"ROOT_QUERY": {"visionConfig": {}}}})),
                Response(page("INIT_STATE", state)), Response(MP4, {"Content-Length": str(len(MP4))})]
            info = kuaishou.download(URL, Path(temporary))
            self.assertEqual(info.notes, [])
            self.assertEqual(info.expected_count, 1)
            calls = build.return_value.open.call_args_list
            self.assertEqual([call.args[0].full_url for call in calls], [URL, URL, "https://cdn.test/hevc.mp4"])
            self.assertEqual(calls[1].args[0].get_header("User-agent"), kuaishou.MOBILE_UA)
            self.assertEqual((Path(temporary) / f"kuaishou_{POST}_001.mp4").read_bytes(), MP4)

    def test_desktop_video_uses_largest_resolution_before_codec_preference(self):
        state = {"defaultClient": {"VisionVideoDetailPhoto:" + POST: {"id": POST, "videoResource": {"json": {
            "hevc": {"adaptationSet": [{"representation": [representation("hevc")]}]},
            "h264": {"adaptationSet": [{"representation": [representation("avc", 1080, 1920)]}]}
        }}}}}
        with tempfile.TemporaryDirectory() as temporary, patch.object(kuaishou, "build_opener") as build:
            build.return_value.open.side_effect = [Response(page("__APOLLO_STATE__", state)), Response(MP4)]
            kuaishou.download(URL, Path(temporary))
            self.assertEqual(build.return_value.open.call_count, 2)
            self.assertEqual(build.return_value.open.call_args.args[0].full_url, "https://cdn.test/avc.mp4")

    def test_missing_requested_post_never_downloads_another_post(self):
        state = {"defaultClient": {"VisionVideoDetailPhoto:other": {"id": "other", "photoUrl": "https://cdn.test/wrong.mp4"}}}
        with tempfile.TemporaryDirectory() as temporary, patch.object(kuaishou, "build_opener") as build:
            build.return_value.open.side_effect = [Response(page("__APOLLO_STATE__", state)), Response(page("INIT_STATE", mobile_state("other")))]
            with self.assertRaisesRegex(RuntimeError, "did not expose"):
                kuaishou.download(URL, Path(temporary))
            self.assertEqual(build.return_value.open.call_count, 2)
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_invalid_and_truncated_downloads_are_not_reported_as_success(self):
        for body, length, error in [(b"<html>Verification required</html>", None, "invalid video"), (MP4, len(MP4) + 10, "incomplete")]:
            with self.subTest(error=error), tempfile.TemporaryDirectory() as temporary, patch.object(kuaishou, "build_opener") as build:
                build.return_value.open.side_effect = [Response(page("__APOLLO_STATE__", {})),
                    Response(page("INIT_STATE", mobile_state())), Response(body, {"Content-Length": str(length)} if length else {})]
                with self.assertRaisesRegex(RuntimeError, error):
                    kuaishou.download(URL, Path(temporary))
                self.assertEqual(list(Path(temporary).glob("*.mp4")), [])
                if length:
                    self.assertEqual(next(Path(temporary).glob("*.part")).read_bytes(), body)

    def test_explicit_login_redirect_remains_a_browser_required_failure(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(kuaishou, "build_opener") as build:
            build.return_value.open.side_effect = [Response(b"<html></html>"), Response(b"<html></html>", url="https://www.kuaishou.com/login")]
            with self.assertRaises(kuaishou.BrowserRequired):
                kuaishou.download(URL, Path(temporary))

    def test_unsupported_routes_stop_before_network_access(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(kuaishou, "build_opener") as build:
            for url in ["https://www.kuaishou.com/profile/person", "https://www.kuaishou.com/photo/123", "https://evil.test/short-video/" + POST]:
                with self.assertRaisesRegex(RuntimeError, "full Kuaishou"):
                    kuaishou.download(url, Path(temporary))
            build.assert_not_called()

    def test_apollo_state_whitespace_and_undefined_values(self):
        html = 'window.__APOLLO_STATE__ = {"defaultClient":{"unused":undefined,"caption":"undefined"}};'
        self.assertEqual(kuaishou.page_state(html, "__APOLLO_STATE__"), {"defaultClient": {"unused": None, "caption": "undefined"}})


if __name__ == "__main__":
    unittest.main()
