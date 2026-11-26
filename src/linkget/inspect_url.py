"""Side-effect-free URL/local-file inspection for CLI and AI agent callers."""
import json
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import parse_qs, urlsplit

if __package__:
    from . import accounts, audio as audio_mod, engines, links, preflight, read_asr, rendered_page, toutiao, web_article, x_article
    from .agent_output import classify_error
    from .media import AUDIO, VIDEO, reference
    from .runtime import run_process
else:
    import accounts, audio as audio_mod, engines, links, preflight, read_asr, rendered_page, toutiao, web_article, x_article
    from agent_output import classify_error
    from media import AUDIO, VIDEO, reference
    from runtime import run_process

SHORT_HOSTS = (
    {"v.douyin.com", "vm.tiktok.com", "vt.tiktok.com", "t.cn", "b23.tv"}
    | links.XHS_SHORT_HOSTS
    | links.SOUNDCLOUD_SHORT_HOSTS
)
PUBLIC_ARTICLE_HOSTS = {"www.raspberrypi.com", "zhuanlan.zhihu.com"}


def _is_short_url(url):
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    return parsed.hostname in SHORT_HOSTS or (
        parsed.hostname in {"www.tiktok.com", "tiktok.com"} and parsed.path.startswith("/t/")
    )


def _session_state(domain, supports_auth):
    if not supports_auth:
        return "not_applicable"
    blocked = set(accounts.preferences().get("blocked", []))
    if domain in blocked:
        return "blocked"
    path = accounts.path_for(domain)
    if path.is_symlink():
        return "unknown"
    return "saved" if path.is_file() else "not_saved"


def _required_tools(engine, domain, audio_mode):
    if domain == "toutiao.com" or engine in {"article", "web_article"}:
        return []
    if engine == "x_article":
        return ["gallery-dl"]
    required = [] if engine in {"douyin", "xiaohongshu", "kuaishou", "pixiv"} else [engine]
    if engine == "yt-dlp" or audio_mode:
        required.extend(["ffmpeg", "ffprobe"])
    if domain == "reddit.com" and engine == "gallery-dl":
        required.extend(["yt-dlp", "ffmpeg"])
    if domain == "youtube.com":
        required.append("deno")
    return list(dict.fromkeys(required))


