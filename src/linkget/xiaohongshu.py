"""Download one Xiaohongshu note's original images or video."""

from http.cookiejar import MozillaCookieJar
from http.client import HTTPException
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

if __package__:
    from .links import MOBILE_UA
else:
    from links import MOBILE_UA

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"


class BrowserRequired(RuntimeError):
    pass


def initial_state(html):
    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*", html)
    if not match:
        return {}
    # SSR includes undefined and empty Maps in unrelated stores. Normalize only
    # these known literals, preserving strings and never executing page code.
    source = re.sub(r'"(?:\\.|[^"\\])*"|\bundefined\b|\bnew\s+Map\s*\(\s*\[\s*\]\s*\)',
                    lambda token: (token[0] if token[0].startswith('"') else
                                   "null" if token[0] == "undefined" else "{}"), html[match.end():])
    try:
        value = json.JSONDecoder().raw_decode(source)[0]
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def media_url(value):
    parsed = urlsplit(value or "")
    host = parsed.hostname or ""
    if (parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}
            or not (host.endswith(".xhscdn.com") or host.endswith(".xiaohongshu.com"))):
        raise RuntimeError("Xiaohongshu returned an unsupported media source; nothing has been imported.")
    return parsed._replace(scheme="https").geturl()


def original_url(key, kind):
    if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", key)
            or any(part in {"", ".", ".."} for part in key.split("/"))):
        raise RuntimeError(f"Xiaohongshu did not expose an original {kind} source. Refusing a potentially watermarked display version.")
    host = "sns-video-bd.xhscdn.com" if kind == "video" else "sns-img-bd.xhscdn.com"
    return "https://" + host + "/" + key


def image_key(item):
    if item.get("fileId"):
        return item["fileId"]
    variants = {entry.get("imageScene"): entry.get("url") for entry in item.get("infoList", [])}
    url = item.get("urlDefault") or variants.get("WB_DFT") or variants.get("H5_DTL") or item.get("url")
    if not url:
        return None
    parsed = urlsplit(media_url(url))
    path = parsed.path.lstrip("/").partition("!")[0]
    if parsed.hostname == "sns-img-bd.xhscdn.com":
        return path
    if (parsed.hostname or "").startswith("sns-webpic-"):
        # Display URLs prepend an expiry timestamp and signature to the file ID.
        match = re.fullmatch(r"\d{8,14}/[a-fA-F0-9]{32}/(.+)", path)
        if match:
            return match[1]
    return None


def note_media(note):
    if note.get("type") == "video":
        key = ((note.get("video") or {}).get("consumer") or {}).get("originVideoKey")
        return [("video", original_url(key, "video"))]
    images = note.get("imageList") or []
    if not images:
        raise RuntimeError("Xiaohongshu returned no photos in this note.")
    media = []
    for item in images:
        key = image_key(item)
        if not key:
            raise RuntimeError("Xiaohongshu returned an incomplete original image set; refusing display versions. Nothing has been imported.")
        media.append(("image", original_url(key, "image")))
    return media


def extension(head, kind):
    if len(head) >= 12 and head[4:8] == b"ftyp":
        if kind == "video":
            return ".mp4"
        brands = {head[8:12]} | {head[i:i + 4] for i in range(16, min(len(head), int.from_bytes(head[:4], "big")), 4)}
        if brands & {b"avif", b"avis"}:
            return ".avif"
        if brands & {b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"hevm", b"hevs"}:
            return ".heic"
        if brands & {b"mif1", b"msf1"}:
            return ".heif"
    if kind == "image":
        if head.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if head.startswith((b"GIF87a", b"GIF89a")):
            return ".gif"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return ".webp"
    raise RuntimeError("Xiaohongshu returned an unsupported format or a verification page; nothing has been imported.")


def download(url, directory, cookie_path=None):
    note_id = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    jar = MozillaCookieJar(str(cookie_path) if cookie_path else None)
    if cookie_path:
        jar.load(ignore_discard=True)
    opener = build_opener(HTTPCookieProcessor(jar))

    def request(target, user_agent=USER_AGENT):
        return opener.open(Request(target, headers={"User-Agent": user_agent,
                                                    "Referer": "https://www.xiaohongshu.com/"}), timeout=20)
    try:
        with request(url) as response:
            state = initial_state(response.read(8_000_000).decode("utf-8", "replace"))
        notes = state.get("note", {}).get("noteDetailMap", {})
        note = (notes.get(note_id) or {}).get("note")
        if not isinstance(note, dict) or note.get("noteId", note_id) != note_id:
            # App shares can expose their media on the mobile page while the
            # desktop page redirects an anonymous request to /login.
            mobile_url = urlsplit(url)._replace(path="/discovery/item/" + note_id).geturl()
            with request(mobile_url, MOBILE_UA) as response:
                mobile = initial_state(response.read(8_000_000).decode("utf-8", "replace"))
            candidate = mobile.get("noteData", {}).get("data", {}).get("noteData")
            if isinstance(candidate, dict) and candidate.get("noteId") == note_id:
                note = candidate
        if not isinstance(note, dict) or note.get("noteId", note_id) != note_id:
            if state.get("user", {}).get("loggedIn") is False:
                raise BrowserRequired("This Xiaohongshu note was not available to the public request. Try your browser login or a fresh share link, keeping its access token.")
            raise RuntimeError("Xiaohongshu did not expose this note. Copy a fresh share link from the post and complete any website verification, then retry.")
        media = note_media(note)  # Validate the whole image set before downloading.
        for index, (kind, target) in enumerate(media, 1):
            with request(target) as response:
                head = response.read(64)
                suffix = extension(head, kind)
                path = directory / f"xiaohongshu_{note_id}_{index:03d}_original{suffix}"
                partial = path.with_suffix(suffix + ".part")
                count = len(head)
                with partial.open("xb") as output:
                    output.write(head)
                    while chunk := response.read(1024 * 1024):
                        count += len(chunk)
                        output.write(chunk)
                expected = response.headers.get("Content-Length")
                if expected and count != int(expected):
                    raise RuntimeError("Xiaohongshu download was incomplete; partial files have been kept.")
                partial.rename(path)
    except HTTPError as error:
        error.close()
        raise RuntimeError(f"Xiaohongshu request failed (HTTP {error.code}). The original may be unavailable; no display-version fallback was used. Open a fresh share link in your browser and check access or verification.") from None
    except (URLError, OSError, HTTPException):
        raise RuntimeError("Xiaohongshu download interrupted. Check your connection and retry; partial files have been kept.") from None
