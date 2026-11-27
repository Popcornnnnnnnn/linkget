"""Site routing, engine command construction and selected-format checks."""
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
if __package__:
    from .runtime import run_process
    from .failures import classify
    from .links import BANDCAMP_RESERVED_HOSTS, canonical_post, single_video_target, soundcloud_track_parts
    from .web_article import validate_public_web_url
    from . import media_limits
else:
    from runtime import run_process
    from failures import classify
    from links import BANDCAMP_RESERVED_HOSTS, canonical_post, single_video_target, soundcloud_track_parts
    from web_article import validate_public_web_url
    import media_limits
SITES = (
    "Instagram, X/Twitter, Bilibili, TikTok, Douyin, Kuaishou, Xiaohongshu, Weibo, "
    "Reddit, Lofter, Pixiv and Pinterest posts; YouTube videos and Shorts; "
    "Vimeo, Dailymotion and Pornhub videos; "
    "YouTube Music, Bilibili Audio, SoundCloud and Bandcamp tracks"
)
AUDIO_ONLY_SITES = {"YouTube Music", "Bilibili Audio", "SoundCloud", "Bandcamp"}
SAVE_TYPES = ("auto", "photo", "video", "article")
MP4_FORMAT = "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]"
GENERIC_VIDEO_OPTIONS = ("--no-plugin-dirs", "--flat-playlist", "--playlist-items", "1",
                         "-f", MP4_FORMAT + "/bv[ext=mp4]")


def is_audio_only_site(site):
    return site in AUDIO_ONLY_SITES


def check_photo_source(engine):
    if engine in {"yt-dlp", "kuaishou"}:
        raise ValueError("Unsupported URL for photo mode. Use --as video or --audio for this link.")
    if engine in {"article", "web_article", "x_article", "rendered_page"}:
        raise ValueError("Unsupported URL for photo mode. Use --as article to archive this page with its images.")


class PreparedCommand(list):
    """Keep a metadata writer's evidence alongside the existing argv list."""
    def __init__(self, command, metadata_proof):
        super().__init__(command)
        self.metadata_proof = metadata_proof


def write_metadata(path, info):
    if __package__:
        from .ownership import Writer
    else:
        from ownership import Writer
    with Writer(path, register=True) as output:
        output.write(json.dumps(info).encode())
        return output.seal()


def needs_login(message):
    """Use the same affirmative, credential-free evidence as error reporting."""
    return classify(message).category in {'login_required', 'login_expired'}



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



def prepare_bilibili(command, info_path, log, audio_only=False):
    """Check quality before downloading; reuse the same metadata for the download."""
    probe = command[:-1] + ["--skip-download", "--dump-single-json", "--no-quiet", command[-1]]
    result = run_process(probe, capture_output=True, text=True, timeout=60)
    lines = result.stdout.rstrip().splitlines()
    messages = "\n".join(lines[:-1]) + "\n" + result.stderr
    log.write(messages)
    log.flush()
    if result.returncode or ("--cookies" in command and needs_login(messages)):
        if needs_login(messages):
            raise LoginRequired("Bilibili authentication response:\n" + messages)
        raise RuntimeError("Could not read this Bilibili video's formats.\n" + "\n".join(messages.splitlines()[-8:]))
    try:
        info = json.loads(lines[-1])
        if not isinstance(info, dict):
            raise ValueError()
    except (IndexError, ValueError):
        raise RuntimeError("Bilibili returned unreadable video information; nothing has been downloaded.") from None
    audio_read = audio_only or ("-f" in command and command[command.index("-f") + 1].startswith("ba")) or "/audio/au" in command[-1]
    reason = None if audio_read else restricted_bilibili_quality(info, messages)
    if reason:
        raise LoginRequired(reason)
    proof = write_metadata(info_path, info)
    return PreparedCommand(command[:-1] + ["--load-info-json", str(info_path)], proof)



