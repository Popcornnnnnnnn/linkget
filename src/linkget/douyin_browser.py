"""Read complete media through Neo or explicitly selected daily Chrome."""
import json
from pathlib import Path
import re
import subprocess


if __package__:
    from .failures import BrowserReadError
else:
    from failures import BrowserReadError


def load_item(path, url):
    """Accept a connector's content-only snapshot without reading a browser."""
    path = Path(path).expanduser()
    if path.stat().st_size > 8_000_000:
        raise ValueError("Douyin page data exceeds the 8 MB limit.")
    return _validated_item(json.loads(path.read_text()), url)


def _validated_item(item, url):
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


def read_item(url, backend='neo'):
    if not re.fullmatch(r"https://www\.douyin\.com/(?:video|note)/\d+", url):
        raise ValueError("Expected a single Douyin post.")
    if backend not in {'neo', 'chrome'}:
        raise ValueError('Douyin rendered reads require neo or explicitly selected chrome.')
    next_step = ('Open this post in Neo and check login, website verification and that all media loads. '
                 'Check the existing Neo MCP connection, then retry; daily Chrome is not used automatically.'
                 if backend == 'neo' else
                 'Open this post in Chrome and check that all media loads, including its full gallery. '
                 'Check Chrome webpage scripting and macOS Automation permissions, then retry.')
    try:
        return _read_item(url, backend)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise BrowserReadError(str(error), next_step) from error


def _read_item(url, backend):
    script = Path(__file__).with_suffix(".applescript")
    javascript = Path(__file__).with_suffix(".js").read_text()
    post_id = url.rstrip("/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"https://www\.douyin\.com/(?:video|note)/\d+", url):
        raise ValueError("Expected a single Douyin post.")
    if backend == 'neo':
        if __package__:
            from .neo_browser import read_dom
        else:
            from neo_browser import read_dom
        item = read_dom(url, javascript)
        if (not isinstance(item, dict) or str(item.get('aweme_id')) != post_id
                or not (item.get('images') or item.get('video'))
                or set(item) - {'aweme_id', 'images', 'video', 'desc', 'browser_controller'}):
            raise ValueError('Neo did not return complete media bound to this exact Douyin post.')
        return _validated_item({key: value for key, value in item.items() if key != 'browser_controller'}, url)
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
