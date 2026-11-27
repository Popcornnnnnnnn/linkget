"""Tests for Phase 3 automation, safety caps, clipping, safe stage cleanup, and MCP server."""
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
from subprocess import CompletedProcess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from linkget import (
    accounts,
    batch,
    cleanup,
    cli,
    engines,
    jobs,
    mcp_server,
    media_limits,
    ownership,
    read_store,
    runtime,
    staging,
    transfers,
)


def _real_run_process(command, **kwargs):
    return subprocess.run(command, **kwargs)


def _probe_duration(ffprobe, path):
    res = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(res.stdout.strip())


def _generate_mp4(ffmpeg, dest, *, duration=4, gop=None):
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c=blue:s=64x64:r=25:d={duration}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
    ]
    if gop is not None:
        cmd.extend(["-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0"])
    cmd.extend(["-c:a", "aac", "-shortest", str(dest)])
    subprocess.run(cmd, check=True, capture_output=True, text=True)


class Phase3AutomationMCPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        for obj, attr, val in (
            (accounts, "directory", self.root / "settings" / "sessions"),
            (tempfile, "tempdir", str(self.root)),
            (cli.sys.stdin, "isatty", False),
            (batch, "SITE_INTERVAL", 0),
        ):
            patcher = (
                patch.object(obj, attr, val)
                if attr in {"tempdir", "SITE_INTERVAL"}
                else patch.object(obj, attr, return_value=val)
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_mcp_save_type_reuses_cli_and_keeps_the_existing_video_scope(self):
        with patch.object(mcp_server, "_run_cli_json") as run:
            mcp_server._call_tool("download_media", {
                "url": "https://www.douyin.com/note/123",
                "folder": str(self.root),
                "save_as": "photo",
            })
            self.assertIn("--as", run.call_args.args[0])
            self.assertEqual(run.call_args.args[0][-1], "photo")
            run.reset_mock()
            with self.assertRaisesRegex(mcp_server._RPCError, "outside dedicated LinkGet routes"):
                mcp_server._call_tool("inspect_url", {
                    "url_or_path": "https://example.com/clip", "save_as": "video",
                })
            run.assert_not_called()
            mcp_server._call_tool("inspect_url", {
                "url_or_path": "https://vimeo.com/1084537", "save_as": "video", "no_network": True,
            })
            self.assertIn("video", run.call_args.args[0])

    def test_media_limits_parsers_and_cli_validation(self):
        self.assertEqual(media_limits.parse_duration("90"), 90.0)
        self.assertEqual(media_limits.parse_duration("90s"), 90.0)
        self.assertEqual(media_limits.parse_duration("10m"), 600.0)
        self.assertEqual(media_limits.parse_duration("1h30m"), 5400.0)
        self.assertEqual(media_limits.parse_duration("1:30"), 90.0)
        self.assertAlmostEqual(media_limits.parse_duration("01:02:03.5"), 3723.5)
        for bad in ("0", "-5", "2d", "abc", "1:75", ""):
            with self.assertRaises(ValueError, msg=bad):
                media_limits.parse_duration(bad)

        self.assertEqual(media_limits.parse_age("0"), 0.0)
        self.assertEqual(media_limits.parse_age("7d"), 7 * 86400.0)
        self.assertEqual(media_limits.parse_age("2d12h"), 2.5 * 86400.0)
        for bad in ("-1d", "invalid", "1:00"):
            with self.assertRaises(ValueError, msg=bad):
                media_limits.parse_age(bad)

        self.assertEqual(media_limits.parse_size("50MB"), 50 * 1024 ** 2)
        self.assertEqual(media_limits.parse_size("1.5GB"), int(1.5 * 1024 ** 3))
        self.assertEqual(media_limits.parse_size("500KB"), 500 * 1024)
        self.assertEqual(media_limits.parse_size("1024B"), 1024)
        for bad in ("0", "-10MB", "50TB", "abc", "1e308GB"):
            with self.assertRaises(ValueError, msg=bad):
                media_limits.parse_size(bad)
        self.assertEqual(media_limits._declared_size({"requested_formats": [{"filesize": True}, {"filesize_approx": 512}]}), 512)

        self.assertEqual(media_limits.parse_clip("0:30-2:00"), (30.0, 120.0))
        self.assertEqual(media_limits.parse_clip("10-45"), (10.0, 45.0))
        self.assertEqual(media_limits.parse_clip("0-15.5"), (0.0, 15.5))
        self.assertEqual(media_limits.ytdlp_section((30.0, 120.0)), "*30.000-120.000")
        for bad in ("2:00-0:30", "10", "-5-10", "abc-10", "10-10"):
            with self.assertRaises(ValueError, msg=bad):
                media_limits.parse_clip(bad)

        bad_cli_cases = [
            ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "--clip", "2:00-0:30"],
            ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "--max-duration", "bad"],
            ["https://www.youtube.com/watch?v=dQw4w9WgXcQ", "--max-size", "10TB"],
            ["inspect", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "--clip", "0-10"],
            ["clean", "--clip", "0-10"],
            ["mcp", "--max-duration", "1m"],
            ["recover", "--max-size", "10MB"],
            ["config", "--max-duration", "5m"],
            ["sites", "--older-than", "7d"],
            ["clean", "--dry-run", "--apply"],
            ["clean", "--older-than", "bad-age"],
            ["clean", "--browser", "chrome"],
            ["mcp", "--browser", "chrome"],
            ["mcp", "--json"],
        ]
        for argv in bad_cli_cases:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as ctx:
                cli.main(argv)
            self.assertEqual(ctx.exception.code, 2, argv)

        for help_argv in (["clean", "--help"], ["help", "clean"], ["mcp", "--help"], ["help", "mcp"]):
            code, out, err = self.run_cli(*help_argv)
            self.assertEqual(code, 0, help_argv)
            self.assertEqual(err, "")
            self.assertTrue(out.strip())

    def test_engines_command_for_clip_and_metadata_limits(self):
        tools = {"yt-dlp": "yt-dlp", "ffmpeg": "/opt/bin/ffmpeg", "deno": "deno"}
        args = type("Args", (), {"clip": "0:30-2:00"})()
        cmd = engines.command_for(args, "https://www.youtube.com/watch?v=abc", "yt-dlp", "youtube.com", self.root, tools)
        idx = cmd.index("--download-sections")
        self.assertEqual(cmd[idx + 1], "*30.000-120.000")

        # Duration exceeded
        with self.assertRaises(ValueError):
            media_limits.check_metadata_limits({"duration": 900}, max_duration=600)
        # Clip start beyond video duration
        with self.assertRaises(ValueError):
            media_limits.check_metadata_limits({"duration": 20}, clip=(30.0, 60.0))
        # Effective clipped duration fits within max_duration
        media_limits.check_metadata_limits({"duration": 300}, max_duration=40.0, clip=(30.0, 60.0))
        with self.assertRaises(ValueError):
            media_limits.check_metadata_limits({"duration": 300}, max_duration=40.0, clip=(30.0, 120.0))
        # Filesize via requested_formats checked when not clipping, skipped when clipping
        with self.assertRaises(ValueError):
            media_limits.check_metadata_limits(
                {"requested_formats": [{"filesize": 600}, {"filesize_approx": 600}]},
                max_size=1000,
            )
        media_limits.check_metadata_limits(
            {"duration": 100, "requested_formats": [{"filesize": 5000000}]},
            max_size=1000,
            clip=(0.0, 10.0),
        )

    def test_ytdlp_predownload_metadata_abort_skips_download(self):
        calls = []

        def fake_run(command, **kwargs):
            calls.append(list(command))
            if "--dump-single-json" in command:
                return CompletedProcess(
                    command,
                    0,
                    json.dumps({"id": "vid1", "title": "Long Talk", "duration": 3600, "filesize_approx": 500_000_000}) + "\n",
                    "",
                )
            raise AssertionError("Download invocation should have been aborted by pre-download limit check")

        out_dir = self.root / "out"
        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(engines, "run_process", side_effect=fake_run), \
                patch.object(cli, "run_process", side_effect=fake_run):
            code, out, err = self.run_cli(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "--folder",
                str(out_dir),
                "--max-duration",
                "5m",
                "--browser",
                "none",
                "--json",
            )
        self.assertEqual(code, 1, out + err)
        self.assertEqual(len(calls), 1)
        self.assertIn("--dump-single-json", calls[0])
        payload = json.loads(out)
        self.assertFalse(payload["ok"])
        self.assertIn("max-duration", payload["error"]["message"])
        self.assertEqual(list(out_dir.glob("*")), [])

    def test_real_ffmpeg_clip_caps_reencode_fallback_and_retry_guard(self):
        ffmpeg = shutil.which("ffmpeg")
        ffprobe = shutil.which("ffprobe")
        if not ffmpeg or not ffprobe:
            self.skipTest("ffmpeg/ffprobe required for real clipping integration test")

        sample_mp4 = self.root / "sample_4s.mp4"
        _generate_mp4(ffmpeg, sample_mp4, duration=4)

        def fake_tools(name):
            return {"ffmpeg": ffmpeg, "ffprobe": ffprobe, "gallery-dl": "gallery-dl", "yt-dlp": "yt-dlp", "deno": "deno"}.get(name)

        def fake_run(command, **kwargs):
            if command and command[0] == "gallery-dl":
                dest_dir = Path(command[command.index("--directory") + 1])
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(sample_mp4, dest_dir / "insta_1.mp4")
                return CompletedProcess(command, 0, "", "")
            return _real_run_process(command, **kwargs)

        clip_out = self.root / "clip_out"
        with patch.object(cli, "find_tool", side_effect=fake_tools), \
                patch.object(engines, "run_process", side_effect=fake_run), \
                patch.object(cli, "run_process", side_effect=fake_run):
            code, out, err = self.run_cli(
                "https://www.instagram.com/p/clip123/",
                "--folder",
                str(clip_out),
                "--clip",
                "0:01-0:03",
                "--browser",
                "none",
                "--json",
            )
        self.assertEqual(code, 0, out + err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        saved = Path(payload["saved_files"][0])
        self.assertTrue(saved.is_file())
        clipped_dur = _probe_duration(ffprobe, saved)
        self.assertGreaterEqual(clipped_dur, 1.5)
        self.assertLessEqual(clipped_dur, 2.6)

        # retry --clip change without --redownload must fail when ready exists
        task_journal = jobs.Journal.create(["https://www.instagram.com/p/retryclip/"], type("Args", (), {"folder": str(self.root / "r_out"), "clip": "0-1"})())
        ready_file = self.root / "ready_clip.mp4"
        shutil.copyfile(sample_mp4, ready_file)
        task_journal.update(1, status="failed", ready={"files": [{"path": str(ready_file), "sha256": jobs.digest(ready_file)}], "live_pairs": [], "prepared": [], "evidence": {}})
        code, out, err = self.run_cli("retry", task_journal.identifier, "--clip", "0-2", "--json")
        self.assertEqual(code, 1)
        retry_err = json.loads(out)
        self.assertFalse(retry_err["ok"])
        self.assertIn("--redownload", retry_err["items"][0]["error"]["message"])

        # retry --max-duration / --max-size against existing ready enforces caps before saving
        cap_stage, _, cap_stage_id = self._make_stage("ready-cap")
        cap_ready = cap_stage / "ready_cap.mp4"
        shutil.copyfile(sample_mp4, cap_ready)
        cap_out = self.root / "cap_ready_out"
        j_cap = jobs.Journal.create(["https://www.instagram.com/p/retrycap/"], type("Args", (), {"folder": str(cap_out)})())
        j_cap.update(
            1,
            status="failed",
            stage=str(cap_stage),
            stage_identity=cap_stage_id,
            ready={"files": [{"path": str(cap_ready), "sha256": jobs.digest(cap_ready)}], "live_pairs": [], "prepared": [], "evidence": {}},
        )
        code, out, _ = self.run_cli("retry", j_cap.identifier, "--max-duration", "2s", "--json")
        self.assertEqual(code, 1)
        self.assertIn("max-duration", json.loads(out)["items"][0]["error"]["message"])
        code, out, _ = self.run_cli("retry", j_cap.identifier, "--max-size", "100B", "--json")
        self.assertEqual(code, 1)
        self.assertIn("max-size", json.loads(out)["items"][0]["error"]["message"])
        self.assertFalse(cap_out.exists() and list(cap_out.iterdir()))

        # yt-dlp end-to-end --clip passes --download-sections and runs post-download check_file_limits
        yt_cmds = []

        def fake_ytdlp_clip(command, **kwargs):
            yt_cmds.append(list(command))
            if "--dump-single-json" in command:
                return CompletedProcess(command, 0, json.dumps({"id": "ytc1", "title": "Short Clip", "duration": 20}) + "\n", "")
            tmpl = Path(command[command.index("-o") + 1]).parent
            shutil.copyfile(sample_mp4, tmpl / "youtube_ytc1.mp4")
            return CompletedProcess(command, 0, "", "")

        yt_out = self.root / "yt_clip_out"
        with patch.object(cli, "find_tool", side_effect=fake_tools), \
                patch.object(engines, "run_process", side_effect=fake_ytdlp_clip), \
                patch.object(cli, "run_process", side_effect=fake_ytdlp_clip):
            code, out, err = self.run_cli(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "--folder",
                str(yt_out),
                "--clip",
                "0:01-0:05",
                "--max-size",
                "100B",
                "--browser",
                "none",
                "--json",
            )
        self.assertEqual(code, 1, out + err)
        self.assertTrue(any("--download-sections" in cmd for cmd in yt_cmds))
        self.assertIn("max-size", json.loads(out)["error"]["message"])
        self.assertFalse(yt_out.exists() and list(yt_out.iterdir()))

        # --clip rejected on image-only Pixiv & --live-photos (CLI exit 2 and retry/download guard)
        with patch.object(cli, "find_tool", side_effect=fake_tools):
            code, out, _ = self.run_cli("https://www.pixiv.net/artworks/12345", "--folder", str(self.root / "px"), "--clip", "0-2", "--browser", "none", "--json")
            self.assertEqual(code, 1)
            self.assertIn("image", json.loads(out)["error"]["message"].lower())
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as lp_ctx:
            cli.main(["https://www.instagram.com/p/lp/", "--folder", str(self.root / "lp"), "--clip", "0-2", "--live-photos"])
        self.assertEqual(lp_ctx.exception.code, 2)
        with patch.object(cli, "find_tool", side_effect=lambda name: "exiftool" if name == "exiftool" else fake_tools(name)):
            j_lp = jobs.Journal.create(["https://www.instagram.com/p/lp-retry/"], type("Args", (), {"folder": str(self.root / "lp"), "live_photos": True})())
            code, out, _ = self.run_cli("retry", j_lp.identifier, "--clip", "0-2", "--json")
            self.assertEqual(code, 1)
            self.assertIn("--live-photos", json.loads(out)["items"][0]["error"]["message"])

        # Post-download --max-duration failure prevents saving
        dur_out = self.root / "dur_fail"
        with patch.object(cli, "find_tool", side_effect=fake_tools), \
                patch.object(engines, "run_process", side_effect=fake_run), \
                patch.object(cli, "run_process", side_effect=fake_run):
            code, out, err = self.run_cli(
                "https://www.instagram.com/p/durfail/",
                "--folder",
                str(dur_out),
                "--max-duration",
                "2s",
                "--browser",
                "none",
                "--json",
            )
        self.assertEqual(code, 1)
        self.assertIn("max-duration", json.loads(out)["error"]["message"])
        self.assertFalse(dur_out.exists() and list(dur_out.iterdir()))

        # Post-download --max-size failure prevents saving
        size_out = self.root / "size_fail"
        with patch.object(cli, "find_tool", side_effect=fake_tools), \
                patch.object(engines, "run_process", side_effect=fake_run), \
                patch.object(cli, "run_process", side_effect=fake_run):
            code, out, err = self.run_cli(
                "https://www.instagram.com/p/sizefail/",
                "--folder",
                str(size_out),
                "--max-size",
                "100B",
                "--browser",
                "none",
                "--json",
            )
        self.assertEqual(code, 1)
        self.assertIn("max-size", json.loads(out)["error"]["message"])
        self.assertFalse(size_out.exists() and list(size_out.iterdir()))

        # Single-keyframe GOP 6s video clipped at 2.0-4.0 triggers ffprobe re-encode fallback,
        # mixed image FileProof(created=True) preservation, and tampered source verify rejection
        long_gop = self.root / "long_gop_6s.mp4"
        _generate_mp4(ffmpeg, long_gop, duration=6, gop=250)
        proof = ownership.FileProof.observe(long_gop)
        kept_img = self.root / "kept.jpg"
        kept_img.write_bytes(b"\xff\xd8\xff\xe0kept-img")
        img_created_proof = ownership.FileProof(kept_img, ownership.file_identity(kept_img), jobs.digest(kept_img), created=True)
        with patch.object(media_limits, "run_process", side_effect=_real_run_process):
            clipped_list = media_limits.clip_media_files(
                ownership.FileList([kept_img, long_gop], [img_created_proof, proof]),
                [],
                self.root / "gop_out",
                clip=(2.0, 4.0),
                ffmpeg=ffmpeg,
                ffprobe=ffprobe,
            )
            self.assertEqual(clipped_list.proof(kept_img), img_created_proof)
            self.assertTrue(clipped_list.proof(kept_img).created)
            with self.assertRaises(ValueError):
                media_limits.clip_media_files(
                    ownership.FileList([long_gop], [proof]),
                    [],
                    self.root / "gop_bad",
                    clip=(10.0, 12.0),
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                )
            with self.assertRaises(ValueError):
                media_limits.clip_media_files(
                    ownership.FileList([kept_img], [img_created_proof]),
                    [],
                    self.root / "img_only_clip",
                    clip=(0.0, 1.0),
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                )
            tamper_src = self.root / "tamper.mp4"
            shutil.copyfile(sample_mp4, tamper_src)
            tamper_proof = ownership.FileProof.observe(tamper_src)
            tamper_src.write_bytes(b"replaced-tamper-bytes")
            with self.assertRaises(RuntimeError):
                media_limits.clip_media_files(
                    ownership.FileList([tamper_src], [tamper_proof]),
                    [],
                    self.root / "tamper_out",
                    clip=(0.0, 1.0),
                    ffmpeg=ffmpeg,
                    ffprobe=ffprobe,
                )
        fallback_dur = _probe_duration(ffprobe, clipped_list[1])
        self.assertGreaterEqual(fallback_dur, 1.5)
        self.assertLessEqual(fallback_dur, 2.6)

    def _make_stage(self, prefix, *, age_days=10, content=b"stage-bytes"):
        d = self.root / f"linkget-{prefix}"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "media.bin"
        f.write_bytes(content)
        old_ts = time.time() - age_days * 86400
        os.utime(f, (old_ts, old_ts))
        os.utime(d, (old_ts, old_ts))
        return d, f, staging.identity(d)

    def test_clean_dry_run_apply_and_ownership_guards(self):
        args_ns = type("Args", (), {"folder": str(self.root / "saved")})()
        # 1) Eligible completed old stage
        s_ok, f_ok, id_ok = self._make_stage("old-ok", age_days=10)
        j_ok = jobs.Journal.create(["https://example.test/1"], args_ns)
        j_ok.update(1, status="completed", stage=str(s_ok), stage_identity=id_ok)

        # 2) Too new completed stage
        s_new, f_new, id_new = self._make_stage("new-ok", age_days=1)
        j_new = jobs.Journal.create(["https://example.test/2"], args_ns)
        j_new.update(1, status="completed", stage=str(s_new), stage_identity=id_new)

        # 3) Active task (locked journal)
        s_act, f_act, id_act = self._make_stage("active", age_days=10)
        j_act = jobs.Journal.create(["https://example.test/3"], args_ns)
        j_act.update(1, status="completed", stage=str(s_act), stage_identity=id_act)
        lock_fd = open(jobs.directory() / f"{j_act.identifier}.lock", "a+")
        self.addCleanup(lock_fd.close)
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        # 4) Incomplete failed task
        s_inc, f_inc, id_inc = self._make_stage("failed", age_days=10)
        j_inc = jobs.Journal.create(["https://example.test/4"], args_ns)
        j_inc.update(1, status="failed", stage=str(s_inc), stage_identity=id_inc)

        # 5) Unverified Photos receipt
        s_ph, f_ph, id_ph = self._make_stage("photos", age_days=10)
        j_ph = jobs.Journal.create(["https://example.test/5"], args_ns)
        j_ph.update(1, status="completed", stage=str(s_ph), stage_identity=id_ph, receipt={"staged": [{"path": str(f_ph)}]})

        # 6) Symlink inside stage
        s_sym, f_sym, id_sym = self._make_stage("sym", age_days=10)
        outside = self.root / "outside_secret.txt"
        outside.write_text("keep")
        (s_sym / "evil").symlink_to(outside)
        j_sym = jobs.Journal.create(["https://example.test/6"], args_ns)
        j_sym.update(1, status="completed", stage=str(s_sym), stage_identity=id_sym)

        # 7) Missing creation-time stage_identity
        s_noid, f_noid, _ = self._make_stage("noid", age_days=10)
        j_noid = jobs.Journal.create(["https://example.test/7"], args_ns)
        j_noid.update(1, status="completed", stage=str(s_noid), stage_identity=None)

        # 8) Identity mismatch (replaced directory with distinct inode on all filesystems)
        s_mis, _, id_old_mis = self._make_stage("mismatch", age_days=10)
        s_mis_new, _, id_new_mis = self._make_stage("mismatch-new", age_days=10)
        self.assertNotEqual(id_new_mis, id_old_mis)
        shutil.rmtree(s_mis)
        s_mis_new.rename(s_mis)
        f_mis = s_mis / "media.bin"
        j_mis = jobs.Journal.create(["https://example.test/8"], args_ns)
        j_mis.update(1, status="completed", stage=str(s_mis), stage_identity=id_old_mis)

        # 9) Overlapping user destination folder
        s_ov, f_ov, id_ov = self._make_stage("overlap", age_days=10)
        ov_args = type("Args", (), {"folder": str(s_ov)})()
        j_ov = jobs.Journal.create(["https://example.test/9"], ov_args)
        j_ov.update(1, status="completed", stage=str(s_ov), stage_identity=id_ov, destination_folder=str(s_ov))

        # 10) Protected stage + previous_stages eligible + snapshot created=False + corrupt snapshot + symlink lock + corrupt journal
        s_prot, f_prot, id_prot = self._make_stage("protected", age_days=10)
        j_prot = jobs.Journal.create(["https://example.test/10"], args_ns)
        j_prot.update(1, status="completed", stage=str(s_prot), stage_identity=id_prot, protected_stages=[str(s_prot)])

        s_prev_old, f_prev_old, id_prev_old = self._make_stage("prev-old", age_days=10)
        s_curr_ok, f_curr_ok, id_curr_ok = self._make_stage("curr-ok", age_days=10)
        j_prev = jobs.Journal.create(["https://example.test/11"], args_ns)
        j_prev.update(
            1,
            status="completed",
            previous_stages=[str(s_prev_old)],
            stage=str(s_curr_ok),
            stage_identity=id_curr_ok,
            stage_snapshots={
                str(s_prev_old): {"created": True, "identity": id_prev_old},
                str(s_curr_ok): {"created": True, "identity": id_curr_ok},
            },
        )

        s_snap_false, f_snap_false, id_snap_false = self._make_stage("snap-false", age_days=10)
        j_sf = jobs.Journal.create(["https://example.test/12"], args_ns)
        j_sf.update(
            1,
            status="completed",
            stage=str(s_snap_false),
            stage_identity=id_snap_false,
            stage_snapshots={str(s_snap_false): {"created": False, "identity": id_snap_false}},
        )

        s_snap_bad, f_snap_bad, id_snap_bad = self._make_stage("snap-bad", age_days=10)
        j_sb = jobs.Journal.create(["https://example.test/13"], args_ns)
        j_sb.update(1, status="completed", stage=str(s_snap_bad), stage_identity=id_snap_bad, stage_snapshots="corrupt")

        s_symlock, f_symlock, id_symlock = self._make_stage("symlock", age_days=10)
        j_sl = jobs.Journal.create(["https://example.test/14"], args_ns)
        j_sl.update(1, status="completed", stage=str(s_symlock), stage_identity=id_symlock)
        (jobs.directory() / f"{j_sl.identifier}.lock").unlink(missing_ok=True)
        (jobs.directory() / f"{j_sl.identifier}.lock").symlink_to(outside)

        corrupt_journal_path = jobs.directory() / "20260101T000000-corrupt12345.json"
        corrupt_journal_path.write_text("{not valid json")

        # Direct TOCTOU check: _remove_stage_tree refuses identity mismatch before unlinking any inner files
        s_toctou_a, _, id_toctou_a = self._make_stage("toctou-a", age_days=10)
        s_toctou_b, f_toctou_b, _ = self._make_stage("toctou-b", age_days=10)
        with self.assertRaises(ValueError):
            cleanup._remove_stage_tree(s_toctou_b, id_toctou_a)
        self.assertTrue(f_toctou_b.is_file())
        shutil.rmtree(s_toctou_a)
        shutil.rmtree(s_toctou_b)

        # With a corrupt journal present, cleanup isolates errors AND safely keeps all stages
        # (because cross-task folder overlap cannot be verified)
        code, out, err = self.run_cli("clean", "--older-than", "7d", "--json")
        self.assertEqual(code, 1, out + err)
        self.assertEqual(err, "")
        corrupt_run = json.loads(out)
        self.assertFalse(corrupt_run["ok"])
        self.assertEqual(len(corrupt_run["errors"]), 1)
        self.assertEqual(corrupt_run["eligible_count"], 0)

        # Remove corrupt journal and verify normal dry-run + apply
        corrupt_journal_path.unlink()
        code, out, err = self.run_cli("clean", "--older-than", "7d", "--json")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(err, "")
        dry = json.loads(out)
        self.assertTrue(dry["ok"])
        self.assertEqual(dry["mode"], "dry_run")
        self.assertEqual(dry["eligible_count"], 3, dry["eligible"])  # s_ok, s_prev_old, s_curr_ok
        self.assertEqual(dry["deleted_count"], 0)
        self.assertTrue(s_ok.exists())
        kept_reasons = {item["reason"] for item in dry["kept"]}
        self.assertTrue(
            {"too_new", "active_task", "incomplete_task", "unverified_photos_receipt", "unsafe_contents", "missing_identity", "identity_mismatch", "overlaps_protected_path", "protected_stage", "unsafe_lock"} <= kept_reasons,
            kept_reasons,
        )

        # Apply
        code, out, err = self.run_cli("clean", "--apply", "--older-than", "7d", "--json")
        self.assertEqual(code, 0, out + err)
        applied = json.loads(out)
        self.assertTrue(applied["ok"])
        self.assertEqual(applied["mode"], "apply")
        self.assertEqual(applied["deleted_count"], 3)
        self.assertEqual(applied["freed_bytes"], 3 * len(b"stage-bytes"))
        self.assertFalse(s_ok.exists())
        self.assertFalse(s_prev_old.exists())
        self.assertFalse(s_curr_ok.exists())
        self.assertIsNone(jobs.Journal.load(j_ok.identifier).data["items"][0]["stage"])
        self.assertEqual(jobs.Journal.load(j_prev.identifier).data["items"][0]["previous_stages"], [])
        self.assertIsNone(jobs.Journal.load(j_prev.identifier).data["items"][0]["stage"])

        # All protected/kept targets remain intact
        for path in (f_new, f_act, f_inc, f_ph, f_sym, outside, f_noid, f_mis, f_ov, f_prot, f_snap_false, f_snap_bad, f_symlock):
            self.assertTrue(path.is_file(), f"{path} was unexpectedly removed")

    def test_mcp_server_newline_framed_and_subprocess(self):
        # Create a completed read task for get_task include_content=True
        read_dir = self.root / "read_out"
        read_dir.mkdir(parents=True)
        (read_dir / "content.json").write_text(json.dumps({"title": "Sample Article", "text": "Hello world"}))
        task_id = "20261123T000001-0123456789ab"
        read_tasks_dir = accounts.directory().parent / "read-tasks"
        read_tasks_dir.mkdir(parents=True, exist_ok=True)
        (read_tasks_dir / f"{task_id}.json").write_text(
            json.dumps({
                "schema": 1,
                "id": task_id,
                "kind": "read",
                "status": "completed",
                "argv": ["https://example.test/post"],
                "created": time.time(),
                "outputs": [str(read_dir)],
            })
        )

        sample_file = self.root / "sample.mp4"
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            _generate_mp4(ffmpeg, sample_file, duration=1)
        else:
            sample_file.write_bytes(b"ID3test")

        sym_media = self.root / "sym_sample.mp4"
        sym_media.symlink_to(sample_file)

        # 1) Newline-delimited JSON-RPC session + notification + null-id + error/redaction cases
        saved_modes = runtime.snapshot_modes()
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "method": "ping"},  # notification without id -> no reply
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "doctor", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "inspect_url", "arguments": {"url_or_path": str(sample_file), "no_network": True}}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "get_task", "arguments": {"task_id": task_id, "kind": "read", "include_content": True}}},
            {"jsonrpc": "2.0", "id": 7, "method": "unknown/method"},
            {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "download_media", "arguments": {"url": "--help", "folder": str(self.root)}}},
            {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "read_content", "arguments": {"url_or_path": "cancel"}}},
            {"jsonrpc": "2.0", "id": None, "method": "unknown/method"},
        ]
        raw_in = b"\n".join(json.dumps(r).encode("utf-8") for r in reqs) + b"\n{bad json\n123\n"
        out_buf = io.BytesIO()
        code = mcp_server.serve(io.BytesIO(raw_in), out_buf)
        self.assertEqual(code, 0)
        self.assertEqual(runtime.snapshot_modes(), saved_modes)
        lines = [line for line in out_buf.getvalue().decode("utf-8").splitlines() if line.strip()]
        msgs = [json.loads(line) for line in lines]
        # Notifications produce no response; 9 id requests + id:null unknown (-32601) + {bad json (-32700) + 123 (-32600) = 12 responses
        self.assertEqual(len(msgs), 12, msgs)
        by_id = {m["id"]: m for m in msgs if m.get("id") is not None}
        self.assertEqual(by_id[1]["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(by_id[2]["result"], {})
        tool_names = [t["name"] for t in by_id[3]["result"]["tools"]]
        self.assertEqual(tool_names, ["inspect_url", "download_media", "read_content", "get_task", "doctor"])
        self.assertEqual(by_id[4]["result"]["structuredContent"]["command"], "doctor")
        self.assertEqual(by_id[5]["result"]["structuredContent"]["command"], "inspect")
        self.assertEqual(by_id[6]["result"]["structuredContent"]["tasks"][0]["content"]["title"], "Sample Article")
        self.assertEqual(by_id[7]["error"]["code"], -32601)
        self.assertEqual(by_id[8]["error"]["code"], -32602)
        self.assertEqual(by_id[9]["error"]["code"], -32602)
        null_errors = [m["error"]["code"] for m in msgs if m.get("id") is None]
        self.assertEqual(sorted(null_errors), [-32700, -32601, -32600])

        # Security/validation rejections (-32602) and URL redaction in download_media errors
        secret_url = "https://www.xiaohongshu.com/explore/0123456789abcdef01234567?xsec_token=TOPSECRET"
        invalid_calls = [
            ("download_media", {"url": secret_url, "folder": str(self.root / "dl"), "clip": "bad-clip"}),
            ("download_media", {"url": secret_url, "folder": str(self.root / "dl"), "browser": "opera"}),
            ("read_content", {"url_or_path": "_worker"}),
            ("read_content", {"url_or_path": str(sym_media)}),
            ("read_content", {"url_or_path": "bad\x00path"}),
            ("read_content", {"url_or_path": "https://example.test/p", "max_frames": 0}),
            ("inspect_url", {"url_or_path": str(sym_media)}),
            ("get_task", {"limit": 999}),
            ("get_task", {"task_id": "../etc/passwd"}),
        ]
        for tool_name, tool_args in invalid_calls:
            with self.subTest(tool=tool_name, args=tool_args), self.assertRaises(mcp_server._RPCError) as rpc_err:
                mcp_server.handle_request({"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {"name": tool_name, "arguments": tool_args}})
            self.assertEqual(rpc_err.exception.code, -32602)

        with patch.object(cli, "main", side_effect=SystemExit(2)):
            err_res = mcp_server.handle_request({
                "jsonrpc": "2.0",
                "id": 98,
                "method": "tools/call",
                "params": {"name": "download_media", "arguments": {"url": secret_url, "folder": str(self.root / "dl")}},
            })
        self.assertTrue(err_res["result"]["isError"])
        self.assertEqual(err_res["result"]["structuredContent"]["command"], "download")
        self.assertNotIn("TOPSECRET", json.dumps(err_res))

        # Verify download_media & read_content argument forwarding & stdout capture
        def fake_cli_run(argv):
            print("NOISY PRINT THAT MUST BE CAPTURED")
            print("NOISY STDERR THAT MUST BE CAPTURED", file=sys.stderr)
            return 0

        with patch.object(cli, "main", side_effect=fake_cli_run) as mock_main:
            res = mcp_server.handle_request({
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {
                    "name": "download_media",
                    "arguments": {
                        "url": "https://example.test/v",
                        "folder": str(self.root / "dl"),
                        "audio": True,
                        "audio_format": "mp3",
                        "clip": "0:05-0:15",
                        "max_duration": "1m",
                        "max_size": "10MB",
                        "browser": "none",
                    },
                },
            })
            self.assertIn("--clip=0:05-0:15", mock_main.call_args[0][0])
            self.assertIn("--audio-format=mp3", mock_main.call_args[0][0])
            self.assertEqual(res["result"]["isError"], True)  # empty mocked stdout returns error payload
            self.assertEqual(res["result"]["structuredContent"]["command"], "download")
        self.assertEqual(runtime.snapshot_modes(), saved_modes)

        with patch.object(mcp_server, "_run_cli_json", return_value={"ok": True, "command": "read"}) as mock_run:
            res = mcp_server.handle_request({
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {
                    "name": "read_content",
                    "arguments": {"url_or_path": "https://example.test/post", "frames": True, "max_frames": 6, "background": True},
                },
            })
            self.assertFalse(res["result"]["isError"])
            passed_argv = mock_run.call_args[0][0]
            self.assertIn("--frames", passed_argv)
            self.assertIn("--max-frames=6", passed_argv)
            self.assertNotIn("--foreground", passed_argv)

            mcp_server.handle_request({
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {"name": "read_content", "arguments": {"url_or_path": "https://example.test/post", "background": False}},
            })
            self.assertIn("--foreground", mock_run.call_args[0][0])

        # 2) Content-Length framed transport round-trip & first-message framed error
        bad_framed_out = io.BytesIO()
        mcp_server.serve(io.BytesIO(b"Content-Length: nope\r\n\r\n"), bad_framed_out)
        bad_buf = io.BytesIO(bad_framed_out.getvalue())
        hdr = bad_buf.readline().decode("ascii")
        self.assertTrue(hdr.startswith("Content-Length: "), hdr)
        bad_len = int(hdr.split(":", 1)[1].strip())
        bad_buf.readline()
        bad_msg = json.loads(bad_buf.read(bad_len).decode("utf-8"))
        self.assertEqual(bad_msg["error"]["code"], -32700)

        framed_reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
        framed_in = bytearray()
        for req in framed_reqs:
            body = json.dumps(req).encode("utf-8")
            framed_in.extend(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
        framed_out = io.BytesIO()
        self.assertEqual(mcp_server.serve(io.BytesIO(bytes(framed_in)), framed_out), 0)
        parsed_framed = []
        buf = io.BytesIO(framed_out.getvalue())
        while True:
            header = buf.readline()
            if not header:
                break
            length = int(header.decode("ascii").split(":", 1)[1].strip())
            buf.readline()
            parsed_framed.append(json.loads(buf.read(length).decode("utf-8")))
        self.assertEqual([m["id"] for m in parsed_framed], [1, 2])
        self.assertEqual(parsed_framed[0]["result"]["protocolVersion"], "2024-11-05")

        # 3) Real subprocess invocation (works in both source tree and installed wheel)
        pkg_parent = str(Path(cli.__file__).resolve().parent.parent)
        env = dict(os.environ)
        env["LINKGET_HOME"] = str(self.root / "settings")
        env["TMPDIR"] = str(self.root)
        env["PYTHONPATH"] = pkg_parent
        sub_in = "\n".join([
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
            json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "doctor", "arguments": {}}}),
        ]) + "\n"
        proc = subprocess.run(
            [sys.executable, "-m", "linkget.cli", "mcp"],
            input=sub_in,
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        sub_lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(sub_lines), 3, proc.stdout)
        sub_msgs = [json.loads(line) for line in sub_lines]
        self.assertEqual([m["id"] for m in sub_msgs], [1, 2, 3])
        self.assertEqual(sub_msgs[2]["result"]["structuredContent"]["command"], "doctor")


if __name__ == "__main__":
    unittest.main()
