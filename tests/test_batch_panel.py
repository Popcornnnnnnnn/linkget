"""Offline terminal acceptance; no network, browser or real Photos access."""
import contextlib
import io
import os
from pathlib import Path
import re
import threading
import unittest
from unittest.mock import patch

from linkget import cli, output, batch
from linkget.media import DownloadResult
from linkget.runtime import _batch
import test_batch

URL = test_batch.URL


class Terminal(io.StringIO):
    def isatty(self):
        return True


class PanelTests(unittest.TestCase):
    def panel(self, stream=None):
        return output.BatchPanel([(1, "分享 https://v.douyin.com/abc/?private=secret"),
                                  (2, URL + "2")], "Photos", stream or Terminal())

    def test_source_text_is_clipped_by_cells_without_control_sequences(self):
        panel = self.panel()
        panel.media(1, [Path("image.webp")], {"description": "中文作品" * 30 + "\n\x1b[2J\x07"})
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            for width in (20, 40, 64, 80, 120):
                lines = panel.lines(width, 24)
                self.assertTrue(all(output.cell_width(line) < width for line in lines))
                self.assertTrue(all("\x1b" not in line and "\n" not in line for line in lines))
            self.assertIn("中文", "\n".join(panel.lines(80, 24)))
            self.assertNotIn("private", "\n".join(panel.lines(80, 24)))
        self.assertEqual(output.cell_width(output.fit_cell("e\u0301中文", 4)), 4)

    def test_malformed_url_does_not_abort_batch_panel_initialization(self):
        panel = output.BatchPanel([(3, "https://[broken"), (7, "no URL")], "Photos", Terminal())
        self.assertEqual(list(panel.rows), [3, 7])
        self.assertEqual(panel.rows[3]["name"], "Invalid link")

    def test_rows_fit_height_and_keep_original_retry_numbers(self):
        panel = output.BatchPanel([(n, URL + str(n)) for n in range(10, 30)], "Photos", Terminal())
        with patch.dict(os.environ, {"NO_COLOR": "1"}):
            lines = panel.lines(80, 8)
        self.assertLess(len(lines), 8)
        self.assertIn("10", lines[1])
        self.assertIn("16 more posts", lines[-1])

    def test_no_color_keeps_live_updates_and_resizing_changes_columns(self):
        stream = Terminal()
        panel = self.panel(stream)
        with patch.dict(os.environ, {"NO_COLOR": "1"}), patch.object(output.shutil, "get_terminal_size", side_effect=[os.terminal_size((100, 24)), os.terminal_size((40, 24))]):
            panel.render()
            panel.render()
        self.assertIn("\x1b[6A\r\x1b[J", stream.getvalue())
        self.assertNotRegex(stream.getvalue(), r"\x1b\[[0-9;]*m")
        last_frame = stream.getvalue().split("\x1b[J")[-1].splitlines()
        self.assertTrue(all(output.cell_width(line) < 40 for line in last_frame))

    def test_elapsed_is_continuous_across_stages_and_fixed_after_completion(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}), patch.object(output.time, "monotonic") as clock:
            clock.return_value = 100
            panel = self.panel()
            panel.phase(1, "Preparing")
            clock.return_value = 120
            panel.phase(1, "Downloading")
            self.assertTrue(panel.lines(100, 24)[1].endswith("20.0s"))
            panel.phase(1, "Waiting")
            self.assertTrue(panel.lines(100, 24)[1].endswith("20.0s"))
            clock.return_value = 125.5
            panel.phase(1, "Importing")
            self.assertTrue(panel.lines(100, 24)[1].endswith("25.5s"))
            clock.return_value = 130
            panel.capture(1, "Done", "1 photo saved · 1 KB · 999.0s", "32", panel.stream)
            clock.return_value = 135  # Receipt bookkeeping happens after Done.
            panel.complete(1, 0)
            panel.capture(1, "Skipped", "2 duplicates", "0", panel.stream)
            clock.return_value = 200
            row = panel.lines(100, 24)[1]
            self.assertTrue(row.endswith("30.0s"))
            self.assertIn("2 duplicates", row)
            self.assertNotIn("999.0s", row)
            self.assertEqual(len(re.findall(r"\d+\.\d+s", row)), 1)

    def test_queue_time_is_excluded_and_subsecond_time_does_not_round_to_a_second(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}), patch.object(output.time, "monotonic") as clock:
            clock.return_value = 100
            panel = self.panel()
            clock.return_value = 150
            self.assertNotRegex(panel.lines(100, 24)[2], r"\d+(?:\.\d+)?s$")
            panel.phase(2, "Preparing")
            clock.return_value = 150.6
            self.assertTrue(panel.lines(100, 24)[2].endswith("0.6s"))
            panel.phase(2, "Saving")
            self.assertTrue(panel.lines(100, 24)[2].endswith("0.6s"))

    def test_failed_and_cancelled_items_freeze_only_time_actually_processed(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}), patch.object(output.time, "monotonic") as clock:
            clock.return_value = 100
            panel = self.panel()
            panel.phase(1, "Downloading")
            clock.return_value = 105
            panel.complete(1, 1)
            panel.complete(2, 130)  # Cancelled in the queue: never started.
            clock.return_value = 120
            panel.complete(1, 1)  # A repeated final update must not extend time.
            self.assertTrue(panel.lines(100, 24)[1].endswith("5.0s"))
            self.assertNotRegex(panel.lines(100, 24)[2], r"\d+(?:\.\d+)?s$")
            panel = self.panel()
            panel.phase(1, "Downloading")
            clock.return_value = 123
            panel.capture(1, "Cancelled", "Download stopped", "33", panel.stream)
            clock.return_value = 126
            panel.complete(1, 130)
            clock.return_value = 150
            self.assertTrue(panel.lines(100, 24)[1].endswith("3.0s"))

    def test_recovered_result_retains_elapsed_column_even_with_long_details(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}), patch.object(output.time, "monotonic") as clock:
            clock.return_value = 10
            panel = self.panel()
            panel.phase(2, "Preparing")
            clock.return_value = 12
            panel.phase(2, "Importing")
            clock.return_value = 16
            panel.capture(2, "Recovered", "Item 2 · 10 saved · 5 duplicates", "0", panel.stream)
            panel.capture(2, "Files kept", "Long diagnostic " * 30, "33", panel.stream)
            panel.complete(2, 0)
            for width in (20, 40, 65, 100):
                row = panel.lines(width, 24)[2]
                self.assertTrue(row.endswith("6.0s"))
                self.assertLess(output.cell_width(row), width)

    def test_batch_timer_freezes_after_finish(self):
        with patch.dict(os.environ, {"NO_COLOR": "1"}), patch.object(output.time, "monotonic") as clock:
            clock.return_value = 10
            panel = self.panel()
            clock.return_value = 20
            panel.finish()
            heading = panel.lines(100, 24)[0]
            self.assertIn("10.0s", heading)
            clock.return_value = 90
            self.assertEqual(panel.lines(100, 24)[0], heading)


