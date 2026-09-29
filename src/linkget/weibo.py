"""Prefer Weibo's unmarked image rendition; retain originals if unavailable."""

from http.client import HTTPException
import json
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


def prefer_clean_images(directory):
    clean, smaller, retained = 0, 0, 0
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
            with urlopen(Request(target, headers={"Referer": "https://weibo.com/", "User-Agent": "Mozilla/5.0"}), timeout=12) as response:
                data = response.read(32_000_001)
                expected = response.headers.get("Content-Length")
            size = jpeg_size(data)
            if (len(data) > 32_000_000 or not size or min(size) < 32
                    or not data.endswith(b"\xff\xd9") or (expected and len(data) != int(expected))):
                retained += 1
                continue
            original = jpeg_size(source.read_bytes())
            smaller += bool(original and size[0] * size[1] < original[0] * original[1])
            # Keep a distinct name so an older marked copy is not deduplicated.
            destination = source.with_name(source.stem + "_preferred" + source.suffix)
            partial = destination.with_suffix(destination.suffix + ".part")
            partial.write_bytes(data)
            partial.replace(destination)
            source.unlink()
            clean += 1
        except (OSError, URLError, HTTPException, ValueError, AttributeError):
            retained += 1
        finally:
            if partial is not None:
                partial.unlink(missing_ok=True)
            metadata.unlink(missing_ok=True)
    retained += len(images - seen)
    notes = []
    if clean:
        notes.append(f"Weibo: preferred clean-source images: {clean}" + (f"; lower resolution: {smaller}." if smaller else "."))
    if retained:
        notes.append(f"Weibo: clean source unavailable for {retained} image(s); kept best available copies, which may contain watermarks.")
    if any(path.suffix.lower() in {".mp4", ".mov"} for path in directory.iterdir()):
        notes.append("Weibo: kept best available video; embedded watermarks may remain.")
    return notes
