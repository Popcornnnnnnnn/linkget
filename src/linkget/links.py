"""Normalize one shared post, resolving only supported short-link hosts."""

import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", "v.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}
TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "www.tiktokv.com"}
XHS_SHORT_HOSTS = {"xhslink.com", "www.xhslink.com", "xhslink.cn", "www.xhslink.cn"}
XHS_HOSTS = {"xiaohongshu.com", "www.xiaohongshu.com"} | XHS_SHORT_HOSTS
WEIBO_HOSTS = {"weibo.com", "www.weibo.com", "m.weibo.cn", "weibo.cn", "video.weibo.com", "t.cn"}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


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
    elif parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}:
        match = re.fullmatch(r"/(?:shorts/|live/|embed/)?([\w-]{11})/?", parsed.path)
        video_id = match[1] if match and (parsed.hostname == "youtu.be" or parsed.path.startswith(("/shorts/", "/live/", "/embed/"))) else None
        if parsed.path == "/watch":
            ids = parse_qs(parsed.query).get("v", [])
            if len(ids) == 1 and re.fullmatch(r"[\w-]{11}", ids[0]):
                video_id = ids[0]
        if video_id:
            return "https://www.youtube.com/watch?" + urlencode({"v": video_id})
    return None


def normalize_link(text):
    urls = re.findall(r'https?://[^\s<>"，。]+', text.strip())
    if len(urls) != 1:
        raise ValueError("Paste one post link (sharing text around it is OK).")
    url = urls[0].rstrip(".,;!）)")
    canonical = canonical_post(url)
    if canonical:
        return canonical
    parsed = urlsplit(url)
    short = parsed.hostname in {"v.douyin.com", "vm.tiktok.com", "vt.tiktok.com", "t.cn"} | XHS_SHORT_HOSTS or (
        parsed.hostname in {"www.tiktok.com", "tiktok.com"} and parsed.path.startswith("/t/"))
    if not short:
        return url
    if parsed.hostname == "v.douyin.com":
        allowed = DOUYIN_HOSTS
    elif parsed.hostname in XHS_SHORT_HOSTS:
        allowed = XHS_HOSTS
    elif parsed.hostname == "t.cn":
        allowed = WEIBO_HOSTS
    else:
        allowed = TIKTOK_HOSTS
    opener = build_opener(NoRedirect())
    for _ in range(5):
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
