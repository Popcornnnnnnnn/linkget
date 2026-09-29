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
    from .photos import originals_directory, retain_originals
    from .version import VERSION
    from .session import LOGIN_URLS, prepare_session
    from .links import normalize_link
    from .douyin import download as download_douyin
else:  # Homebrew launches this file directly.
    import accounts
    from photos import originals_directory, retain_originals
    from version import VERSION
    from session import LOGIN_URLS, prepare_session
    from links import normalize_link
    from douyin import download as download_douyin

VIDEO = {".mp4", ".mov", ".m4v"}
MEDIA = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif", ".tif", ".tiff"} | VIDEO
SITES = "Instagram, X/Twitter, Bilibili, TikTok and Douyin posts (photos and videos)"


class LoginRequired(RuntimeError):
    pass


def needs_login(message):
    """Match explicit authentication failures, not generic 403/429/network errors."""
    return bool(re.search(
        r"login[_ -]required|log[ -]?in (?:is )?required|(?:please|must|need to) (?:log[ -]?in|sign in)|"
        r"(?:account|login) credentials (?:required|needed)|invalid login credentials|"
        r"(?:cookies|authentication|authorization)['\"]? (?:are |is )?(?:required|needed)|"
        r"fresh cookies.*needed|登录后|请(?:先)?登录|需要登录",
        message, re.I))


def login_guidance(args, domain, url, reason):
    status("Stopped", reason, "33", stream=sys.stderr)
    if args.browser == "none":
        status("Next", "Public-only mode is enabled. Run without --browser none to use a browser login.", stream=sys.stderr)
    elif not sys.stdin.isatty():
        status("Sign in", LOGIN_URLS[domain], stream=sys.stderr)
        status("Next", "Run linkget in an interactive terminal to connect a browser and continue.", stream=sys.stderr)


def login_result(site, state, saved=False):
    if state.state == "valid":
        status(site, "Connected", "32")
    elif state.state == "missing":
        status(site, "Not connected" if state.detail == "Not saved" else "Not signed in")
    elif state.state == "unverified":
        detail = ("Network error · login unchanged" if saved and state.detail == "Network check failed"
                  else "Could not verify: " + state.detail)
        status(site, detail, "33")
    else:
        status(site, state.detail, "33")


def show_logins():
    settings = accounts.preferences()
    states = {}
    pending = {site: domain for site, domain in accounts.SITES.items()
               if domain not in settings.get("blocked", [])}
    results = {}
    if pending:
        with activity("Checking"), ThreadPoolExecutor(max_workers=len(pending)) as executor:
            futures = {site: executor.submit(accounts.status, domain) for site, domain in pending.items()}
            results = {site: future.result() for site, future in futures.items()}
    for site, domain in accounts.SITES.items():
        if domain in settings.get("blocked", []):
            status(site, "Disconnected")
            states[site] = "disconnected"
        else:
            state = results[site]
            login_result(site, state, saved=True)
            states[site] = state.state
    return states


def account_command(args, parser):
    command, *sites = args.link
    if command == "auth":
        if sites:
            parser.error("Use linkget auth, then choose a website to connect.")
        if args.profile and args.browser in {"auto", "none"}:
            parser.error("--profile requires an explicit --browser.")
        states = show_logins()
        while sys.stdin.isatty() and args.browser != "none":
            if all(state == "valid" for state in states.values()):
                break
            default = next((site for site, state in states.items() if state == "disconnected"), None)
            if default is None:
                default = next((site for site, state in states.items() if state in {"missing", "invalid"}), None)
            prompt = (f"\n  Press Enter to connect {default}.\n"
                      "  To connect a different website, type its name (e.g. instagram).\n"
                      "  Type q and press Enter to exit.\n  > " if default else
                      "\n  To connect a website, type its name (e.g. bilibili).\n"
                      "  Press Enter to exit.\n  > ")
            site = input(prompt).strip().lower()
            if site == "q" or (not site and default is None):
                break
            site = site or default
            if site not in accounts.SITES:
                print("  Choose: " + ", ".join(accounts.SITES))
                continue
            if recover_login(args, accounts.SITES[site], "Connect " + site, proactive=True):
                status(site, "Connected", "32")
                states[site] = "valid"
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


