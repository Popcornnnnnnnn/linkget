"""Shared batch context and coordinated interaction, without CLI dependencies."""
from contextlib import nullcontext
import threading
import os
import signal
import subprocess
import time

_batch = threading.local()

def batch_lock(name):
    state = getattr(_batch, "state", None)
    return state[name] if state else nullcontext()



def check_batch_cancelled():
    state = getattr(_batch, "state", None)
    if state and state["cancelled"].is_set():
        raise KeyboardInterrupt


def cancellation_check():
    """Capture the parent's cancellation event for adapter download workers."""
    state = getattr(_batch, "state", None)
    def check():
        if state and state["cancelled"].is_set():
            raise KeyboardInterrupt
    return check



def batch_interaction(callback):
    """Run browser/login work on the batch coordinator, one request at a time."""
    state = getattr(_batch, "state", None)
    if not state:
        return callback()
    request = {"callback": callback, "item": _batch.item, "done": threading.Event()}
    state["interactions"].put(request)
    while not request["done"].wait(0.1):
        check_batch_cancelled()
    if "error" in request:
        raise request["error"]
    return request["result"]


def run_process(command, timeout=None, **kwargs):
    """Cancel a batch engine and its ffmpeg children, retaining partial files."""
    if not getattr(_batch, "state", None):
        return subprocess.run(command, timeout=timeout, **kwargs)
    capture = kwargs.pop("capture_output", False)
    if capture:
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    check_batch_cancelled()
    process = subprocess.Popen(command, start_new_session=True, **kwargs)
    started = time.monotonic()
    try:
        while True:
            check_batch_cancelled()
            if timeout is not None and time.monotonic() - started >= timeout:
                raise subprocess.TimeoutExpired(command, timeout)
            try:
                output, error = process.communicate(timeout=0.1)
                check_batch_cancelled()
                return subprocess.CompletedProcess(command, process.returncode, output, error)
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        # The engine may already have exited while a child still holds a pipe.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
        raise
