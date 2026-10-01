"""Download one Kuaishou post video from its public web representations."""

from http.cookiejar import MozillaCookieJar
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPCookieProcessor, Request, build_opener

DESKTOP_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
MOBILE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Version/17.0 Mobile/15E148 Safari/604.1"


class BrowserRequired(RuntimeError):
    pass


def page_state(html, name):
    match = re.search(r"window\." + re.escape(name) + r"\s*=\s*", html)
    if not match:
        return {}
    source = html[match.end():]
    try:
        state = json.JSONDecoder().raw_decode(source)[0]
    except ValueError:
        # Older Apollo pages include unquoted undefined object values.
        source = re.sub(r"([:\[,])\s*undefined(?=\s*[,}\]])", r"\1null", source)
        try:
            state = json.JSONDecoder().raw_decode(source)[0]
        except ValueError:
            raise RuntimeError("Could not read Kuaishou initial state.") from None
    return state if isinstance(state, dict) else {}


def desktop_photo(html, post_id):
    client = page_state(html, "__APOLLO_STATE__").get("defaultClient", {})
    if not isinstance(client, dict):
        return None
    photo = client.get("VisionVideoDetailPhoto:" + post_id)
    return photo if isinstance(photo, dict) and str(photo.get("id")) == post_id else None


def mobile_photo(html, post_id):
    for entry in page_state(html, "INIT_STATE").values():
        if not isinstance(entry, dict) or entry.get("result") != 1:
            continue
        photo = entry.get("photo")
        if not isinstance(photo, dict):
            continue
        shared_id = parse_qs(str(photo.get("share_info", ""))).get("photoId", [])
        if shared_id == [post_id] or str(photo.get("photoId")) == post_id:
            return photo
    return None


def video_url(photo):
    candidates = []
    resource = photo.get("videoResource") or {}
    resource = resource.get("json", {}) if isinstance(resource, dict) else {}
    manifests = []
    if isinstance(resource, dict):
        manifests.extend((resource.get(codec), codec) for codec in ("hevc", "h264"))
    manifests.append((photo.get("manifest"), None))
    for manifest, codec in manifests:
        if not isinstance(manifest, dict):
            continue
        for adaptation in manifest.get("adaptationSet", []):
            for representation in adaptation.get("representation", []):
                target = representation.get("url")
                if not isinstance(target, str) or urlsplit(target).scheme not in {"http", "https"}:
                    continue
                pixels = representation.get("height", 0) * representation.get("width", 0)
                hevc = (codec or representation.get("videoCodec")) in {"hevc", "h265"}
                candidates.append(((pixels, hevc, representation.get("avgBitrate", 0)), target))
    if candidates:
        # Resolution comes first; keep the existing HEVC preference at equal
        # resolution. Bitrate only breaks ties within the selected codec.
        return max(candidates, key=lambda candidate: candidate[0])[1]
    target = photo.get("photoUrl")
    if isinstance(target, str) and urlsplit(target).scheme in {"http", "https"}:
        return target
    raise RuntimeError("Kuaishou returned no usable video. Image galleries are not supported.")


def download(url, directory, cookie_path=None):
    parsed = urlsplit(url)
    match = re.fullmatch(r"/short-video/([A-Za-z0-9]+)/?", parsed.path)
    if (parsed.hostname not in {"kuaishou.com", "www.kuaishou.com"} or not match
            or parsed.scheme not in {"http", "https"} or parsed.username or parsed.password
            or parsed.port not in {None, 80, 443}):
        raise RuntimeError("Use the full Kuaishou /short-video/ post URL. Profiles and image galleries are not supported.")
    post_id = match[1]
    jar = MozillaCookieJar(str(cookie_path) if cookie_path else None)
    if cookie_path:
        jar.load(ignore_discard=True)
    opener = build_opener(HTTPCookieProcessor(jar))

    def request(target, user_agent=DESKTOP_UA):
        return opener.open(Request(target, headers={
            "User-Agent": user_agent, "Referer": "https://www.kuaishou.com/"
        }), timeout=20)

    try:
        with request(url) as response:
            html = response.read(8_000_000).decode("utf-8", "replace")
            final_url = response.geturl()
        photo = desktop_photo(html, post_id)
        if photo is None:
            # Desktop SSR sometimes contains only site configuration. The
            # public mobile representation supplies this same post's manifest.
            with request(url, MOBILE_UA) as response:
                mobile_html = response.read(8_000_000).decode("utf-8", "replace")
                final_url = response.geturl()
            photo = mobile_photo(mobile_html, post_id)
            if photo is None:
                if "login" in urlsplit(final_url).path:
                    raise BrowserRequired("This Kuaishou post redirected to login. Try your browser login.")
                raise RuntimeError("Kuaishou did not expose this video's media. Check the post's availability or website verification in your browser.")
        target = video_url(photo)
        path = directory / f"kuaishou_{post_id}_001.mp4"
        partial = path.with_suffix(".mp4.part")
        with request(target) as response:
            head = response.read(64)
            if len(head) < 12 or head[4:8] != b"ftyp":
                raise RuntimeError("Kuaishou returned an invalid video instead of MP4 media.")
            count = len(head)
            with partial.open("xb") as output:
                output.write(head)
                while chunk := response.read(1024 * 1024):
                    count += len(chunk)
                    output.write(chunk)
            expected = response.headers.get("Content-Length")
            if expected and count != int(expected):
                raise RuntimeError("Kuaishou download was incomplete; partial files have been kept.")
            partial.rename(path)
        return []
    except HTTPError as error:
        raise RuntimeError(f"Kuaishou request failed (HTTP {error.code}). Open the post in your browser and check access.") from None
    except (URLError, OSError):
        raise RuntimeError("Kuaishou download interrupted. Check your connection and retry.") from None