def browser_notice(browser):
    if sys.platform == "darwin" and browser in {"chrome", "edge", "brave"}:
        name = "Microsoft Edge" if browser == "edge" else browser.capitalize()
        status("Access", f"macOS may ask to read {name} Safe Storage.")
        print("              In the 'security' prompt, enter your Mac login password.")
        print("              Allow: this read. Always Allow: remember permission.")
    elif sys.platform == "darwin" and browser == "safari":
        status("Access", "Safari data may require Full Disk Access for your terminal in System Settings > Privacy & Security.")
    else:
        status("Access", f"Reading website logins from {browser.capitalize()}; your website passwords are not requested.")


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
        browser_notice(browser)
        with activity("Connecting"):
            results = accounts.connect_browser(tool, browser, profile)
        if all(state.state == "unavailable" for state in results.values()):
            status(browser.capitalize(), next(iter(results.values())).detail, "33")
        else:
            for site, domain in accounts.SITES.items():
                login_result(site, results[domain])
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
                status("Next", f"Allow access to {browser.capitalize()}, or choose another browser.")
            elif "Network" in reason or "Rate limited" in reason or "Website" in reason or "Unexpected" in reason:
                status("Next", "Check your connection or complete website verification in your browser, then retry.")
            else:
                status("Sign in", LOGIN_URLS[domain])
                status("Next", f"Sign in using {browser.capitalize()}" + (" (the selected profile)" if profile else "") + ", then return here.")
            answer = input("  Enter to continue · b to change browser · q to cancel: ").strip().lower()
            if answer == "q":
                return False
            if answer == "b":
                browser = None
                continue
        browser_notice(browser)
        specification = browser + "/" + domain + (":" + str(Path(profile).expanduser()) if profile else "")
        with activity("Checking"):
            state = accounts.import_browser(tool, specification, domain)
        if state.state == "valid":
            accounts.set_preferences(browser=browser, profile=profile, setup=True)
            return True
        reason = state.detail
        status("Could not connect", reason, "33")


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
    if result.returncode:
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


def status(label, value, color="0", stream=None):
    """Aligned terminal output; redirected output remains plain text."""
    stream = stream if stream is not None else sys.stdout
    field = f"{label:<12}"
    if stream.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb":
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
    if host == "b23.tv":
        raise ValueError("Use the full bilibili.com URL. b23.tv short links are not supported yet.")
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
        command = [tools[engine], "--config-ignore", "--directory", str(media_dir)]
        if domain == "tiktok.com":
            command.extend(["-o", "extractor.tiktok.audio=false", "-o", "extractor.tiktok.covers=false",
                            "-o", "extractor.tiktok.subtitles=false", "-o", "extractor.tiktok.videos=true",
                            "-o", "extractor.tiktok.photos=true", "-o", "extractor.tiktok.filename=tiktok_{id}_{num:03d}.{extension}"])
    else:
        command = [tools[engine], "--ignore-config", "--no-playlist", "--no-progress",
                   "--ffmpeg-location", str(Path(tools["ffmpeg"]).parent),
                   "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
                   "-S", "res,fps,hdr", "--merge-output-format", "mp4", "-o", str(media_dir / "bilibili_%(id)s.%(ext)s")]
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


