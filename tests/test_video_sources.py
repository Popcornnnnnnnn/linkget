"""Exercise single-video routing through the shared CLI and inspection paths."""
import contextlib
import io
import json
from pathlib import Path
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, article_archive, batch, cli, content, inspect_url, links
from media_stub import uninspected


class VideoSourceTests(unittest.TestCase):
    def test_inspection_routes_share_and_embed_links_as_video(self):
        cases = (
            ("https://www.vimeo.com/12345?share=copy", "Vimeo",
             "https://player.vimeo.com/video/12345"),
            ("https://player.vimeo.com/video/12345", "Vimeo",
             "https://player.vimeo.com/video/12345"),
            ("https://dai.ly/x123abc?start=5", "Dailymotion",
             "https://www.dailymotion.com/video/x123abc"),
            ("https://www.dailymotion.com/video/x123abc_example-video?playlist=x111",
             "Dailymotion", "https://www.dailymotion.com/video/x123abc"),
            ("https://m.pornhub.com/view_video.php?utm_source=share&viewkey=phexample",
             "Pornhub", "https://www.pornhub.com/view_video.php?viewkey=phexample"),
            ("https://www.pornhub.com/embed/phexample", "Pornhub",
             "https://www.pornhub.com/view_video.php?viewkey=phexample"),
        )
        for url, site, canonical in cases:
            with self.subTest(url=url):
                result = inspect_url.inspect_target(url, no_network=True)
                self.assertTrue(result["supported"], result)
                self.assertEqual((result["site"], result["engine"]), (site, "yt-dlp"))
                self.assertEqual(result["canonical_url"], canonical)
                self.assertEqual(result["tools_required"], ["yt-dlp", "ffmpeg", "ffprobe"])
                self.assertFalse(result["supports_auth"])
                self.assertIsNone(article_archive.classify_article_target(canonical))
        self.assertEqual(batch.site_key("https://dai.ly/x123abc"), "dailymotion.com")

    def test_collections_and_invalid_video_links_are_not_routed_to_an_engine(self):
        for url in (
            "https://vimeo.com/showcase/12345",
            "https://vimeo.com/12345/abcdef1234",
            "https://player.vimeo.com/video/12345?h=abcdef1234",
            "https://www.dailymotion.com/playlist/x123abc",
            "https://dai.ly/x123abc/collection",
            "https://www.pornhub.com/playlist/12345",
            "https://www.pornhub.com/view_video.php?viewkey=",
            "https://www.pornhub.com/view_video.php?viewkey=phExample",
            "https://www.pornhub.com/view_video.php?viewkey=phexample&viewkey=",
        ):
            with self.subTest(url=url):
                result = inspect_url.inspect_target(url, no_network=True)
                self.assertFalse(result["supported"], result)
                self.assertIsNone(article_archive.classify_article_target(url))
        lookalike = inspect_url.inspect_target("https://pornhub.com.example.org/embed/phexample", no_network=True)
        self.assertEqual(lookalike["engine"], "web_article")

    def test_pornhub_read_cache_keeps_distinct_video_ids(self):
        first = links.normalize_link("https://m.pornhub.com/view_video.php?viewkey=phfirst&utm_source=demo")
        second = links.normalize_link("https://www.pornhub.com/embed/phsecond")
        self.assertEqual(content.source_reference(first), "www.pornhub.com/view_video.php?viewkey=phfirst")
        self.assertNotEqual(content.source_reference(first), content.source_reference(second))

    def test_pornhub_folder_download_uses_the_existing_ytdlp_pipeline(self):
        url = "https://www.pornhub.com/view_video.php?viewkey=phexample"
        media = b"synthetic video"

        def run(command, **kwargs):
            self.assertIn("--no-playlist", command)
            self.assertNotIn("--cookies", command)
            if "--dump-single-json" in command:
                self.assertEqual(command[-1], url)
                return CompletedProcess(command, 0, json.dumps({"id": "phexample"}), "")
            self.assertIn("--load-info-json", command)
            output = Path(command[command.index("-o") + 1])
            self.assertEqual(output.name, "pornhub_%(id)s.%(ext)s")
            output.with_name("pornhub_phexample.mp4").write_bytes(media)
            return CompletedProcess(command, 0)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            destination = root / "downloads"
            with (
                patch.object(accounts, "directory", return_value=root / "sessions"),
                patch.object(tempfile, "tempdir", str(root)),
                patch.object(cli, "find_tool", side_effect=lambda tool: tool),
                patch.object(cli, "inspect_files", side_effect=uninspected),
                patch.object(cli.subprocess, "run", side_effect=run) as engine,
                contextlib.redirect_stdout(io.StringIO()),
            ):
                code = cli.main([url, "--folder", str(destination), "--browser", "none", "--non-interactive"])
            self.assertEqual(code, 0)
            self.assertEqual(engine.call_count, 2)
            self.assertEqual((destination / "pornhub_phexample.mp4").read_bytes(), media)
