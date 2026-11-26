"""Normalize one shared post, resolving only supported short-link hosts."""

import re
from concurrent.futures import Future, TimeoutError
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

if __package__:
    from .runtime import _batch, check_batch_cancelled
else:
    from runtime import _batch, check_batch_cancelled

MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", "v.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}
TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "www.tiktokv.com"}
XHS_SHORT_HOSTS = {"xhslink.com", "www.xhslink.com", "xhslink.cn", "www.xhslink.cn"}
XHS_HOSTS = {"xiaohongshu.com", "www.xiaohongshu.com"} | XHS_SHORT_HOSTS
WEIBO_HOSTS = {"weibo.com", "www.weibo.com", "m.weibo.cn", "weibo.cn", "video.weibo.com", "t.cn"}
BILIBILI_HOSTS = {"bilibili.com", "www.bilibili.com", "m.bilibili.com", "b23.tv"}
X_HOSTS = {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
SOUNDCLOUD_SHORT_HOSTS = {"on.soundcloud.com"}
SOUNDCLOUD_HOSTS = {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"} | SOUNDCLOUD_SHORT_HOSTS
SOUNDCLOUD_RESERVED_USERS = {
    "discover", "stream", "feed", "search", "stations", "likes", "reposts",
    "following", "followers", "tracks", "albums", "popular-tracks", "playlists",
    "charts", "you", "upload", "messages", "notifications", "settings", "people",
    "signin", "signup", "pages", "terms-of-use", "pro", "go", "jobs", "imprint",
    "mobile",
}
SOUNDCLOUD_RESERVED_TABS = {
    "sets", "tracks", "albums", "reposts", "likes", "popular-tracks",
    "following", "followers", "comments", "recommended", "spotlight",
}
SOUNDCLOUD_RESERVED_TOKENS = {"albums", "sets", "recommended", "comments", "reposts", "likes"}
BANDCAMP_RESERVED_HOSTS = {"bandcamp.com", "www.bandcamp.com", "daily.bandcamp.com"}


def single_video_target(parsed):
    """Return the site, domain, and canonical URL for a supported video."""
    host, path = parsed.hostname, parsed.path
    if host in {"vimeo.com", "www.vimeo.com", "player.vimeo.com"}:
        pattern = r"/video/([0-9]+)/?" if host == "player.vimeo.com" else r"/([0-9]+)/?"
        match = re.fullmatch(pattern, path)
        if match and "h" not in parse_qs(parsed.query, keep_blank_values=True):
            # yt-dlp's Vimeo page client requires login. Use the public player.
            return "Vimeo", "vimeo.com", f"https://player.vimeo.com/video/{match[1]}"
    if host in {"dailymotion.com", "www.dailymotion.com", "dai.ly"}:
        if host == "dai.ly":
            pattern = r"/(x[0-9a-z]+)/?"
        else:
            pattern = r"/(?:embed/)?video/(x[0-9a-z]+)(?:_[\w-]+)?/?"
        match = re.fullmatch(pattern, path)
        if match:
            return "Dailymotion", "dailymotion.com", f"https://www.dailymotion.com/video/{match[1]}"
    if host in {"pornhub.com", "www.pornhub.com", "m.pornhub.com"}:
        match = re.fullmatch(r"/embed/([a-z0-9]+)/?", path)
        keys = parse_qs(parsed.query, keep_blank_values=True).get("viewkey", [])
        if match:
            key = match[1]
        elif path in {"/view_video.php", "/video/show"} and len(keys) == 1 and re.fullmatch(r"[a-z0-9]+", keys[0]):
            key = keys[0]
        else:
            return None
        return "Pornhub", "pornhub.com", "https://www.pornhub.com/view_video.php?" + urlencode({"viewkey": key})
    return None


def soundcloud_track_parts(path):
    parts = [part for part in path.split("/") if part]
    if len(parts) not in {2, 3}:
        return None
    if parts[0].lower() in SOUNDCLOUD_RESERVED_USERS or parts[1].lower() in SOUNDCLOUD_RESERVED_TABS:
        return None
    if not re.fullmatch(r"[\w-]+", parts[0]) or not re.fullmatch(r"[\w-]+", parts[1]):
        return None
    if len(parts) == 3 and (parts[2].lower() in SOUNDCLOUD_RESERVED_TOKENS or not re.fullmatch(r"[\w-]+", parts[2])):
        return None
    return parts


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ResolutionCache:
    """Share exact short URLs in a batch, including concurrent requests.

    No cookies are used here. Query tokens stay in the key, and failures are
    removed so a later attempt can retry. The batch owns this memory only.
    """
    def __init__(self):
        self.lock = threading.Lock()
        self.pending = {}

    def resolve(self, url, callback):
        check_batch_cancelled()
        with self.lock:
            future = self.pending.get(url)
            owner = future is None
            if owner:
                future = self.pending[url] = Future()
        if owner:
            try:
                value = callback()
                check_batch_cancelled()
            except BaseException as error:
                with self.lock:
                    self.pending.pop(url, None)
                    future.set_exception(error)
                raise
            future.set_result(value)
        while True:
            check_batch_cancelled()
            try:
                return future.result(timeout=0.1)
            except TimeoutError:
                if future.done():
                    # Completion can race the timeout. Retrieve the result;
                    # this also propagates a resolver's own TimeoutError.
                    return future.result()


def canonical_post(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise ValueError("Enter a complete http/https post URL without credentials or a custom port.")
    if parsed.hostname in DOUYIN_HOSTS:
        match = re.fullmatch(r"/(?:share/)?(video|note|slides)/(\d+)/?", parsed.path)
        if match:
            kind = "video" if match[1] == "video" else "note"
            return f"https://www.douyin.com/{kind}/{match[2]}"
        ids = parse_qs(parsed.query).get("modal_id", [])
        if len(ids) == 1 and re.fullmatch(r"\d+", ids[0]):
            return "https://www.douyin.com/video/" + ids[0]
    elif parsed.hostname in TIKTOK_HOSTS:
        match = re.fullmatch(r"/(@[\w.-]+|share)/(photo|video)/(\d+)/?", parsed.path)
        if match:
            return f"https://www.tiktok.com/{match[1]}/{match[2]}/{match[3]}"
    elif parsed.hostname in XHS_HOSTS:
        match = re.fullmatch(r"/(?:explore|discovery/item)/([\da-f]{24})/?", parsed.path)
        if match:
            # The share token is needed for many posts. Do not strip it as tracking.
            return f"https://www.xiaohongshu.com/explore/{match[1]}" + ("?" + parsed.query if parsed.query else "")
    elif parsed.hostname in WEIBO_HOSTS:
        match = re.fullmatch(r"/tv/show/(\d+:[A-Za-z0-9]+)/?", parsed.path)
        if match:
            return f"https://weibo.com/tv/show/{match[1]}"
        ids = parse_qs(parsed.query).get("fid", [])
        if parsed.hostname == "video.weibo.com" and parsed.path == "/show" and len(ids) == 1 and re.fullmatch(r"\d+:[A-Za-z0-9]+", ids[0]):
            return f"https://weibo.com/tv/show/{ids[0]}"
        match = re.fullmatch(r"/(?:detail|status|\d+)/([A-Za-z0-9]+)/?", parsed.path)
        if match:
            return f"https://weibo.com/detail/{match[1]}"
    elif parsed.hostname in BILIBILI_HOSTS:
        match = re.fullmatch(r"/video/(BV[A-Za-z0-9]+)/?", parsed.path)
        if match:
            return f"https://www.bilibili.com/video/{match[1]}" + ("?" + parsed.query if parsed.query else "")
        match = re.fullmatch(r"/audio/(au\d+)/?", parsed.path)
        if match:
            return f"https://www.bilibili.com/audio/{match[1]}"
    elif parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        match = re.fullmatch(r"/(?:shorts/|live/|embed/)?([\w-]{11})/?", parsed.path)
        video_id = match[1] if match and (parsed.hostname == "youtu.be" or parsed.path.startswith(("/shorts/", "/live/", "/embed/"))) else None
        if parsed.path == "/watch":
            ids = parse_qs(parsed.query).get("v", [])
            if len(ids) == 1 and re.fullmatch(r"[\w-]{11}", ids[0]):
                video_id = ids[0]
        if video_id:
            return "https://www.youtube.com/watch?" + urlencode({"v": video_id})
    elif parsed.hostname == "music.youtube.com":
        if parsed.path == "/watch":
            ids = parse_qs(parsed.query).get("v", [])
            if len(ids) == 1 and re.fullmatch(r"[\w-]{11}", ids[0]):
                return "https://music.youtube.com/watch?" + urlencode({"v": ids[0]})
    elif parsed.hostname in {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}:
        parts = soundcloud_track_parts(parsed.path)
        if parts:
            return "https://soundcloud.com/" + "/".join(parts)
    elif parsed.hostname and parsed.hostname.endswith(".bandcamp.com") and parsed.hostname not in BANDCAMP_RESERVED_HOSTS:
        match = re.fullmatch(r"/track/([^/]+)/?", parsed.path)
        if match:
            return f"https://{parsed.hostname}/track/{match[1]}"
    elif parsed.hostname in X_HOSTS:
        match = re.fullmatch(r"/([A-Za-z0-9_]{1,32})/article/(\d+)/?", parsed.path)
        if match:
            return f"https://x.com/{match[1]}/article/{match[2]}"
        match = re.fullmatch(r"/i/article/(\d+)/?", parsed.path)
        if match:
            return f"https://x.com/i/article/{match[1]}"
    video = single_video_target(parsed)
    return video[2] if video else None


def normalize_link(text):
    urls = re.findall(r'https?://[^\s<>"，。]+', text.strip())
    if len(urls) != 1:
        raise ValueError("Paste one post link (sharing text around it is OK).")
    url = urls[0].rstrip(".,;!）)")
    canonical = canonical_post(url)
    if canonical:
        return canonical
    parsed = urlsplit(url)
    short = parsed.hostname in {"v.douyin.com", "vm.tiktok.com", "vt.tiktok.com", "t.cn", "b23.tv"} | XHS_SHORT_HOSTS | SOUNDCLOUD_SHORT_HOSTS or (
        parsed.hostname in {"www.tiktok.com", "tiktok.com"} and parsed.path.startswith("/t/"))
    if not short:
        return url
    if parsed.hostname == "v.douyin.com":
        allowed = DOUYIN_HOSTS
    elif parsed.hostname in XHS_SHORT_HOSTS:
        allowed = XHS_HOSTS
    elif parsed.hostname == "t.cn":
        allowed = WEIBO_HOSTS
    elif parsed.hostname == "b23.tv":
        allowed = BILIBILI_HOSTS
    elif parsed.hostname in SOUNDCLOUD_SHORT_HOSTS:
        allowed = SOUNDCLOUD_HOSTS
    else:
        allowed = TIKTOK_HOSTS
    state = getattr(_batch, "state", None)
    cache = state.get("resolution_cache") if state else None
    if cache is not None:
        return cache.resolve(url, lambda: _resolve_short_link(url, allowed))
    return _resolve_short_link(url, allowed)


def _resolve_short_link(url, allowed):
    opener = build_opener(NoRedirect())
    for _ in range(5):
        check_batch_cancelled()
        parsed = urlsplit(url)
        if parsed.hostname not in allowed:
            raise ValueError("The short link redirected outside its platform. Use the full post URL.")
        canonical = canonical_post(url)
        if canonical:
            return canonical
        try:
            try:
                response = opener.open(Request(url, headers={"User-Agent": MOBILE_UA if allowed == DOUYIN_HOSTS else "facebookexternalhit/1.1"}), timeout=8)
            except HTTPError as error:
                response = error
            with response:
                location = response.headers.get("Location")
                if response.code not in {301, 302, 303, 307, 308} or not location:
                    break
                url = urljoin(url, location)
        except (URLError, OSError):
            raise RuntimeError("Could not resolve the short link. Check your connection or paste the full post URL.") from None
    raise ValueError("The short link did not resolve to a single post. Open it in your browser and copy the full post URL.")
