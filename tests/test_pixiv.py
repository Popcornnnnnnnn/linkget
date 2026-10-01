"""Pixiv web originals: complete static works, login boundaries and invalid media."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, cli, pixiv, session


class Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}


def data(body, **extra):
    return Response(json.dumps({"error": False, "body": body, **extra}).encode())


class PixivTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_complete_original_gallery_needs_no_engine_or_login(self):
        urls = [f"https://i.pximg.net/img-original/123_p{i}.png" for i in range(2)]
        image = b"\x89PNG\r\n\x1a\n" + b"image" * 20
        with patch.object(pixiv, "build_opener") as network, patch.object(cli, "find_tool", return_value=None), \
                patch.object(accounts, "directory", return_value=self.root / "sessions"), \
                contextlib.redirect_stdout(io.StringIO()):
            network.return_value.open.side_effect = [
                data({"illustType": 1, "pageCount": 2}),
                data([{"urls": {"original": url, "regular": "https://i.pximg.net/preview"}} for url in urls]),
                Response(image), Response(image),
            ]
            self.assertEqual(cli.main(["https://www.pixiv.net/en/artworks/123", "--browser", "none", "--folder", str(self.root / "out")]), 0)
            self.assertEqual(sorted(p.name for p in (self.root / "out").iterdir()), ["pixiv_123_p0.png", "pixiv_123_p1.png"])
            requests = network.return_value.open.call_args_list
            self.assertEqual([call.args[0].full_url for call in requests[2:]], urls)
            self.assertTrue(all(call.args[0].get_header("Referer") == "https://www.pixiv.net/" for call in requests))

    def test_viewer_check_distinguishes_invalid_cookie_and_inconclusive_pages(self):
        cases = [(200, {"error": False, "body": {"following": 0, "followers": 0, "mypixivCount": 0}}, "valid"),
                 (401, {"error": True, "message": "Invalid PHPSESSID"}, "invalid"),
                 (200, {"error": False, "body": {}}, "unverified"),
                 (200, {"authenticated": True}, "unverified"),
                 (503, {"error": False, "body": {}}, "unverified")]
        for code, body, expected in cases:
            with self.subTest(code=code, body=body):
                self.assertEqual(session.classify_response("pixiv.net", code, body).state, expected)

    def test_incomplete_page_list_is_not_saved_as_success(self):
        with patch.object(pixiv, "build_opener") as network:
            network.return_value.open.side_effect = [data({"illustType": 1, "pageCount": 2}), data([{"urls": {"original": "https://i.pximg.net/one.jpg"}}])]
            with self.assertRaisesRegex(RuntimeError, "all artwork pages"):
                pixiv.download("https://www.pixiv.net/artworks/123", self.root)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_ugoira_does_not_save_its_still_cover(self):
        with patch.object(pixiv, "build_opener") as network:
            network.return_value.open.return_value = data({"illustType": 2, "pageCount": 1})
            with self.assertRaisesRegex(RuntimeError, "Ugoira"):
                pixiv.download("https://www.pixiv.net/artworks/123", self.root)
            self.assertEqual(network.return_value.open.call_count, 1)

    def test_explicit_login_response_can_use_browser_recovery(self):
        with patch.object(pixiv, "build_opener") as network:
            network.return_value.open.return_value = data(None, error=True, message="ログインしてください")
            with self.assertRaises(pixiv.BrowserRequired):
                pixiv.download("https://www.pixiv.net/artworks/123", self.root)

    def test_html_and_truncated_originals_are_not_completed_images(self):
        for body, headers, message in [(b"<html>Access denied</html>", {}, "non-image"),
                                      (b"\xff\xd8\xff" + b"x" * 100, {"Content-Length": "500"}, "incomplete")]:
            with self.subTest(message=message), tempfile.TemporaryDirectory() as temp, patch.object(pixiv, "build_opener") as network:
                network.return_value.open.side_effect = [data({"illustType": 0, "pageCount": 1}), data([{"urls": {"original": "https://i.pximg.net/one.jpg"}}]), Response(body, headers)]
                with self.assertRaisesRegex(RuntimeError, message):
                    pixiv.download("https://www.pixiv.net/artworks/123", Path(temp))
                self.assertEqual(list(Path(temp).glob("*.jpg")), [])
