"""Normalize one shared post, resolving only supported short-link hosts."""

import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"
DOUYIN_HOSTS = {"douyin.com", "www.douyin.com", "v.douyin.com", "iesdouyin.com", "www.iesdouyin.com"}
TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "vm.tiktok.com", "vt.tiktok.com", "www.tiktokv.com"}


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
    short = parsed.hostname in {"v.douyin.com", "vm.tiktok.com", "vt.tiktok.com"} or (
        parsed.hostname in {"www.tiktok.com", "tiktok.com"} and parsed.path.startswith("/t/"))
    if not short:
        return url
    allowed = DOUYIN_HOSTS if parsed.hostname == "v.douyin.com" else TIKTOK_HOSTS
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