class TerminalBatchTests(unittest.TestCase):
    setUp = test_batch.BatchTests.setUp

    def run_terminal(self, links, download, *options, photos=None, stream=None):
        stream = stream if stream is not None else Terminal()
        errors = io.StringIO()
        destination = ["--photos"] if photos else ["--folder", str(self.root / "output")]
        with patch.dict(os.environ, {"TERM": "xterm-256color", "NO_COLOR": "1"}), patch.object(output.shutil, "get_terminal_size", return_value=os.terminal_size((100, 24))), patch.object(cli, "download_pixiv", side_effect=download), patch.object(cli, "import_photos", side_effect=photos), contextlib.redirect_stdout(stream), contextlib.redirect_stderr(errors):
            result = cli.main(["batch", *links, *destination, "--browser", "none", *options])
        return result, stream.getvalue(), errors.getvalue()

    def final_frame(self, text):
        return text.split("\x1b[J")[-1]

    @patch.object(batch, "site_key", side_effect=lambda link: link)
    def test_success_is_fixed_order_and_hides_repetitive_events(self, _site_key):
        barrier = threading.Barrier(4)

        def download(url, folder, cookies):
            barrier.wait(timeout=3)
            (folder / "image.webp").write_bytes(url.encode())
            return DownloadResult(expected_count=1, metadata={"description": "中文图文作品" * 10})

        result, out, err = self.run_terminal([URL + str(n) for n in range(1, 5)], download, "--jobs", "4")
        self.assertEqual((result, err), (0, ""))
        frame = self.final_frame(out)
        self.assertEqual(re.findall(r"^\s+(\d+)\s+.*✓ Done", frame, re.M), ["1", "2", "3", "4"])
        self.assertIn("中文图文作品", frame)
        self.assertIn("4 completed · 0 failed", frame)
        for label in ("Working...", "Destination", "Cleanup", "Quality", "Media count", "Progress"):
            self.assertNotIn(label, out)

    @patch.object(batch, "site_key", side_effect=lambda link: link)
    def test_photos_lock_waiting_is_visible_while_one_item_imports(self, _site_key):
        downloads = threading.Barrier(3)
        release = threading.Event()
        imported = threading.Lock()
        waiting_seen = threading.Event()

        class WatchingTerminal(Terminal):
            def write(self, value):
                if "Waiting" in value and "Importing" in value:
                    waiting_seen.set()
                    release.set()
                return super().write(value)

        def download(url, folder, cookies):
            downloads.wait(timeout=3)
            (folder / "image.jpg").write_bytes(url.encode())

        def save(files, date):
            self.assertTrue(imported.acquire(blocking=False))
            try:
                self.assertTrue(release.wait(3), "Waiting was never rendered")
                return files, 0
            finally:
                imported.release()

        result, out, err = self.run_terminal([URL + str(n) for n in range(3)], download, photos=save, stream=WatchingTerminal())
        self.assertEqual((result, err), (0, ""))
        self.assertTrue(waiting_seen.is_set())
        self.assertIn("Photos import", out)

    def test_failure_notices_are_printed_after_the_panel_with_retry(self):
        def download(url, folder, cookies):
            if url.endswith("2"):
                (folder / "partial.jpg.part").write_bytes(b"partial")
                raise RuntimeError("HTTP Error 403: Forbidden\nDetailed reason")
            (folder / "image.jpg").write_bytes(url.encode())

        result, out, err = self.run_terminal([URL + str(n) for n in (1, 2, 3)], download)
        self.assertEqual(result, 1)
        self.assertIn("! Failed", self.final_frame(out))
        self.assertIn("2 completed · 1 failed", out)
        self.assertIn("linkget retry", out)
        self.assertIn("[2] Access refused", err)
        self.assertIn("Detailed reason", err)
        self.assertIn("Files kept", err)
        self.assertNotIn("\x1b", err)
        self.assertTrue(list(self.root.glob("linkget-*/media/partial.jpg.part")))

    def test_cancel_marks_queued_items_and_keeps_partial_downloads(self):
        def download(url, folder, cookies):
            (folder / "partial.jpg.part").write_bytes(b"partial")
            raise KeyboardInterrupt

        result, out, err = self.run_terminal([URL + "1", URL + "2", URL + "3"], download, "--jobs", "1")
        self.assertEqual(result, 130)
        self.assertEqual(self.final_frame(out).count("· Cancelled"), 3)
        self.assertIn("0 active", self.final_frame(out))
        self.assertIn("linkget retry", out)
        self.assertIn("Files kept", err)

    def test_login_prompt_is_not_overwritten_when_panel_resumes(self):
        def download(args, setup, records):
            return 0 if cli.recover_login(args, "pixiv.net", "Login needed") else 1

        def prompt(*args):
            print("PROMPT remains in scrollback")
            cli.status("Next", "Connect this browser")
            return True

        with patch.object(cli, "download", side_effect=download), patch.object(cli, "recover_login_interactive", side_effect=prompt):
            result, out, err = self.run_terminal([URL + "1"], None)
        self.assertEqual((result, err), (0, ""))
        after_prompt = out.split("PROMPT remains in scrollback\n", 1)[1]
        self.assertTrue(after_prompt.startswith("  [1/1] Next"))
        self.assertIn("Batch", after_prompt)

    def test_coordinator_interrupt_acknowledges_stopping_and_releases_workers(self):
        started = threading.Event()

        def download(args, setup, records):
            started.set()
            self.assertTrue(_batch.state["cancelled"].wait(3))
            return 130

        def interrupt(*args, **kwargs):
            self.assertTrue(started.wait(3))
            raise KeyboardInterrupt

        with patch.object(cli, "download", side_effect=download), patch.object(batch, "wait", side_effect=interrupt):
            result, out, err = self.run_terminal([URL + "1", URL + "2"], None, "--jobs", "1")
        self.assertEqual((result, err), (130, ""))
        self.assertEqual(out.count("Stopping"), 1)
        self.assertEqual(self.final_frame(out).count("· Cancelled"), 2)

    def test_retry_keeps_failed_items_original_number(self):
        def download(url, folder, cookies):
            if url.endswith("2"):
                raise RuntimeError("HTTP Error 403: Forbidden")
            (folder / "image.jpg").write_bytes(url.encode())

        self.assertEqual(self.run_terminal([URL + "1", URL + "2"], download)[0], 1)
        stream = Terminal()

        def retry(url, folder, cookies):
            (folder / "second.jpg").write_bytes(b"second")

        with patch.dict(os.environ, {"TERM": "xterm", "NO_COLOR": "1"}), patch.object(cli, "download_pixiv", side_effect=retry), contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()):
            result = cli.main(["retry", "--browser", "none"])
        self.assertEqual(result, 0)
        self.assertEqual(re.findall(r"^\s+(\d+)\s+.*✓ Done", self.final_frame(stream.getvalue()), re.M), ["2"])

    def test_dumb_terminal_uses_plain_log(self):
        def download(url, folder, cookies):
            (folder / "image.jpg").write_bytes(b"image")

        stream = Terminal()
        with patch.dict(os.environ, {"TERM": "dumb"}), patch.object(cli, "download_pixiv", side_effect=download), contextlib.redirect_stdout(stream), contextlib.redirect_stderr(io.StringIO()):
            result = cli.main(["batch", URL + "1", "--folder", str(self.root / "output"), "--browser", "none"])
        self.assertEqual(result, 0)
        self.assertNotIn("\x1b", stream.getvalue())
        self.assertIn("Destination", stream.getvalue())
        self.assertIn("Working...", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
