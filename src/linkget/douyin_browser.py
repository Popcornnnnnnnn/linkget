"""Read media through the already enabled connection to daily Chrome."""
import json
from pathlib import Path
import re
import subprocess


def load_item(path, url):
    """Accept a connector's content-only snapshot without reading a browser."""
    path = Path(path).expanduser()
    if path.stat().st_size > 8_000_000:
        raise ValueError("Douyin page data exceeds the 8 MB limit.")
    item = json.loads(path.read_text())
    post_id = url.rstrip('/').rsplit('/', 1)[-1]
    if (not isinstance(item, dict) or str(item.get('aweme_id')) != post_id
            or set(item) - {'aweme_id', 'images', 'video', 'desc'}
            or not (item.get('images') or item.get('video'))):
        raise ValueError("Expected content-only page data for this exact Douyin post.")
    if item.get('images') and (not isinstance(item['images'], list)
                              or any(not isinstance(image, dict) for image in item['images'])):
        raise ValueError("Douyin page data contains invalid images.")
    if item.get('video') and not isinstance(item['video'], dict):
        raise ValueError("Douyin page data contains an invalid video.")
    return item


def read_item(url):
    script = Path(__file__).with_suffix(".applescript")
    javascript = Path(__file__).with_suffix(".js").read_text()
    post_id = url.rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"https://www\.douyin\.com/(?:video|note)/\d+", url):
        raise ValueError("Expected a single Douyin post.")
    expression = ("location.hostname === 'www.douyin.com' && location.pathname.endsWith('/" + post_id
                  + "') ? JSON.stringify((" + javascript + ")()) : 'null'")
    try:
        result = subprocess.run(["/usr/bin/osascript", str(script), url, expression],
                                capture_output=True, text=True, timeout=50)
    except subprocess.TimeoutExpired:
        raise RuntimeError("Douyin browser access timed out; nothing has been imported.") from None
    if result.returncode:
        error = result.stderr.strip()
        if "JavaScript from Apple Events" in error:
            raise RuntimeError('Chrome blocks webpage scripting. Enable View → Developer → Allow JavaScript from Apple Events, then retry.')
        raise RuntimeError("Douyin browser access failed: " + (error or "Chrome is unavailable."))
    try:
        item = json.loads(result.stdout)
        if (not isinstance(item, dict) or not (item.get("images") or item.get("video"))
                or str(item.get("aweme_id", post_id)) != post_id):
            raise ValueError()
        return {"aweme_id": post_id, **item}
    except (ValueError, AttributeError):
        raise RuntimeError("Chrome returned media for an unexpected post; nothing has been imported.") from None
