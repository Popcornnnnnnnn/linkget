"""Terminal presentation shared by single and batch downloads."""
from contextlib import contextmanager, ExitStack
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import unicodedata
from urllib.parse import urlsplit
if __package__:
    from .runtime import _batch, batch_lock, check_batch_cancelled, json_mode, non_interactive, quiet_status
    from .media import AUDIO, MEDIA, VIDEO
else:
    from runtime import _batch, batch_lock, check_batch_cancelled, json_mode, non_interactive, quiet_status
    from media import AUDIO, MEDIA, VIDEO

STATUS_LABEL_WIDTH = 23


def terminal_styles(stream):
    return (not quiet_status() and not non_interactive() and stream.isatty()
            and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb")



def emphasis(value, stream=None):
    stream = stream if stream is not None else sys.stdout
    return f"\033[1m{value}\033[0m" if terminal_styles(stream) else str(value)



def status(label, value, color="0", stream=None, *, label_width=STATUS_LABEL_WIDTH):
    """Aligned terminal output; redirected output remains plain text."""
    if json_mode():
        return
    stream = stream if stream is not None else sys.stdout
    if quiet_status() and stream is sys.stdout:
        return
    item = getattr(_batch, "item", None)
    state = getattr(_batch, "state", None)
    panel = state.get("panel") if state else None
    if panel and item:
        with state["output"]:
            if panel.capture(item[0], label, value, color, stream):
                return
    prefix = f"[{item[0]}/{item[1]}] " if item else ""
    color = color if terminal_styles(stream) else None
    with batch_lock("output"):
        print_field(label, value, label_width=label_width, indent="  " + prefix, color=color, stream=stream)


def print_field(label, value, *, label_width, indent="", color=None, stream=None):
    field = pad_cell(label, label_width)
    continuation = " " * (cell_width(indent + field) + 1)
    if color is not None:
        field = f"\033[{color}m{field}\033[0m"
    lines = str(value).splitlines() or [""]
    print(f"{indent}{field} {lines[0]}", file=stream, flush=True)
    for line in lines[1:]:
        print(continuation + line, file=stream, flush=True)



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
            if ".temp." in name or Path(name).suffix.lower() not in MEDIA | AUDIO:
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
    audios = sum(path.suffix.lower() in AUDIO for path in files)
    photos = len(files) - videos - audios
    parts = []
    for count, singular, plural in (
        (photos, "photo", "photos"),
        (videos, "video", "videos"),
        (audios, "audio file", "audio files"),
    ):
        if count:
            parts.append(f"{count} {singular if count == 1 else plural}")
    return ", ".join(parts) + " saved"


def cell_text(value):
    """Keep source text on one row, without terminal controls."""
    value = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07]*(?:\x07|\x1b\\)", "", str(value))
    return "".join(" " if c.isspace() else c for c in value
                   if c.isspace() or not unicodedata.category(c).startswith("C"))


def cell_width(value):
    """Measure CJK and combining text in terminal cells, with ambiguous characters narrow."""
    return sum(
        2 if unicodedata.east_asian_width(c) in "WF" else 1
        for c in value
        if not unicodedata.combining(c) and unicodedata.category(c) not in {"Mn", "Me"}
    )


def pad_cell(value, width):
    return value + " " * max(0, width - cell_width(value))


def wrap_cells(value, width):
    """Wrap prose on word boundaries without breaking paths or other tokens."""
    lines = []
    line = ""
    used = 0
    for word in value.split():
        size = cell_width(word)
        if line and used + 1 + size > width:
            lines.append(line)
            line, used = "", 0
        if line:
            line += " "
            used += 1
        line += word
        used += size
    return "\n".join([*lines, line])


def fit_cell(value, width, pad=False):
    value = cell_text(value)
    ellipsis = "…"
    if width <= 0:
        return ""
    if cell_width(value) > width:
        clipped, used = [], 0
        limit = width - cell_width(ellipsis)
        for c in value:
            size = cell_width(c)
            if used + size > limit:
                break
            clipped.append(c)
            used += size
        value = "".join(clipped) + ellipsis
    return pad_cell(value, width) if pad else value


