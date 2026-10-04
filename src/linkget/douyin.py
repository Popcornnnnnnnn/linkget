"""Download the media exposed by Douyin's web detail or mobile sharing page."""

from http.cookiejar import MozillaCookieJar
from http.client import HTTPException
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener
import concurrent.futures

if __package__:
    from .links import MOBILE_UA
    from .media import DownloadResult
else:
    from links import MOBILE_UA
    from media import DownloadResult


class MediaUnavailable(RuntimeError):
    """Post metadata was absent; this does not establish login expiry."""


class DownloadInterrupted(RuntimeError):
    """A failed transport can be retried using the browser's current media source."""


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


def share_bootstrap_pending(html, post_id):
    """Recognize the requested post's SSR shell before its media is populated."""
    match = re.search(r"window\._ROUTER_DATA\s*=\s*", html)
    if not match:
        return False
    try:
        data = json.JSONDecoder().raw_decode(html[match.end():])[0]
        return any(isinstance(page, dict) and str(page.get("itemId")) == post_id
                   and page.get("renderInSSR") == 1 and not page.get("videoInfoRes")
                   for page in data.get("loaderData", {}).values())
    except (ValueError, AttributeError, TypeError):
        return False


# The public playback endpoint clamps a request to the best gear the post
# actually has, so asking for 1080p never upscales and recovers detail that
# the share page's 720p default drops. Unsupported ratios fall back to it.
BEST_PLAYBACK_RATIO = "1080p"


def playback_url(url, best_ratio=False):
    """Point an snssdk play address at ordinary playback, optionally top gear."""
    parsed = urlsplit(url)
    if parsed.hostname != "aweme.snssdk.com" or parsed.path not in {"/aweme/v1/play/", "/aweme/v1/playwm/"}:
        return url
    # The mobile share page points at the watermarked distribution variant.
    # Keep the same video ID and query when requesting its ordinary playback variant.
    if parsed.path == "/aweme/v1/playwm/":
        parsed = parsed._replace(path="/aweme/v1/play/")
    if not best_ratio:
        return parsed.geturl()
    query = [(key, BEST_PLAYBACK_RATIO if key == "ratio" else value)
             for key, value in parse_qsl(parsed.query, keep_blank_values=True)]
    if not any(key == "ratio" for key, _ in query):
        query.append(("ratio", BEST_PLAYBACK_RATIO))
    return parsed._replace(query=urlencode(query)).geturl()


def known_playback(url):
    return urlsplit(url).path.rstrip("/").endswith("/aweme/v1/play")


def address_urls(address):
    return [url for url in (address or {}).get("url_list") or [] if isinstance(url, str) and url]


def video_candidates(item, prefer_clean=True):
    """Exposed video renditions as (quality, url), best quality first.

    Douyin lists its gears in video.bit_rate, each with its own play_addr;
    video.play_addr is only the default one. Rank by pixel count, then the
    declared bitrate and size, so the ordinary playback variant is not
    assumed to be the best available copy.
    """
    video = item.get("video") or {}
    candidates = []
    for entry in video.get("bit_rate") or []:
        if not isinstance(entry, dict):
            continue
        address = entry.get("play_addr") or {}
        quality = (int(address.get("width") or 0) * int(address.get("height") or 0),
                   int(entry.get("bit_rate") or 0), int(address.get("data_size") or 0))
        candidates += [(quality, playback_url(url) if prefer_clean else url) for url in address_urls(address)]
    for field in ("play_addr", "play_addr_h264", "play_addr_265"):
        address = video.get(field) or {}
        quality = (int(address.get("width") or 0) * int(address.get("height") or 0), 0,
                   int(address.get("data_size") or 0))
        # A default play_addr carries whatever ratio the API chose; ask the
        # endpoint for the best it will serve. Explicit gears keep their own.
        candidates += [(quality, playback_url(url, best_ratio=True) if prefer_clean else url)
                       for url in address_urls(address)]
    candidates.sort(key=lambda candidate: candidate[0], reverse=True)
    return candidates


def video_urls(item, prefer_clean=True):
    """Ordered unique video URLs, best quality first."""
    ordered, seen = [], set()
    for _, url in video_candidates(item, prefer_clean):
        if url not in seen:
            seen.add(url)
            ordered.append(url)
    return ordered


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
    urls = video_urls(item, prefer_clean)
    if not urls:
        raise RuntimeError("Douyin did not return downloadable photos or video.")
    url = urls[0]
    if prefer_clean and not known_playback(url) and (item.get("video") or {}).get("has_watermark") is not False:
        raise RuntimeError("No verified watermark-free Douyin video source. Refusing watermarked or unknown variants; nothing has been imported.")
    return [("video", url)]


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


def download(url, directory, cookie_path=None, *, browser_item=None):
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
        item, fallback_item = browser_item, None
        if item is not None and str(item.get("aweme_id")) != post_id:
            raise RuntimeError("Browser media belongs to a different Douyin post; nothing has been imported.")
        if cookie_path and item is None:
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
                # A cold request can first return the post's SSR shell, then issue
                # a visitor cookie on the next response, before exposing media.
                for _ in range(3):
                    had_visitor = any(c.name == "ttwid" and "iesdouyin.com" in c.domain for c in jar)
                    with request(share_url, "https://www.iesdouyin.com/") as response:
                        html = response.read(8_000_000).decode("utf-8", "replace")
                    item = share_item(html, post_id)
                    new_visitor = not had_visitor and any(c.name == "ttwid" and "iesdouyin.com" in c.domain for c in jar)
                    if item is not None or not (new_visitor or share_bootstrap_pending(html, post_id)):
                        break
            except (URLError, OSError, HTTPException) as error:
                if isinstance(error, HTTPError):
                    error.close()
                if fallback_item is None:
                    raise
            if item is None:
                item = fallback_item
        if item is None:
            raise MediaUnavailable("Douyin did not expose this post's media. Open the post in the selected browser, complete any verification, then retry. The post may also be unavailable; this does not prove your login expired.")

        fallback_used = False
        try:
            media = media_urls(item)
        except RuntimeError:
            media = media_urls(item, prefer_clean=False)
            fallback_used = True

        initial_fallback = fallback_used
        def _download_one(index, kind, media_url):
            local_fallback = False
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
                local_fallback = True
            with response:
                head = response.read(64)
                extension = media_extension(head, kind)
                # Photos deduplicates by filename: distinguish this playback variant
                # from an older watermarked download without replacing either file.
                variant = "_clean" if kind == "video" and known_playback(media_url) else ""
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
            return path.name, local_fallback or initial_fallback

        variants = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(_download_one, index, kind, media_url) for index, (kind, media_url) in enumerate(media, 1)]
            for future in concurrent.futures.as_completed(futures):
                name, fallback = future.result()
                variants[name] = "fallback" if fallback else "preferred_source"
                if fallback:
                    fallback_used = True

        return DownloadResult(["Douyin: clean source unavailable; kept the exposed media, which may contain watermarks."] if fallback_used else [], len(media), variants)
    except HTTPError as error:
        raise RuntimeError(f"Douyin request failed (HTTP {error.code}). Open the post in your browser and check access or verification, then retry.") from None
    except (URLError, OSError, HTTPException) as error:
        reason = error.reason if isinstance(error, URLError) else error
        detail = str(reason) or type(reason).__name__
        raise DownloadInterrupted(f"Douyin download failed: {detail}\nPartial files, if any, have been kept.") from None