def selected_quality(info_path, audio_only=False):
    info = json.loads(info_path.read_text())
    selected = info.get("requested_formats") or [info]
    video = None if audio_only else next((item for item in selected if item.get("vcodec") not in {None, "none"}), None)
    if video and video.get("width") and video.get("height"):
        codec = video["vcodec"].split(".")[0]
        codec = {"avc1": "H.264", "hvc1": "HEVC", "hev1": "HEVC", "av01": "AV1"}.get(codec, codec)
        text = f"{video['width']}×{video['height']} · {codec}"
        if video.get("fps"):
            text += f" · {video['fps']:g} fps"
        return text
    if audio_only or not video:
        audio = next((item for item in selected if item.get("acodec") not in {None, "none"}), None)
        if not audio:
            return None
        acodec = str(audio["acodec"]).split(".")[0].lower()
        acodec = {"mp4a": "AAC", "opus": "Opus", "mp3": "MP3", "flac": "FLAC", "vorbis": "Vorbis", "aac": "AAC"}.get(acodec, acodec.upper())
        parts = [acodec]
        bitrate = audio.get("abr") or audio.get("tbr")
        if bitrate:
            parts.append(f"{float(bitrate):.0f} kbps")
        if audio.get("asr"):
            parts.append(f"{float(audio['asr']) / 1000:g} kHz")
        return " · ".join(parts)
    return None



def prepare_video(command, info_path, log, domain, audio_only=False):
    if domain == "bilibili.com":
        return prepare_bilibili(command, info_path, log, audio_only=audio_only)
    if domain == "youtube.com":
        site = "YouTube Music" if urlsplit(command[-1]).hostname == "music.youtube.com" else "YouTube"
    else:
        site = {
            "weibo.com": "Weibo", "reddit.com": "Reddit", "soundcloud.com": "SoundCloud",
            "bandcamp.com": "Bandcamp", "vimeo.com": "Vimeo", "dailymotion.com": "Dailymotion",
            "pornhub.com": "Pornhub",
        }.get(domain, urlsplit(command[-1]).hostname if domain is None else "Site")
    probe = command[:-1] + ["--skip-download", "--dump-single-json", "--no-quiet", command[-1]]
    result = run_process(probe, capture_output=True, text=True, timeout=90)
    lines = result.stdout.rstrip().splitlines()
    messages = "\n".join(lines[:-1]) + "\n" + result.stderr
    log.write(messages)
    log.flush()
    if result.returncode or ("--cookies" in command and needs_login(messages)):
        if needs_login(messages):
            raise LoginRequired(f"{site} authentication response:\n" + messages)
        if domain == "youtube.com" and ("PO Token" in messages or "JavaScript" in messages or "challenge" in messages.lower()):
            raise RuntimeError("YouTube's verification could not be completed. Run brew upgrade yt-dlp deno, then retry.\n" + "\n".join(messages.splitlines()[-6:]))
        raise RuntimeError(f"Could not read this {site} media.\n" + "\n".join(messages.splitlines()[-8:]))
    try:
        info = json.loads(lines[-1])
        if not isinstance(info, dict):
            raise ValueError()
    except (IndexError, ValueError):
        raise RuntimeError(f"{site} returned unreadable media information; nothing has been downloaded.") from None
    if info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
        raise ValueError(f"Use a single {site} link, not a playlist, album, or channel.")
    if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"}:
        raise ValueError(f"Live or upcoming {site} streams are not supported. Try the processed replay after it ends.")
    proof = write_metadata(info_path, info)
    return PreparedCommand(command[:-1] + ["--load-info-json", str(info_path)], proof)



