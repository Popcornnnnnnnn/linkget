"""Download the media exposed by Douyin's web detail or mobile sharing page."""

from http.cookiejar import MozillaCookieJar
from http.client import HTTPException
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

if __package__:
    from .links import MOBILE_UA
else:
    from links import MOBILE_UA


def share_item(html, post_id):
    match = re.search(r"window\._ROUTER_DATA\s*=\s*", html)
    if not match:
        return None
    try:
        data = json.JSONDecoder().raw_decode(html[match.end():])[0]
        for page in data.get("loaderData", {}).values():
            if not isinstance(page, dict):
                continue
            info = page.get("videoInfoRes") or {}
            for item in info.get("item_list") or []:
                if str(item.get("aweme_id")) == post_id:
                    return item
    except (ValueError, AttributeError, TypeError):
        pass
    return None


def media_urls(item, prefer_clean=True):
    """Prefer known clean variants; optionally accept exposed native media."""
    images = item.get("images") or (item.get("image_post_info") or {}).get("images")
    if images:
        media = []
        for image in images:
            urls = image.get("url_list") or (image.get("display_image") or {}).get("url_list") or []
            if not urls:
                raise RuntimeError("Douyin returned an incomplete image set; nothing has been imported.")
            clean = None
            for candidate in urls:
                parsed = urlsplit(candidate)
                template = unquote(parsed.path).partition("~")[2].split(":", 1)[0]
                if (parsed.scheme == "https" and (parsed.hostname or "").endswith(".douyinpic.com")
                        and template in {"tplv-dy-aweme-images", "tplv-dy-lqen-new"}):
                    clean = candidate
                    break
            if clean is None:
                if prefer_clean:
                    raise RuntimeError("No verified watermark-free Douyin image source. Refusing watermarked or unknown variants; nothing has been imported.")
                clean = urls[0]
            media.append(("image", clean))
        return media
    urls = (item.get("video", {}).get("play_addr") or {}).get("url_list") or []
    if not urls:
        raise RuntimeError("Douyin did not return downloadable photos or video.")
    url = urlsplit(urls[0])
    # The mobile share page points at the watermarked distribution variant.
    # Keep the same video ID and query when requesting its ordinary playback variant.
    if prefer_clean and url.hostname == "aweme.snssdk.com" and url.path == "/aweme/v1/playwm/":
        url = url._replace(path="/aweme/v1/play/")
    known_playback = url.hostname == "aweme.snssdk.com" and url.path == "/aweme/v1/play/"
    if prefer_clean and not known_playback and item.get("video", {}).get("has_watermark") is not False:
        raise RuntimeError("No verified watermark-free Douyin video source. Refusing watermarked or unknown variants; nothing has been imported.")
    return [("video", url.geturl())]


def media_extension(head, kind):
    if kind == "video" and len(head) >= 12 and head[4:8] == b"ftyp":
        return ".mp4"
    if kind == "image":
        if head.startswith(b"\xff\xd8\xff"):
            return ".jpg"
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return ".png"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return ".webp"
    raise RuntimeError("Douyin returned an unsupported media format or a verification page; nothing has been imported.")


def download(url, directory, cookie_path=None):
    post_id = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    jar = MozillaCookieJar(str(cookie_path) if cookie_path else None)
    if cookie_path:
        jar.load(ignore_discard=True)
    opener = build_opener(HTTPCookieProcessor(jar))

    def request(target, referer):
        if urlsplit(target).scheme not in {"http", "https"}:
            raise RuntimeError("Douyin returned an unsupported media URL.")
        return opener.open(Request(target, headers={"User-Agent": MOBILE_UA, "Referer": referer}), timeout=20)

    try:
        item = fallback_item = None
        if cookie_path:
            # Browser cookies remain scoped to douyin.com; never copy them to iesdouyin.com or a CDN.
            try:
                with request(f"https://www.douyin.com/aweme/v1/web/aweme/detail/?aweme_id={post_id}", "https://www.douyin.com/") as response:
                    detail = json.loads(response.read(8_000_000)).get("aweme_detail")
                if isinstance(detail, dict) and str(detail.get("aweme_id")) == post_id:
                    media_urls(detail, prefer_clean=False)
                    fallback_item = detail
                    media_urls(detail)  # Try the public page for a cleaner source when needed.
                    item = detail
            except (HTTPError, ValueError, AttributeError, RuntimeError):
                pass
        share_url = f"https://www.iesdouyin.com/share/video/{post_id}/"
        if item is None:
            try:
                had_visitor = any(c.name == "ttwid" and "iesdouyin.com" in c.domain for c in jar)
                with request(share_url, "https://www.iesdouyin.com/") as response:
                    item = share_item(response.read(8_000_000).decode("utf-8", "replace"), post_id)
                # Repeat only when the first response newly issues a visitor cookie but omits media.
                if item is None and not had_visitor and any(c.name == "ttwid" and "iesdouyin.com" in c.domain for c in jar):
                    with request(share_url, "https://www.iesdouyin.com/") as response:
                        item = share_item(response.read(8_000_000).decode("utf-8", "replace"), post_id)
            except (URLError, OSError, HTTPException) as error:
                if isinstance(error, HTTPError):
                    error.close()
                if fallback_item is None:
                    raise
            if item is None:
                item = fallback_item
        if item is None:
            raise RuntimeError("Douyin did not expose this post's media. Open the post in the selected browser, complete any verification, then retry. The post may also be unavailable; this does not prove your login expired.")
        fallback_used = False
        try:
            media = media_urls(item)
        except RuntimeError:
            media = media_urls(item, prefer_clean=False)
            fallback_used = True
        for index, (kind, media_url) in enumerate(media, 1):
            try:
                response = request(media_url, share_url)
            except HTTPError as error:
                error.close()
                if error.code not in {403, 404, 410}:
                    raise
                fallback = media_urls(item, prefer_clean=False)[index - 1][1]
                if kind == "image":
                    images = item.get("images") or item["image_post_info"]["images"]
                    image = images[index - 1]
                    candidates = (image.get("download_url_list") or []) + (image.get("url_list") or (image.get("display_image") or {}).get("url_list") or [])
                    fallback = next((candidate for candidate in candidates if candidate != media_url), fallback)
                if fallback == media_url:
                    raise
                response = request(fallback, share_url)
                media_url = fallback
                fallback_used = True
            with response:
                head = response.read(64)
                extension = media_extension(head, kind)
                # Photos deduplicates by filename: distinguish this playback variant
                # from an older watermarked download without replacing either file.
                playback = urlsplit(media_url)
                variant = "_clean" if kind == "video" and playback.hostname == "aweme.snssdk.com" and playback.path == "/aweme/v1/play/" else ""
                path = directory / f"douyin_{post_id}_{index:03d}{variant}{extension}"
                partial = path.with_suffix(extension + ".part")
                count = len(head)
                with partial.open("xb") as output:
                    output.write(head)
                    while chunk := response.read(1024 * 1024):
                        count += len(chunk)
                        output.write(chunk)
                expected = response.headers.get("Content-Length")
                if expected and count != int(expected):
                    raise RuntimeError("Douyin media download was incomplete; partial files have been kept.")
                partial.rename(path)
        return (["Douyin: clean source unavailable; kept the exposed media, which may contain watermarks."] if fallback_used else [])
    except HTTPError as error:
        raise RuntimeError(f"Douyin request failed (HTTP {error.code}). Open the post in your browser and check access or verification, then retry.") from None
    except (URLError, OSError, HTTPException):
        raise RuntimeError("Douyin download interrupted. Check your connection and retry; partial files have been kept.") from None
