"""Portable CLI; download engines are installed separately (normally by Homebrew)."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import filecmp
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

if __package__:
    from . import accounts
    from .photos import originals_directory, retain_originals, verify_library_copies
    from .version import VERSION
    from .session import LOGIN_URLS, prepare_session
    from .links import normalize_link
    from .douyin import download as download_douyin, MediaUnavailable as DouyinMediaUnavailable
    from .xiaohongshu import download as download_xiaohongshu, BrowserRequired
    from .kuaishou import download as download_kuaishou, BrowserRequired as KuaishouBrowserRequired
    from .pixiv import download as download_pixiv, BrowserRequired as PixivBrowserRequired
    from .weibo import prefer_clean_images
    from .media import DownloadResult, inspect_files, quality_summary, gallery_evidence, reference, compare_reports, write_report
    from .live import prepare_pairs, retain_pairs, save_pairs
else:  # Homebrew launches this file directly.
    import accounts
    from photos import originals_directory, retain_originals, verify_library_copies
    from version import VERSION
    from session import LOGIN_URLS, prepare_session
    from links import normalize_link
    from douyin import download as download_douyin, MediaUnavailable as DouyinMediaUnavailable
    from xiaohongshu import download as download_xiaohongshu, BrowserRequired
    from kuaishou import download as download_kuaishou, BrowserRequired as KuaishouBrowserRequired
    from pixiv import download as download_pixiv, BrowserRequired as PixivBrowserRequired
    from weibo import prefer_clean_images
    from media import DownloadResult, inspect_files, quality_summary, gallery_evidence, reference, compare_reports, write_report
    from live import prepare_pairs, retain_pairs, save_pairs

VIDEO = {".mp4", ".mov", ".m4v"}
MEDIA = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif", ".tif", ".tiff"} | VIDEO
SITES = "Instagram, X/Twitter, Bilibili, TikTok, Douyin, Kuaishou, Xiaohongshu, Weibo, Reddit, Lofter, Pixiv and Pinterest posts; YouTube videos and Shorts"


class LoginRequired(RuntimeError):
    pass


def needs_login(message):
    """Match explicit authentication failures, not generic 403/429/network errors."""
    return bool(re.search(
        r"login[_ -]required|log[ -]?in (?:is )?required|(?:please|must|need to) (?:log[ -]?in|sign in)|"
        r"(?:account|login) credentials (?:required|needed)|invalid login credentials|"
        r"(?:cookies|authentication|authorization)['\"]? (?:are |is )?(?:required|needed)|"
        r"fresh cookies.*needed|(?:cookies|session)(?: are| is| have| has)? (?:expired|no longer valid)|"
        r"sign in (?:to|if)|redirect to login page|登录后|请(?:先)?登录|需要登录",
        message, re.I))


def login_guidance(args, domain, url, reason):
    status("Stopped", reason, "33", stream=sys.stderr)
    if args.browser == "none":
        status("Next", "Public-only mode is enabled. Run without --browser none to use a browser login.", stream=sys.stderr)
    elif not sys.stdin.isatty():
        sign_in_link(domain, stream=sys.stderr)
        status("Next", "Run linkget in an interactive terminal to connect a browser and continue.", stream=sys.stderr)


def report_failure(message):
    label, next_step = "Error", None
    if re.search(r"HTTP(?: Error)?[ :]+429|too many requests|rate.?limit", message, re.I):
        label, next_step = "Rate limit", "Wait before retrying this item."
    elif re.search(r"captcha|challenge|verification page|verification required|confirm you.re not a bot", message, re.I):
        label, next_step = "Website check", "Open the post in your browser, complete any website check, then retry this item."
    elif re.search(r"timed? out|timeout|connection (?:reset|refused)|TLS|SSL|could not resolve|temporary failure in name resolution", message, re.I):
        label, next_step = "Network", "Check your connection, then retry this item."
    elif re.search(r"HTTP(?: Error)?[ :]+403|forbidden|blocked by network security", message, re.I):
        label, next_step = "Access refused", "Open the post in your browser to check access or website verification, then retry this item."
    elif re.search(r"HTTP(?: Error)?[ :]+(?:404|410)|unavailable, deleted, or private|video unavailable", message, re.I):
        label, next_step = "Unavailable", "Check that the post still opens in your browser, or copy a fresh share link."
    elif re.search(r"unsupported|not supported|not implemented", message, re.I):
        label, next_step = "Unsupported", "Run linkget sites to check the supported links and media."
    status(label, message, "31", stream=sys.stderr)
    if next_step:
        status("Next", next_step, stream=sys.stderr)
    return label.lower().replace(" ", "_")


def login_result(site, state, saved=False, public_summary=False):
    domain = accounts.SITES[site]
    if public_summary:
        if domain == "instagram.com":
            if state.state == "valid":
                status(site, "Connected", "1;32")
            else:
                status(site, "Login for tested posts", "1;33")
            return
        if domain == "reddit.com":
            status(site, "Public access unverified", "1;33")
            return
        if domain in accounts.PUBLIC_ACCESS and domain != "bilibili.com":
            status(site, "Public access", "1")
            return
    suffix = " · higher quality" if public_summary and domain == "bilibili.com" else ""
    if state.state == "valid":
        status(site, "Connected" + suffix, "1;32")
    elif state.state == "missing":
        detail = ("Login for higher quality" if suffix else
                  "Not connected" if state.detail == "Not saved" else "Not signed in")
        status(site, detail, "1")
    elif state.state == "unverified":
        detail = ("Network error · login unchanged" if saved and state.detail == "Network check failed"
                  else "Could not verify: " + state.detail)
        status(site, detail + suffix, "1;33")
    else:
        status(site, state.detail + suffix, "1;33")


def show_logins(args=None, detailed_site=None):
    settings = accounts.preferences()
    states = {}
    pending = {site: domain for site, domain in accounts.SITES.items()
               if domain not in settings.get("blocked", [])}
    results = {}
    if pending:
        with activity("Checking"), ThreadPoolExecutor(max_workers=len(pending)) as executor:
            futures = {site: executor.submit(accounts.status, domain) for site, domain in pending.items()}
            results = {site: future.result() for site, future in futures.items()}
    rejected = [pending[site] for site, state in results.items() if state.state == "invalid"]
    if rejected and args is not None and sys.stdin.isatty() and args.browser != "none":
        tool = find_tool("gallery-dl")
        if tool:
            with activity("Refreshing"):
                refreshed = accounts.refresh_saved(tool, rejected, args.browser, args.profile)
            for site, domain in pending.items():
                if domain in refreshed:
                    results[site] = refreshed[domain]
    for site, domain in accounts.SITES.items():
        if domain in settings.get("blocked", []):
            login_result(site, accounts.Session("disconnected", "Disconnected"), saved=True,
                         public_summary=site != detailed_site)
            states[site] = "disconnected"
        else:
            state = results[site]
            login_result(site, state, saved=True, public_summary=site != detailed_site)
            states[site] = state.state
    return states


def account_command(args, parser):
    command, *sites = args.link
    if command == "auth":
        if len(sites) > 1 or (sites and sites[0] not in accounts.SITES):
            parser.error("Use linkget auth to check all sites, or linkget auth SITE to connect one.")
        if args.profile and args.browser in {"auto", "none"}:
            parser.error("--profile requires an explicit --browser.")
        states = show_logins(args, detailed_site=sites[0] if sites else None)
        if sites:
            site = sites[0]
            if states[site] != "valid" and sys.stdin.isatty() and args.browser != "none":
                if recover_login(args, accounts.SITES[site], "Connect " + site, proactive=True):
                    login_result(site, accounts.Session("valid", "Valid"))
        return 0
    if len(sites) != 1 or sites[0] not in {*accounts.SITES, "all"}:
        parser.error("Use linkget logout SITE, or linkget logout all.")
    accounts.logout(sites[0])
    status("Disconnected", f"{sites[0]} · saved login removed; automatic browser access disabled")
    return 0


def choose_browser():
    browsers = accounts.available_browsers()
    default = accounts.default_browser()
    if default in browsers:
        browsers.remove(default)
        browsers.insert(0, default)
    if not browsers:
        status("Browser", "No supported browser found. You can still download public posts.")
        return None
    print("\n  Choose a browser whose logins you want to use:\n")
    for index, name in enumerate(browsers, 1):
        print(f"    {index}. {name.capitalize()}" + (" (default browser)" if name == default else ""))
    print("    0. Skip for now\n")
    while True:
        choice = input("  Browser [1]: ").strip() or "1"
        if choice == "0":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(browsers):
            return browsers[int(choice) - 1]
        print("  Enter a number from the list.")


def browser_access_help(browser):
    """Permission help after a failed read, never a speculative prompt notice."""
    if sys.platform == "darwin" and browser in {"chrome", "edge", "brave"}:
        name = "Microsoft Edge" if browser == "edge" else browser.capitalize()
        status("Next", f"Check the {name} profile and its Keychain access permission, then retry or choose another browser.")
    elif sys.platform == "darwin" and browser == "safari":
        status("Next", "Check Safari data access in System Settings > Privacy & Security > Full Disk Access, then retry.")
    else:
        status("Next", f"Check the {browser.capitalize()} profile and browser data permissions, then retry or choose another browser.")


def first_run(args):
    if args.browser == "none" or not sys.stdin.isatty() or accounts.preferences().get("setup"):
        return
    print("\n  Welcome to linkget\n\n  Connect a browser to use your existing website logins.\n  Only supported websites are saved locally. You can skip this.\n")
    browser = args.browser if args.browser != "auto" else choose_browser()
    profile = args.profile
    results = {}
    while browser:
        tool = find_tool("gallery-dl")
        if not tool:
            status("Setup needed", installation_hint(["gallery-dl"]), "33")
            print("  Browser connection skipped. Public downloads remain available.\n")
            return
        with activity("Connecting"):
            results = accounts.connect_browser(tool, browser, profile)
        if all(state.state == "unavailable" for state in results.values()):
            status(browser.capitalize(), next(iter(results.values())).detail, "33")
            browser_access_help(browser)
        else:
            for site, domain in accounts.SITES.items():
                login_result(site, results[domain], public_summary=True)
                if (domain not in accounts.PUBLIC_ACCESS and domain != "pixiv.net"
                        and results[domain].state in {"missing", "invalid", "unverified"}):
                    sign_in_link(domain)
            if any(domain not in accounts.PUBLIC_ACCESS and domain != "pixiv.net"
                   and state.state in {"missing", "invalid", "unverified"} for domain, state in results.items()):
                status("Next", f"Open these links in {browser.capitalize()}" + (" (the selected profile)" if profile else "")
                       + ", sign in or complete verification, then return here and type r to check again.")
        usable_read = any(state.state != "unavailable" for state in results.values())
        if usable_read:
            accounts.set_preferences(browser=browser, profile=profile)
        choice = input("\n  Enter to continue · b to choose another browser · r to retry: ").strip().lower()
        if choice == "b":
            browser = choose_browser()
            profile = None
        elif choice == "r":
            continue
        else:
            break
    accounts.set_preferences(setup=True)
    print()
    return results


def recover_login(args, domain, reason, proactive=False):
    """Connect a site from account management or resume a download in place."""
    if not sys.stdin.isatty() or args.browser == "none":
        return False
    settings = accounts.preferences()
    site = next(site for site, site_domain in accounts.SITES.items() if site_domain == domain)
    if domain in settings.get("blocked", []) and not proactive:
        if input("  Browser access is disabled for this site. Reconnect? [y/N]: ").strip().lower() != "y":
            return False
        proactive = True
    browser = args.browser if args.browser != "auto" else settings.get("browser")
    profile = args.profile if args.browser != "auto" else settings.get("profile")
    source = settings.get("sources", {}).get(domain)
    if args.browser == "auto" and source:
        head, _, saved_profile = source.partition(":")
        browser = head.split("/", 1)[0]
        profile = saved_profile or None
    tool = find_tool("gallery-dl")
    if not tool:
        raise RuntimeError(installation_hint(["gallery-dl"]))
    if not proactive:
        status("Action needed", reason, "33")
    while True:
        new_browser = not browser or proactive
        proactive = False
        if not browser:
            browser = choose_browser()
            profile = None
            if not browser:
                return False
        if not new_browser:
            if "access" in reason.lower() or "decrypt" in reason.lower() or "read browser" in reason.lower():
                browser_access_help(browser)
            else:
                sign_in_link(domain)
                status("Next", f"Open this link in {browser.capitalize()}" + (" (the selected profile)" if profile else "")
                       + f", sign in to {emphasis(site)} or complete verification, then return here.")
                if "Network" in reason or "Rate limited" in reason:
                    status("Note", "The check did not complete. Check your connection or try again later.")
            answer = input("  Press Enter to check again · b to change browser · q to cancel: ").strip().lower()
            if answer == "q":
                return False
            if answer == "b":
                browser = None
                continue
        specification = browser + "/" + domain + (":" + str(Path(profile).expanduser()) if profile else "")
        with activity("Checking " + site):
            state = accounts.import_browser(tool, specification, domain)
        if state.state == "valid":
            accounts.set_preferences(browser=browser, profile=profile, setup=True)
            return True
        reason = state.detail
        status(site, "Could not connect · " + reason, "1;33")


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
    result = subprocess.run(probe, capture_output=True, text=True, timeout=60)
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
    reason = restricted_bilibili_quality(info, messages)
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
    result = subprocess.run(probe, capture_output=True, text=True, timeout=90)
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


def terminal_styles(stream):
    return stream.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def emphasis(value, stream=None):
    stream = stream if stream is not None else sys.stdout
    return f"\033[1m{value}\033[0m" if terminal_styles(stream) else str(value)


def sign_in_link(domain, stream=None):
    stream = stream if stream is not None else sys.stdout
    site = next(site for site, site_domain in accounts.SITES.items() if site_domain == domain)
    url = LOGIN_URLS[domain]
    # Keep the full URL readable/copyable even when OSC 8 links are unsupported.
    link = f"\033]8;;{url}\033\\{url}\033]8;;\033\\" if terminal_styles(stream) else url
    status("Sign in", f"{emphasis(site, stream)} · {link}", stream=stream)


def status(label, value, color="0", stream=None):
    """Aligned terminal output; redirected output remains plain text."""
    stream = stream if stream is not None else sys.stdout
    field = f"{label:<12}"
    if terminal_styles(stream):
        field = f"\033[{color}m{field}\033[0m"
    lines = str(value).splitlines() or [""]
    print(f"  {field} {lines[0]}", file=stream, flush=True)
    for line in lines[1:]:
        print(" " * 15 + line, file=stream, flush=True)


def format_size(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1000 or unit == "TB":
            return f"{size:.0f} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1000


def downloaded_bytes(directory):
    """Estimate bytes written, excluding sidecars and duplicate merge outputs."""
    sizes = {}
    with os.scandir(directory) as entries:
        for entry in entries:
            name = entry.name.removesuffix(".part")
            if ".temp." in name or Path(name).suffix.lower() not in MEDIA | {".m4a", ".aac", ".opus", ".webm"}:
                continue
            try:
                if entry.is_file():
                    sizes[name] = max(sizes.get(name, 0), entry.stat().st_size)
            except FileNotFoundError:
                # Downloaders rename completed parts and delete merged tracks.
                continue
    track_stems = {match[1] for name in sizes if (match := re.fullmatch(r"(.+)\.f[^.]+\.[^.]+", name))}
    return sum(size for name, size in sizes.items() if Path(name).stem not in track_stems)


def saved_description(files):
    videos = sum(path.suffix.lower() in VIDEO for path in files)
    photos = len(files) - videos
    parts = [f"{count} {kind}{'s' if count != 1 else ''}" for count, kind in ((photos, "photo"), (videos, "video")) if count]
    return ", ".join(parts) + " saved"


@contextmanager
def activity(label, media_dir=None):
    """Animate one temporary line while work runs, then clear it on every exit."""
    stream = sys.stdout
    if not stream.isatty() or os.environ.get("TERM") == "dumb":
        status(label, "Working...")
        yield
        return

    stopped = threading.Event()
    started = time.monotonic()
    field = f"{label:<12}"
    if "NO_COLOR" not in os.environ:
        field = f"\033[36m{field}\033[0m"

    def animate():
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        index = 0
        size = ""
        while not stopped.is_set():
            elapsed = int(time.monotonic() - started)
            if media_dir is not None and index % 3 == 0:
                size = f"~{format_size(downloaded_bytes(media_dir))} · "
            stream.write(f"\r\033[2K  {field} {frames[index % len(frames)]} {size}{elapsed}s")
            stream.flush()
            index += 1
            stopped.wait(0.1)

    worker = threading.Thread(target=animate, name="linkget-progress", daemon=True)
    try:
        worker.start()
        yield
    finally:
        stopped.set()
        if worker.ident is not None:
            worker.join()
        stream.write("\r\033[2K")
        stream.flush()


def find_tool(name):
    found = shutil.which(name)
    if found:
        return found
    for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
        path = Path(prefix) / name
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def installation_hint(missing):
    formulae = list(dict.fromkeys("ffmpeg" if name == "ffprobe" else name for name in missing))
    return "Missing tools: " + ", ".join(missing) + "\nInstall: brew install " + " ".join(formulae) + "\nNeed Homebrew? Visit https://brew.sh/, then run linkget doctor."


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
        if domain == "tiktok.com":
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
        if domain == "reddit.com":
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


def save_folder(files, folder):
    folder.mkdir(parents=True, exist_ok=True)
    saved = []
    skipped = 0
    for source in files:
        target = folder / source.name
        suffix = 1
        while True:
            if target.is_file() and filecmp.cmp(source, target, shallow=False):
                skipped += 1
                break
            created = False
            try:
                with target.open("xb") as output:
                    created = True
                    with source.open("rb") as input_file:
                        shutil.copyfileobj(input_file, output)
                saved.append(source)
                break
            except FileExistsError:
                target = folder / f"{source.stem}-{suffix}{source.suffix}"
                suffix += 1
            except BaseException:
                if created:
                    target.unlink(missing_ok=True)
                raise
    return saved, skipped


def import_photos(files, date_now, live_pairs=None):
    retained = retain_originals(files)
    staged = set(retained)
    if live_pairs:
        paired = retain_pairs(list(live_pairs.values()), originals_directory())
        staged.update(paired)
        replacements = dict(zip((name for pair in live_pairs for name in pair), paired))
        retained = [replacements.get(source.name, path) for source, path in zip(files, retained)]
    unique = list(dict.fromkeys(retained))
    source_indexes = [retained.index(path) for path in unique]
    importer = Path(__file__).with_name("import.applescript")
    result = subprocess.run(["/usr/bin/osascript", str(importer),
                             "--date-now" if date_now else "--keep-date",
                             *map(str, unique)], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Photos could not import the media. Allow terminal access in System Settings > Privacy & Security > Automation, or use --folder for unsupported formats.\n"
                           + "Originals are safe in: " + str(originals_directory()) + "\n" + result.stderr.strip())
    response, _, identifier_text = result.stdout.strip().partition("\n")
    if not re.fullmatch(r"\d+:\d+(?::\d+)*", response):
        raise RuntimeError("Photos returned an invalid import result. Original files have been kept.")
    saved, skipped, *indexes = map(int, response.split(":"))
    if (saved + skipped != len(unique) or saved != len(indexes)
            or len(set(indexes)) != len(indexes)
            or any(index < 1 or index > len(unique) for index in indexes)):
        raise RuntimeError("Photos returned an unexpected item count. Original files have been kept.")
    try:
        verify_library_copies(unique, identifier_text.split("\t") if identifier_text else [])
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        raise RuntimeError(str(error) + "\nOriginals are safe in: " + str(originals_directory())) from error
    for path in staged:
        path.unlink(missing_ok=True)
    return [files[source_indexes[index - 1]] for index in indexes], skipped + len(files) - len(unique)


def doctor():
    missing = []
    print("  Tools\n")
    for name in ("gallery-dl", "yt-dlp", "ffmpeg", "ffprobe", "deno"):
        path = find_tool(name)
        if not path:
            missing.append(name)
            status(name, "Missing", "33")
            continue
        try:
            check = subprocess.run([path, "-version" if name in {"ffmpeg", "ffprobe"} else "--version"], text=True, capture_output=True, timeout=10)
            if check.returncode:
                raise RuntimeError("Could not run")
            version = check.stdout.splitlines()[0]
            if name in {"ffmpeg", "ffprobe"}:
                version = version.split(" Copyright", 1)[0].removeprefix(name + " version ")
            status(name, version, "32")
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            missing.append(name)
            status(name, f"Could not run: {path}", "33")
    if missing:
        print()
        status("Setup needed", installation_hint(missing), "33")
    return 1 if missing else 0


def configured_destination():
    setting = accounts.preferences().get("destination", {"mode": "photos"})
    if not isinstance(setting, dict) or setting.get("mode") not in {"photos", "cwd", "folder"}:
        raise ValueError("Invalid default destination. Run linkget config --photos or linkget config --folder.")
    if setting["mode"] == "folder" and (not isinstance(setting.get("path"), str) or not Path(setting["path"]).is_absolute()):
        raise ValueError("Invalid default folder. Run linkget config --folder PATH.")
    return setting


def configure(args, parser):
    if args.link != ["config"] or args.browser != "auto" or args.profile or args.date_now:
        parser.error("Use linkget config, linkget config --photos, or linkget config --folder [PATH].")
    if args.photos:
        setting = {"mode": "photos"}
    elif args.folder is True:
        setting = {"mode": "cwd"}
    elif args.folder is not None:
        folder = args.folder.expanduser().resolve()
        if folder.exists() and not folder.is_dir():
            raise ValueError(f"Not a folder: {folder}")
        setting = {"mode": "folder", "path": str(folder)}
    else:
        setting = configured_destination()
    changed = args.photos or args.folder is not None
    if changed:
        accounts.set_preferences(destination=setting)
    description = {"photos": "Photos", "cwd": "Current folder (where you run linkget)", "folder": setting.get("path")}[setting["mode"]]
    status("Default", description)
    if changed:
        status("Saved", "Used when no --folder or --photos is specified.")
    else:
        print("\n  Change default:\n")
        for command, detail in [
            ("linkget config --photos", "Photos"),
            ("linkget config --folder", "Current folder on each run"),
            ("linkget config --folder ~/Downloads", "Fixed folder; replace with your path"),
        ]:
            print(f"  {command:<36} {detail}")
        print("\n  To override once, omit config. Your saved default stays unchanged.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="linkget",
        description="Save photos and videos from supported sites to macOS Photos or a folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Commands:
  help                 Show this help (also -h or --help)
  sites                List supported sites and media
  doctor               Check download tools and installation
  config               View or change the default save destination
  batch [LINK ...]     Download links in order; paste one per line if omitted
  compare A.json B.json Compare reports for the same links
  auth                 Check logins and public access without sign-in prompts
  auth SITE            Connect one website explicitly (e.g. auth bilibili)
  logout SITE          Remove a saved login and disable automatic browser access
  logout all           Disconnect all websites

Account sites: instagram, x, bilibili, tiktok, douyin, xiaohongshu, weibo, youtube, reddit, kuaishou, lofter, pixiv, pinterest

Examples:
  linkget                              Paste a link or sharing text at the prompt
  linkget https://v.douyin.com/CODE/    Save to the configured default
  linkget URL --photos                 Save to Photos this time
  linkget URL --folder                 Save to the current folder
  linkget URL --folder ~/Downloads     Save to a folder
  linkget batch URL1 URL2 --folder     Download multiple posts
  linkget batch --folder ~/Downloads   Paste links; blank line starts downloads
  linkget auth                         Check website logins and public access
  linkget auth bilibili                Connect Bilibili for higher quality
  linkget config                       View the default save destination
  linkget config --photos              Default to Photos (initial setting)
  linkget config --folder              Default to the current folder each time
  linkget config --folder ~/Downloads  Default to a fixed folder

First use guides you through connecting a browser. You can skip it.
Downloads use public access first, then your connected logins when needed.
Paste sharing text at the prompt to avoid shell quoting and special characters.""")
    parser.add_argument("link", nargs="*", metavar="LINK_OR_COMMAND", help="post URL, sharing text, or a command listed below")
    parser.add_argument("--version", action="version", version="%(prog)s " + VERSION)
    destination_options = parser.add_mutually_exclusive_group()
    destination_options.add_argument("--folder", nargs="?", const=True, type=Path,
                                     help="save to FOLDER, or the current folder if omitted; overrides the default")
    destination_options.add_argument("--photos", action="store_true", help="save to Photos; overrides the default")
    parser.add_argument("--browser", choices=["auto", "none", "chrome", "firefox", "safari", "edge", "brave"], default="auto", help="auto: public first, then saved login and browser refresh if needed; none: public only; specify a browser to use its login")
    parser.add_argument("--profile", help="path to the selected browser profile")
    parser.add_argument("--date-now", action="store_true", help="set the date of newly imported Photos items to now")
    parser.add_argument("--report", type=Path, help="write download evidence to a new JSON file for comparison")
    parser.add_argument("--live-photos", action="store_true", help="package exposed Live Photo pairs on macOS; original downloads are preserved")
    tokens = list(sys.argv[1:] if argv is None else argv)
    # An HTTP URL immediately after bare --folder is the post, never a directory.
    for index in range(len(tokens) - 2, -1, -1):
        if tokens[index] == "--folder" and tokens[index + 1].startswith(("http://", "https://")):
            tokens.insert(index + 1, ".")
    args = parser.parse_intermixed_args(tokens)
    if args.link == ["help"]:
        parser.print_help()
        return 0
    if args.link and args.link[0] == "compare":
        if len(args.link) != 3:
            parser.error("Use linkget compare FIRST.json SECOND.json.")
        try:
            rows = compare_reports(*(json.loads(Path(path).expanduser().read_text()) for path in args.link[1:]))
            for index, row in enumerate(rows, 1):
                status("Item", str(index))
                status("Comparison", row["result"])
                if "first" in row:
                    status("First", f"{row['first_count']} files · {row['first']}")
                    status("Second", f"{row['second_count']} files · {row['second']}")
            status("Quality", "Uploader maximum and watermark absence remain unverified.")
            return 0
        except (OSError, ValueError, KeyError, TypeError) as error:
            report_failure(str(error))
            return 1
    if args.link and args.link[0] == "config":
        try:
            return configure(args, parser)
        except (OSError, ValueError, RuntimeError) as error:
            status("Error", str(error), "31", stream=sys.stderr)
            return 1
    if args.link and args.link[0] == "login":
        parser.error("Browser connection is now part of the normal flow. Run linkget or linkget URL.")
    if args.link and args.link[0] in {"auth", "logout"}:
        try:
            return account_command(args, parser)
        except (OSError, ValueError, RuntimeError) as error:
            status("Error", str(error), "31", stream=sys.stderr)
            return 1
        except (KeyboardInterrupt, EOFError):
            status("Cancelled", "Stopped", "33", stream=sys.stderr)
            return 130
    is_batch = bool(args.link and args.link[0] == "batch")
    batch_links = args.link[1:] if is_batch else []
    if args.link and not is_batch and not any("http://" in item or "https://" in item for item in args.link) and args.link not in (["doctor"], ["sites"]):
        parser.error("Unknown command. Use linkget sites, linkget auth, linkget config, or linkget help. To download, paste a post URL.")
    args.link = " ".join(args.link)
    if args.link == "doctor":
        return doctor()
    if args.link == "sites":
        for site, content in [("Instagram", "Photos, videos and reels"), ("X / Twitter", "Post photos and videos"), ("Bilibili", "BV/av videos and Opus media"), ("TikTok", "Post photos and videos; share links"), ("Douyin", "Post photos and videos; share links"), ("Kuaishou", "Post videos; image galleries not supported"), ("Xiaohongshu", "Note photos and videos; xhslink shares"), ("Weibo", "Post photos and videos; t.cn shares"), ("YouTube", "Videos and Shorts; youtu.be links"), ("Reddit", "Post photos and videos"), ("Lofter", "Post photos and videos (trial)"), ("Pixiv", "Artwork images (trial); Ugoira not supported"), ("Pinterest", "Pins and boards")]:
            status(site, content)
        print("\n  Media only. Post text is not saved. Availability varies by URL.")
        return 0
    if args.profile and args.browser in {"auto", "none"}:
        parser.error("--profile requires an explicit --browser, such as chrome or firefox.")
    try:
        if args.report and args.report.expanduser().exists():
            raise ValueError("The report already exists. Choose a new path to preserve the comparison reference.")
        if args.report and not args.report.expanduser().parent.is_dir():
            raise ValueError("The report's parent folder does not exist. Choose an existing folder.")
        if args.photos:
            args.folder = None
        elif args.folder is True:
            args.folder = Path.cwd()
        elif args.folder is None:
            setting = configured_destination()
            if setting["mode"] == "cwd":
                args.folder = Path.cwd()
            elif setting["mode"] == "folder":
                args.folder = Path(setting["path"])
        if args.folder is not None and args.date_now:
            parser.error("--date-now applies to Photos imports. Use --photos --date-now to override a folder default.")
        records = []
        code = batch_download(args, parser, batch_links, records) if is_batch else download(args, first_run(args) or {}, records)
        if args.report:
            write_report(args.report, VERSION, records, code)
            status("Report", str(args.report.expanduser().resolve()))
        return code
    except (OSError, ValueError, RuntimeError) as error:
        report_failure(str(error))
        return 1
    except (KeyboardInterrupt, EOFError):
        status("Cancelled", "Download stopped", "33", stream=sys.stderr)
        return 130


