"""Prefer Weibo's unmarked image rendition; retain originals if unavailable."""

from http.client import HTTPException
import json
import shutil
import subprocess
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def clean_url(url):
    parsed = urlsplit(url)
    parts = parsed.path.split("/")
    if (parsed.scheme != "https" or not (parsed.hostname or "").endswith(".sinaimg.cn")
            or parsed.username or parsed.password or parsed.port not in {None, 443}
            or len(parts) != 3 or not parts[2].lower().endswith((".jpg", ".jpeg"))):
        return None
    return parsed._replace(path="/oslarge/" + parts[2], query="", fragment="").geturl()


def jpeg_size(data):
    """Read JPEG dimensions so error placeholders cannot replace a real image."""
    if not data.startswith(b"\xff\xd8"):
        return None
    index = 2
    while index + 4 <= len(data):
        if data[index] != 255:
            return None
        while index < len(data) and data[index] == 255:
            index += 1
        if index >= len(data):
            return None
        marker = data[index]
        index += 1
        if marker in {0xD9, 0xDA}:
            return None
        length = int.from_bytes(data[index:index + 2], "big")
        if length < 2 or index + length > len(data):
            return None
        if marker in {0xC0, 0xC1, 0xC2} and length >= 7:
            return (int.from_bytes(data[index + 5:index + 7], "big"),
                    int.from_bytes(data[index + 3:index + 5], "big"))
        index += length
    return None


def watermark_difference(original, marked, alternate, width, height):
    """Conservative pixel comparison, not a general watermark detector.

    Compare equal-size renditions, then require the actual large image to
    resemble the marked rendition at the changed pixels. Distributed changes
    (different resizing/compression) must not cause a quality downgrade.
    """
    count = width * height
    if not count or any(len(pixels) != count for pixels in (original, marked, alternate)):
        return None
    rows = []
    advantage = 0
    for index, (native, reference, candidate) in enumerate(zip(original, marked, alternate)):
        if abs(reference - candidate) >= 32:
            rows.append(index // width)
            advantage += abs(native - candidate) - abs(native - reference)
    changed = len(rows)
    if changed < 64:
        return False
    if changed > count * 0.03:
        return None
    # Ninety percent of the strong differences must form a short horizontal
    # band. This also supports centered marks, without assuming a corner.
    trim = changed // 20
    if rows[-1 - trim] - rows[trim] + 1 > max(32, min(96, height // 10)):
        return None
    if advantage < changed * 12:
        return None
    return True


def compare_sources(original, marked, alternate, ffmpeg):
    size = jpeg_size(alternate)
    native_size = jpeg_size(original)
    if (not ffmpeg or not size or not native_size or jpeg_size(marked) != size
            or size[0] * size[1] > 8_000_000
            or abs(native_size[0] / native_size[1] - size[0] / size[1]) > 0.01):
        return None

    def decode(data):
        result = subprocess.run([
            ffmpeg, "-nostdin", "-v", "error", "-threads", "1", "-noautorotate",
            "-i", "pipe:0", "-vf", f"scale={size[0]}:{size[1]}:flags=lanczos",
            "-frames:v", "1", "-threads", "1", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1",
        ], input=data, capture_output=True, timeout=12)
        if result.returncode or len(result.stdout) != size[0] * size[1]:
            raise ValueError("Image comparison failed")
        return result.stdout

    return watermark_difference(decode(original), decode(marked), decode(alternate), *size)


def fetch_image(url):
    with urlopen(Request(url, headers={"Referer": "https://weibo.com/", "User-Agent": "Mozilla/5.0"}), timeout=12) as response:
        data = response.read(32_000_001)
        expected = response.headers.get("Content-Length")
    size = jpeg_size(data)
    if (len(data) > 32_000_000 or not size or min(size) < 32
            or not data.endswith(b"\xff\xd9") or (expected and len(data) != int(expected))):
        raise ValueError("Invalid image rendition")
    return data


def prefer_clean_images(directory, ffmpeg=None):
    selected, resolution_limited, retained, unchanged = 0, 0, 0, 0
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    images = {path for path in directory.iterdir() if path.suffix.lower() in {".jpg", ".jpeg"}}
    seen = set()
    for metadata in directory.glob("*.json"):
        partial = None
        try:
            source = metadata.with_suffix("")
            if not source.is_file() or source.suffix.lower() not in {".jpg", ".jpeg"}:
                continue
            seen.add(source)
            info = json.loads(metadata.read_text())
            target = clean_url(info.get("url", ""))
            if not target:
                retained += 1
                continue
            if not ffmpeg:
                retained += 1
                continue
            data = fetch_image(target)
            original = source.read_bytes()
            if original == data:
                unchanged += 1
                continue
            reference = fetch_image(target.replace("/oslarge/", "/mw690/", 1))
            decision = compare_sources(original, reference, data, ffmpeg)
            if decision is not True:
                if decision is False:
                    unchanged += 1
                else:
                    retained += 1
                continue
            size, original_size = jpeg_size(data), jpeg_size(original)
            # Removing a mark must never trade away image detail. A smaller
            # rendition is diagnostic only, even when its watermark is gone.
            if size[0] < original_size[0] or size[1] < original_size[1]:
                resolution_limited += 1
                continue
            # Keep a distinct name so an older marked copy is not deduplicated.
            destination = source.with_name(source.stem + "_preferred" + source.suffix)
            partial = destination.with_suffix(destination.suffix + ".part")
            partial.write_bytes(data)
            partial.replace(destination)
            source.unlink()
            selected += 1
        except (OSError, URLError, HTTPException, ValueError, AttributeError, subprocess.SubprocessError):
            retained += 1
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
    retained += len(images - seen)
    notes = []
    if selected:
        notes.append(f"Weibo: selected alternate images: {selected}; original resolution preserved. Embedded marks may remain.")
    if resolution_limited:
        notes.append(f"Weibo: kept original resolution for {resolution_limited} image(s); watermark removal unavailable at this resolution.")
    if unchanged:
        notes.append(f"Weibo: kept original quality for {unchanged} image(s); no added watermark difference detected.")
    if retained:
        notes.append(f"Weibo: could not confirm a watermark improvement for {retained} image(s); kept original quality. These copies may contain watermarks." + (" Install ffmpeg to enable image comparison; run linkget doctor for setup help." if not ffmpeg else ""))
    if any(path.suffix.lower() in {".mp4", ".mov"} for path in directory.iterdir()):
        notes.append("Weibo: kept best available video; embedded watermarks may remain.")
    return notes
