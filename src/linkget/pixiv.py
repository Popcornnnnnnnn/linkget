"""Download original static pages of one Pixiv artwork through its web API."""

from http.cookiejar import MozillaCookieJar
from http.client import HTTPException
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

if __package__:
    from .runtime import cancellation_check
    from .media import DownloadResult
else:
    from runtime import cancellation_check
    from media import DownloadResult


class BrowserRequired(RuntimeError):
    pass


def download(url, directory, cookie_path=None):
    check_cancelled = cancellation_check()
    check_cancelled()
    match = re.fullmatch(r"/(?:en/)?artworks/(\d+)/?", urlsplit(url).path)
    if not match:
        raise ValueError("Use a single Pixiv artwork link, not a profile or collection.")
    artwork_id = match[1]
    jar = MozillaCookieJar(str(cookie_path) if cookie_path else None)
    if cookie_path:
        jar.load(ignore_discard=True)
    opener = build_opener(HTTPCookieProcessor(jar))

    def request(target):
        return opener.open(Request(target, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.pixiv.net/",
        }), timeout=20)

    def artwork_data(suffix=""):
        with request(f"https://www.pixiv.net/ajax/illust/{artwork_id}{suffix}") as response:
            data = json.loads(response.read(8_000_000))
        if not isinstance(data, dict):
            raise RuntimeError("Pixiv returned unreadable artwork information.")
        if data.get("error"):
            message = str(data.get("message", "")).lower()
            if any(word in message for word in ("login", "log in", "sign in", "ログイン", "登录")):
                raise BrowserRequired("Pixiv requires a browser login to access this artwork.")
            raise RuntimeError("This Pixiv artwork is unavailable or restricted for this request.")
        return data.get("body")

    try:
        info = artwork_data()
        if not isinstance(info, dict) or not isinstance(info.get("pageCount"), int):
            raise RuntimeError("Pixiv returned unreadable artwork information.")
        if info.get("illustType") == 2:
            raise RuntimeError("Pixiv Ugoira animation is not supported. Try a static artwork.")
        pages = artwork_data("/pages")
        if not isinstance(pages, list) or not pages or len(pages) != info["pageCount"]:
            raise RuntimeError("Pixiv did not return all artwork pages; no partial gallery was saved.")
        targets = []
        for page in pages:
            target = page.get("urls", {}).get("original") if isinstance(page, dict) else None
            parsed = urlsplit(target or "")
            if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".pximg.net"):
                raise RuntimeError("Pixiv did not expose original images for every page.")
            targets.append(target)
        variants = {}
        for index, target in enumerate(targets):
            with request(target) as response:
                head = response.read(64)
                if head.startswith(b"\xff\xd8\xff"):
                    extension = ".jpg"
                elif head.startswith(b"\x89PNG\r\n\x1a\n"):
                    extension = ".png"
                elif head.startswith((b"GIF87a", b"GIF89a")):
                    extension = ".gif"
                elif head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                    extension = ".webp"
                else:
                    raise RuntimeError("Pixiv returned a non-image response for an original page.")
                path = directory / f"pixiv_{artwork_id}_p{index}{extension}"
                partial = path.with_suffix(extension + ".part")
                count = len(head)
                with partial.open("xb") as output:
                    output.write(head)
                    while chunk := response.read(1024 * 1024):
                        check_cancelled()
                        count += len(chunk)
                        output.write(chunk)
                expected = response.headers.get("Content-Length")
                if expected and count != int(expected):
                    raise RuntimeError("Pixiv download was incomplete; partial files have been kept.")
                partial.rename(path)
                variants[path.name] = "web_original"
        return DownloadResult(expected_count=len(pages), variants=variants)
    except HTTPError as error:
        if error.code in {401, 403} and not cookie_path:
            raise BrowserRequired("Pixiv denied this request. Try a browser login if the artwork is accessible to your account.") from None
        raise RuntimeError(f"Pixiv request failed (HTTP {error.code}). Check access to the artwork.") from None
    except (URLError, OSError, HTTPException):
        raise RuntimeError("Pixiv download interrupted. Check your connection and retry.") from None
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("Pixiv returned unreadable artwork or image information.") from None
