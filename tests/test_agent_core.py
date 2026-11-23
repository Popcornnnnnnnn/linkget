"""Tests for agent-facing CLI features: --non-interactive, --json, inspect, read --stdout, read show/wait."""
import contextlib
import io
import json
import os
from pathlib import Path
from subprocess import CompletedProcess
import sys
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, batch, cli, engines, inspect_url, jobs, read_store, read_tasks, runtime, toutiao
from linkget.media import DownloadResult


class AgentCoreTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for obj, attr, val in (
            (accounts, "directory", self.root / "settings/sessions"),
            (cli.tempfile, "tempdir", str(self.root)),
            (cli.sys.stdin, "isatty", False),
            (batch, "SITE_INTERVAL", 0),
        ):
            patcher = patch.object(obj, attr, val) if attr in {"tempdir", "SITE_INTERVAL"} else patch.object(obj, attr, return_value=val)
            patcher.start()
            self.addCleanup(patcher.stop)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_non_interactive_and_env_prevent_prompts_even_on_tty(self):
        with patch.object(sys.stdin, "isatty", return_value=True), \
                patch("builtins.input", side_effect=AssertionError("should not prompt")), \
                patch.object(sys.stdin, "read", side_effect=AssertionError("should not block on tty stdin.read")):
            code, out, err = self.run_cli("--non-interactive", "--browser", "none")
            self.assertEqual(code, 1)
            self.assertIn("link is required", out + err)

            with patch.dict(os.environ, {"LINKGET_NON_INTERACTIVE": "1"}):
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as err_exit:
                    cli.main(["batch", "--folder", str(self.root / "dl")])
                self.assertEqual(err_exit.exception.code, 2)

                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as read_exit:
                    cli.main(["read", "batch"])
                self.assertEqual(read_exit.exception.code, 2)

                self.assertIsNone(cli.first_run(type("Args", (), {"browser": "auto"})()))
                self.assertFalse(cli.recover_login_interactive(type("Args", (), {"browser": "auto", "profile": None})(), "youtube.com", "login"))

        self.assertEqual(runtime.snapshot_modes(), (False, False, False))

    def test_json_commands_sites_config_doctor_tasks_compare_logout_and_errors(self):
        # sites --json
        code, out, err = self.run_cli("sites", "--json")
        self.assertEqual(code, 0, err)
        self.assertEqual(err, "")
        sites_data = json.loads(out)
        self.assertEqual(sites_data["schema_version"], 1)
        self.assertEqual(sites_data["command"], "sites")
        self.assertTrue(sites_data["ok"])
        names = {row["name"] for row in sites_data["sites"]}
        self.assertIn("YouTube Music", names)
        self.assertIn("SoundCloud", names)

        # config --json is read-only when no flags modify it
        code, out, err = self.run_cli("config", "--json")
        self.assertEqual(code, 0, err)
        self.assertFalse((self.root / "settings/preferences.json").exists())
        cfg = json.loads(out)
        self.assertEqual(cfg["command"], "config")
        self.assertFalse(cfg["changed"])

        dl = self.root / "dl"
        code, out, err = self.run_cli("config", "--folder", str(dl), "--json")
        self.assertEqual(code, 0, err)
        cfg2 = json.loads(out)
        self.assertTrue(cfg2["changed"])
        self.assertEqual(cfg2["destination"], {"mode": "folder", "path": str(dl.resolve())})

        # doctor --json
        with patch.object(cli, "find_tool", side_effect=lambda name: None if name == "deno" else f"/opt/bin/{name}"), \
                patch.object(cli.subprocess, "run", return_value=CompletedProcess([], 0, "tool 1.2.3\n", "")):
            code, out, err = self.run_cli("doctor", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        doc = json.loads(out)
        self.assertEqual(doc["command"], "doctor")
        self.assertFalse(doc["ok"])
        self.assertIn("deno", doc["missing_tools"])

        # tasks --json and cleanup preview
        journal = jobs.Journal.create(["https://example.invalid/post?token=secret123"], type("Args", (), {"folder": str(dl)})())
        code, out, _ = self.run_cli("tasks", "--json")
        self.assertEqual(code, 0)
        tasks_data = json.loads(out)
        self.assertEqual(tasks_data["command"], "tasks")
        self.assertEqual(tasks_data["tasks"][0]["id"], journal.identifier)
        self.assertNotIn("secret123", out)

        # compare --json
        r1 = self.root / "r1.json"
        r2 = self.root / "r2.json"
        payload = {"schema": 1, "items": [{"reference": "r", "status": "completed", "files": [{"sha256": "a", "kind": "audio", "codec": "aac"}]}]}
        r1.write_text(json.dumps(payload))
        r2.write_text(json.dumps(payload))
        code, out, _ = self.run_cli("compare", str(r1), str(r2), "--json")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["rows"][0]["same_bytes"])

        # logout --json
        code, out, _ = self.run_cli("logout", "all", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["target"], "all")

        # structured JSON error redacts secrets
        code, out, err = self.run_cli("recover", "20260101T000000-000000000000", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        err_json = json.loads(out)
        self.assertFalse(err_json["ok"])
        self.assertEqual(err_json["command"], "recover")
        self.assertIn("category", err_json["error"])

    def test_download_batch_retry_recover_json_output(self):
        out_dir = self.root / "out"
        audio_dir = self.root / "audio"

        def fake_pixiv(url, folder, cookies):
            (folder / "art.jpg").write_bytes(b"img-bytes")

        def fake_ytdlp(command, **kwargs):
            if "--dump-single-json" in command:
                return CompletedProcess(command, 0, json.dumps({"id": "sc9", "title": "Track", "uploader": "Artist", "acodec": "mp3"}) + "\n", "")
            tmpl = Path(command[command.index("-o") + 1]).parent
            (tmpl / "soundcloud_sc9.mp3").write_bytes(b"mp3data")
            return CompletedProcess(command, 0)

        def fake_prepare_audio(files, live_pairs, meta_map, default_meta, url, site, out_p, tools, target_format=None):
            out_p.mkdir(parents=True, exist_ok=True)
            f = out_p / "Artist - Track.mp3"
            f.write_bytes(b"tagged-mp3")
            return [f], []

        def fake_inspect(files, variants=None):
            return [{"file": p.name, "kind": "audio" if p.suffix == ".mp3" else "image", "source": "engine_selected", "probe_ok": True, "sha256": jobs.digest(p)} for p in files]

        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(cli, "download_pixiv", side_effect=fake_pixiv), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect):
            code, out, err = self.run_cli("https://www.pixiv.net/artworks/456", "--folder", str(out_dir), "--browser", "none", "--json")
        self.assertEqual(code, 0, out + err)
        self.assertEqual(err, "")
        dl_json = json.loads(out)
        self.assertTrue(dl_json["ok"])
        self.assertEqual(dl_json["command"], "download")
        self.assertEqual(dl_json["saved_count"], 1)
        self.assertEqual(dl_json["saved_files"], [str((out_dir / "art.jpg").resolve())])

        # batch --json with one failure + one success, then retry & recover --json
        attempts = {"count": 0}

        def flaky_ytdlp(command, **kwargs):
            if "--dump-single-json" not in command:
                attempts["count"] += 1
                if attempts["count"] == 1:
                    return CompletedProcess(command, 1, "", "HTTP Error 503: Unavailable")
            return fake_ytdlp(command, **kwargs)

        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(cli, "download_pixiv", side_effect=fake_pixiv), \
                patch.object(engines, "run_process", side_effect=flaky_ytdlp), \
                patch.object(cli, "run_process", side_effect=flaky_ytdlp), \
                patch.object(cli, "prepare_audio", side_effect=fake_prepare_audio), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect):
            code, out, err = self.run_cli("batch", "https://www.pixiv.net/artworks/457", "https://soundcloud.com/a/b", "--folder", str(audio_dir), "--browser", "none", "--json")
        self.assertEqual(code, 1)
        self.assertEqual(err, "")
        b_json = json.loads(out)
        self.assertEqual(b_json["command"], "batch")
        self.assertEqual(b_json["completed_count"], 1)
        self.assertEqual(b_json["failed_count"], 1)
        self.assertIn("retryable", b_json["items"][1]["error"])
        self.assertIsInstance(b_json["items"][1]["error"]["retryable"], bool)
        task_id = b_json["task_id"]

        # write_report failure emits a single JSON error object (not two)
        bad_report = self.root / "missing-dir" / "report.json"
        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(cli, "download_pixiv", side_effect=fake_pixiv), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect):
            code, out, _ = self.run_cli("https://www.pixiv.net/artworks/458", "--folder", str(out_dir), "--report", str(bad_report), "--browser", "none", "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])

        # Failed save -> retry stages, then recover --json
        with patch.object(cli, "find_tool", return_value="tool"), \
                patch.object(engines, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "run_process", side_effect=fake_ytdlp), \
                patch.object(cli, "prepare_audio", side_effect=fake_prepare_audio), \
                patch.object(cli, "inspect_files", side_effect=fake_inspect), \
                patch.object(cli, "save_folder", side_effect=OSError("disk full")):
            code, out, _ = self.run_cli("retry", task_id, "--json")
        self.assertEqual(code, 1)
        retry_json = json.loads(out)
        self.assertEqual(retry_json["command"], "retry")
        self.assertFalse(retry_json["ok"])

        with patch.object(cli, "inspect_files", side_effect=fake_inspect), \
                patch("linkget.recovery.inspect_files", side_effect=fake_inspect):
            code, out, err = self.run_cli("recover", "--json")
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out)["tasks"][0]["task_id"], task_id)

            code, out, err = self.run_cli("recover", task_id, "--json")
        self.assertEqual(code, 0, out + err)
        rec_json = json.loads(out)
        self.assertTrue(rec_json["ok"])
        self.assertEqual(rec_json["command"], "recover")
        self.assertEqual(rec_json["items"][0]["status"], "completed")
        self.assertTrue((audio_dir / "Artist - Track.mp3").is_file())

    def test_inspect_command_offline_local_short_links_and_ytdlp_metadata(self):
        # 1) Offline --no-network inspect creates zero journals
        with patch.object(accounts, "directory", return_value=self.root / "settings/sessions"):
            code, out, err = self.run_cli("inspect", "https://on.soundcloud.com/abc", "--no-network", "--json")
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        self.assertTrue(data["ok"])
        self.assertTrue(data["short_link_unresolved"])
        self.assertTrue(data["audio_only"])
        self.assertEqual(data["engine"], "yt-dlp")
        self.assertFalse((self.root / "settings/jobs").exists())

        for short_url, expected_engine in (
            ("https://b23.tv/abc123", "yt-dlp"),
            ("https://xhslink.com/o/abc123", "xiaohongshu"),
            ("https://vm.tiktok.com/ZMr123/", "gallery-dl"),
        ):
            code, out, err = self.run_cli("inspect", short_url, "--no-network", "--json")
            self.assertEqual(code, 0, err)
            short_data = json.loads(out)
            self.assertTrue(short_data["ok"])
            self.assertEqual(short_data["engine"], expected_engine)

        # Public article host scope & token validation
        for bad_article in (
            "https://www.raspberrypi.com/news/some-post/",
            "http://zhuanlan.zhihu.com/p/12345",
            "https://user:topsecret123@zhuanlan.zhihu.com/p/12345?token=leak456",
        ):
            code, out, _ = self.run_cli("inspect", bad_article, "--no-network", "--json")
            self.assertEqual(code, 1)
            bad_payload = json.loads(out)
            self.assertFalse(bad_payload["ok"])
            self.assertNotIn("topsecret123", out)
            self.assertNotIn("leak456", out)

        # Local file: probed, missing ffprobe, corrupt ffprobe failure, and symlink rejection
        sample = self.root / "clip.mp3"
        sample.write_bytes(b"id3")
        with patch.object(cli, "find_tool", return_value="tool"), \
                patch("linkget.inspect_url.read_asr.probe", return_value=(12.5, True)):
            code, out, _ = self.run_cli("inspect", str(sample), "--no-network", "--json")
        self.assertEqual(code, 0)
        local_data = json.loads(out)
        self.assertEqual(local_data["kind"], "local_file")
        self.assertEqual(local_data["probe_status"], "probed_local")
        self.assertEqual(local_data["metadata"]["duration"], 12.5)

        with patch.object(cli, "find_tool", return_value=None):
            code, out, _ = self.run_cli("inspect", str(sample), "--no-network", "--json")
        self.assertEqual(code, 0)
        missing_data = json.loads(out)
        self.assertTrue(missing_data["ok"])
        self.assertEqual(missing_data["probe_status"], "missing_tool")
        self.assertIn("ffprobe", missing_data["tools_missing"])

        with patch.object(cli, "find_tool", return_value="tool"), \
                patch("linkget.inspect_url.read_asr.probe", side_effect=RuntimeError("corrupt stream")):
            code, out, _ = self.run_cli("inspect", str(sample), "--no-network", "--json")
        self.assertEqual(code, 1)
        failed_local = json.loads(out)
        self.assertFalse(failed_local["ok"])
        self.assertEqual(failed_local["probe_status"], "failed")
        self.assertIn("corrupt stream", failed_local["error"]["message"])

        sym = self.root / "link.mp3"
        sym.symlink_to(sample)
        code, out, _ = self.run_cli("inspect", str(sym), "--no-network", "--json")
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(out)["ok"])

        # Playlist rejected cleanly
        code, out, _ = self.run_cli("inspect", "https://soundcloud.com/artist/sets/album", "--no-network", "--json")
        self.assertEqual(code, 1)
        rej = json.loads(out)
        self.assertFalse(rej["ok"])
        self.assertFalse(rej["supported"])

        # Live mocked yt-dlp metadata inspection
        info = {
            "title": "Song <Live>",
            "artist": "Band - Topic",
            "duration": 185,
            "filesize_approx": 4_200_000,
            "acodec": "mp4a.40.2",
            "subtitles": {"en": [{"url": "https://x"}]},
            "automatic_captions": {"zh-Hans": [{"url": "https://y"}]},
            "thumbnail": "https://cdn.example/c.jpg",
        }
        with patch.object(cli, "find_tool", return_value="yt-dlp"), \
                patch("linkget.inspect_url.run_process", return_value=CompletedProcess([], 0, json.dumps(info) + "\n", "")):
            code, out, err = self.run_cli("inspect", "https://music.youtube.com/watch?v=BaW_jenozKc", "--browser", "none", "--json")
        self.assertEqual(code, 0, err)
        live = json.loads(out)
        self.assertEqual(live["probe_status"], "probed_metadata")
        self.assertEqual(live["metadata"]["title"], "Song <Live>")
        self.assertEqual(live["metadata"]["duration"], 185)
        self.assertEqual(live["metadata"]["subtitles_available"], ["en", "zh-Hans"])

        # Focused help
        code, out, _ = self.run_cli("inspect", "--help")
        self.assertEqual(code, 0)
        self.assertIn("URL_OR_PATH", out)
        self.assertIn("--no-network", out)

    def test_read_stdout_json_background_show_and_wait(self):
        article = {
            "kind": "article",
            "title": "Agent Guide",
            "text": "Line one\nLine two",
            "content_complete": True,
            "content_basis": "scoped_page_dom",
        }
        # 1) --stdout (md / txt / json) with zero status clutter on stdout
        for idx, (fmt, expected) in enumerate((("md", "# Agent Guide"), ("txt", "Line one\nLine two"), ("json", '"content_basis": "scoped_page_dom"'))):
            folder = self.root / f"read-stdout-{idx}"
            with patch.object(toutiao, "read_browser", return_value=article), \
                    patch.object(toutiao, "validate_page", return_value=article), \
                    patch("linkget.rendered_page.selected_backend", return_value="chrome"), \
                    patch("linkget.rendered_page.selection", return_value={"browser": "chrome", "source": "test", "supported": True, "enabled": True}):
                code, out, err = self.run_cli("read", "https://www.toutiao.com/article/7000000000000000001/", "--folder", str(folder), "--stdout", "--stdout-format", fmt)
            self.assertEqual(code, 0, out + err)
            self.assertIn(expected, out)
            self.assertNotIn("content_ready", out)

        # 2) --foreground --json
        fg_folder = self.root / "read-fg-json"
        with patch.object(toutiao, "read_browser", return_value=article), \
                patch.object(toutiao, "validate_page", return_value=article), \
                patch("linkget.rendered_page.selected_backend", return_value="chrome"), \
                patch("linkget.rendered_page.selection", return_value={"browser": "chrome", "source": "test", "supported": True, "enabled": True}):
            code, out, err = self.run_cli("read", "https://www.toutiao.com/article/7000000000000000002/", "--folder", str(fg_folder), "--foreground", "--json")
        self.assertEqual(code, 0, err)
        fg_json = json.loads(out)
        self.assertTrue(fg_json["ok"])
        self.assertEqual(fg_json["command"], "read")
        self.assertEqual(fg_json["mode"], "foreground")
        self.assertEqual(fg_json["result"]["title"], "Agent Guide")

        # 3) read show by folder, --format txt, --json, and symlink refusal
        code, out, _ = self.run_cli("read", "show", str(fg_folder), "--format", "txt")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "Line one\nLine two")
        code, out, _ = self.run_cli("read", "show", str(fg_folder), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["title"], "Agent Guide")

        sym_folder = self.root / "read-sym"
        sym_folder.symlink_to(fg_folder)
        code, out, _ = self.run_cli("read", "show", str(sym_folder), "--json")
        self.assertEqual(code, 1)
        self.assertIn("symbolic link", json.loads(out)["error"]["message"])

        # 4) Background read --json submission + wait (complete & timeout 124) + status --json + show TASK
        with patch.object(read_tasks.subprocess, "Popen") as popen:
            popen.return_value.pid = 424242
            code, out, _ = self.run_cli("read", "https://www.toutiao.com/article/7000000000000000003/", "--folder", str(self.root / "bg-out"), "--json")
        self.assertEqual(code, 0)
        sub = json.loads(out)
        self.assertEqual(sub["mode"], "background")
        task_id = sub["task_id"]

        # Wait times out cleanly (124) when active & still running
        bg_out = self.root / "bg-out"
        bg_out.mkdir(exist_ok=True)
        read_store.write_json(bg_out / "content.json", {**article, "schema_version": read_store.SCHEMA})
        with patch.object(read_tasks, "active", return_value=True):
            code, out, _ = self.run_cli("read", "wait", task_id, "--timeout", "0.2", "--interval", "0.1", "--json")
        self.assertEqual(code, 124)
        self.assertTrue(json.loads(out)["timed_out"])

        # Mark completed -> wait succeeds and show by task ID returns saved content
        read_tasks.update(task_id, status="completed", phase="completed", outputs=[str(bg_out.resolve())])
        code, out, _ = self.run_cli("read", "wait", task_id, "--timeout", "1", "--json")
        self.assertEqual(code, 0)
        wait_data = json.loads(out)
        self.assertTrue(wait_data["ok"])
        self.assertFalse(wait_data["timed_out"])
        self.assertEqual(wait_data["content_path"], str((bg_out / "content.json").resolve()))

        code, out, _ = self.run_cli("read", "show", task_id)
        self.assertEqual(code, 0)
        self.assertIn("Line one\nLine two", out)

        # 5) read --foreground --json and read batch --foreground --json emit JSON error when cli.download fails
        def failing_download(d_args, *rest, **kw):
            d_args._last_failure = (engines.LoginRequired("cookies needed"), "login")
            return 1

        with patch.object(cli, "download", side_effect=failing_download):
            code, out, _ = self.run_cli("read", "https://www.douyin.com/video/7000000000000000004", "--folder", str(self.root / "read-fail"), "--foreground", "--json")
        self.assertEqual(code, 1)
        read_err = json.loads(out)
        self.assertFalse(read_err["ok"])
        self.assertEqual(read_err["command"], "read")
        self.assertEqual(read_err["error"]["category"], "login_required")

        with patch.object(cli, "download", side_effect=failing_download):
            code, out, _ = self.run_cli("read", "batch", "https://www.douyin.com/video/7000000000000000005", "--folder", str(self.root / "read-batch-fail"), "--foreground", "--json")
        self.assertEqual(code, 1)
        rb_err = json.loads(out)
        self.assertFalse(rb_err["ok"])
        self.assertEqual(rb_err["items"][0]["error"]["category"], "login_required")

        # 6) read show skips translate/summarize file outputs when target is omitted
        with patch.object(read_tasks.subprocess, "Popen") as popen:
            popen.return_value.pid = 424243
            self.run_cli("read", "https://www.toutiao.com/article/7000000000000000003/", "--folder", str(self.root / "ignored-submit"), "--json")
        # Create a newer completed translate task whose output is a markdown file
        ai_out = bg_out / "translation.md"
        ai_out.write_text("# Translated\n")
        tr_id = "99991231T235959-abcdef123456"
        tr_path = read_tasks.paths(tr_id)[0]
        read_store.write_json(tr_path, {"schema": 1, "id": tr_id, "kind": "translate", "argv": ["translate"], "status": "completed", "outputs": [str(ai_out.resolve())]})
        code, out, _ = self.run_cli("read", "show", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["title"], "Agent Guide")


if __name__ == "__main__":
    unittest.main()
