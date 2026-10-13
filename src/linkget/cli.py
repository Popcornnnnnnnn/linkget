"""Portable CLI; download engines are installed separately (normally by Homebrew)."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

if __package__:
    from . import accounts, jobs, destinations, preflight, recovery, staging
    from .runtime import _batch, batch_lock, check_batch_cancelled, batch_interaction, run_process
    from .output import status, activity, terminal_styles, emphasis, format_size, downloaded_bytes, saved_description, batch_media, saving_activity
    from .batch import run_batch
    from .engines import route, browser_spec, command_for, restricted_bilibili_quality, prepare_bilibili, selected_quality, prepare_video, LoginRequired, needs_login
    from .photos import originals_directory, retain_originals, verify_library_copies
    from .version import VERSION
    from .session import LOGIN_URLS, prepare_session
    from .links import normalize_link
    from .douyin import download as download_douyin, MediaUnavailable as DouyinMediaUnavailable
    from .douyin_browser import read_item as read_douyin_browser
    from .douyin import DownloadInterrupted as DouyinDownloadInterrupted
    from .xiaohongshu import download as download_xiaohongshu, BrowserRequired
    from .kuaishou import download as download_kuaishou, BrowserRequired as KuaishouBrowserRequired
    from .pixiv import download as download_pixiv, BrowserRequired as PixivBrowserRequired
    from .weibo import prefer_clean_images
    from .media import DownloadResult, inspect_files, quality_summary, gallery_evidence, reference, compare_reports, write_report
    from .live import prepare_pairs, save_pairs
else:  # Homebrew launches this file directly.
    import accounts, jobs, destinations, preflight, recovery, staging
    from runtime import _batch, batch_lock, check_batch_cancelled, batch_interaction, run_process
    from output import status, activity, terminal_styles, emphasis, format_size, downloaded_bytes, saved_description, batch_media, saving_activity
    from batch import run_batch
    from engines import route, browser_spec, command_for, restricted_bilibili_quality, prepare_bilibili, selected_quality, prepare_video, LoginRequired, needs_login
    from photos import originals_directory, retain_originals, verify_library_copies
    from version import VERSION
    from session import LOGIN_URLS, prepare_session
    from links import normalize_link
    from douyin import download as download_douyin, MediaUnavailable as DouyinMediaUnavailable
    from douyin_browser import read_item as read_douyin_browser
    from douyin import DownloadInterrupted as DouyinDownloadInterrupted
    from xiaohongshu import download as download_xiaohongshu, BrowserRequired
    from kuaishou import download as download_kuaishou, BrowserRequired as KuaishouBrowserRequired
    from pixiv import download as download_pixiv, BrowserRequired as PixivBrowserRequired
    from weibo import prefer_clean_images
    from media import DownloadResult, inspect_files, quality_summary, gallery_evidence, reference, compare_reports, write_report
    from live import prepare_pairs, save_pairs

VIDEO = {".mp4", ".mov", ".m4v"}
MEDIA = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".heif", ".avif", ".tif", ".tiff"} | VIDEO
SITES = "Instagram, X/Twitter, Bilibili, TikTok, Douyin, Kuaishou, Xiaohongshu, Weibo, Reddit, Lofter, Pixiv and Pinterest posts; YouTube videos and Shorts"


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
        if domain == "douyin.com":
            status(site, "Public access · Chrome assist when needed", "1")
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
    return batch_interaction(lambda: recover_login_interactive(args, domain, reason, proactive))


def recover_login_interactive(args, domain, reason, proactive=False):
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


def sign_in_link(domain, stream=None):
    stream = stream if stream is not None else sys.stdout
    site = next(site for site, site_domain in accounts.SITES.items() if site_domain == domain)
    url = LOGIN_URLS[domain]
    # Keep the full URL readable/copyable even when OSC 8 links are unsupported.
    link = f"\033]8;;{url}\033\\{url}\033]8;;\033\\" if terminal_styles(stream) else url
    status("Sign in", f"{emphasis(site, stream)} · {link}", stream=stream)


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
    if os.environ.get('LINKGET_GUI_JOB') == '1':
        try:
            os.setsid()
        except OSError:
            pass
    tokens = list(sys.argv[1:] if argv is None else argv)
    if tokens and tokens[0] == 'read':
        if __package__:
            from .read_tasks import main as read_content
        else:
            from read_tasks import main as read_content
        return read_content(tokens[1:])
    parser = argparse.ArgumentParser(
        prog="linkget",
        description="Save photos and videos from supported sites to macOS Photos or a folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Commands:
  help                 Show this help (also -h or --help)
  sites                List supported sites and media
  read URL --folder D  Read/transcribe in the background (read --help)
  read status [TASK]   Show background reading progress and results
  read logs TASK       Show a background read task's log
  read cancel TASK     Cancel a background read task; preserve checkpoints
  read batch [LINK ...] Read multiple posts or local files in the background
  read translate DIR   Translate saved text with the configured model
  read summarize DIR   Summarize saved text with source references
  read recover         List unfinished reads and retained checkpoints
  read retry [DIR]     Resume a retained unfinished read in the background
  read config          View/change recognition, translation and fallback settings
  read doctor          Check local recognition tools and model
  doctor               Check download tools and installation
  config               View or change the default save destination
  batch [LINK ...]     Download all links concurrently; paste one per line if omitted
  retry [TASK]         Retry unfinished items from the latest or specified task
  recover [TASK]       List kept files, or save a complete retained task
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
  linkget read URL                     Submit a background content read
  linkget read recording.m4a           Transcribe local audio in the background
  linkget read translate DIR --language zh Translate saved text into Chinese
  linkget read status TASK             Check progress and find result paths
  linkget read URL --foreground        Wait for completion and its exit code
  linkget auth                         Check website logins and public access
  linkget auth bilibili                Connect Bilibili for higher quality
  linkget config                       View the default save destination
  linkget config --photos              Default to Photos (initial setting)
  linkget config --folder              Default to the current folder each time
  linkget config --folder ~/Downloads  Default to a fixed folder

Read processing runs in the background by default and returns a task ID.
Closing the terminal does not stop it; use read status/logs/cancel to manage it.
Use --foreground with read/translate/summarize when a script needs to wait.
Configured AI fallback handles provider outages; read config --help lists settings.

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
    parser.add_argument("--jobs", type=int, metavar="N", help="limit simultaneous batch downloads; default: all links")
    parser.add_argument("--verify", action="store_true", help="recover: verify existing Photos originals before cleaning staged copies")
    parser.add_argument("--item", type=int, metavar="N", help="recover: save or verify only one unfinished item")
    parser.add_argument("--redownload", action="store_true", help="retry: download again instead of reusing a complete retained set")
    tokens = list(sys.argv[1:] if argv is None else argv)
    # An HTTP URL immediately after bare --folder is the post, never a directory.
    for index in range(len(tokens) - 2, -1, -1):
        if tokens[index] == "--folder" and tokens[index + 1].startswith(("http://", "https://")):
            tokens.insert(index + 1, ".")
    args = parser.parse_intermixed_args(tokens)
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be a positive integer.")
    if args.item is not None and args.item < 1:
        parser.error("--item must be a positive integer.")
    if args.redownload and (not args.link or args.link[0] != "retry"):
        parser.error("--redownload applies to linkget retry only.")
    if (args.verify or args.item is not None) and (not args.link or args.link[0] != "recover"):
        parser.error("--verify and --item apply to linkget recover TASK only.")
    if args.link and args.link[0] in {"retry", "recover"}:
        command, *identifiers = args.link
        if len(identifiers) > 1:
            parser.error("Use linkget " + command + " [TASK].")
        try:
            if command == "recover" and not identifiers:
                if args.verify or args.item:
                    parser.error("Select a task: linkget recover TASK.")
                return recovery.listing(status)
            journal = jobs.select(identifiers[0] if identifiers else None)
            restore_options(args, journal, tokens)
            preflight.destination(args, find_tool)
            if command == "recover":
                return recovery.restore(journal, args, status, save_folder, import_photos)
            args._journal = journal
            args._indices = journal.incomplete()
            links = [journal.data["items"][i - 1]["input"] for i in args._indices]
            records = []
            code = batch_download(args, parser, links, records) if links else 0
            if args.report:
                write_report(args.report, VERSION, records, code)
            return code
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            report_failure(str(error))
            return 1
        except (KeyboardInterrupt, EOFError):
            status("Cancelled", "Recovery stopped; retained files are safe", "33")
            return 130
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
        code = doctor()
        if args.folder is not None or args.photos or args.live_photos:
            if args.folder is True:
                args.folder = Path.cwd()
            try:
                preflight.destination(args, find_tool)
                status("Destination", "Writable · " + str(args.folder or originals_directory()))
                if args.photos:
                    status("Photos", "Automation and managed-copy checks run at import time")
            except (OSError, ValueError, RuntimeError) as error:
                report_failure(str(error))
                return 1
        return code
    if args.link == "sites":
        for site, content in [("Instagram", "Photos, videos and reels"), ("X / Twitter", "Post photos and videos"), ("Bilibili", "BV/av videos and Opus media"), ("TikTok", "Post photos and videos; share links"), ("Douyin", "Post photos and videos; share links"), ("Kuaishou", "Post videos; image galleries not supported"), ("Xiaohongshu", "Note photos and videos; xhslink shares"), ("Weibo", "Post photos and videos; t.cn shares"), ("YouTube", "Videos and Shorts; youtu.be links"), ("Reddit", "Post photos and videos"), ("Lofter", "Post photos and videos (trial)"), ("Pixiv", "Artwork images (trial); Ugoira not supported"), ("Pinterest", "Pins and boards")]:
            status(site, content)
        status("Toutiao", "Article text / local video transcription via linkget read; Chrome page required")
        print("\n  Use linkget read for video speech on these channels, or Toutiao article text.\n  Pixiv is image-only. Availability varies by URL; read --help shows transcription options.")
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
        preflight.destination(args, find_tool)
        records = []
        if is_batch:
            code = batch_download(args, parser, batch_links, records)
        else:
            journal = jobs.Journal.create([args.link], args)
            with journal.claim(), jobs.item(journal, 1):
                code = download(args, first_run(args) or {}, records)
                jobs.record(status="completed" if code == 0 else "cancelled" if code == 130 else "failed")
            if code:
                status("Retry", "linkget retry " + journal.identifier, "33")
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


def restore_options(args, journal, tokens):
    saved = journal.data["options"]
    for name in jobs.OPTIONS:
        flags = {"--" + name.replace("_", "-")}
        if name == "folder":
            flags.add("--photos")
        if not any(token.split("=", 1)[0] in flags for token in tokens) and name in saved:
            setattr(args, name, saved[name])
    if args.photos:
        args.folder = None
    elif args.folder is True:
        args.folder = Path.cwd()
    elif args.folder is not None:
        args.folder = Path(args.folder).expanduser().resolve()
    if args.folder is not None and args.date_now:
        raise ValueError("--date-now applies to Photos imports only.")
    if args.profile and args.browser in {"auto", "none"}:
        if not any(token.split("=", 1)[0] == "--profile" for token in tokens):
            args.profile = None
    if args.profile and args.browser in {"auto", "none"}:
        raise ValueError("--profile requires an explicit browser.")
    if args.report and (args.report.expanduser().exists() or not args.report.expanduser().parent.is_dir()):
        raise ValueError("Choose a new report path in an existing folder.")


def save_folder(files, folder):
    return destinations.save_folder(files, folder)


def import_photos(files, date_now, live_pairs=None):
    return destinations.import_photos(files, date_now, live_pairs, retain_originals, verify_library_copies)


def batch_download(args, parser, links, records=None):
    return run_batch(args, parser, links, records, download, first_run, status, jobs.Journal.create)


def download(args, setup_results, records=None, media_consumer=None):
    stage = None
    session_dir = None
    info_path = None
    read_metadata = {}
    evidence = {"status": "failed", "reference": None, "files": []}
    domain = None
    try:
        check_batch_cancelled()
        current = jobs.current()
        if current:
            journal, index = current
            ready = journal.data["items"][index - 1].get("ready")
            if ready and not args.redownload:
                # A failed save already has a checked complete set. Retry the save.
                result = recovery.restore(journal, args, status, save_folder, import_photos, claimed=True, indices=[index])
                evidence.update(ready.get("evidence", {}), status="completed" if result == 0 else "failed")
                return result
        url = args.link or (input("  Paste a link (no quotes needed): ") if sys.stdin.isatty() else sys.stdin.read())
        if current:
            staging.remember_earlier(status)
            previous = journal.data["items"][index - 1]
            if previous.get("stage"):
                stages = previous.get("previous_stages", []) + [previous["stage"]]
                jobs.record(previous_stages=list(dict.fromkeys(stages)))
        jobs.record(input=url, status="running", ready=None, receipt=None, stage=None, stage_identity=None)
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
        cookie_spec = browser_spec(args, "gallery-dl", domain) if engine != "douyin" and args.browser not in {"auto", "none"} else None
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
                    session = batch_interaction(lambda: prepare_session(tools["gallery-dl"], cookie_spec, cookie_path, domain))
            elif session.state == "valid":
                accounts.copy_saved(domain, cookie_path)
            if session.state != "valid":
                if recover_login(args, domain, session.detail) and accounts.copy_saved(domain, cookie_path):
                    pass
                else:
                    raise LoginRequired(f"{site}: {session.detail}")
            else:
                def save_session():
                    accounts.save(cookie_path, domain)
                    accounts.remember_source(domain, cookie_spec)
                batch_interaction(save_session)
        used_browser = cookie_path is not None
        if domain == "bilibili.com" and args.browser == "auto" and domain not in accounts.preferences().get("blocked", []):
            session_dir = tempfile.TemporaryDirectory(prefix="linkget-session-")
            candidate = Path(session_dir.name) / "cookies.txt"
            if accounts.copy_saved(domain, candidate):
                cookie_path = candidate
        stage = Path(tempfile.mkdtemp(prefix="linkget-"))
        jobs.record(stage=str(stage.resolve()), stage_identity=staging.identity(stage))
        tried_saved = cookie_path is not None
        tried_browser = used_browser or domain in setup_results
        browser_item = None
        tried_douyin_page = False
        attempt = 0
        while True:
            check_batch_cancelled()
            try:
                source_notes = []
                download_info = DownloadResult()
                media_dir = stage / ("media" if attempt == 0 else f"signed-in-media-{attempt}")
                media_dir.mkdir()
                if not getattr(_batch, "state", None):
                    print()
                with (stage / "download.log").open("w") as log:
                    log.write(f"Source: {url}\n")
                    command = None
                    if engine not in {"douyin", "xiaohongshu", "kuaishou", "pixiv"}:
                        command = command_for(args, url, engine, domain, media_dir, tools, cookie_path)
                    if engine == "yt-dlp":
                        info_path = stage / "video-info.json"
                        with activity("Preparing"):
                            command = prepare_video(command, info_path, log, domain)
                        if getattr(_batch, "state", {}).get("panel"):
                            batch_media([], json.loads(info_path.read_text()))
                        reader = getattr(args, 'read_metadata_consumer', None)
                        if reader and reader(json.loads(info_path.read_text()), url, site):
                            evidence.update(status='completed')
                            return 0
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
                                notes = (downloader(url, media_dir, browser_item=browser_item)
                                         if engine == "douyin" and browser_item is not None
                                         else downloader(url, media_dir, cookie_path))
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
                            result = run_process(command, stdout=log, stderr=subprocess.STDOUT)
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
                if media_consumer is not None:
                    if __package__:
                        from .read_formats import source_metadata
                    else:
                        from read_formats import source_metadata
                    read_metadata.update(source_metadata(download_info.metadata))
                    for sidecar in media_dir.glob('*.json'):
                        read_metadata.update(source_metadata(json.loads(sidecar.read_text())))
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
                accepted = MEDIA | ({'.m4a', '.mp3', '.aac', '.opus', '.ogg', '.wav', '.flac', '.webm'} if media_consumer else set())
                files = sorted(path for path in media_dir.iterdir() if path.is_file() and path.suffix.lower() in accepted)
                unfinished = [path for path in media_dir.iterdir() if path.suffix.lower() not in accepted]
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
            except (DouyinMediaUnavailable, DouyinDownloadInterrupted):
                if (not tried_douyin_page and args.browser != "none"
                        and domain not in accounts.preferences().get("blocked", [])
                        and (args.browser == "chrome" or args.browser == "auto" and accounts.default_browser() == "chrome")):
                    tried_douyin_page = True
                    status("Browser", "Reading the post in daily Chrome")
                    browser_item = batch_interaction(lambda: read_douyin_browser(url))
                    attempt += 1
                    continue
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
                            state, _ = batch_interaction(lambda: accounts.import_auto(exporter, domain))
                        if state.state == "valid" and accounts.copy_saved(domain, candidate):
                            cookie_path = candidate
                            continue
                        reason = state.detail
                if recover_login(args, domain, reason) and accounts.copy_saved(domain, candidate):
                    tried_browser = True
                    cookie_path = candidate
                    continue
                raise LoginRequired(reason) from None
        batch_media(files, download_info.metadata)
        with activity("Checking") if getattr(_batch, "state", None) else nullcontext():
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
        if media_consumer is not None:
            metadata = json.loads(info_path.read_text()) if info_path is not None else read_metadata
            media_consumer(files, media_records, download_info, url, site, metadata)
            evidence.update(status="completed")
            return 0
        ready = {"files": [{"path": str(path.resolve()), "sha256": record.get("sha256") or jobs.digest(path)}
                           for path, record in zip(files, media_records)],
                 "live_pairs": download_info.live_pairs, "prepared": [], "evidence": dict(evidence)}
        jobs.record(ready=ready)
        prepared = []
        if download_info.live_pairs:
            if args.live_photos:
                with activity("Pairing Live Photos"):
                    prepared = prepare_pairs(files, download_info.live_pairs, stage / "live-photos")
                ready["prepared"] = [jobs.snapshot(pair) for pair in prepared]
                jobs.record(ready=ready)
                status("Live Photos", f"{len(prepared)} source pairs recognized by macOS")
            else:
                status("Live Photos", f"{len(download_info.live_pairs)} pairs saved separately · use --live-photos to pair")
        with saving_activity("Saving" if folder else "Importing"):
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
            staging.remember(stage, journal.data["items"][index - 1].get("stage_identity") if current else None)
            staging.cleanup(status, folder)
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
            if media_consumer is not None:
                shutil.rmtree(stage)
            elif domain == "weibo.com":
                for metadata in stage.glob("*/*.json"):
                    metadata.unlink(missing_ok=True)
            if media_consumer is None:
                try:
                    staging.remember(stage, journal.data["items"][index - 1].get("stage_identity") if current else None)
                except (OSError, ValueError, RuntimeError) as error:
                    status("Files kept", f"Temporary files could not be recorded for cleanup: {error}", "33", stream=sys.stderr)
                status("Files kept", str(stage), "33", stream=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
