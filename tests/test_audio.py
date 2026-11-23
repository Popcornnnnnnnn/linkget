"""Audio extraction, music site routing, config defaults, and recovery tests."""

import argparse
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from linkget import accounts, audio, batch, cli, engines, jobs, links, media, output, recovery


class FakeResponse(io.BytesIO):
    def __init__(self, body=b"", code=200, headers=None, url=None):
        super().__init__(body)
        self.code = code
        self.headers = headers or {}
        self._url = url

    def geturl(self):
        return self._url


class AudioRoutingTests(unittest.TestCase):
    def test_music_sites_normalize_and_route(self):
        cases = [
            (
                "Listen https://music.youtube.com/watch?v=BaW_jenozKc&list=RDAMVM123",
                "https://music.youtube.com/watch?v=BaW_jenozKc",
                ("YouTube Music", "yt-dlp", "youtube.com"),
            ),
            (
                "https://m.bilibili.com/audio/au12345?from=share",
                "https://www.bilibili.com/audio/au12345",
                ("Bilibili Audio", "yt-dlp", "bilibili.com"),
            ),
            (
                "https://m.soundcloud.com/artist-one/track-two?si=secret123",
                "https://soundcloud.com/artist-one/track-two",
                ("SoundCloud", "yt-dlp", "soundcloud.com"),
            ),
            (
                "https://soundcloud.com/artist-one/private-track/s-abc123?utm_source=share",
                "https://soundcloud.com/artist-one/private-track/s-abc123",
                ("SoundCloud", "yt-dlp", "soundcloud.com"),
            ),
            (
                "https://indie-band.bandcamp.com/track/demo-song?from=embed",
                "https://indie-band.bandcamp.com/track/demo-song",
                ("Bandcamp", "yt-dlp", "bandcamp.com"),
            ),
        ]
        for raw, expected_url, expected_route in cases:
            with self.subTest(raw=raw):
                normalized = links.normalize_link(raw)
                self.assertEqual(normalized, expected_url)
                routed = engines.route(normalized)
                self.assertEqual(routed, expected_route)
                self.assertTrue(engines.is_audio_only_site(routed[0]))

    def test_short_music_links_resolve(self):
        with patch.object(links, "build_opener") as opener:
            opener.return_value.open.return_value = FakeResponse(
                code=302, headers={"Location": "https://soundcloud.com/artist-one/track-two?si=tracking"}
            )
            self.assertEqual(
                links.normalize_link("https://on.soundcloud.com/abc123"),
                "https://soundcloud.com/artist-one/track-two",
            )
            opener.return_value.open.return_value = FakeResponse(
                code=302, headers={"Location": "https://www.bilibili.com/audio/au98765"}
            )
            self.assertEqual(
                links.normalize_link("https://b23.tv/audio123"),
                "https://www.bilibili.com/audio/au98765",
            )

    def test_music_playlists_albums_and_tabs_rejected(self):
        rejected = [
            "https://music.youtube.com/playlist?list=PL123456",
            "https://music.youtube.com/browse/MPREb_123",
            "https://music.youtube.com/channel/UC123",
            "https://soundcloud.com/artist-one/sets/my-album",
            "https://soundcloud.com/discover",
            "https://soundcloud.com/artist-one/tracks",
            "https://soundcloud.com/artist-one/track-two/recommended",
            "https://indie-band.bandcamp.com/album/full-lp",
            "https://bandcamp.com/track/not-artist-host",
            "https://daily.bandcamp.com/track/editorial",
        ]
        for url in rejected:
            with self.subTest(url=url), self.assertRaises(ValueError):
                engines.route(links.normalize_link(url))

    def test_batch_site_key_groups_music_hosts(self):
        self.assertEqual(batch.site_key("https://on.soundcloud.com/xyz"), "soundcloud.com")
        self.assertEqual(batch.site_key("https://music.youtube.com/watch?v=BaW_jenozKc"), "youtube.com")
        self.assertEqual(batch.site_key("https://artist.bandcamp.com/track/song"), "bandcamp.com")


class AudioCliConfigTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for obj, attr, val in (
            (accounts, "directory", self.root / "settings/sessions"),
            (cli.tempfile, "tempdir", str(self.root)),
            (cli.sys.stdin, "isatty", False),
        ):
            patcher = patch.object(obj, attr, return_value=val) if attr != "tempdir" else patch.object(obj, attr, val)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_config_audio_folder_save_query_and_clear(self):
        music = self.root / "my music"
        code, out, _ = self.run_cli("config")
        self.assertEqual(code, 0)
        self.assertIn("Audio default", out)
        self.assertIn("Not set", out)
        self.assertFalse((self.root / "settings/preferences.json").exists())

        code, out, _ = self.run_cli("config", "--audio-folder", str(music))
        self.assertEqual(code, 0)
        self.assertIn(str(music.resolve()), out)
        self.assertFalse(music.exists())
        self.assertEqual(accounts.preferences()["audio_destination"], {"mode": "folder", "path": str(music.resolve())})

        code, out, _ = self.run_cli("config", "--clear-audio-folder")
        self.assertEqual(code, 0)
        self.assertIn("Not set", out)
        self.assertIsNone(accounts.preferences()["audio_destination"])

        code, out, _ = self.run_cli("config", "--audio-folder")
        self.assertEqual(code, 0)
        self.assertIn("Current folder", out)
        self.assertEqual(accounts.preferences()["audio_destination"], {"mode": "cwd"})

    def test_invalid_audio_flag_combinations_fail_early(self):
        bad_invocations = [
            ["--audio", "--photos", "https://www.youtube.com/watch?v=BaW_jenozKc"],
            ["--audio-format", "mp3", "--photos", "https://www.youtube.com/watch?v=BaW_jenozKc"],
            ["--audio", "--date-now", "https://www.youtube.com/watch?v=BaW_jenozKc"],
            ["--audio", "--live-photos", "https://www.youtube.com/watch?v=BaW_jenozKc"],
            ["config", "--audio"],
            ["config", "--audio-folder", "--clear-audio-folder"],
            ["--audio-folder", str(self.root / "m"), "https://www.youtube.com/watch?v=BaW_jenozKc"],
            ["--clear-audio-folder", "https://www.youtube.com/watch?v=BaW_jenozKc"],
        ]
        for argv in bad_invocations:
            with self.subTest(argv=argv), patch.object(cli, "first_run") as setup, contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    cli.main(argv)
                self.assertEqual(raised.exception.code, 2)
                setup.assert_not_called()

    def test_audio_requires_folder_when_photos_default_and_no_audio_folder(self):
        code, _, err = self.run_cli("https://www.youtube.com/watch?v=BaW_jenozKc", "--audio", "--browser", "none")
        self.assertEqual(code, 1)
        self.assertIn("Audio downloads require a folder", err)

        code, _, err = self.run_cli("https://soundcloud.com/artist/track", "--browser", "none")
        self.assertEqual(code, 1)
        self.assertIn("Audio downloads require a folder", err)

    def test_audio_folder_precedence_and_pixiv_rejection(self):
        global_dir = self.root / "global"
        audio_dir = self.root / "audio-default"
        explicit_dir = self.root / "explicit"
        self.assertEqual(self.run_cli("config", "--folder", str(global_dir))[0], 0)
        self.assertEqual(cli.resolve_audio_folder(), global_dir.resolve())
        self.assertEqual(self.run_cli("config", "--audio-folder", str(audio_dir))[0], 0)
        self.assertEqual(cli.resolve_audio_folder(), audio_dir.resolve())
        self.assertEqual(cli.resolve_audio_folder(explicit_dir), explicit_dir.resolve())

        with patch.object(cli, "find_tool", return_value="tool"):
            code, _, err = self.run_cli("https://www.pixiv.net/artworks/123", "--audio", "--browser", "none")
        self.assertEqual(code, 1)
        self.assertIn("Pixiv artworks are images only", err)

    def test_focused_help_and_sites_include_audio(self):
        code, out, _ = self.run_cli("config", "--help")
        self.assertEqual(code, 0)
        self.assertIn("--audio-folder", out)
        self.assertIn("--clear-audio-folder", out)

        code, out, _ = self.run_cli("batch", "--help")
        self.assertEqual(code, 0)
        self.assertIn("--audio", out)
        self.assertIn("--audio-format", out)

        code, out, _ = self.run_cli("help")
        self.assertEqual(code, 0)
        options_section = out.split("Commands:")[0]
        self.assertIn("--audio", options_section)
        self.assertNotIn("--audio-folder", options_section)
        self.assertNotIn("--clear-audio-folder", options_section)

        code, out, _ = self.run_cli("sites")
        self.assertEqual(code, 0)
        for name in ("YouTube Music", "Bilibili Audio", "SoundCloud", "Bandcamp"):
            self.assertIn(name, out)

    def test_engine_command_and_public_music_login_errors_do_not_crash(self):
        tools = {"yt-dlp": "yt-dlp", "ffmpeg": "/opt/bin/ffmpeg", "ffprobe": "ffprobe", "deno": "deno"}
        cmd = engines.command_for(
            argparse.Namespace(),
            "https://www.youtube.com/watch?v=BaW_jenozKc",
            "yt-dlp",
            "youtube.com",
            self.root,
            tools,
            audio_only=True,
        )
        self.assertIn("ba[ext=m4a]/ba/b", cmd)
        self.assertNotIn("--merge-output-format", cmd)

        read_cmd = engines.command_for(
            argparse.Namespace(read_audio=True),
            "https://www.youtube.com/watch?v=BaW_jenozKc",
            "yt-dlp",
            "youtube.com",
            self.root,
            tools,
            audio_only=True,
        )
        self.assertIn("ba/b", read_cmd)

        # Bilibili audio skips video-resolution login restriction check.
        bili_info = {"formats": [{"height": 360, "quality": 16}]}
        bili_out = "Format(s) 1080P 高清 are missing\n" + json.dumps(bili_info) + "\n"
        with patch.object(engines, "run_process", return_value=CompletedProcess([], 0, bili_out, "")):
            prepared = engines.prepare_bilibili(
                ["yt-dlp", "-f", "ba[ext=m4a]/ba/b", "https://www.bilibili.com/video/BV123"],
                self.root / "bili.json",
                io.StringIO(),
                audio_only=True,
            )
        self.assertIn("--load-info-json", prepared)

        # SoundCloud / Bandcamp LoginRequired must not raise StopIteration/KeyError.
        self.run_cli("config", "--audio-folder", str(self.root / "music"))
        with patch.object(cli, "find_tool", return_value="tool"), patch.object(
            engines,
            "run_process",
            return_value=CompletedProcess([], 1, "", "HTTP Error 401: Login required"),
        ):
            code, _, err = self.run_cli("https://soundcloud.com/artist-one/track-two")
        self.assertEqual(code, 1)
        self.assertIn("public access", err)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
class AudioPipelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.tools = {"ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe")}
        for obj, attr, val in (
            (accounts, "directory", self.root / "settings/sessions"),
            (cli.tempfile, "tempdir", str(self.root)),
            (cli.sys.stdin, "isatty", False),
        ):
            patcher = patch.object(obj, attr, return_value=val) if attr != "tempdir" else patch.object(obj, attr, val)
            patcher.start()
            self.addCleanup(patcher.stop)

    def make_av_mp4(self, path):
        subprocess.run(
            [
                self.tools["ffmpeg"], "-y", "-v", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1",
                "-c:v", "mpeg4", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                str(path),
            ],
            check=True,
        )

    def make_silent_mp4(self, path):
        subprocess.run(
            [
                self.tools["ffmpeg"], "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
                "-c:v", "mpeg4",
                str(path),
            ],
            check=True,
        )

    def make_cover_jpg(self, path):
        subprocess.run(
            [
                self.tools["ffmpeg"], "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=green:s=64x64",
                "-frames:v", "1",
                str(path),
            ],
            check=True,
        )

    def test_lossless_extraction_tags_cover_probe_and_deterministic_dedupe(self):
        src = self.root / "src.mp4"
        self.make_av_mp4(src)
        cover = self.root / "thumb.jpg"
        self.make_cover_jpg(cover)
        meta = {
            "title": "Artist Name - My Song: Part/1?",
            "artist": "Artist Name - Topic",
            "album": "Single Album",
            "upload_date": "20261123",
            "thumbnail": "https://cdn.example/cover.jpg",
        }
        out_one = self.root / "stage1" / "audio"
        out_two = self.root / "stage2" / "audio"
        dest = self.root / "music"

        with patch.object(audio, "urlopen", side_effect=lambda *a, **kw: FakeResponse(cover.read_bytes())):
            files_one, notes_one = audio.prepare_audio(
                [src], [], {}, meta, "https://music.youtube.com/watch?v=BaW_jenozKc&list=PL1",
                "YouTube Music", out_one, self.tools,
            )
            files_two, _ = audio.prepare_audio(
                [src], [], {}, meta, "https://music.youtube.com/watch?v=BaW_jenozKc",
                "YouTube Music", out_two, self.tools,
            )

        self.assertEqual(notes_one, [])
        self.assertEqual(len(files_one), 1)
        result_file = files_one[0]
        # Avoids duplicating "Artist Name - Artist Name - ...", strips " - Topic", sanitizes / : ?
        self.assertEqual(result_file.name, "Artist Name - My Song- Part-1.m4a")
        self.assertEqual(result_file.name, files_two[0].name)
        self.assertEqual(result_file.read_bytes(), files_two[0].read_bytes())

        # Probe verifies audio stream (not attached_pic mjpeg!) and metadata tags.
        records = media.inspect_files([result_file])
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["kind"], "audio")
        self.assertEqual(rec["codec"], "aac")
        self.assertTrue(rec["probe_ok"])
        self.assertIsNone(rec["width"])
        summary = media.quality_summary(records)
        self.assertIn("aac", summary)
        self.assertIn("44.1 kHz", summary)
        self.assertNotIn("Dimensions unavailable", summary)
        self.assertNotIn("mjpeg", summary)

        ffprobe_json = json.loads(
            subprocess.check_output(
                [self.tools["ffprobe"], "-v", "error", "-show_format", "-show_streams", "-of", "json", str(result_file)],
                text=True,
            )
        )
        tags = {k.lower(): v for k, v in (ffprobe_json["format"].get("tags") or {}).items()}
        self.assertEqual(tags.get("artist"), "Artist Name")
        self.assertEqual(tags.get("album"), "Single Album")
        self.assertEqual(tags.get("date"), "2026-11-23")
        self.assertEqual(tags.get("comment"), "music.youtube.com/watch?v=BaW_jenozKc")
        self.assertTrue(any(s.get("codec_type") == "video" and (s.get("disposition") or {}).get("attached_pic") == 1 for s in ffprobe_json["streams"]))

        # Deterministic bytes mean repeat save_folder skips as duplicate.
        saved, skipped = cli.save_folder(files_one, dest)
        self.assertEqual((len(saved), skipped), (1, 0))
        saved2, skipped2 = cli.save_folder(files_two, dest)
        self.assertEqual((len(saved2), skipped2), (0, 1))
        self.assertEqual(len(list(dest.iterdir())), 1)

    def test_conversions_lossy_honesty_note_and_cover_fallbacks(self):
        src = self.root / "src.mp4"
        self.make_av_mp4(src)
        cover = self.root / "thumb.jpg"
        self.make_cover_jpg(cover)
        meta = {"title": "Track", "uploader": "Creator", "thumbnail": "https://cdn.example/cover.jpg"}

        for fmt, expected_codec, expect_lossy_note in (
            ("m4a", "aac", False),
            ("mp3", "mp3", False),
            ("opus", "opus", False),
            ("flac", "flac", True),
            ("wav", "pcm_s16le", True),
        ):
            with self.subTest(fmt=fmt), patch.object(audio, "urlopen", side_effect=lambda *a, **kw: FakeResponse(cover.read_bytes())):
                files, notes = audio.prepare_audio(
                    [src], [], {}, meta, "https://www.youtube.com/watch?v=BaW_jenozKc",
                    "YouTube", self.root / ("out-" + fmt), self.tools, target_format=fmt,
                )
                self.assertEqual(files[0].suffix, "." + fmt)
                records = media.inspect_files(files)
                self.assertTrue(records[0]["probe_ok"])
                self.assertEqual(records[0]["codec"], expected_codec)
                if expect_lossy_note:
                    self.assertTrue(any("does not restore lost audio detail" in n for n in notes))
                else:
                    self.assertFalse(any("does not restore lost audio detail" in n for n in notes))

        # Cover fetch failure warns and still saves audio.
        with patch.object(audio, "urlopen", side_effect=URLError("offline")):
            files, notes = audio.prepare_audio(
                [src], [], {}, meta, "https://www.youtube.com/watch?v=BaW_jenozKc",
                "YouTube", self.root / "out-no-cover", self.tools,
            )
            self.assertEqual(len(files), 1)
            self.assertTrue(any("Cover art unavailable" in n for n in notes))

    def test_mixed_silent_and_image_only_posts(self):
        silent = self.root / "silent.mp4"
        self.make_silent_mp4(silent)
        with self.assertRaisesRegex(RuntimeError, "no audio track"):
            audio.prepare_audio([silent], [], {}, {}, "https://x.com/u/status/1", "X", self.root / "s1", self.tools)

        img = self.root / "photo.jpg"
        img.write_bytes(b"fake-jpg")
        with self.assertRaisesRegex(RuntimeError, "No video or audio track"):
            audio.prepare_audio([img], [], {}, {}, "https://x.com/u/status/1", "X", self.root / "s2", self.tools)

        # Live Photo motion clip is excluded; if only image + motion remain, fail clearly.
        motion = self.root / "motion.mp4"
        self.make_av_mp4(motion)
        with self.assertRaisesRegex(RuntimeError, "No video or audio track"):
            audio.prepare_audio([img, motion], [(img.name, motion.name)], {}, {}, "https://weibo.com/detail/1", "Weibo", self.root / "s3", self.tools)

        # Mixed post with 1 still image + 1 real video extracts only the real video.
        real_vid = self.root / "real.mp4"
        self.make_av_mp4(real_vid)
        files, _ = audio.prepare_audio(
            [img, real_vid], [], {"real.mp4": {"title": "Mixed Video", "author": {"name": "Poster"}}},
            {}, "https://x.com/u/status/1", "X", self.root / "s4", self.tools,
        )
        self.assertEqual([p.name for p in files], ["Poster - Mixed Video.m4a"])

    def test_filename_utf8_cap_and_report_compare_message(self):
        long_title = "歌" * 100
        name = audio.build_filename({"artist": "歌手", "title": long_title}, "fallback", ".m4a")
        self.assertTrue(name.endswith(".m4a"))
        self.assertLessEqual(len(name.encode("utf-8")), 190)

        a = {"schema": 1, "items": [{"reference": "r", "status": "completed", "files": [{"sha256": "1", "kind": "audio", "codec": "aac"}]}]}
        b = {"schema": 1, "items": [{"reference": "r", "status": "completed", "files": [{"sha256": "2", "kind": "audio", "codec": "mp3"}]}]}
        self.assertIn("listening comparison", media.compare_reports(a, b)[0]["result"])
        self.assertEqual(output.saved_description([Path("a.m4a")]), "1 audio file saved")
        self.assertEqual(output.saved_description([Path("a.mp3"), Path("b.flac")]), "2 audio files saved")


class MixedBatchAndRecoveryAudioTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for obj, attr, val in (
            (accounts, "directory", self.root / "settings/sessions"),
            (cli.tempfile, "tempdir", str(self.root)),
            (cli.sys.stdin, "isatty", False),
            (batch, "SITE_INTERVAL", 0),
        ):
            patcher = patch.object(obj, attr, return_value=val) if attr not in {"tempdir", "SITE_INTERVAL"} else patch.object(obj, attr, val)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_mixed_batch_routes_video_to_photos_and_music_to_audio_folder(self):
        audio_dir = self.root / "Music"
        self.assertEqual(self.run_cli("config", "--audio-folder", str(audio_dir))[0], 0)

        def fake_pixiv(url, folder, cookies):
            (folder / "art.jpg").write_bytes(b"img")

        def fake_ytdlp(command, **kwargs):
            if "--dump-single-json" in command:
                return CompletedProcess(command, 0, json.dumps({"id": "sc1", "title": "Song", "uploader": "Artist", "acodec": "mp3"}) + "\n", "")
            out_tmpl = Path(command[command.index("-o") + 1]).parent
            (out_tmpl / "soundcloud_sc1.mp3").write_bytes(b"mp3data")
            return CompletedProcess(command, 0)

        def fake_prepare_audio(files, live_pairs, meta_map, default_meta, url, site, out_dir, tools, target_format=None):
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / "Artist - Song.mp3"
            out_file.write_bytes(b"tagged-mp3")
            return [out_file], []

        def fake_inspect(files, variants=None):
            return [{"file": p.name, "kind": "audio" if p.suffix == ".mp3" else "image", "source": "engine_selected", "probe_ok": True, "sha256": jobs.digest(p)} for p in files]

        with patch.object(cli.sys, "platform", "darwin"), \
                patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(cli, "download_pixiv", side_effect=fake_pixiv), \
                patch.object(engines, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "prepare_audio", side_effect=fake_prepare_audio), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect), \
                patch.object(recovery, "inspect_files", side_effect=fake_inspect), \
                patch.object(cli, "import_photos", side_effect=lambda files, date: (files, 0)) as photos:
            code, out, err = self.run_cli(
                "batch", "https://www.pixiv.net/artworks/123", "https://soundcloud.com/artist/song", "--browser", "none"
            )
        self.assertEqual(code, 0, err + out)
        photos.assert_called_once()
        self.assertTrue((audio_dir / "Artist - Song.mp3").is_file())
        self.assertFalse((audio_dir / "art.jpg").exists())

    def test_audio_retry_and_recover_preserve_audio_mode_and_block_photos(self):
        audio_dir = self.root / "Music"
        self.assertEqual(self.run_cli("config", "--audio-folder", str(audio_dir))[0], 0)

        def fake_ytdlp(command, **kwargs):
            if "--dump-single-json" in command:
                return CompletedProcess(command, 0, json.dumps({"id": "sc1", "title": "Song", "uploader": "Artist", "acodec": "mp3"}) + "\n", "")
            out_tmpl = Path(command[command.index("-o") + 1]).parent
            (out_tmpl / "soundcloud_sc1.mp3").write_bytes(b"mp3data")
            return CompletedProcess(command, 0)

        def fake_prepare_audio(files, live_pairs, meta_map, default_meta, url, site, out_dir, tools, target_format=None):
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / "Artist - Song.mp3"
            out_file.write_bytes(b"tagged-mp3")
            return [out_file], []

        def fake_inspect(files, variants=None):
            return [{"file": p.name, "kind": "audio", "source": "engine_selected", "probe_ok": True, "sha256": jobs.digest(p)} for p in files]

        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(engines, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "prepare_audio", side_effect=fake_prepare_audio), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect), \
                patch.object(cli, "save_folder", side_effect=OSError("disk full")):
            code, _, _ = self.run_cli("https://soundcloud.com/artist/song", "--audio-format", "mp3", "--browser", "none")
        self.assertEqual(code, 1)
        task = jobs.all_journals()[0]
        self.assertTrue(task.data["options"]["audio"])
        self.assertEqual(task.data["options"]["audio_format"], "mp3")

        # Cannot recover audio into Photos or with --verify.
        with patch.object(cli.sys, "platform", "darwin"), patch.object(cli.preflight, "destination"), \
                patch.object(recovery, "inspect_files", side_effect=fake_inspect):
            code, out, err = self.run_cli("recover", task.identifier, "--photos")
            self.assertEqual(code, 1)
            self.assertIn("Photos", out + err)
            code, out, err = self.run_cli("recover", task.identifier, "--verify")
            self.assertEqual(code, 1)
            self.assertIn("--verify", out + err)

        # Normal recover saves to the recorded audio folder without re-downloading or needing ffmpeg.
        with patch.object(cli, "find_tool", return_value=None), \
                patch.object(engines, "run_process", side_effect=AssertionError("should not redownload")), \
                patch.object(recovery, "inspect_files", side_effect=fake_inspect):
            code, out, err = self.run_cli("recover", task.identifier)
        self.assertEqual(code, 0, out + err)
        self.assertTrue((audio_dir / "Artist - Song.mp3").is_file())

    def test_short_music_url_and_photos_flag_checked_upfront_on_non_macos(self):
        audio_dir = self.root / "Music"
        self.assertEqual(self.run_cli("config", "--photos")[0], 0)
        self.assertEqual(self.run_cli("config", "--audio-folder", str(audio_dir))[0], 0)

        with patch.object(cli.sys, "platform", "linux"), \
                patch.object(cli.preflight, "originals_directory", side_effect=AssertionError("should not check Photos")):
            code, _, err = self.run_cli("https://music.youtube.com/watch?v=BaW_jenozKc", "--photos", "--browser", "none")
        self.assertEqual(code, 1)
        self.assertIn("Photos", err)

        def fake_ytdlp(command, **kwargs):
            if "--dump-single-json" in command:
                return CompletedProcess(command, 0, json.dumps({"id": "sc2", "title": "ShortTrack", "uploader": "Artist", "acodec": "mp3"}) + "\n", "")
            out_tmpl = Path(command[command.index("-o") + 1]).parent
            (out_tmpl / "soundcloud_sc2.mp3").write_bytes(b"mp3data")
            return CompletedProcess(command, 0)

        def fake_prepare_audio(files, live_pairs, meta_map, default_meta, url, site, out_dir, tools, target_format=None):
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / "Artist - ShortTrack.mp3"
            out_file.write_bytes(b"tagged-mp3")
            return [out_file], []

        def fake_inspect(files, variants=None):
            return [{"file": p.name, "kind": "audio", "source": "engine_selected", "probe_ok": True, "sha256": jobs.digest(p)} for p in files]

        with patch.object(cli.sys, "platform", "linux"), \
                patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(cli, "normalize_link", return_value="https://soundcloud.com/artist/short-track"), \
                patch.object(engines, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "prepare_audio", side_effect=fake_prepare_audio), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect):
            code, out, err = self.run_cli("https://on.soundcloud.com/abc123", "--browser", "none")
        self.assertEqual(code, 0, out + err)
        self.assertTrue((audio_dir / "Artist - ShortTrack.mp3").is_file())

    def test_retry_preserves_explicit_custom_folder_over_audio_folder_and_recover_rejects_audio_format(self):
        audio_dir = self.root / "Music"
        custom_dir = self.root / "Custom"
        self.assertEqual(self.run_cli("config", "--audio-folder", str(audio_dir))[0], 0)

        attempts = {"count": 0}

        def fake_ytdlp(command, **kwargs):
            if "--dump-single-json" in command:
                return CompletedProcess(command, 0, json.dumps({"id": "sc3", "title": "RetrySong", "uploader": "Artist", "acodec": "mp3"}) + "\n", "")
            attempts["count"] += 1
            if attempts["count"] == 1:
                return CompletedProcess(command, 1, "", "HTTP Error 503: Service Unavailable")
            out_tmpl = Path(command[command.index("-o") + 1]).parent
            (out_tmpl / "soundcloud_sc3.mp3").write_bytes(b"mp3data")
            return CompletedProcess(command, 0)

        def fake_prepare_audio(files, live_pairs, meta_map, default_meta, url, site, out_dir, tools, target_format=None):
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / "Artist - RetrySong.mp3"
            out_file.write_bytes(b"tagged-mp3")
            return [out_file], []

        def fake_inspect(files, variants=None):
            return [{"file": p.name, "kind": "audio", "source": "engine_selected", "probe_ok": True, "sha256": jobs.digest(p)} for p in files]

        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(engines, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "prepare_audio", side_effect=fake_prepare_audio), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect):
            code, _, _ = self.run_cli("https://soundcloud.com/artist/retry-song", "--folder", str(custom_dir), "--browser", "none")
        self.assertEqual(code, 1)
        task = jobs.all_journals()[0]

        code, out, err = self.run_cli("recover", task.identifier, "--audio-format", "mp3")
        self.assertEqual(code, 1)
        self.assertIn("--audio-format cannot be used with recover", out + err)
        self.assertIn("--redownload", out + err)

        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(engines, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "prepare_audio", side_effect=fake_prepare_audio), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect):
            code, out, err = self.run_cli("retry", task.identifier)
        self.assertEqual(code, 0, out + err)
        self.assertTrue((custom_dir / "Artist - RetrySong.mp3").is_file())
        self.assertFalse((audio_dir / "Artist - RetrySong.mp3").exists())

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe are required")
    def test_cover_mux_failure_falls_back_without_unlinking(self):
        tools = {"ffmpeg": shutil.which("ffmpeg"), "ffprobe": shutil.which("ffprobe")}
        src = self.root / "src.mp4"
        cover = self.root / "cover.jpg"
        subprocess.run(
            [
                tools["ffmpeg"], "-y", "-v", "error",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1",
                "-c:v", "mpeg4", "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                str(src),
            ],
            check=True,
        )
        subprocess.run(
            [
                tools["ffmpeg"], "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=green:s=64x64",
                "-frames:v", "1",
                str(cover),
            ],
            check=True,
        )
        meta = {"title": "CoverFallback", "artist": "Artist", "thumbnail": "https://cdn.example/cover.jpg"}
        real_run = audio.run_process

        def flaky_mux(command, **kwargs):
            if "-frames:v" not in command and "audio-cover" in command[-1]:
                return CompletedProcess(command, 1, "", "mux failed")
            return real_run(command, **kwargs)

        with patch.object(audio, "urlopen", side_effect=lambda *a, **kw: FakeResponse(cover.read_bytes())), \
                patch.object(audio, "run_process", side_effect=flaky_mux):
            files, notes = audio.prepare_audio(
                [src], [], {}, meta, "https://www.youtube.com/watch?v=BaW_jenozKc",
                "YouTube", self.root / "stage" / "audio", tools,
            )
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].is_file())
        self.assertEqual(files[0].parent.name, "audio")
        self.assertTrue(any("without thumbnail" in n for n in notes))

    def test_p2_retry_folder_from_default_ready_audio_format_batch_and_login_guidance(self):
        global_dir = self.root / "Downloads"
        audio_dir = self.root / "Music"
        self.assertEqual(self.run_cli("config", "--folder", str(global_dir))[0], 0)
        self.assertEqual(self.run_cli("config", "--audio-folder", str(audio_dir))[0], 0)

        # 1) Mixed batch early failure on audio short link before destination_folder recording
        # still retries into --audio-folder rather than global --folder.
        calls = {"pixiv": 0, "ytdlp": 0}

        def fake_pixiv(url, folder, cookies):
            calls["pixiv"] += 1
            (folder / "art.jpg").write_bytes(b"img")

        def fake_normalize(text):
            if "on.soundcloud.com" in text and calls["ytdlp"] == 0:
                calls["ytdlp"] += 1
                raise RuntimeError("Could not resolve the short link.")
            return "https://soundcloud.com/artist/track-p2"

        def fake_ytdlp(command, **kwargs):
            if "--dump-single-json" in command:
                return CompletedProcess(command, 0, json.dumps({"id": "p2", "title": "P2Track", "uploader": "Artist", "acodec": "mp3"}) + "\n", "")
            out_tmpl = Path(command[command.index("-o") + 1]).parent
            (out_tmpl / "soundcloud_p2.mp3").write_bytes(b"mp3data")
            return CompletedProcess(command, 0)

        def fake_prepare_audio(files, live_pairs, meta_map, default_meta, url, site, out_dir, tools, target_format=None):
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / "Artist - P2Track.mp3"
            out_file.write_bytes(b"tagged-mp3")
            return [out_file], []

        def fake_inspect(files, variants=None):
            return [{"file": p.name, "kind": "audio" if p.suffix == ".mp3" else "image", "source": "engine_selected", "probe_ok": True, "sha256": jobs.digest(p)} for p in files]

        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(cli, "download_pixiv", side_effect=fake_pixiv), \
                patch.object(cli, "normalize_link", side_effect=fake_normalize), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect):
            code, _, _ = self.run_cli("batch", "https://www.pixiv.net/artworks/123", "https://on.soundcloud.com/xyz", "--browser", "none")
        self.assertEqual(code, 1)
        task = jobs.all_journals()[0]
        self.assertTrue(task.data["options"]["folder_from_default"])

        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(cli, "normalize_link", return_value="https://soundcloud.com/artist/track-p2"), \
                patch.object(engines, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "prepare_audio", side_effect=fake_prepare_audio), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect):
            code, out, err = self.run_cli("retry", task.identifier)
        self.assertEqual(code, 0, out + err)
        self.assertTrue((audio_dir / "Artist - P2Track.mp3").is_file())
        self.assertFalse((global_dir / "Artist - P2Track.mp3").exists())

        # 2) retry --audio-format on ready audio item without --redownload rejects clearly.
        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(engines, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "prepare_audio", side_effect=fake_prepare_audio), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect), \
                patch.object(cli, "save_folder", side_effect=OSError("disk full")):
            code, _, _ = self.run_cli("https://soundcloud.com/artist/track-ready", "--audio-format", "mp3", "--browser", "none")
        self.assertEqual(code, 1)
        ready_task = jobs.all_journals()[0]
        with patch.object(cli, "find_tool", return_value="tool"):
            code, out, err = self.run_cli("retry", ready_task.identifier, "--audio-format", "flac")
        self.assertEqual(code, 1)
        self.assertIn("--redownload", out + err)

        # 3) All-music batch without --audio on non-macOS with Photos default auto-enables audio.
        self.assertEqual(self.run_cli("config", "--photos")[0], 0)
        with patch.object(cli.sys, "platform", "linux"), \
                patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(cli.preflight, "originals_directory", side_effect=AssertionError("should not check Photos")), \
                patch.object(engines, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "prepare_audio", side_effect=fake_prepare_audio), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect):
            code, out, err = self.run_cli("batch", "https://soundcloud.com/artist/a", "https://indie.bandcamp.com/track/b", "--browser", "none")
        self.assertEqual(code, 0, out + err)

        # 4) Public-only login_guidance prints public-access note on interactive TTY or --browser none.
        for browser_mode, is_tty in (("none", False), ("auto", True)):
            err_buf = io.StringIO()
            with patch.object(cli.sys.stdin, "isatty", return_value=is_tty), contextlib.redirect_stderr(err_buf):
                cli.login_guidance(argparse.Namespace(browser=browser_mode), "soundcloud.com", "https://soundcloud.com/a/b", "401")
            self.assertIn("public access", err_buf.getvalue())

    def test_p3_metadata_truncation_thumbnail_help_and_empty_audio_message(self):
        meta = audio.extract_metadata(
            {"title": "Song &amp; <Live>\x00", "artist": "Band\x00 &amp; <Co>", "thumbnail": "https://[bad"},
            "https://soundcloud.com/a/b",
            "SoundCloud",
        )
        self.assertEqual(meta["title"], "Song & <Live>")
        self.assertEqual(meta["artist"], "Band & <Co>")
        self.assertEqual(meta["thumbnail"], "")
        self.assertEqual(audio.fetch_cover("https://[bad", self.root, "ffmpeg")[0], None)

        long_name = audio.build_filename({"artist": "歌手", "title": "歌" * 100}, "fallback", ".m4a", index=2, total=3)
        self.assertTrue(long_name.endswith(" (2).m4a"))
        self.assertLessEqual(len(long_name.encode("utf-8")), 190)

        code, out, _ = self.run_cli("recover", "--help")
        self.assertEqual(code, 0)
        self.assertNotIn("--audio-format", out)

        out_dir = self.root / "empty-audio"
        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(engines, "run_process", return_value=CompletedProcess(["yt-dlp"], 0, "{}", "")), \
                patch.object(cli, "run_process", return_value=CompletedProcess(["yt-dlp"], 0, "", "")):
            code, out, err = self.run_cli("https://soundcloud.com/artist/empty", "--folder", str(out_dir), "--browser", "none")
        self.assertEqual(code, 1)
        self.assertIn("No audio was downloaded from this post.", out + err)


if __name__ == "__main__":
    unittest.main()
