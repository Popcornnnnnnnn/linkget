"""Site routing, engine command construction and selected-format checks."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
if __package__:
    from .runtime import run_process
else:
    from runtime import run_process
SITES = "Instagram, X/Twitter, Bilibili, TikTok, Douyin, Kuaishou, Xiaohongshu, Weibo, Reddit, Lofter, Pixiv and Pinterest posts; YouTube videos and Shorts"


def needs_login(message):
    """Match explicit authentication failures, not generic 403/429/network errors."""
    return bool(re.search(
        r"login[_ -]required|log[ -]?in (?:is )?required|(?:please|must|need to) (?:log[ -]?in|sign in)|"
        r"(?:account|login) credentials (?:required|needed)|invalid login credentials|"
        r"(?:cookies|authentication|authorization)['\"]? (?:are |is )?(?:required|needed)|"
        r"fresh cookies.*needed|(?:cookies|session)(?: are| is| have| has)? (?:expired|no longer valid)|"
        r"sign in (?:to|if)|redirect to login page|登录后|请(?:先)?登录|需要登录",
        message, re.I))



class LoginRequired(RuntimeError):
    pass


def restricted_bilibili_quality(info, messages):
    """Only warn when the extractor advertises a missing standard HD format."""
    resolutions = {16: 360, 32: 480, 64: 720, 74: 720, 80: 1080, 112: 1080, 116: 1080, 120: 2160, 127: 4320}
    available = max((resolutions.get(f.get("quality"), min(f.get("width") or f.get("height") or 0,
                                                         f.get("height") or 0))
                     for f in info.get("formats", [])), default=0)
    required = []
    for names in re.findall(r"Format\(s\) (.*?) are missing", messages):
        for name in names.split(","):
            match = re.fullmatch(r"\s*(720|1080)P(?:\s*(?:高清|准高清))?\s*", name, re.I)
            if match and int(match[1]) > available:
                required.append(int(match[1]))
    if required:
        return f"This video offers {max(required)}p, but only {available}p is available to this request. Sign in to request higher quality; account permissions may also apply."
    return None



def prepare_bilibili(command, info_path, log):
    """Check quality before downloading; reuse the same metadata for the download."""
    probe = command[:-1] + ["--skip-download", "--dump-single-json", "--no-quiet", command[-1]]
    result = run_process(probe, capture_output=True, text=True, timeout=60)
    lines = result.stdout.rstrip().splitlines()
    messages = "\n".join(lines[:-1]) + "\n" + result.stderr
    log.write(messages)
    log.flush()
    if result.returncode or ("--cookies" in command and needs_login(messages)):
        if needs_login(messages):
            raise LoginRequired("Bilibili requires a signed-in account for this video.")
        raise RuntimeError("Could not read this Bilibili video's formats.\n" + "\n".join(messages.splitlines()[-8:]))
    try:
        info = json.loads(lines[-1])
        if not isinstance(info, dict):
            raise ValueError()
    except (IndexError, ValueError):
        raise RuntimeError("Bilibili returned unreadable video information; nothing has been downloaded.") from None
    audio_read = '-f' in command and command[command.index('-f') + 1] == 'ba/b'
    reason = None if audio_read else restricted_bilibili_quality(info, messages)
    if reason:
        raise LoginRequired(reason)
    info_path.write_text(json.dumps(info))
    info_path.chmod(0o600)
    return command[:-1] + ["--load-info-json", str(info_path)]



def selected_quality(info_path):
    info = json.loads(info_path.read_text())
    selected = info.get("requested_formats") or [info]
    video = next((item for item in selected if item.get("vcodec") not in {None, "none"}), None)
    if not video or not video.get("width") or not video.get("height"):
        return None
    codec = video["vcodec"].split(".")[0]
    codec = {"avc1": "H.264", "hvc1": "HEVC", "hev1": "HEVC", "av01": "AV1"}.get(codec, codec)
    text = f"{video['width']}×{video['height']} · {codec}"
    if video.get("fps"):
        text += f" · {video['fps']:g} fps"
    return text



def prepare_video(command, info_path, log, domain):
    if domain == "bilibili.com":
        return prepare_bilibili(command, info_path, log)
    site = "YouTube" if domain == "youtube.com" else "Weibo" if domain == "weibo.com" else "Reddit" if domain == "reddit.com" else "Site"
    probe = command[:-1] + ["--skip-download", "--dump-single-json", "--no-quiet", command[-1]]
    result = run_process(probe, capture_output=True, text=True, timeout=90)
    lines = result.stdout.rstrip().splitlines()
    messages = "\n".join(lines[:-1]) + "\n" + result.stderr
    log.write(messages)
    log.flush()
    if result.returncode or ("--cookies" in command and needs_login(messages)):
        if needs_login(messages):
            raise LoginRequired(f"{site} asks you to sign in or complete verification for this video.")
        if domain == "youtube.com" and ("PO Token" in messages or "JavaScript" in messages or "challenge" in messages.lower()):
            raise RuntimeError("YouTube's verification could not be completed. Run brew upgrade yt-dlp deno, then retry.\n" + "\n".join(messages.splitlines()[-6:]))
        if domain == "reddit.com" and "HTTP Error 404" in messages:
            if "--cookies" not in command:
                raise LoginRequired(f"{site} returned 404. This post may be deleted, age-restricted, or quarantined. Run linkget auth reddit to connect a Reddit login and retry.")
            raise RuntimeError(f"This {site} post is unavailable, deleted, or private.")
        raise RuntimeError(f"Could not read this {site} video.\n" + "\n".join(messages.splitlines()[-8:]))
    try:
        info = json.loads(lines[-1])
        if not isinstance(info, dict):
            raise ValueError()
    except (IndexError, ValueError):
        raise RuntimeError(f"{site} returned unreadable video information; nothing has been downloaded.") from None
    if info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
        raise ValueError(f"Use a single {site} video link, not a playlist or channel.")
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        raise ValueError(f"Live or upcoming {site} streams are not supported. Try the processed replay after it ends.")
    info_path.write_text(json.dumps(info))
    info_path.chmod(0o600)
    return command[:-1] + ["--load-info-json", str(info_path)]



def route(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Enter a complete http/https post URL.")
    host, path = parsed.hostname, parsed.path
    if host in {"instagram.com", "www.instagram.com"} and re.fullmatch(r"/(p|reel|tv)/[\w-]+/?", path):
        return "Instagram", "gallery-dl", "instagram.com"
    if host in {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"} and re.fullmatch(r"/(?:[\w]+|i/web)/status/\d+(?:/(?:photo|video)/\d+)?/?", path):
        return "X", "gallery-dl", "x.com"
    if host in {"bilibili.com", "www.bilibili.com"}:
        if re.fullmatch(r"/video/(?:BV[\w]+|av\d+)/?", path):
            return "Bilibili video", "yt-dlp", "bilibili.com"
        if re.fullmatch(r"/opus/\d+/?", path):
            return "Bilibili post", "gallery-dl", "bilibili.com"
    if host == "t.bilibili.com" and re.fullmatch(r"/\d+/?", path):
        return "Bilibili post", "gallery-dl", "bilibili.com"
    if host in {"tiktok.com", "www.tiktok.com"} and re.fullmatch(r"/(@[\w.-]+|share)/(photo|video)/\d+/?", path):
        return "TikTok", "gallery-dl", "tiktok.com"
    if host in {"douyin.com", "www.douyin.com"} and re.fullmatch(r"/(video|note)/\d+/?", path):
        return "Douyin", "douyin", "douyin.com"
    if host == "www.xiaohongshu.com" and re.fullmatch(r"/explore/[\da-f]{24}", path):
        return "Xiaohongshu", "xiaohongshu", "xiaohongshu.com"
    if host == "weibo.com" and re.fullmatch(r"/detail/[A-Za-z0-9]+", path):
        return "Weibo", "gallery-dl", "weibo.com"
    if host == "weibo.com" and re.fullmatch(r"/tv/show/\d+:[A-Za-z0-9]+", path):
        return "Weibo video", "yt-dlp", "weibo.com"
    if host == "www.youtube.com" and re.fullmatch(r"/watch", path) and re.fullmatch(r"v=[\w-]{11}", parsed.query):
        return "YouTube", "yt-dlp", "youtube.com"
    if host in {"reddit.com", "www.reddit.com", "old.reddit.com"} and "/comments/" in path:
        return "Reddit", "gallery-dl", "reddit.com"
    if host == "redd.it":
        return "Reddit", "gallery-dl", "reddit.com"
    if host in {"kuaishou.com", "www.kuaishou.com", "v.kuaishou.com"} or host.endswith(".kuaishou.com"):
        return "Kuaishou", "kuaishou", "kuaishou.com"
    if host in {"lofter.com", "www.lofter.com"} or host.endswith(".lofter.com"):
        return "Lofter", "gallery-dl", "lofter.com"
    if host in {"pixiv.net", "www.pixiv.net"} and re.fullmatch(r"/(?:en/)?artworks/\d+/?", path):
        return "Pixiv", "pixiv", "pixiv.net"
    if host in {"pinterest.com", "www.pinterest.com", "pin.it"} or host.endswith(".pinterest.com") or host.endswith(".pinterest.co.uk"):
        return "Pinterest", "gallery-dl", "pinterest.com"
    raise ValueError("Unsupported URL. Supported: " + SITES)



def browser_spec(args, engine, domain):
    browser = args.browser
    profile = args.profile
    if browser == "auto":
        default = Path.home() / "Library/Application Support/Google/Chrome/Default"
        if not default.is_dir():
            return None
        browser, profile = "chrome", str(default)
    if browser == "none":
        return None
    value = browser + ("/" + domain if engine == "gallery-dl" else "")
    return value + (":" + str(Path(profile).expanduser()) if profile else "")



def command_for(args, url, engine, domain, media_dir, tools, cookie_path=None):
    if engine == "gallery-dl":
        command = [tools[engine], "--config-ignore", "--write-metadata", "--directory", str(media_dir)]
        if domain == "instagram.com":
            # Use gallery-dl's external-extractor hook in its own environment.
            # Its default count includes intentionally excluded music tracks.
            sources = [{"from": str(Path(__file__).resolve().parent),
                        "import": ["instagram_visual"]}, None]
            command.extend(["-o", "extractor.module-sources=" + json.dumps(sources),
                            "-o", "extractor.instagram.audio=false",
                            "-o", "extractor.instagram.previews=false"])
        elif domain == "tiktok.com":
            command.extend(["-o", "extractor.tiktok.audio=false", "-o", "extractor.tiktok.covers=false",
                            "-o", "extractor.tiktok.subtitles=false", "-o", "extractor.tiktok.videos=true",
                            "-o", "extractor.tiktok.photos=true", "-o", "extractor.tiktok.filename=tiktok_{id}_{num:03d}.{extension}"])
        elif domain == "weibo.com":
            command.extend(["--write-metadata", "-o", "extractor.weibo.videos=true", "-o", "extractor.weibo.retweets=true",
                            "-o", "extractor.weibo.text=false", "-o", "extractor.weibo.livephoto=true",
                            "-o", "extractor.weibo.filename=weibo_{status[id]}_{num:03d}.{extension}"])
        elif domain == "reddit.com":
            # Homebrew/pipx isolate gallery-dl from yt-dlp's Python package.
            module = next((Path(tools["yt-dlp"]).resolve().parent.parent / "lib").glob(
                "python*/site-packages/yt_dlp"), None)
            if module is not None:
                command.extend(["-o", "downloader.ytdl.module=" + str(module)])
            command.extend(["-o", "downloader.ytdl.format=bestvideo+bestaudio/best",
                            "-o", "downloader.ytdl.raw-options=" + json.dumps({
                                "merge_output_format": "mp4",
                                "ffmpeg_location": str(Path(tools["ffmpeg"]).parent),
                            })])
    else:
        command = [tools[engine], "--ignore-config", "--no-playlist", "--no-progress",
                   "--ffmpeg-location", str(Path(tools["ffmpeg"]).parent)]
        if getattr(args, 'read_audio', False):
            command.extend(['-f', 'ba/b'])
        elif domain == "reddit.com":
            command.extend(["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"])
        else:
            command.extend(["-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]", "-S", "res,fps,hdr,vbr", "--merge-output-format", "mp4"])
        command.extend(["-o", str(media_dir / (domain.split(".")[0] + "_%(id)s.%(ext)s"))])
        if domain == "youtube.com":
            command.extend(["--js-runtimes", "deno:" + tools["deno"]])
    if cookie_path is not None:
        command.extend(["--cookies", str(cookie_path)])
    command.append(url)
    return command