def batch_download(args, parser, links, records=None):
    if not links:
        if sys.stdin.isatty():
            print("\n  Paste one link or sharing text per line. Blank line starts downloads.\n")
            while line := input("  > ").strip():
                links.append(line)
        else:
            links = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    if not links:
        parser.error("The batch is empty. Supply links or paste one per line.")
    setup_results = first_run(args) or {}
    completed, failed = 0, []
    for index, link in enumerate(links, 1):
        status("Item", f"{index}/{len(links)}")
        item_args = argparse.Namespace(**vars(args))
        item_args.link = link
        result = download(item_args, setup_results, records)
        if result == 130:
            status("Batch", f"{completed} completed · {len(failed)} failed · {len(links) - index + 1} not completed", "33")
            return 130
        if result:
            failed.append(index)
        else:
            completed += 1
    status("Batch", f"{completed} completed · {len(failed)} failed", "33" if failed else "32")
    if failed:
        status("Failed items", ", ".join(map(str, failed)), "33")
    return 1 if failed else 0


def download(args, setup_results, records=None):
    stage = None
    session_dir = None
    info_path = None
    evidence = {"status": "failed", "reference": None, "files": []}
    try:
        url = args.link or (input("  Paste a link (no quotes needed): ") if sys.stdin.isatty() else sys.stdin.read())
        started = time.monotonic()
        with activity("Resolving") if any(host in url for host in ("v.douyin.com", "vm.tiktok.com", "vt.tiktok.com", "tiktok.com/t/", "xhslink.com", "xhslink.cn", "t.cn/")) else nullcontext():
            url = normalize_link(url)
        site, engine, domain = route(url)
        evidence.update(reference=reference(url), site=site)
        if not args.folder and sys.platform != "darwin":
            raise ValueError("Photos is available on macOS only. Use --folder to choose a destination.")
        required = ([] if engine in {"douyin", "xiaohongshu", "kuaishou", "pixiv"} else [engine]) + (["ffmpeg", "ffprobe"] if engine == "yt-dlp" else [])
        if domain == "reddit.com" and engine == "gallery-dl":
            required.extend(["yt-dlp", "ffmpeg"])
        if domain == "youtube.com":
            required.append("deno")
        cookie_spec = browser_spec(args, "gallery-dl", domain) if args.browser not in {"auto", "none"} else None
        if cookie_spec and "gallery-dl" not in required:
            required.append("gallery-dl")
        tools = {name: find_tool(name) for name in required}
        missing = [name for name, path in tools.items() if not path]
        if missing:
            raise RuntimeError(installation_hint(missing))
        folder = args.folder.expanduser().resolve() if args.folder else None
        if folder:
            folder.mkdir(parents=True, exist_ok=True)
        destination = str(folder) if folder else "Photos"
        status("Source", site)
        status("Destination", destination)
        cookie_path = None
        if cookie_spec:
            session_dir = tempfile.TemporaryDirectory(prefix="linkget-session-")
            cookie_path = Path(session_dir.name) / "cookies.txt"
            session = setup_results.get(domain)
            if session is None:
                with activity("Preparing"):
                    session = prepare_session(tools["gallery-dl"], cookie_spec, cookie_path, domain)
            elif session.state == "valid":
                accounts.copy_saved(domain, cookie_path)
            if session.state != "valid":
                if recover_login(args, domain, session.detail) and accounts.copy_saved(domain, cookie_path):
                    pass
                else:
                    raise LoginRequired(f"{site}: {session.detail}")
            else:
                accounts.save(cookie_path, domain)
                accounts.remember_source(domain, cookie_spec)
        used_browser = cookie_path is not None
        if domain == "bilibili.com" and args.browser == "auto" and domain not in accounts.preferences().get("blocked", []):
            session_dir = tempfile.TemporaryDirectory(prefix="linkget-session-")
            candidate = Path(session_dir.name) / "cookies.txt"
            if accounts.copy_saved(domain, candidate):
                cookie_path = candidate
        stage = Path(tempfile.mkdtemp(prefix="linkget-"))
        tried_saved = cookie_path is not None
        tried_browser = used_browser or domain in setup_results
        attempt = 0
        while True:
            try:
                source_notes = []
                download_info = DownloadResult()
                media_dir = stage / ("media" if attempt == 0 else f"signed-in-media-{attempt}")
                media_dir.mkdir()
                print()
                with (stage / "download.log").open("w") as log:
                    command = None
                    if engine not in {"douyin", "xiaohongshu", "kuaishou", "pixiv"}:
                        command = command_for(args, url, engine, domain, media_dir, tools, cookie_path)
                    if engine == "yt-dlp":
                        info_path = stage / "video-info.json"
                        with activity("Preparing"):
                            command = prepare_video(command, info_path, log, domain)
                        quality = selected_quality(info_path)
                        if quality:
                            status("Quality", quality)
                    with activity("Downloading", media_dir):
                        if engine in {"douyin", "xiaohongshu", "kuaishou", "pixiv"}:
                            try:
                                if engine == "douyin":
                                    downloader = download_douyin
                                elif engine == "xiaohongshu":
                                    downloader = download_xiaohongshu
                                elif engine == "pixiv":
                                    downloader = download_pixiv
                                else:
                                    downloader = download_kuaishou
                                notes = downloader(url, media_dir, cookie_path)
                                if isinstance(notes, DownloadResult):
                                    download_info = notes
                                    source_notes.extend(notes.notes)
                                elif isinstance(notes, list):
                                    source_notes.extend(notes)
                            except (BrowserRequired, KuaishouBrowserRequired, PixivBrowserRequired) as error:
                                raise LoginRequired(str(error)) from None
                            except RuntimeError as error:
                                log.write(str(error) + "\n")
                                raise
                            result = None
                        else:
                            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
                if result is not None and result.returncode:
                    if domain == "weibo.com" and engine == "gallery-dl":
                        for metadata in media_dir.glob("*.json"):
                            metadata.unlink()
                    tail = (stage / "download.log").read_text(errors="replace").splitlines()[-8:]
                    log_text = "\n".join(tail)
                    if needs_login(log_text):
                        raise LoginRequired(f"{site} requires a signed-in account to download this post.")
                    if domain == "reddit.com" and "blocked by network security" in log_text.lower():
                        raise LoginRequired(f"{site} requires a signed-in account to download this post. Run linkget auth reddit to connect a Reddit login.")
                    if any(msg in log_text for msg in ("Video unavailable", "HTTP Error 404: Not Found", "not found")):
                        raise RuntimeError(f"This {site} post is unavailable, deleted, or private.")
                    if "blocked by copyright" in log_text.lower() or "region" in log_text.lower():
                        raise RuntimeError(f"This {site} post is blocked due to copyright or region restrictions.")
                    raise RuntimeError("Download failed.\n" + log_text)
                if engine == "gallery-dl":
                    # Weibo's clean-image comparison needs its source sidecars.
                    if domain == "weibo.com":
                        download_info = gallery_evidence(media_dir, domain, remove=False)
                        with activity("Checking sources"):
                            source_notes = prefer_clean_images(media_dir, find_tool("ffmpeg"))
                        download_info.live_pairs = [(image if (media_dir / image).exists() else str(Path(image).with_name(Path(image).stem + "_preferred" + Path(image).suffix)), video)
                                                    for image, video in download_info.live_pairs]
                    else:
                        download_info = gallery_evidence(media_dir, domain)
                for note in source_notes:
                    status("Media", note)
                files = sorted(path for path in media_dir.iterdir() if path.is_file() and path.suffix.lower() in MEDIA)
                unfinished = [path for path in media_dir.iterdir() if path.suffix.lower() not in MEDIA]
                if unfinished:
                    raise RuntimeError("The download contains unsupported or incomplete files. Files have been kept for review.")
                if not files:
                    report = (stage / "download.log").read_text(errors="replace")
                    if needs_login(report):
                        raise LoginRequired(f"{site} requires a signed-in account to download this post.")
                    if domain == "tiktok.com":
                        raise RuntimeError("TikTok returned no downloadable media. Open the post in your browser and check its availability or website verification, then retry.")
                    raise RuntimeError("No photos or videos were downloaded from this post.")
                if download_info.expected_count is not None and len(files) != download_info.expected_count:
                    raise RuntimeError(f"Incomplete media set: {len(files)} / {download_info.expected_count} files. Files have been kept.")
                break
            except DouyinMediaUnavailable:
                # A missing public response can still work with an already
                # authorized session. Do not infer expiry or prompt for login.
                if (args.browser == "auto" and not tried_saved
                        and domain not in accounts.preferences().get("blocked", [])):
                    tried_saved = True
                    if session_dir is None:
                        session_dir = tempfile.TemporaryDirectory(prefix="linkget-session-")
                    candidate = Path(session_dir.name) / "cookies.txt"
                    if accounts.copy_saved(domain, candidate):
                        cookie_path = candidate
                        attempt += 1
                        continue
                raise
            except LoginRequired as failure:
                if args.browser == "none":
                    raise
                if session_dir is None:
                    session_dir = tempfile.TemporaryDirectory(prefix="linkget-session-")
                candidate = Path(session_dir.name) / "cookies.txt"
                attempt += 1
                if not tried_saved:
                    tried_saved = True
                    if accounts.copy_saved(domain, candidate):
                        cookie_path = candidate
                        continue
                reason = str(failure)
                if not tried_browser and sys.stdin.isatty():
                    tried_browser = True
                    exporter = find_tool("gallery-dl")
                    if exporter:
                        with activity("Checking"):
                            state, _ = accounts.import_auto(exporter, domain)
                        if state.state == "valid" and accounts.copy_saved(domain, candidate):
                            cookie_path = candidate
                            continue
                        reason = state.detail
                if recover_login(args, domain, reason) and accounts.copy_saved(domain, candidate):
                    tried_browser = True
                    cookie_path = candidate
                    continue
                raise LoginRequired(reason) from None
        media_records = inspect_files(files, download_info.variants)
        evidence.update(files=media_records, expected_count=download_info.expected_count,
                        missing_motion=download_info.missing_motion, live_pairs=download_info.live_pairs,
                        highest_quality="not_verified", watermark="not_verified")
        if any(record.get('probe_ok') is False for record in media_records):
            raise RuntimeError("The downloaded media did not pass format validation. Files have been kept; nothing was saved or imported.")
        status("Quality", quality_summary(media_records))
        count_text = f"{len(files)} / {download_info.expected_count} files" if download_info.expected_count is not None else f"{len(files)} files · source count unknown"
        status("Media count", count_text)
        if any(r['source'] == "fallback" for r in media_records):
            status("Source", "Fallback media included", "33")
        prepared = []
        if download_info.live_pairs:
            if args.live_photos:
                with activity("Pairing Live Photos"):
                    prepared = prepare_pairs(files, download_info.live_pairs, stage / "live-photos")
                status("Live Photos", f"{len(prepared)} source pairs recognized by macOS")
            else:
                status("Live Photos", f"{len(download_info.live_pairs)} pairs saved separately · use --live-photos to pair")
        with activity("Saving" if folder else "Importing"):
            if folder:
                saved, skipped = save_folder(files, folder)
                if prepared:
                    live_saved, live_skipped = save_pairs(prepared, folder / "Live Photos")
                    evidence['paired_exports'] = len(live_saved) // 2
                    status("Live exports", f"{len(live_saved) // 2} saved · {live_skipped // 2} duplicates")
            else:
                if prepared:
                    saved, skipped = import_photos(files, args.date_now, dict(zip(download_info.live_pairs, prepared)))
                else:
                    saved, skipped = import_photos(files, args.date_now)
            if saved:
                summary = f"{saved_description(saved)} · {format_size(sum(path.stat().st_size for path in saved))}"
            else:
                summary = "No new files"
            shutil.rmtree(stage)
            stage = None
        status("Done", f"{summary} · {time.monotonic() - started:.1f}s", "32")
        if skipped:
            status("Skipped", f"{skipped} duplicate{'s' if skipped != 1 else ''}")
        evidence.update(status="completed", saved_count=len(saved), skipped_count=skipped)
        return 0
    except LoginRequired as error:
        evidence['failure'] = "login_required"
        login_guidance(args, domain, url, str(error))
        return 1
    except subprocess.TimeoutExpired:
        evidence['failure'] = report_failure("The website request timed out.")
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        evidence['failure'] = report_failure(str(error))
        return 1
    except (KeyboardInterrupt, EOFError):
        evidence['status'] = "cancelled"
        status("Cancelled", "Download stopped", "33", stream=sys.stderr)
        return 130
    finally:
        if records is not None:
            records.append(evidence)
        if info_path is not None:
            info_path.unlink(missing_ok=True)
        if session_dir is not None:
            session_dir.cleanup()
        if stage is not None:
            if domain == "weibo.com":
                for metadata in stage.glob("*/*.json"):
                    metadata.unlink(missing_ok=True)
            status("Files kept", str(stage), "33", stream=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