def route(url):
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Enter a complete http/https post URL.")
    host, path = parsed.hostname, parsed.path
    if host in {"instagram.com", "www.instagram.com"} and re.fullmatch(r"/(p|reel|tv)/[\w-]+/?", path):
        return "Instagram", "gallery-dl", "instagram.com"
    if host in {"x.com", "www.x.com", "mobile.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}:
        if re.fullmatch(r"/(?:[\w]+|i/web)/status/\d+(?:/(?:photo|video)/\d+)?/?", path):
            return "X", "gallery-dl", "x.com"
        if re.fullmatch(r"/(?:[A-Za-z0-9_]{1,32}|i)/article/\d+/?", path):
            return "X Article", "x_article", "x.com"
    if host in {"bilibili.com", "www.bilibili.com"}:
        if re.fullmatch(r"/video/(?:BV[\w]+|av\d+)/?", path):
            return "Bilibili video", "yt-dlp", "bilibili.com"
        if re.fullmatch(r"/audio/au\d+/?", path):
            return "Bilibili Audio", "yt-dlp", "bilibili.com"
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
    if host == "music.youtube.com":
        if re.fullmatch(r"/watch", path) and re.fullmatch(r"v=[\w-]{11}", parsed.query):
            return "YouTube Music", "yt-dlp", "youtube.com"
        if path.startswith(("/playlist", "/browse", "/channel")):
            raise ValueError("Use a single YouTube Music track link, not a playlist, album, or channel.")
    video = single_video_target(parsed)
    if video:
        site, domain, _ = video
        return site, "yt-dlp", domain
    if host in {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}:
        parts = [part for part in path.split("/") if part]
        if len(parts) >= 2 and parts[1].lower() == "sets":
            raise ValueError("Use a single SoundCloud track link, not a playlist or set.")
        if soundcloud_track_parts(path):
            return "SoundCloud", "yt-dlp", "soundcloud.com"
    if host and host.endswith(".bandcamp.com") and host not in BANDCAMP_RESERVED_HOSTS:
        if re.fullmatch(r"/track/[^/]+/?", path):
            return "Bandcamp", "yt-dlp", "bandcamp.com"
        if path.startswith("/album/"):
            raise ValueError("Use a single Bandcamp track link, not an album.")
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


def route_video(url, *, no_network=False):
    """Keep dedicated yt-dlp routes and let yt-dlp select the extractor for other links."""
    # Check the entry URL. yt-dlp handles redirects and subsequent requests.
    url = canonical_post(url) or url
    url = validate_public_web_url(url, resolve_dns=not no_network)
    try:
        site, engine, domain = route(url)
    except ValueError:
        pass
    else:
        if is_audio_only_site(site):
            raise ValueError("Use --audio for this music link. --video requires a single video.")
        if engine == "yt-dlp":
            return url, site, domain
    # No dedicated domain key means no site-specific options or saved credentials.
    return url, urlsplit(url).hostname, None



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



def command_for(args, url, engine, domain, media_dir, tools, cookie_path=None, audio_only=False):
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
        elif audio_only:
            command.extend(["-f", "ba[ext=m4a]/ba/b"])
        elif domain == "reddit.com":
            command.extend(["-f", "bestvideo+bestaudio/best", "--merge-output-format", "mp4"])
        elif domain is None:
            command.extend([*GENERIC_VIDEO_OPTIONS, "-S", "res,fps,hdr,vbr", "--merge-output-format", "mp4"])
        else:
            command.extend(["-f", MP4_FORMAT, "-S", "res,fps,hdr,vbr", "--merge-output-format", "mp4"])
        prefix = domain.split(".")[0] if domain else "%(extractor_key)s"
        command.extend(["-o", str(media_dir / (prefix + "_%(id)s.%(ext)s"))])
        if domain == "youtube.com":
            command.extend(["--js-runtimes", "deno:" + tools["deno"]])
        clip_raw = getattr(args, "clip", None)
        if clip_raw:
            clip_range = media_limits.parse_clip(clip_raw)
            command.extend(["--download-sections", media_limits.ytdlp_section(clip_range)])
    if cookie_path is not None:
        command.extend(["--cookies", str(cookie_path)])
    command.append(url)
    return command