class BatchPanel:
    """One coordinator paints the rows; workers only update their state."""
    def __init__(self, pairs, destination, stream=None):
        self.stream = stream if stream is not None else sys.stdout
        self.destination = destination
        self.started = time.monotonic()
        self.finished = None
        self.suspended = False
        self.drawn = 0
        self.line_widths = []
        self.notices = []
        self.rows = {}
        for index, link in pairs:
            match = re.search(r"https?://[^\s]+", link)
            try:
                parsed = urlsplit(match[0]) if match else None
            except ValueError:
                parsed = None
            name = ((parsed.hostname or "Post") + " · " + (parsed.path.rstrip("/").rsplit("/", 1)[-1] or "Post")) if parsed else "Invalid link"
            self.rows[index] = {"name": name, "phase": "Queued", "started": None, "finished": None,
                                "directory": None, "summary": "", "media": "", "warning": "", "error": ""}

    def phase(self, index, label, directory=None):
        row = self.rows[index]
        if row["started"] is None:
            row["started"] = time.monotonic()
        row.update(phase=label, directory=directory)

    def capture(self, index, label, value, color, stream):
        if self.suspended:
            return False
        row = self.rows[index]
        value = str(value)
        if label == "Source" and "Fallback" not in value:
            row["name"] = value + " · " + row["name"].split(" · ", 1)[-1] if not row.get("title") else row["name"]
        elif label in {"Done", "Recovered"}:
            # Single-download logs carry their own elapsed time. The panel
            # keeps one item clock across phases, retries and recovery saves.
            value = re.sub(r" · \d+(?:\.\d+)?s$", "", value)
            self.complete(index, 0)
            row.update(phase="Done", summary=re.sub(r"^Item \d+ · ", "", value).replace(" saved", ""), directory=None)
        elif label == "Skipped":
            row["summary"] += " · " + value
        elif label == "Cancelled":
            self.complete(index, 130)
        elif color == "31" or label == "Stopped":
            row["error"] = cell_text(value)
        elif label not in {"Destination", "Quality", "Media count", "Cleanup", "Progress"} and value != "Working...":
            row["warning"] = label if label == "Files kept" else cell_text(value)
            self.notices.append((index, label, value, color, stream))
        if color == "31" or label in {"Stopped", "Cancelled"}:
            self.notices.append((index, label, value, color, stream))
        return True

    def media(self, index, files, metadata):
        row = self.rows[index]
        title = next((metadata.get(key) for key in ("title", "description", "caption") if isinstance(metadata.get(key), str) and metadata[key].strip()), None)
        if title:
            row.update(name=cell_text(title).strip(), title=True)
        formats = sorted({path.suffix.lstrip(".").upper() for path in files})
        if files:
            row["media"] = saved_description(files).removesuffix(" saved") + " · " + "/".join(formats)

    def complete(self, index, result):
        row = self.rows[index]
        if row["finished"] is None:
            row["finished"] = time.monotonic()
        row.update(phase="Done" if result == 0 else "Cancelled" if result == 130 else "Failed", directory=None)

    def lines(self, columns, height):
        now = time.monotonic()
        width = max(1, columns - 1)  # Leave the last cell unused: no autowrap.
        done = sum(row["phase"] == "Done" for row in self.rows.values())
        failed = sum(row["phase"] == "Failed" for row in self.rows.values())
        active = sum(row["phase"] not in {"Done", "Failed", "Cancelled", "Queued"} for row in self.rows.values())
        queued = sum(row["phase"] == "Queued" for row in self.rows.values())
        cancelled = sum(row["phase"] == "Cancelled" for row in self.rows.values())
        heading = f"Batch  {len(self.rows)} posts · {done} done · {active} active"
        if queued:
            heading += f" · {queued} queued"
        if failed:
            heading += f" · {failed} failed"
        if cancelled:
            heading += f" · {cancelled} cancelled"
        destination = "Photos" if self.destination == "Photos" else (Path(self.destination).name or self.destination) + "/"
        batch_end = self.finished if self.finished is not None else now
        heading += f" · {batch_end - self.started:.1f}s → {destination}"
        lines = [fit_cell(heading, width)]
        number_width = max(2, len(str(max(self.rows))))
        # Drop the title column first in narrow terminals; keep stage and result.
        name_width = min(32, max(12, width // 3)) if width >= 65 else 0
        # Share extra room between long phase labels and results on wide screens.
        phase_width = min(21, 13 + max(0, columns - 100) // 2)
        limit = max(0, height - 4)
        for index, row in list(self.rows.items())[:limit]:
            phase = row["phase"]
            detail = row["summary"] if phase == "Done" else row["error"] if phase == "Failed" else row["media"]
            if phase == "Waiting":
                detail = ("Photos import" if self.destination == "Photos" else "Folder save")
            if phase not in {"Done", "Failed", "Cancelled", "Queued"}:
                if row["directory"] is not None:
                    try:
                        detail = format_size(downloaded_bytes(row["directory"]))
                    except OSError:
                        pass
            if row["warning"]:
                detail += (" · " if detail else "") + "! " + row["warning"]
            marker = "✓" if phase == "Done" else "!" if phase == "Failed" else "·" if phase in {"Queued", "Waiting", "Cancelled"} else "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(now * 5) % 10]
            prefix = f"  {index:>{number_width}}  "
            if name_width:
                prefix += fit_cell(row["name"], name_width, pad=True) + "  "
            prefix += fit_cell(marker + " " + phase, phase_width, pad=True) + " "
            elapsed = ""
            if row["started"] is not None:
                end = row["finished"] if row["finished"] is not None else now
                elapsed = f"{end - row['started']:.1f}s"
            elapsed_width = cell_width(elapsed)
            show_elapsed = 0 < elapsed_width < width
            available = width - elapsed_width - 1 if show_elapsed else width
            prefix_width = cell_width(prefix)
            if prefix_width < available:
                line = prefix + fit_cell(detail, available - prefix_width, pad=show_elapsed)
            else:
                line = fit_cell(prefix.rstrip(), available, pad=show_elapsed)
            line = line + " " + elapsed if show_elapsed else line.rstrip()
            if terminal_styles(self.stream):
                color = "32" if phase == "Done" else "31" if phase == "Failed" else "33" if phase in {"Waiting", "Cancelled"} or row["warning"] else "0"
                line = f"\033[{color}m{line}\033[0m"
            lines.append(line)
        if len(self.rows) > limit:
            lines.append(fit_cell(f"  … {len(self.rows) - limit} more posts", width))
        return lines

    def erase(self, size=None):
        if self.drawn:
            size = size or shutil.get_terminal_size((80, 24))
            # A narrower terminal can reflow previously drawn rows.
            physical_rows = sum(max(1, (width + size.columns - 1) // size.columns) for width in self.line_widths)
            physical_rows = min(physical_rows, max(1, size.lines - 1))
            self.stream.write(f"\033[{physical_rows}A\r\033[J")
            self.drawn = 0

    def render(self):
        if self.suspended:
            return
        size = shutil.get_terminal_size((80, 24))
        self.erase(size)
        lines = self.lines(size.columns, size.lines)
        self.stream.write("\n".join(lines) + "\n")
        self.stream.flush()
        self.drawn = len(lines)
        self.line_widths = [cell_width(cell_text(line)) for line in lines]

    @contextmanager
    def interaction(self):
        self.erase()
        self.stream.flush()
        self.suspended = True
        try:
            yield
        finally:
            self.suspended = False
            self.render()

    def finish(self):
        if self.finished is None:
            self.finished = time.monotonic()
        self.render()
        self.drawn = 0  # Leave the final rows in scrollback.
        number_width = len(str(max(self.rows)))
        notices = [(f"[{index:>{number_width}}] {label}:", value, color, stream)
                   for index, label, value, color, stream in self.notices]
        label_width = max((cell_width(label) for label, *_ in notices), default=0)
        for label, value, color, stream in notices:
            color = color if terminal_styles(stream) else None
            print_field(label, value, label_width=label_width, indent="  ", color=color, stream=stream)


def batch_media(files, metadata=None):
    state = getattr(_batch, "state", None)
    if state and state.get("panel"):
        with state["output"]:
            state["panel"].media(_batch.item[0], files, metadata or {})


@contextmanager
def saving_activity(label):
    """Show Waiting before taking the serialized destination lock."""
    with ExitStack() as stack:
        if getattr(_batch, "state", None):
            with activity("Waiting"):
                stack.enter_context(batch_lock("saving"))
        with activity(label):
            yield



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
            if state.get("panel"):
                state["panel"].phase(key, label, media_dir)
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
    if quiet_status() or non_interactive() or not stream.isatty() or os.environ.get("TERM") == "dumb":
        status(label, "Working...")
        yield
        return

    stopped = threading.Event()
    started = time.monotonic()
    field = pad_cell(label, STATUS_LABEL_WIDTH)
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