def _summarize_ytdlp_info(info, audio_only=False):
    formats = info.get("formats") or []
    sub_langs = sorted({
        lang for field in ("subtitles", "automatic_captions")
        for lang in (info.get(field) or {})
        if isinstance(lang, str)
    })
    artist = audio_mod.extract_metadata(info, info.get("webpage_url") or "", "").get("artist") or None
    media_type = "audio" if audio_only or (info.get("vcodec") in {None, "none"} and info.get("acodec") not in {None, "none"}) else "video"
    summary = []
    if info.get("width") and info.get("height") and not audio_only:
        summary.append(f"{info['width']}x{info['height']}")
    acodec = info.get("acodec")
    if acodec and acodec != "none":
        summary.append(str(acodec).split(".")[0])
    filesize = info.get("filesize") or info.get("filesize_approx")
    if not filesize and formats:
        sizes = [item.get("filesize") or item.get("filesize_approx") for item in formats if item.get("filesize") or item.get("filesize_approx")]
        filesize = max(sizes) if sizes else None
    is_live = bool(info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming", "post_live"})
    return {
        "title": audio_mod._clean_text(info.get("track") or info.get("title") or info.get("fulltitle") or "") or None,
        "artist": artist,
        "uploader": audio_mod._clean_text(info.get("uploader") or info.get("channel") or "") or None,
        "duration": info.get("duration"),
        "is_live": is_live,
        "media_type": media_type,
        "filesize_approx": filesize,
        "formats_summary": " · ".join(summary) if summary else None,
        "subtitles_available": sub_langs,
        "thumbnail_available": bool(audio_mod._thumbnail_url(info)),
    }


def _inspect_local_file(path, *, find_tool):
    kind = "audio" if path.suffix.lower() in AUDIO else "video" if path.suffix.lower() in VIDEO else "image"
    duration = None
    has_audio = None
    tools_needed = ["ffmpeg", "ffprobe", "whisper-cli"] if kind in {"audio", "video"} else []
    missing = [name for name in tools_needed if not find_tool(name)]
    probe_status = "probed_local"
    if kind in {"audio", "video"}:
        if "ffprobe" in missing:
            probe_status = "missing_tool"
        else:
            try:
                duration, has_audio = read_asr.probe(path)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                return {
                    "ok": False,
                    "supported": True,
                    "kind": "local_file",
                    "site": "Local file",
                    "engine": "local",
                    "domain": None,
                    "canonical_url": None,
                    "reference": path.name,
                    "path": str(path),
                    "audio_only": kind == "audio",
                    "effective_audio_mode": kind == "audio",
                    "supports_read": True,
                    "supports_auth": False,
                    "session_state": "not_applicable",
                    "tools_required": tools_needed,
                    "tools_missing": missing,
                    "destination_preview": None,
                    "destination_error": None,
                    "probe_status": "failed",
                    "metadata": {
                        "media_type": kind,
                        "duration": None,
                        "audio": None,
                        "size_bytes": path.stat().st_size,
                    },
                    "login_required": False,
                    "error": classify_error(error),
                }
    return {
        "ok": True,
        "supported": True,
        "kind": "local_file",
        "site": "Local file",
        "engine": "local",
        "domain": None,
        "canonical_url": None,
        "reference": path.name,
        "path": str(path),
        "audio_only": kind == "audio",
        "effective_audio_mode": kind == "audio",
        "supports_read": True,
        "supports_auth": False,
        "session_state": "not_applicable",
        "tools_required": tools_needed,
        "tools_missing": missing,
        "destination_preview": None,
        "destination_error": None,
        "probe_status": probe_status,
        "metadata": {
            "media_type": kind,
            "duration": duration,
            "audio": has_audio,
            "size_bytes": path.stat().st_size,
        },
        "login_required": False,
    }


def inspect_target(
    raw_input,
    *,
    audio=False,
    article=False,
    video=False,
    browser="auto",
    no_network=False,
    find_tool=None,
    resolve_audio_folder=None,
    resolve_article_folder=None,
    configured_destination=None,
):
    if find_tool is None:
        find_tool = lambda name: None
    text = (raw_input or "").strip()
    if not text:
        err = ValueError("Supply one URL or local file path to inspect.")
        return {"ok": False, "supported": False, "error": classify_error(err)}

    if not re.search(r"https?://", text):
        if video:
            err = ValueError("--video requires one public HTTP or HTTPS URL.")
            return {"ok": False, "supported": False, "error": classify_error(err)}
        expanded = Path(text).expanduser()
        if not expanded.is_symlink() and expanded.is_file():
            return _inspect_local_file(expanded.resolve(), find_tool=find_tool)
        err = ValueError("Enter one supported URL or an existing local media file.")
        return {"ok": False, "supported": False, "error": classify_error(err)}

    urls = re.findall(r'https?://[^\s<>"，。]+', text)
    if len(urls) != 1:
        err = ValueError("Paste one post or music URL to inspect.")
        return {"ok": False, "supported": False, "error": classify_error(err)}
    raw_url = urls[0].rstrip(".,;!）)")
    short_unresolved = False
    try:
        parsed_raw = urlsplit(raw_url)
    except ValueError as error:
        return {"ok": False, "supported": False, "error": classify_error(error)}

    try:
        cand_host = (parsed_raw.hostname or "").lower().rstrip(".")
        if video:
            url, site, domain = engines.route_video(raw_url, no_network=no_network)
            engine = "yt-dlp"
        elif cand_host in toutiao.HOSTS:
            url = toutiao.canonical(raw_url)
            site, engine, domain = "Toutiao", "rendered_page", "toutiao.com"
        elif cand_host in PUBLIC_ARTICLE_HOSTS:
            url, _ = rendered_page.public_article_url(raw_url)
            site = "Raspberry Pi" if cand_host == "www.raspberrypi.com" else "Zhihu"
            engine, domain = "article", cand_host
        elif cand_host == web_article.WECHAT_HOST:
            url, _ = web_article.canonical_wechat_url(raw_url)
            site, engine, domain = "WeChat", "web_article", web_article.WECHAT_HOST
        elif x_article.is_x_article_url(raw_url) or (article and x_article.is_x_status_url(raw_url)):
            url = x_article.canonical_x_url(raw_url)
            site, engine, domain = "X Article", "x_article", "x.com"
        elif no_network:
            url = links.canonical_post(raw_url) or raw_url
            if _is_short_url(url):
                short_unresolved = True
                site_guess = "SoundCloud" if parsed_raw.hostname in links.SOUNDCLOUD_SHORT_HOSTS else (
                    "Bilibili" if parsed_raw.hostname == "b23.tv" else
                    "Douyin" if parsed_raw.hostname == "v.douyin.com" else
                    "Weibo" if parsed_raw.hostname == "t.cn" else
                    "Xiaohongshu" if parsed_raw.hostname in links.XHS_SHORT_HOSTS else "TikTok"
                )
                domain_guess = {
                    "SoundCloud": "soundcloud.com", "Bilibili": "bilibili.com", "Douyin": "douyin.com",
                    "Weibo": "weibo.com", "Xiaohongshu": "xiaohongshu.com", "TikTok": "tiktok.com",
                }[site_guess]
                engine_guess = {
                    "SoundCloud": "yt-dlp",
                    "Bilibili": "yt-dlp",
                    "TikTok": "gallery-dl",
                    "Douyin": "douyin",
                    "Xiaohongshu": "xiaohongshu",
                    "Weibo": "gallery-dl",
                }[site_guess]
                site, engine, domain = site_guess, engine_guess, domain_guess
            elif web_article.is_known_media_host(cand_host):
                site, engine, domain = engines.route(url)
            else:
                url = web_article.validate_public_web_url(raw_url, resolve_dns=False)
                site, engine, domain = cand_host or "Web", "web_article", cand_host or None
        elif web_article.is_known_media_host(cand_host):
            url = links.normalize_link(raw_url)
            resolved_host = (urlsplit(url).hostname or "").lower().rstrip(".")
            if cand_host in toutiao.HOSTS or resolved_host in toutiao.HOSTS:
                url = toutiao.canonical(url)
                site, engine, domain = "Toutiao", "rendered_page", "toutiao.com"
            elif resolved_host in PUBLIC_ARTICLE_HOSTS:
                url, _ = rendered_page.public_article_url(url)
                site = "Raspberry Pi" if resolved_host == "www.raspberrypi.com" else "Zhihu"
                engine, domain = "article", resolved_host
            elif resolved_host == web_article.WECHAT_HOST:
                url, _ = web_article.canonical_wechat_url(url)
                site, engine, domain = "WeChat", "web_article", web_article.WECHAT_HOST
            else:
                site, engine, domain = engines.route(url)
        else:
            url = web_article.validate_public_web_url(raw_url, resolve_dns=False)
            site, engine, domain = cand_host or "Web", "web_article", cand_host or None
    except (OSError, ValueError, RuntimeError) as error:
        return {"ok": False, "supported": False, "error": classify_error(error)}

    audio_only = not video and (
        engines.is_audio_only_site(site) or parsed_raw.hostname in links.SOUNDCLOUD_SHORT_HOSTS
    )
    effective_audio = bool(audio or audio_only)
    if effective_audio and engine in {"pixiv", "article", "web_article", "x_article"}:
        msg = (
            "Pixiv artworks are images only and contain no audio."
            if engine == "pixiv"
            else "Article pages do not contain downloadable audio."
        )
        err = ValueError(msg)
        return {"ok": False, "supported": True, "site": site, "domain": domain, "canonical_url": url, "error": classify_error(err)}

    is_article_target = (
        engine in {"article", "web_article", "x_article"}
        or (domain == "toutiao.com" and "/article/" in urlsplit(url).path)
    )
    supports_auth = domain in accounts.SITES.values()
    tools_required = _required_tools(engine, domain, effective_audio)
    tools_missing = [name for name in tools_required if not find_tool(name)]

    dest_preview = None
    dest_error = None
    try:
        if effective_audio and resolve_audio_folder is not None:
            dest_preview = str(resolve_audio_folder(None))
        elif is_article_target and resolve_article_folder is not None:
            dest_preview = str(resolve_article_folder(None))
        elif configured_destination is not None:
            setting = configured_destination()
            if setting["mode"] == "photos":
                dest_preview = "Photos"
            elif setting["mode"] == "cwd":
                dest_preview = str(Path.cwd())
            elif setting["mode"] == "folder":
                dest_preview = setting["path"]
    except (OSError, ValueError, RuntimeError) as error:
        dest_error = str(error)

    payload = {
        "ok": True,
        "supported": True,
        "kind": "url",
        "site": site,
        "engine": engine,
        "domain": domain or urlsplit(url).hostname,
        "canonical_url": None if short_unresolved else url,
        "reference": reference(url),
        "short_link_unresolved": short_unresolved,
        "audio_only": audio_only,
        "effective_audio_mode": effective_audio,
        "supports_read": engine != "pixiv" and not (video and domain is None),
        "supports_article_download": is_article_target or domain == "x.com",
        "supports_auth": supports_auth,
        "session_state": _session_state(domain, supports_auth),
        "tools_required": tools_required,
        "tools_missing": tools_missing,
        "destination_preview": dest_preview,
        "destination_error": dest_error,
        "probe_status": "skipped_no_network" if no_network else "routing_only",
        "metadata": None,
        "login_required": False,
    }

    if no_network or short_unresolved or engine != "yt-dlp":
        return payload

    ytdlp = find_tool("yt-dlp")
    if not ytdlp:
        payload["probe_status"] = "missing_tool"
        return payload

    command = [ytdlp, "--skip-download", "--dump-single-json", "--no-playlist", "--no-warnings"]
    if video:
        command.append("--ignore-config")
        if domain is None:
            command.extend(engines.GENERIC_VIDEO_OPTIONS)
    with tempfile.TemporaryDirectory(prefix="linkget-inspect-") as session_dir:
        cookie_file = Path(session_dir) / "cookies.txt"
        if supports_auth and browser != "none":
            if domain not in set(accounts.preferences().get("blocked", [])) and accounts.copy_saved(domain, cookie_file):
                command.extend(["--cookies", str(cookie_file)])
        command.append(url)
        try:
            result = run_process(command, capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired as error:
            payload["ok"] = False
            payload["probe_status"] = "failed"
            payload["error"] = classify_error(error, phase="download")
            return payload

    if result.returncode:
        messages = "\n".join(part for part in ((result.stdout or "").strip(), (result.stderr or "").strip()) if part)
        if engines.needs_login(messages):
            payload["login_required"] = True
            payload["probe_status"] = "login_required"
            payload["error"] = classify_error(engines.LoginRequired(f"{site}: authentication required"), phase="login")
            payload["ok"] = False
            return payload
        payload["ok"] = False
        payload["probe_status"] = "failed"
        tail = "\n".join(messages.strip().splitlines()[-6:]) or f"Could not inspect {site} media."
        payload["error"] = classify_error(RuntimeError(tail), phase="download")
        return payload

    lines = (result.stdout or "").rstrip().splitlines()
    try:
        info = json.loads(lines[-1])
        if not isinstance(info, dict):
            raise ValueError("Invalid metadata")
    except (IndexError, ValueError):
        payload["ok"] = False
        payload["probe_status"] = "failed"
        payload["error"] = classify_error(RuntimeError(f"{site} returned unreadable media information."))
        return payload

    if info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
        payload["ok"] = False
        payload["supported"] = False
        payload["probe_status"] = "rejected_collection"
        payload["error"] = classify_error(ValueError(f"Use a single {site} link, not a playlist, album, or channel."))
        return payload

    payload["probe_status"] = "probed_metadata"
    payload["metadata"] = _summarize_ytdlp_info(info, audio_only=effective_audio)
    if payload["metadata"]["is_live"]:
        payload["ok"] = False
        payload["probe_status"] = "rejected_live"
        payload["error"] = classify_error(ValueError(f"Live or upcoming {site} streams are not supported."))
    return payload
