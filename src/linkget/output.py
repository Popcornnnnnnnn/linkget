"""Terminal presentation shared by single and batch downloads."""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import sys
import threading
import time
if __package__:
    from .runtime import _batch, batch_lock, check_batch_cancelled
else:
    from runtime import _batch, batch_lock, check_batch_cancelled
VIDEO = {".mp4", ".mov", ".m4v"}
MEDIA = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif", ".tif", ".tiff"} | VIDEO

def terminal_styles(stream):
    return stream.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"



def emphasis(value, stream=None):
    stream = stream if stream is not None else sys.stdout
    return f"\033[1m{value}\033[0m" if terminal_styles(stream) else str(value)



def status(label, value, color="0", stream=None):
    """Aligned terminal output; redirected output remains plain text."""
    stream = stream if stream is not None else sys.stdout
    item = getattr(_batch, "item", None)
    prefix = f"[{item[0]}/{item[1]}] " if item else ""
    field = f"{label:<12}"
    if terminal_styles(stream):
        field = f"\033[{color}m{field}\033[0m"
    lines = str(value).splitlines() or [""]
    with batch_lock("output"):
        print(f"  {prefix}{field} {lines[0]}", file=stream, flush=True)
        for line in lines[1:]:
            print(" " * (15 + len(prefix)) + line, file=stream, flush=True)



def format_size(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000



def downloaded_bytes(directory):
    """Estimate bytes written, excluding sidecars and duplicate merge outputs."""
    sizes = {}
    with os.scandir(directory) as entries:
        for entry in entries:
            name = entry.name.removesuffix(".part")
            if ".temp." in name or Path(name).suffix.lower() not in MEDIA | {".m4a", ".aac", ".opus", ".webm"}:
                continue
            try:
                if entry.is_file():
                    sizes[name] = max(sizes.get(name, 0), entry.stat().st_size)
            except FileNotFoundError:
                # Downloaders rename completed parts and delete merged tracks.
                continue
    track_stems = {match[1] for name in sizes if (match := re.fullmatch(r"(.+)\.f[^.]+\.[^.]+", name))}
    return sum(size for name, size in sizes.items() if Path(name).stem not in track_stems)



def saved_description(files):
    videos = sum(path.suffix.lower() in VIDEO for path in files)
    photos = len(files) - videos
    parts = [f"{count} {kind}{'s' if count != 1 else ''}" for count, kind in ((photos, "photo"), (videos, "video")) if count]
    return ", ".join(parts) + " saved"



@contextmanager
def activity(label, media_dir=None):
    """Animate one temporary line while work runs, then clear it on every exit."""
    stream = sys.stdout
    if getattr(_batch, "state", None):
        check_batch_cancelled()
        state = _batch.state
        key = _batch.item[0]
        entry = {"phase": label, "started": time.monotonic(), "directory": media_dir}
        with state["output"]:
            state["progress"][key] = entry
        status(label, "Working...")
        try:
            yield
            if label not in {"Saving", "Importing"}:
                check_batch_cancelled()
        finally:
            with state["output"]:
                if state["progress"].get(key) is entry:
                    state["progress"].pop(key, None)
        return
    if not stream.isatty() or os.environ.get("TERM") == "dumb":
        status(label, "Working...")
        yield
        return

    stopped = threading.Event()
    started = time.monotonic()
    field = f"{label:<12}"
    if "NO_COLOR" not in os.environ:
        field = f"\033[36m{field}\033[0m"

    def animate():
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        index = 0
        size = ""
        while not stopped.is_set():
            elapsed = int(time.monotonic() - started)
            if media_dir is not None and index % 3 == 0:
                size = f"~{format_size(downloaded_bytes(media_dir))} · "
            stream.write(f"\r\033[2K  {field} {frames[index % len(frames)]} {size}{elapsed}s")
            stream.flush()
            index += 1
            stopped.wait(0.1)

    worker = threading.Thread(target=animate, name="linkget-progress", daemon=True)
    try:
        worker.start()
        yield
    finally:
        stopped.set()
        if worker.ident is not None:
            worker.join()
        stream.write("\r\033[2K")
        stream.flush()
