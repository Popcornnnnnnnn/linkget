"""Cancellation of real local processes; no network or desktop access."""
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest

from linkget import runtime


class CancellationTests(unittest.TestCase):
    def test_cancel_terminates_engine_group_and_preserves_partial_download(self):
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "partial.part"
            state = {"cancelled": threading.Event()}
            result = []
            script = "import pathlib,time; pathlib.Path(__import__('sys').argv[1]).write_bytes(b'partial'); time.sleep(30)"
            def work():
                runtime._batch.state = state
                try:
                    runtime.run_process([sys.executable, "-c", script, str(partial)], capture_output=True, text=True)
                except KeyboardInterrupt:
                    result.append("cancelled")
                finally:
                    del runtime._batch.state
            worker = threading.Thread(target=work)
            worker.start()
            try:
                deadline = time.monotonic() + 3
                while not partial.exists() and time.monotonic() < deadline:
                    threading.Event().wait(.01)
                self.assertTrue(partial.exists())
                state["cancelled"].set()
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertEqual(result, ["cancelled"])
                self.assertEqual(partial.read_bytes(), b"partial")
            finally:
                state["cancelled"].set()
                worker.join(3)

    def test_timeout_is_still_reported_as_timeout(self):
        runtime._batch.state = {"cancelled": threading.Event()}
        try:
            with self.assertRaises(subprocess.TimeoutExpired):
                runtime.run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=.1, capture_output=True)
        finally:
            del runtime._batch.state

    def test_cancel_reaps_children_even_after_the_parent_has_exited(self):
        with tempfile.TemporaryDirectory() as directory:
            pid_file = Path(directory) / "child.pid"
            state = {"cancelled": threading.Event()}
            cancelled = threading.Event()
            script = "import subprocess,sys,pathlib; child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); pathlib.Path(sys.argv[1]).write_text(str(child.pid))"
            def work():
                runtime._batch.state = state
                try:
                    runtime.run_process([sys.executable, "-c", script, str(pid_file)], capture_output=True)
                except KeyboardInterrupt:
                    cancelled.set()
                finally:
                    del runtime._batch.state
            worker = threading.Thread(target=work)
            worker.start()
            try:
                deadline = time.monotonic() + 3
                while not pid_file.exists() and time.monotonic() < deadline:
                    threading.Event().wait(.01)
                self.assertTrue(pid_file.exists())
                child = int(pid_file.read_text())
                threading.Event().wait(.05)
                state["cancelled"].set()
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertTrue(cancelled.is_set())
                state_text = subprocess.run(["ps", "-o", "stat=", "-p", str(child)], capture_output=True, text=True).stdout.strip()
                self.assertTrue(not state_text or state_text.startswith("Z"), "A cancelled engine child remained active")
            finally:
                state["cancelled"].set()
                worker.join(3)

    def test_capture_output_drains_large_pipes_without_deadlock(self):
        runtime._batch.state = {"cancelled": threading.Event()}
        try:
            result = runtime.run_process([sys.executable, "-c", "print('x'*200000)"], capture_output=True, text=True, timeout=3)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(len(result.stdout.strip()), 200000)
        finally:
            del runtime._batch.state