def import_photos(files, date_now):
    retained = retain_originals(files)
    unique = list(dict.fromkeys(retained))
    source_indexes = [retained.index(path) for path in unique]
    importer = Path(__file__).with_name("import.applescript")
    result = subprocess.run(["/usr/bin/osascript", str(importer),
                             "--date-now" if date_now else "--keep-date",
                             *map(str, unique)], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError("Photos could not import the media. Allow terminal access in System Settings > Privacy & Security > Automation, or use --folder for unsupported formats.\n"
                           + "Originals are safe in: " + str(originals_directory()) + "\n" + result.stderr.strip())
    response = result.stdout.strip()
    if not re.fullmatch(r"\d+:\d+(?::\d+)*", response):
        raise RuntimeError("Photos returned an invalid import result. Original files have been kept.")
    saved, skipped, *indexes = map(int, response.split(":"))
    if (saved + skipped != len(unique) or saved != len(indexes)
            or len(set(indexes)) != len(indexes)
            or any(index < 1 or index > len(unique) for index in indexes)):
        raise RuntimeError("Photos returned an unexpected item count. Original files have been kept.")
    return [files[source_indexes[index - 1]] for index in indexes], skipped + len(files) - len(unique)


def doctor():
    missing = []
    print("  Tools\n")
    for name in ("gallery-dl", "yt-dlp", "ffmpeg", "ffprobe"):
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="linkget",
        description="Save photos and videos from supported sites to macOS Photos or a folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Commands:
  help                 Show this help (also -h or --help)
  sites                List supported sites and media
  doctor               Check download tools and installation
  auth                 View website logins and connect a website
  logout SITE          Remove a saved login and disable automatic browser access
  logout all           Disconnect all websites

Account sites: instagram, x, bilibili, tiktok, douyin

Examples:
  linkget                              Paste a link or sharing text at the prompt
  linkget https://v.douyin.com/CODE/    Save to Photos
  linkget URL --folder                 Save to the current folder
  linkget URL --folder ~/Downloads     Save to a folder
  linkget auth                         View or connect website logins

First use guides you through connecting a browser. You can skip it.
Downloads use public access first, then your connected logins when needed.
Paste sharing text at the prompt to avoid shell quoting and special characters.""")
    parser.add_argument("link", nargs="*", metavar="LINK_OR_COMMAND", help="post URL, sharing text, or a command listed below")
    parser.add_argument("--version", action="version", version="%(prog)s " + VERSION)
    parser.add_argument("--folder", nargs="?", const=Path("."), type=Path,
                        help="save to FOLDER, or the current folder if omitted; without --folder, save to Photos")
    parser.add_argument("--browser", choices=["auto", "none", "chrome", "firefox", "safari", "edge", "brave"], default="auto", help="auto: public first, then saved login and browser refresh if needed; none: public only; specify a browser to use its login")
    parser.add_argument("--profile", help="path to the selected browser profile")
    parser.add_argument("--date-now", action="store_true", help="set the date of newly imported Photos items to now")
    tokens = list(sys.argv[1:] if argv is None else argv)
    # An HTTP URL immediately after bare --folder is the post, never a directory.
    for index in range(len(tokens) - 2, -1, -1):
        if tokens[index] == "--folder" and tokens[index + 1].startswith(("http://", "https://")):
            tokens.insert(index + 1, ".")
    args = parser.parse_intermixed_args(tokens)
    if args.link == ["help"]:
        parser.print_help()
        return 0
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
    if args.link and not any("http://" in item or "https://" in item for item in args.link) and args.link not in (["doctor"], ["sites"]):
        parser.error("Unknown command. Use linkget sites, linkget auth, or linkget help. To download, paste a post URL.")
    args.link = " ".join(args.link)
    if args.link == "doctor":
        return doctor()
    if args.link == "sites":
        for site, content in [("Instagram", "Photos, videos and reels"), ("X / Twitter", "Post photos and videos"), ("Bilibili", "BV/av videos and Opus media"), ("TikTok", "Post photos and videos; share links"), ("Douyin", "Post photos and videos; share links")]:
            status(site, content)
        print("\n  Media only. Post text is not saved. Availability varies by URL.")
        return 0
    if args.folder and args.date_now:
        parser.error("--date-now applies to Photos imports and cannot be used with --folder.")
    if args.profile and args.browser in {"auto", "none"}:
        parser.error("--profile requires an explicit --browser, such as chrome or firefox.")
    stage = None
    session_dir = None
    info_path = None
    try:
        setup_results = first_run(args) or {}
        url = args.link or (input("  Paste a link (no quotes needed): ") if sys.stdin.isatty() else sys.stdin.read())
        started = time.monotonic()
        with activity("Resolving") if any(host in url for host in ("v.douyin.com", "vm.tiktok.com", "vt.tiktok.com", "tiktok.com/t/")) else nullcontext():
            url = normalize_link(url)
        site, engine, domain = route(url)
        if not args.folder and sys.platform != "darwin":
            raise ValueError("Photos is available on macOS only. Use --folder to choose a destination.")
        required = ([] if engine == "douyin" else [engine]) + (["ffmpeg", "ffprobe"] if engine == "yt-dlp" else [])
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
                browser_notice(args.browser)
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
        if engine == "yt-dlp" and args.browser == "auto" and domain not in accounts.preferences().get("blocked", []):
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
                media_dir = stage / ("media" if attempt == 0 else f"signed-in-media-{attempt}")
                media_dir.mkdir()
                print()
                with (stage / "download.log").open("w") as log:
                    command = None
                    if engine != "douyin":
                        command = command_for(args, url, engine, domain, media_dir, tools, cookie_path)
                    if engine == "yt-dlp":
                        info_path = stage / "video-info.json"
                        with activity("Preparing"):
                            command = prepare_bilibili(command, info_path, log)
                        quality = selected_quality(info_path)
                        if quality:
                            status("Quality", quality)
                    with activity("Downloading", media_dir):
                        if engine == "douyin":
                            try:
                                download_douyin(url, media_dir, cookie_path)
                            except RuntimeError as error:
                                log.write(str(error) + "\n")
                                raise
                            result = None
                        else:
                            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
                if result is not None and result.returncode:
                    tail = (stage / "download.log").read_text(errors="replace").splitlines()[-8:]
                    if needs_login("\n".join(tail)):
                        raise LoginRequired(f"{site} requires a signed-in account to download this post.")
                    raise RuntimeError("Download failed.\n" + "\n".join(tail))
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
                break
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
                        settings = accounts.preferences()
                        specification = settings.get("sources", {}).get(domain)
                        browser = specification.split("/", 1)[0] if specification else settings.get("browser")
                        if browser and domain not in settings.get("blocked", []):
                            browser_notice(browser)
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
        with activity("Saving" if folder else "Importing"):
            if folder:
                saved, skipped = save_folder(files, folder)
            else:
                saved, skipped = import_photos(files, args.date_now)
            if saved:
                summary = f"{saved_description(saved)} · {format_size(sum(path.stat().st_size for path in saved))}"
            else:
                summary = "No new files"
            shutil.rmtree(stage)
            stage = None
        status("Done", f"{summary} · {time.monotonic() - started:.1f}s", "32")
        if not folder:
            status("Originals", str(originals_directory()))
        if skipped:
            status("Skipped", f"{skipped} duplicate{'s' if skipped != 1 else ''}")
        return 0
    except LoginRequired as error:
        login_guidance(args, domain, url, str(error))
        return 1
    except subprocess.TimeoutExpired:
        status("Error", "The website did not respond in time. Check your connection and retry.", "31", stream=sys.stderr)
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        status("Error", str(error), "31", stream=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        status("Cancelled", "Download stopped", "33", stream=sys.stderr)
        return 130
    finally:
        if info_path is not None:
            info_path.unlink(missing_ok=True)
        if session_dir is not None:
            session_dir.cleanup()
        if stage is not None:
            status("Files kept", str(stage), "33", stream=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
