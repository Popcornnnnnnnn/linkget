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
from urllib.parse import urlsplit

if __package__:
    from . import accounts, agent_output, article_archive, cleanup, inspect_url, jobs, destinations, mcp_server, media_limits, preflight, recovery, staging, transfers, ownership, rendered_page, runtime, web_article, x_article
    from .runtime import _batch, batch_lock, check_batch_cancelled, batch_interaction, run_process
    from .output import status, activity, terminal_styles, emphasis, format_size, downloaded_bytes, saved_description, batch_media, saving_activity, cell_width, print_field, wrap_cells
    from .batch import run_batch
    from .engines import SITES, route, route_video, browser_spec, command_for, restricted_bilibili_quality, prepare_bilibili, selected_quality, prepare_video, LoginRequired, needs_login, is_audio_only_site
    from .photos import originals_directory, retain_originals, verify_library_copies
    from .failures import classify, redact
    from .version import VERSION
    from .session import LOGIN_URLS, prepare_session
    from .links import SOUNDCLOUD_SHORT_HOSTS, canonical_post, normalize_link, single_url
    from .douyin import download as download_douyin, MediaUnavailable as DouyinMediaUnavailable
    from .douyin_browser import read_item as read_douyin_browser, load_item as load_douyin_page
    from .douyin import DownloadInterrupted as DouyinDownloadInterrupted
    from .xiaohongshu import download as download_xiaohongshu, BrowserRequired
    from .kuaishou import download as download_kuaishou, BrowserRequired as KuaishouBrowserRequired
    from .pixiv import download as download_pixiv, BrowserRequired as PixivBrowserRequired
    from .weibo import prefer_clean_images
    from .media import VIDEO, MEDIA, AUDIO, DownloadResult, inspect_files, quality_summary, gallery_evidence, reference, compare_reports, write_report
    from .audio import AUDIO_FORMATS, prepare_audio
    from .live import prepare_pairs, save_pairs
else:  # Homebrew launches this file directly.
    import accounts, agent_output, article_archive, cleanup, inspect_url, jobs, destinations, mcp_server, media_limits, preflight, recovery, staging, transfers, ownership, rendered_page, runtime, web_article, x_article
    from runtime import _batch, batch_lock, check_batch_cancelled, batch_interaction, run_process
    from output import status, activity, terminal_styles, emphasis, format_size, downloaded_bytes, saved_description, batch_media, saving_activity, cell_width, print_field, wrap_cells
    from batch import run_batch
    from engines import SITES, route, route_video, browser_spec, command_for, restricted_bilibili_quality, prepare_bilibili, selected_quality, prepare_video, LoginRequired, needs_login, is_audio_only_site
    from photos import originals_directory, retain_originals, verify_library_copies
    from failures import classify, redact
    from version import VERSION
    from session import LOGIN_URLS, prepare_session
    from links import SOUNDCLOUD_SHORT_HOSTS, canonical_post, normalize_link, single_url
    from douyin import download as download_douyin, MediaUnavailable as DouyinMediaUnavailable
    from douyin_browser import read_item as read_douyin_browser, load_item as load_douyin_page
    from douyin import DownloadInterrupted as DouyinDownloadInterrupted
    from xiaohongshu import download as download_xiaohongshu, BrowserRequired
    from kuaishou import download as download_kuaishou, BrowserRequired as KuaishouBrowserRequired
    from pixiv import download as download_pixiv, BrowserRequired as PixivBrowserRequired
    from weibo import prefer_clean_images
    from media import VIDEO, MEDIA, AUDIO, DownloadResult, inspect_files, quality_summary, gallery_evidence, reference, compare_reports, write_report
    from audio import AUDIO_FORMATS, prepare_audio
    from live import prepare_pairs, save_pairs


def login_guidance(args, domain, url, reason):
    status("Stopped", redact(reason), "33", stream=sys.stderr)
    if domain not in LOGIN_URLS:
        status("Next", "This site uses public access in linkget; check the URL or open it in your browser.", stream=sys.stderr)
        return
    if args.browser == "none":
        status("Next", "Public-only mode is enabled. Run without --browser none to use a browser login.", stream=sys.stderr)
    elif not runtime.is_interactive():
        sign_in_link(domain, stream=sys.stderr)
        status("Next", "Run linkget in an interactive terminal to connect a browser and continue.", stream=sys.stderr)


def report_failure(error, phase=None):
    failure = classify(error, phase=phase)
    status(failure.label, failure.message, "31", stream=sys.stderr)
    status("Next", failure.next_step, stream=sys.stderr)
    return failure.category


def record_failure(args, error, phase):
    failure = classify(error, phase=phase)
    args._last_failure = (error, phase)
    jobs.record(failure={'category': failure.category, 'message': failure.message,
                         'next_step': failure.next_step, 'phase': phase,
                         'retryable': bool(failure.retryable)})
    if phase == 'download':
        args._retry_failure = failure
        cause, seen = error, set()
        while cause is not None and id(cause) not in seen:
            seen.add(id(cause))
            headers = getattr(cause, 'headers', None)
            if headers is not None and headers.get('Retry-After') is not None:
                args._retry_after = headers.get('Retry-After')
                break
            cause = cause.__cause__ or (None if cause.__suppress_context__ else cause.__context__)


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
            status(site, "Public access · configured page reader when needed", "1")
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
    if rejected and args is not None and runtime.is_interactive() and args.browser != "none":
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
            if states[site] != "valid" and runtime.is_interactive() and args.browser != "none":
                if recover_login(args, accounts.SITES[site], "Connect " + site, proactive=True):
                    login_result(site, accounts.Session("valid", "Valid"))
        return 0
    if len(sites) != 1 or sites[0] not in {*accounts.SITES, "all"}:
        parser.error("Use linkget logout SITE, or linkget logout all.")
    accounts.logout(sites[0])
    if getattr(args, "json", False):
        agent_output.emit({
            "schema_version": agent_output.SCHEMA_VERSION,
            "ok": True,
            "command": "logout",
            "target": sites[0],
        })
    else:
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
    if getattr(args, "video", False) or args.browser == "none" or not runtime.is_interactive() or accounts.preferences().get("setup"):
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
    if not runtime.is_interactive() or args.browser == "none":
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
        status("Action needed", redact(reason), "33")
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
        status(site, "Could not connect · " + redact(reason), "1;33")


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


def collect_doctor(args=None):
    missing = []
    tools_info = {}
    for name in ("gallery-dl", "yt-dlp", "ffmpeg", "ffprobe", "deno"):
        path = find_tool(name)
        if not path:
            missing.append(name)
            tools_info[name] = {"available": False, "path": None, "version": None}
            continue
        try:
            check = subprocess.run([path, "-version" if name in {"ffmpeg", "ffprobe"} else "--version"], text=True, capture_output=True, timeout=10)
            if check.returncode:
                raise RuntimeError("Could not run")
            version = check.stdout.splitlines()[0]
            if name in {"ffmpeg", "ffprobe"}:
                version = version.split(" Copyright", 1)[0].removeprefix(name + " version ")
            tools_info[name] = {"available": True, "path": path, "version": version}
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            missing.append(name)
            tools_info[name] = {"available": False, "path": path, "version": None, "error": f"Could not run: {path}"}
    if __package__:
        from . import diagnostics
    else:
        import diagnostics
    diag_data, rows, diagnostic_code = diagnostics.inspect_data(args)
    code = 1 if missing else diagnostic_code
    payload = {
        "schema_version": agent_output.SCHEMA_VERSION,
        "ok": code == 0,
        "command": "doctor",
        "version": VERSION,
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "tools": tools_info,
        "missing_tools": missing,
        **diag_data,
    }
    return payload, rows, code


def _print_doctor(payload, rows):
    print("  Tools\n")
    for name, info in payload["tools"].items():
        if info["available"]:
            status(name, info["version"], "32")
        elif info.get("error"):
            status(name, info["error"], "33")
        else:
            status(name, "Missing", "33")
    if payload["missing_tools"]:
        print()
        status("Setup needed", installation_hint(payload["missing_tools"]), "33")
    print()
    for label, detail in rows:
        status(label, detail)


def doctor(args=None):
    payload, rows, code = collect_doctor(args)
    if not runtime.json_mode():
        _print_doctor(payload, rows)
    return code


def configured_destination():
    setting = accounts.preferences().get("destination", {"mode": "photos"})
    if not isinstance(setting, dict) or setting.get("mode") not in {"photos", "cwd", "folder"}:
        raise ValueError("Invalid default destination. Run linkget config --photos or linkget config --folder.")
    if setting["mode"] == "folder" and (not isinstance(setting.get("path"), str) or not Path(setting["path"]).is_absolute()):
        raise ValueError("Invalid default folder. Run linkget config --folder PATH.")
    return setting


def configured_audio_destination():
    setting = accounts.preferences().get("audio_destination")
    if setting is None:
        return None
    if not isinstance(setting, dict) or setting.get("mode") not in {"cwd", "folder"}:
        raise ValueError("Invalid default audio folder. Run linkget config --audio-folder [PATH] or --clear-audio-folder.")
    if setting["mode"] == "folder" and (not isinstance(setting.get("path"), str) or not Path(setting["path"]).is_absolute()):
        raise ValueError("Invalid default audio folder. Run linkget config --audio-folder PATH.")
    return setting


def resolve_audio_folder(explicit_folder=None):
    if explicit_folder is True:
        return Path.cwd()
    if explicit_folder is not None:
        return Path(explicit_folder).expanduser().resolve()
    audio_setting = configured_audio_destination()
    if audio_setting is not None:
        return Path.cwd() if audio_setting["mode"] == "cwd" else Path(audio_setting["path"])
    setting = configured_destination()
    if setting["mode"] == "cwd":
        return Path.cwd()
    if setting["mode"] == "folder":
        return Path(setting["path"])
    raise ValueError("Audio downloads require a folder. Use --folder [PATH] or set a default with linkget config --audio-folder PATH.")


def configured_article_destination():
    setting = accounts.preferences().get("article_destination")
    if setting is None:
        return None
    if (not isinstance(setting, dict)
            or setting.get("mode") not in {"cwd", "folder"}
            or (setting.get("mode") == "folder"
                and (not isinstance(setting.get("path"), str) or not Path(setting["path"]).is_absolute()))):
        raise ValueError("Invalid default article destination. Run linkget config --article-folder PATH or --clear-article-folder.")
    return setting


def resolve_article_folder(explicit_folder=None):
    if explicit_folder is True:
        return Path.cwd().resolve()
    if explicit_folder is not None:
        return Path(explicit_folder).expanduser().resolve()
    article_setting = configured_article_destination()
    if article_setting is not None:
        if article_setting["mode"] == "cwd":
            return Path.cwd().resolve()
        return Path(article_setting["path"]).expanduser().resolve()
    setting = configured_destination()
    if setting["mode"] == "cwd":
        return Path.cwd().resolve()
    if setting["mode"] == "folder":
        return Path(setting["path"]).expanduser().resolve()
    raise ValueError("Article downloads require a folder. Use --folder [PATH] or set a default with linkget config --article-folder PATH.")


def _is_article_link_text(text, force_article=False):
    try:
        return article_archive.classify_article_target(text, force_article=force_article) is not None
    except ValueError:
        return False


def _acquire_x_article(url, args, setup_results, find_tool, *, cookie_path=None):
    blocked = set(accounts.preferences().get("blocked", []))
    browser_mode = getattr(args, "browser", "auto")
    rendered_backend = getattr(args, "rendered_backend", None)

    def try_browser():
        if browser_mode == "none" or "x.com" in blocked:
            return None
        choice = rendered_page.selection(backend=rendered_backend, browser=browser_mode)
        if not choice.get("supported"):
            return None
        status("Page reader", choice["browser"] + " · " + choice["source"], stream=sys.stderr)
        return batch_interaction(lambda: rendered_page.read_x_article(url, backend=choice["browser"]))

    if x_article.status_url_for_gallery_dl(url) is None:
        try:
            browser_item = try_browser()
        except (OSError, ValueError, RuntimeError) as error:
            if "x_article_login_required" in str(error).lower():
                raise LoginRequired("X login is required in your browser to read this article.") from None
            raise
        if browser_item is not None:
            return browser_item
        raise ValueError(
            "Direct /i/article/ID links require a logged-in Chrome or BrowserOS Neo reader, "
            "or use the post URL (https://x.com/<user>/status/<id>)."
        )

    gallery_dl_bin = find_tool("gallery-dl")
    primary_error = None
    if gallery_dl_bin:
        session_dir = None
        try:
            active_cookie = cookie_path
            candidate = None
            if active_cookie is None and browser_mode not in {"auto", "none"} and "x.com" not in blocked:
                cookie_spec = browser_spec(args, "gallery-dl", "x.com")
                if cookie_spec:
                    session_dir = tempfile.TemporaryDirectory(prefix="linkget-x-session-")
                    candidate = Path(session_dir.name) / "cookies.txt"
                    session = (setup_results or {}).get("x.com")
                    if session is None:
                        with activity("Preparing"):
                            session = batch_interaction(lambda: prepare_session(gallery_dl_bin, cookie_spec, candidate, "x.com"))
                    elif session.state == "valid":
                        accounts.copy_saved("x.com", candidate)
                    if session.state != "valid":
                        if not (recover_login(args, "x.com", session.detail) and accounts.copy_saved("x.com", candidate)):
                            raise LoginRequired(f"X Article: {session.detail}")
                    else:
                        def save_x_session():
                            accounts.save(candidate, "x.com")
                            accounts.remember_source("x.com", cookie_spec)
                        batch_interaction(save_x_session)
                    active_cookie = candidate
            elif active_cookie is None and browser_mode == "auto" and "x.com" not in blocked:
                session_dir = tempfile.TemporaryDirectory(prefix="linkget-x-session-")
                candidate = Path(session_dir.name) / "cookies.txt"
                if accounts.copy_saved("x.com", candidate):
                    active_cookie = candidate

            try:
                result = x_article.fetch_via_gallery_dl(url, gallery_dl_bin, cookie_path=active_cookie)
            except x_article.XArticleLoginRequired as error:
                retried = None
                if browser_mode != "none" and "x.com" not in blocked:
                    if session_dir is None:
                        session_dir = tempfile.TemporaryDirectory(prefix="linkget-x-session-")
                        candidate = Path(session_dir.name) / "cookies.txt"
                    refreshed_cookie = None
                    reason = str(error)
                    if browser_mode == "auto" and cookie_path is None and runtime.is_interactive():
                        with activity("Checking"):
                            state, _ = batch_interaction(lambda: accounts.import_auto(gallery_dl_bin, "x.com"))
                        if state.state == "valid" and accounts.copy_saved("x.com", candidate):
                            refreshed_cookie = candidate
                        else:
                            reason = state.detail
                    if refreshed_cookie is None and recover_login(args, "x.com", reason) and accounts.copy_saved("x.com", candidate):
                        refreshed_cookie = candidate
                    if refreshed_cookie is not None:
                        try:
                            retried = x_article.fetch_via_gallery_dl(url, gallery_dl_bin, cookie_path=refreshed_cookie)
                        except x_article.XArticleError as retry_err:
                            error = retry_err
                if retried is not None:
                    result = retried
                else:
                    primary_error = error
                    result = False
            except x_article.XArticleError as error:
                primary_error = error
                result = False

            if result is None:
                raise ValueError("This X post is not an X Article. Omit --article to download normal post media.")
            if isinstance(result, dict):
                return result
        finally:
            if session_dir is not None:
                session_dir.cleanup()

    browser_error = None
    try:
        browser_item = try_browser()
    except (OSError, ValueError, RuntimeError) as error:
        browser_error = error
        browser_item = None
    if browser_item is not None:
        return browser_item

    if isinstance(primary_error, x_article.XArticleLoginRequired):
        raise LoginRequired(str(primary_error)) from None
    if primary_error is not None:
        raise primary_error
    if browser_error is not None:
        raise browser_error
    raise RuntimeError(installation_hint(["gallery-dl"]))


def _acquire_article_for_download(url, target, args, setup_results):
    kind = target["kind"]
    if kind == "x_article":
        return _acquire_x_article(url, args, setup_results, find_tool)
    if kind == "web_article":
        with activity("Fetching article"):
            return web_article.read_article(url)

    browser_mode = getattr(args, "browser", "auto")
    rendered_backend = getattr(args, "rendered_backend", None)
    reader_choice = (
        rendered_page.selection(backend=rendered_backend, browser=browser_mode)
        if browser_mode != "none"
        else {"supported": False}
    )

    if kind == "wechat_article":
        wechat_browser_eligible = bool(target.get("wechat_browser_eligible"))
        if wechat_browser_eligible and rendered_backend is not None and browser_mode != "none":
            status("Page reader", rendered_backend + " · command option", stream=sys.stderr)
            item = batch_interaction(lambda: rendered_page.read_wechat(url, backend=rendered_backend))
            return {
                **item,
                "site": "WeChat",
                "reference": reference(url),
                "is_wechat": True,
                "acquisition": "browseros_neo_rendered_page" if rendered_backend == "neo" else "chrome_rendered_page",
                "content_basis": "complete_browseros_neo_wechat_dom" if rendered_backend == "neo" else "complete_daily_chrome_wechat_dom",
                "limitations": ["Only this bounded WeChat article DOM was read; HTTP transfer completeness and comments were not verified."],
            }
        try:
            with activity("Fetching article"):
                return web_article.read_wechat_http(url)
        except web_article.WeChatGateError:
            if wechat_browser_eligible and browser_mode != "none" and reader_choice.get("supported"):
                backend = reader_choice["browser"]
                status("Page reader", backend + " · " + reader_choice["source"], stream=sys.stderr)
                item = batch_interaction(lambda: rendered_page.read_wechat(url, backend=backend))
                return {
                    **item,
                    "site": "WeChat",
                    "reference": reference(url),
                    "is_wechat": True,
                    "acquisition": "browseros_neo_rendered_page" if backend == "neo" else "chrome_rendered_page",
                    "content_basis": "complete_browseros_neo_wechat_dom" if backend == "neo" else "complete_daily_chrome_wechat_dom",
                    "limitations": ["Only this bounded WeChat article DOM was read; HTTP transfer completeness and comments were not verified."],
                }
            raise

    if kind == "public_article":
        if browser_mode == "none" or not reader_choice.get("supported"):
            raise ValueError("This article requires a supported browser page reader (Chrome or BrowserOS Neo).")
        backend = reader_choice["browser"]
        status("Page reader", backend + " · " + reader_choice["source"], stream=sys.stderr)
        item = batch_interaction(lambda: rendered_page.read_public(url, backend=backend))
        item = rendered_page.validate_public_page(item, url)
        return {
            **item,
            "site": target["site"],
            "reference": reference(url),
            "acquisition": "browseros_neo_rendered_page" if backend == "neo" else "chrome_rendered_page",
            "content_basis": "complete_browseros_neo_scoped_article_dom" if backend == "neo" else "complete_daily_chrome_scoped_article_dom",
            "read_scope": "scoped_public_article_rendered_text_only",
            "limitations": [
                "Only this scoped article DOM was read; HTTP transfer completeness, "
                "linked documents and embedded media were not verified."
            ],
        }

    if kind == "toutiao_article":
        if __package__:
            from . import read_store, toutiao
        else:
            import read_store, toutiao
        page_data = getattr(args, "page_data", None)
        if page_data:
            page = read_store.read_json(page_data)
        else:
            if browser_mode == "none" or not reader_choice.get("supported"):
                raise ValueError("Toutiao articles require a supported browser page reader or --page-data with --browser none.")
            backend = reader_choice["browser"]
            status("Page reader", backend + " · " + reader_choice["source"], stream=sys.stderr)
            page = batch_interaction(lambda: toutiao.read_browser(url, backend=backend))
        item = toutiao.validate_page(url, page)
        if item.get("kind") != "article":
            raise ValueError("This Toutiao link is not an article.")
        return {**item, "site": target["site"], "reference": reference(url)}

    raise ValueError(f"Unsupported article target kind: {kind}")


def download_article_target(args, target, setup_results, evidence, started, *, prefetched_article=None, cookie_path=None):
    if getattr(args, "_explicit_photos", False) or getattr(args, "photos", False):
        raise ValueError("Articles cannot be imported into Photos. Use --folder [PATH] or omit --photos.")
    if getattr(args, "date_now", False):
        raise ValueError("--date-now applies to Photos imports only, not article downloads.")
    if getattr(args, "live_photos", False):
        raise ValueError("--live-photos cannot be used with article downloads.")
    if getattr(args, "audio", False) or getattr(args, "audio_format", None):
        raise ValueError("Article pages do not contain downloadable audio.")
    if getattr(args, "clip", None) or getattr(args, "max_duration", None) or getattr(args, "max_size", None):
        raise ValueError("--clip, --max-duration, and --max-size apply only to audio and video downloads, not articles.")
    if getattr(args, "page_data", None) and target["kind"] != "toutiao_article":
        raise ValueError("--page-data applies only to Douyin or Toutiao posts with --browser none.")

    if getattr(args, "_explicit_folder", False) and getattr(args, "folder", None) is not None:
        parent_folder = resolve_article_folder(args.folder)
    elif getattr(args, "folder_from_default", False) or not getattr(args, "folder", None):
        parent_folder = resolve_article_folder(None)
    else:
        parent_folder = resolve_article_folder(args.folder)
    preflight.writable_directory(parent_folder)

    url = target["url"]
    evidence.update(reference=reference(url), site=target["site"])
    status("Source", target["site"])
    status("Destination", str(parent_folder))

    if prefetched_article is not None:
        article = prefetched_article
    elif target["kind"] == "x_article" and cookie_path is not None:
        article = _acquire_x_article(url, args, setup_results, find_tool, cookie_path=cookie_path)
    else:
        article = _acquire_article_for_download(url, target, args, setup_results)

    bundle_dir = article_archive.allocate_bundle_dir(parent_folder, article)
    try:
        clean, written = article_archive.write_article_bundle(
            bundle_dir,
            article,
            mode="download",
            download_assets=not bool(getattr(args, "no_assets", False)),
            ffprobe_bin=find_tool("ffprobe"),
        )
    except BaseException:
        try:
            if bundle_dir.exists() and not any(bundle_dir.iterdir()):
                bundle_dir.rmdir()
        except OSError:
            pass
        raise

    written_sorted = [p for p in written if p.is_file()]
    jobs.record(
        destination_folder=str(bundle_dir),
        destination_files=[{"path": str(p)} for p in written_sorted],
    )
    rel_files = []
    for p in written_sorted:
        try:
            rel = str(p.relative_to(bundle_dir))
        except ValueError:
            rel = p.name
        rel_files.append({"path": rel, "bytes": p.stat().st_size})
    total_bytes = sum(entry["bytes"] for entry in rel_files)
    evidence.update(
        status="completed",
        site=clean.get("site") or target["site"],
        reference=clean.get("reference") or reference(url),
        article_folder=str(bundle_dir),
        title=clean.get("title"),
        author=clean.get("author"),
        saved_count=len(written_sorted),
        skipped_count=0,
        assets_downloaded=clean.get("assets_downloaded", 0),
        assets_failed=clean.get("assets_failed", 0),
        files=rel_files,
    )
    if clean.get("title"):
        status("Article", clean["title"])
    status("Folder", str(bundle_dir))
    assets_dl = clean.get("assets_downloaded", 0)
    assets_fail = clean.get("assets_failed", 0)
    if getattr(args, "no_assets", False):
        status("Assets", "Skipped (--no-assets)")
    elif assets_dl or assets_fail:
        asset_msg = f"{assets_dl} downloaded"
        if assets_fail:
            asset_msg += f" · {assets_fail} failed (kept original links)"
        status("Assets", asset_msg, "33" if assets_fail else "32")
    status("Done", f"{len(written_sorted)} files · {format_size(total_bytes)} · {time.monotonic() - started:.1f}s", "32")
    return 0


def configure(args, parser):
    if (args.link != ["config"] or args.browser != "auto" or args.profile or args.date_now
            or args.rendered_backend or args.live_photos or args.report or args.article or args.no_assets):
        parser.error("Use linkget config, linkget config --photos, linkget config --folder [PATH], linkget config --audio-folder [PATH], or linkget config --article-folder [PATH].")
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
    changed_dest = args.photos or args.folder is not None
    changes = {"destination": setting} if changed_dest else {}
    if args.clear_audio_folder:
        audio_setting = None
        changes["audio_destination"] = None
    elif args.audio_folder is True:
        audio_setting = {"mode": "cwd"}
        changes["audio_destination"] = audio_setting
    elif args.audio_folder is not None:
        audio_folder = args.audio_folder.expanduser().resolve()
        if audio_folder.exists() and not audio_folder.is_dir():
            raise ValueError(f"Not a folder: {audio_folder}")
        audio_setting = {"mode": "folder", "path": str(audio_folder)}
        changes["audio_destination"] = audio_setting
    else:
        audio_setting = configured_audio_destination()
    changed_audio = args.clear_audio_folder or args.audio_folder is not None
    if args.clear_article_folder:
        article_setting = None
        changes["article_destination"] = None
    elif args.article_folder is True:
        article_setting = {"mode": "cwd"}
        changes["article_destination"] = article_setting
    elif args.article_folder is not None:
        article_folder = args.article_folder.expanduser().resolve()
        if article_folder.exists() and not article_folder.is_dir():
            raise ValueError(f"Not a folder: {article_folder}")
        article_setting = {"mode": "folder", "path": str(article_folder)}
        changes["article_destination"] = article_setting
    else:
        article_setting = configured_article_destination()
    changed_article = args.clear_article_folder or args.article_folder is not None
    if args.reader_browser is not None:
        changes["reader_browser"] = args.reader_browser
    if changes:
        accounts.set_preferences(**changes)
    description = {"photos": "Photos", "cwd": "Current folder (where you run linkget)", "folder": setting.get("path")}[setting["mode"]]
    audio_description = (
        "Not set (uses folder default when set)" if audio_setting is None
        else "Current folder (where you run linkget)" if audio_setting["mode"] == "cwd"
        else audio_setting["path"]
    )
    article_description = (
        "Not set (uses folder default when set)" if article_setting is None
        else "Current folder (where you run linkget)" if article_setting["mode"] == "cwd"
        else article_setting["path"]
    )
    status("Default", description)
    status("Audio default", audio_description)
    status("Article default", article_description)
    preferences = accounts.preferences()
    choice = rendered_page.selection()
    sources = preferences.get('sources', {})
    names = sorted({value.split('/', 1)[0].split(':', 1)[0] for value in sources.values() if isinstance(value, str)})
    if getattr(args, "json", False):
        agent_output.emit({
            "schema_version": agent_output.SCHEMA_VERSION,
            "ok": True,
            "command": "config",
            "destination": setting,
            "audio_destination": audio_setting,
            "article_destination": article_setting,
            "reader_browser": preferences.get("reader_browser", "auto"),
            "page_controller": choice,
            "login_browsers": names,
            "saved_browser": preferences.get("browser"),
            "changed": bool(changes),
        })
        return 0
    status("Page preference", preferences.get("reader_browser", "auto"))
    status("Effective reader", choice['browser'] + " · " + choice['source'] + ("" if choice['supported'] else " · controller unavailable"))
    status("Login browser sources", ", ".join(names) if names else preferences.get('browser') or "None configured")
    status("Selection", "Page option > browser option > environment > personal reader > saved browser > system default")
    status("Privacy", "Login refresh sources and page controller are separate; saved login validity was not checked.")
    if changed_dest or changed_audio or changed_article:
        status("Saved", "Used when no --folder or --photos is specified.")
    else:
        print("\n  Change default:\n")
        examples = [
            ("linkget config --reader-browser neo", "Use BrowserOS Neo for page reading"),
            ("linkget config --reader-browser auto", "Follow saved/system browser for page reading"),
            ("linkget config --photos", "Photos"),
            ("linkget config --folder", "Current folder on each run"),
            ("linkget config --folder ~/Downloads", "Fixed folder; replace with your path"),
            ("linkget config --audio-folder ~/Music", "Default folder for audio and music downloads"),
            ("linkget config --clear-audio-folder", "Clear the saved default audio folder"),
            ("linkget config --article-folder ~/Articles", "Default folder for article downloads"),
            ("linkget config --clear-article-folder", "Clear the saved default article folder"),
        ]
        label_width = max(cell_width(command) for command, _ in examples)
        for command, detail in examples:
            print_field(command, detail, label_width=label_width, indent="  ")
        print("\n  To override once, omit config. Your saved default stays unchanged.")
    return 0


SUPPORTED_SITES = (
    ("Instagram", "Photos, videos and reels"),
    ("X / Twitter", "Post photos, videos and X Articles"),
    ("Bilibili", "BV/av videos, Opus media and au audio"),
    ("TikTok", "Post photos and videos; share links"),
    ("Douyin", "Post photos and videos; share links"),
    ("Kuaishou", "Post videos; image galleries not supported"),
    ("Xiaohongshu", "Note photos and videos; xhslink shares"),
    ("Weibo", "Post photos and videos; t.cn shares"),
    ("YouTube", "Videos and Shorts; youtu.be links"),
    ("YouTube Music", "Single music.youtube.com tracks (audio)"),
    ("Vimeo", "Single publicly embeddable videos"),
    ("Dailymotion", "Single videos and dai.ly shares"),
    ("Pornhub", "Single watch or embed links (trial; no account support)"),
    ("Bilibili Audio", "Single /audio/au... tracks (audio)"),
    ("SoundCloud", "Single tracks and on.soundcloud.com shares (audio)"),
    ("Bandcamp", "Single *.bandcamp.com/track/... tracks (audio)"),
    ("Reddit", "Post photos and videos"),
    ("Lofter", "Post photos and videos (trial)"),
    ("Pixiv", "Artwork images (trial); Ugoira not supported"),
    ("Pinterest", "Pins and boards"),
)


def _is_audio_only_link_text(text):
    try:
        urls = re.findall(r'https?://[^\s<>"，。]+', (text or "").strip())
        if len(urls) != 1:
            return False
        candidate = urls[0].rstrip(".,;!）)")
        target = canonical_post(candidate) or candidate
        if urlsplit(target).hostname in SOUNDCLOUD_SHORT_HOSTS:
            return True
        site, _, _ = route(target)
        return is_audio_only_site(site)
    except ValueError:
        return False


def validate_video_options(args):
    if getattr(args, "video", False) and (
        args.audio or args.article or args.no_assets or args.live_photos or args.page_data
    ):
        raise ValueError("--video cannot be combined with --audio, --article, --no-assets, --live-photos, or --page-data.")


def main(argv=None):
    saved_modes = runtime.snapshot_modes()
    tokens = list(sys.argv[1:] if argv is None else argv)
    code = 1
    try:
        code = _main(tokens)
        return code
    finally:
        runtime.restore_modes(saved_modes)
        if code in (None, 0):
            try:
                if __package__:
                    from . import update as update_mod
                else:
                    import update as update_mod
                update_mod.maybe_notify_update(VERSION, tokens)
            except Exception:
                pass


def _main(argv=None):
    if os.environ.get('LINKGET_GUI_JOB') == '1':
        try:
            os.setsid()
        except OSError:
            pass
    tokens = list(sys.argv[1:] if argv is None else argv)
    if tokens and tokens[0] == 'version':
        if __package__:
            from .build_info import main as version_details
        else:
            from build_info import main as version_details
        return version_details(tokens[1:])
    if tokens and tokens[0] == 'upgrade':
        if __package__:
            from . import update as update_mod
        else:
            import update as update_mod
        return update_mod.run_upgrade(tokens[1:], current_version=VERSION)
    if tokens == ['help', 'upgrade']:
        if __package__:
            from . import update as update_mod
        else:
            import update as update_mod
        return update_mod.run_upgrade(['--help'], current_version=VERSION)
    if tokens and tokens[0] == 'tasks':
        if __package__:
            from .tasks import main as task_status
        else:
            from tasks import main as task_status
        return task_status(tokens[1:])
    if tokens and tokens[0] == 'read':
        if __package__:
            from .read_tasks import main as read_content
        else:
            from read_tasks import main as read_content
        return read_content(tokens[1:])
    parser = argparse.ArgumentParser(
        prog="linkget",
        description="Save photos, videos, and audio from supported sites to macOS Photos or a folder.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Commands:
  help                 Show this help (also -h or --help)
  version              Show detailed local build and source identity
  upgrade              Check for updates and upgrade linkget (--check, --json)
  sites                List supported sites and media
  inspect URL          Inspect one URL or local file without downloading
  read URL --folder D  Read/transcribe in the background (read --help)
  read status [TASK]   Show background reading progress and results
  read wait [TASK]     Wait for a background read task to finish
  read show [TARGET]   Print completed read content to stdout
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
  tasks [TASK]         View download and background read tasks
  retry [TASK]         Retry unfinished items from the latest or specified task
  recover [TASK]       List kept files, or save a complete retained task
  clean                Preview or remove old completed temporary staging dirs
  mcp                  Run a stdio JSON-RPC 2.0 MCP server for AI agents
  compare A.json B.json Compare reports for the same links
  auth                 Check logins and public access without sign-in prompts
  auth SITE            Connect one website explicitly (e.g. auth bilibili)
  logout SITE          Remove a saved login and disable automatic browser access
  logout all           Disconnect all websites

Account sites: instagram, x, bilibili, tiktok, douyin, xiaohongshu, weibo, youtube, reddit, kuaishou, lofter, pixiv, pinterest

Examples:
  linkget                              Paste a link or sharing text at the prompt
  linkget inspect URL --json           Inspect routing/metadata before downloading
  linkget https://v.douyin.com/CODE/    Save to the configured default
  linkget URL --photos                 Save to Photos this time
  linkget URL --folder                 Save to the current folder
  linkget URL --folder ~/Downloads     Save to a folder
  linkget URL --video --folder         Save one video; let yt-dlp select the extractor
  linkget URL --audio --folder ~/Music Save original audio stream with tags & cover
  linkget URL --audio-format mp3       Convert extracted audio to MP3
  linkget URL --non-interactive --json Machine-readable download without prompts
  linkget batch URL1 URL2 --folder     Download multiple posts
  linkget batch --folder ~/Downloads   Paste links; blank line starts downloads
  linkget read URL                     Submit a background content read
  linkget read URL --stdout            Run read and print Markdown to stdout
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
  linkget config --audio-folder ~/Music Default folder for audio and music downloads

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
    parser.add_argument("--audio", action="store_true", help="save audio only to a folder (music links enable this automatically)")
    parser.add_argument("--video", action="store_true", help="save a single video with yt-dlp, including from sites outside linkget sites")
    parser.add_argument("--audio-format", choices=list(AUDIO_FORMATS), metavar="FORMAT",
                        help="convert audio to FORMAT (" + ", ".join(AUDIO_FORMATS) + "); implies --audio")
    parser.add_argument("--audio-folder", nargs="?", const=True, type=Path,
                        help="config only: save the default audio folder; omit PATH for the current folder on each run")
    parser.add_argument("--clear-audio-folder", action="store_true",
                        help="config only: clear the saved default audio folder")
    parser.add_argument("--article", action="store_true",
                        help="download a web page or X post as an offline article archive")
    parser.add_argument("--no-assets", action="store_true",
                        help="article downloads only: skip downloading embedded images and videos")
    parser.add_argument("--article-folder", nargs="?", const=True, type=Path,
                        help="config only: save the default article folder; omit PATH for the current folder on each run")
    parser.add_argument("--clear-article-folder", action="store_true",
                        help="config only: clear the saved default article folder")
    parser.add_argument("--json", action="store_true", help="print structured JSON output on stdout (implies --non-interactive)")
    parser.add_argument("--non-interactive", action="store_true", help="never prompt or animate terminal spinners")
    parser.add_argument("--no-network", action="store_true", help="inspect only: skip resolving short links or probing remote metadata")
    parser.add_argument("--browser", choices=["auto", "none", "chrome", "firefox", "safari", "edge", "brave"], default="auto", help="auto: public first, then saved login and browser refresh if needed; none: public only; specify a browser to use its login")
    parser.add_argument("--probe-browser", choices=["neo", "chrome"], help="doctor only: explicitly inspect one browser connection or OS access without opening a page")
    parser.add_argument("--check-session", choices=list(accounts.SITES), help="doctor only: explicitly verify one saved website session; no browser export")
    parser.add_argument("--reader-browser", choices=["auto", "chrome", "neo"], help="config only: save the page-reading preference; auto follows saved browser choice or system default")
    parser.add_argument("--rendered-backend", choices=["chrome", "neo"], help="override the page-reading controller for this download; credentials are unchanged")
    parser.add_argument("--profile", help="path to the selected browser profile")
    parser.add_argument("--date-now", action="store_true", help="set the date of newly imported Photos items to now")
    parser.add_argument("--report", type=Path, help="write download evidence to a new JSON file for comparison")
    parser.add_argument("--live-photos", action="store_true", help="package exposed Live Photo pairs on macOS; original downloads are preserved")
    parser.add_argument("--page-data", type=Path, help="Douyin only: content-only JSON for one post from a browser connector; requires --browser none")
    parser.add_argument("--jobs", type=int, metavar="N", help="limit simultaneous batch downloads; default: 3; one task per website with a short gap")
    parser.add_argument("--verify", action="store_true", help="recover: verify existing Photos originals before cleaning staged copies")
    parser.add_argument("--item", type=int, metavar="N", help="recover: save or verify only one unfinished item")
    parser.add_argument("--redownload", action="store_true", help="retry: download again instead of reusing a complete retained set")
    parser.add_argument("--clip", metavar="START-END",
                        help="clip video/audio to START-END (for example 0:30-2:00 or 10-45)")
    parser.add_argument("--max-duration", metavar="DURATION",
                        help="refuse downloads longer than DURATION (for example 10m, 90s, or 1:30)")
    parser.add_argument("--max-size", metavar="SIZE",
                        help="refuse downloads larger than SIZE (for example 100MB or 1.5GB)")
    parser.add_argument("--older-than", metavar="AGE",
                        help="clean only: minimum age of completed temporary stages (default: 7d)")
    parser.add_argument("--dry-run", action="store_true",
                        help="clean only: preview eligible temporary stages without deleting (default)")
    parser.add_argument("--apply", action="store_true",
                        help="clean only: delete eligible old temporary stages")
    tokens = list(sys.argv[1:] if argv is None else argv)
    # An HTTP URL immediately after bare --folder is the post, never a directory.
    for index in range(len(tokens) - 2, -1, -1):
        if tokens[index] == "--folder" and tokens[index + 1].startswith(("http://", "https://")):
            tokens.insert(index + 1, ".")
    if __package__:
        from . import command_help
    else:
        import command_help
    if command_help.show(parser, tokens):
        return 0
    for action in parser._actions:
        if action.dest in {'reader_browser', 'probe_browser', 'check_session', 'audio_folder', 'clear_audio_folder', 'article_folder', 'clear_article_folder', 'no_network', 'verify', 'item', 'redownload', 'older_than', 'dry_run', 'apply'}:
            action.help = argparse.SUPPRESS
    args = parser.parse_intermixed_args(tokens)
    if args.non_interactive or args.json:
        runtime.set_non_interactive(True)
    if args.json:
        runtime.set_json_mode(True)
    is_clean_cmd = bool(args.link and args.link[0] == "clean")
    is_mcp_cmd = bool(args.link and args.link[0] == "mcp")
    if (args.older_than is not None or args.dry_run or args.apply) and not is_clean_cmd:
        parser.error("--older-than, --dry-run, and --apply apply to linkget clean only.")
    if args.dry_run and args.apply:
        parser.error("Do not combine --dry-run with --apply.")
    has_media_limits = bool(args.clip is not None or args.max_duration is not None or args.max_size is not None)
    if has_media_limits and args.link and args.link[0] in {"inspect", "recover", "compare", "config", "auth", "logout", "doctor", "sites", "clean", "mcp", "login"}:
        parser.error("--clip, --max-duration, and --max-size apply only to downloads, batch, and retry.")
    if args.clip is not None:
        try:
            media_limits.parse_clip(args.clip)
        except ValueError as error:
            parser.error(str(error))
        if args.live_photos:
            parser.error("--clip cannot be combined with --live-photos.")
    if args.max_duration is not None:
        try:
            media_limits.parse_duration(args.max_duration)
        except ValueError as error:
            parser.error(str(error))
    if args.max_size is not None:
        try:
            media_limits.parse_size(args.max_size)
        except ValueError as error:
            parser.error(str(error))
    if args.no_network and (not args.link or args.link[0] != "inspect"):
        parser.error("--no-network applies to linkget inspect only.")
    if (args.probe_browser or args.check_session) and args.link != ["doctor"]:
        parser.error("Live diagnostic checks require linkget doctor.")
    if args.reader_browser is not None and args.link != ["config"]:
        parser.error("--reader-browser is saved with linkget config only.")
    if (args.audio_folder is not None or args.clear_audio_folder) and args.link != ["config"]:
        parser.error("--audio-folder and --clear-audio-folder are saved with linkget config only.")
    if args.audio_folder is not None and args.clear_audio_folder:
        parser.error("Do not combine --audio-folder with --clear-audio-folder.")
    if (args.article_folder is not None or args.clear_article_folder) and args.link != ["config"]:
        parser.error("--article-folder and --clear-article-folder are saved with linkget config only.")
    if args.article_folder is not None and args.clear_article_folder:
        parser.error("Do not combine --article-folder with --clear-article-folder.")
    if args.audio_format is not None:
        args.audio = True
    if args.video and args.link and args.link[0] in (command_help.SPECS.keys() - {"batch", "inspect"}) | {"login"}:
        parser.error("--video applies to downloads, batch, and inspect. Retry uses the original task mode.")
    try:
        validate_video_options(args)
    except ValueError as error:
        parser.error(str(error))
    if args.audio:
        if args.link == ["config"]:
            parser.error("Use linkget config --audio-folder [PATH] to set the default audio folder.")
        if args.photos:
            parser.error("Audio files cannot be imported into Photos. Use --folder or linkget config --audio-folder.")
        if args.date_now:
            parser.error("--date-now applies to Photos imports only, not --audio.")
        if args.live_photos:
            parser.error("--live-photos cannot be combined with --audio.")
    if args.article:
        if args.link == ["config"]:
            parser.error("Use linkget config --article-folder [PATH] to set the default article folder.")
        if args.audio:
            parser.error("Do not combine --article with --audio.")
        if args.photos:
            parser.error("Articles cannot be imported into Photos. Use --folder or linkget config --article-folder.")
        if args.date_now:
            parser.error("--date-now applies to Photos imports only, not --article.")
        if args.live_photos:
            parser.error("--live-photos cannot be combined with --article.")
        if has_media_limits:
            parser.error("--clip, --max-duration, and --max-size apply only to audio and video downloads, not --article.")
    if args.no_assets and args.link and args.link[0] in {"inspect", "recover", "compare", "config", "auth", "logout", "doctor", "sites", "clean", "mcp", "login"}:
        parser.error("--no-assets applies only to article downloads, batch, and retry.")
    if args.page_data:
        args.page_data = args.page_data.expanduser().resolve()
        if args.browser != "none" or len(args.link) not in {1, 2}:
            parser.error("--page-data requires one Douyin post (or retry TASK) and --browser none.")
        if args.link[0] == "retry":
            pass
        elif len(args.link) != 1 or not args.link[0].startswith(('http://', 'https://')):
            parser.error("--page-data applies to a single Douyin post, not other commands or batches.")
    if args.jobs is not None and args.jobs < 1:
        parser.error("--jobs must be a positive integer.")
    if args.item is not None and args.item < 1:
        parser.error("--item must be a positive integer.")
    if args.redownload and (not args.link or args.link[0] != "retry"):
        parser.error("--redownload applies to linkget retry only.")
    if (args.verify or args.item is not None) and (not args.link or args.link[0] != "recover"):
        parser.error("--verify and --item apply to linkget recover TASK only.")
    if args.link and args.link[0] == "inspect":
        target = " ".join(args.link[1:]).strip()
        if not target:
            parser.error("Use linkget inspect URL_OR_PATH.")
        if (args.photos or args.date_now or args.live_photos or args.report
                or args.jobs is not None or args.verify or args.item is not None
                or args.redownload or args.folder is not None or args.page_data is not None
                or args.no_assets):
            parser.error("Use --video, --audio, --article, --browser, --no-network, --json, or --non-interactive with linkget inspect.")
        payload = inspect_url.inspect_target(
            target,
            audio=args.audio,
            article=args.article,
            video=args.video,
            browser=args.browser,
            no_network=args.no_network,
            find_tool=find_tool,
            resolve_audio_folder=resolve_audio_folder,
            resolve_article_folder=resolve_article_folder,
            configured_destination=configured_destination,
        )
        payload["schema_version"] = agent_output.SCHEMA_VERSION
        payload["command"] = "inspect"
        if args.json:
            agent_output.emit(payload)
        elif not payload.get("ok"):
            err = payload.get("error") or {}
            status(err.get("label", "Error"), err.get("message", "Could not inspect target."), "31", stream=sys.stderr)
            if err.get("next_step"):
                status("Next", err["next_step"], stream=sys.stderr)
        else:
            status("Site", payload["site"])
            status("Engine", payload["engine"])
            if payload.get("canonical_url"):
                status("Canonical URL", payload["canonical_url"])
            elif payload.get("short_link_unresolved"):
                status("Canonical URL", "Short link unresolved (--no-network)")
            status("Audio mode", "Yes" if payload["effective_audio_mode"] else "No")
            status("Read support", "Yes" if payload["supports_read"] else "No")
            if payload.get("supports_article_download"):
                status("Article download", "Yes")
            status("Session", payload["session_state"])
            if payload.get("tools_required"):
                tool_summary = ", ".join(payload["tools_required"]) + (
                    f" (missing: {', '.join(payload['tools_missing'])})" if payload.get("tools_missing") else " · ready"
                )
                status("Tools", tool_summary)
            if payload.get("destination_preview"):
                status("Destination", payload["destination_preview"])
            elif payload.get("destination_error"):
                status("Destination", payload["destination_error"], "33")
            status("Probe", payload["probe_status"])
            meta = payload.get("metadata") or {}
            if meta.get("title"):
                status("Title", meta["title"])
            if meta.get("artist") or meta.get("uploader"):
                status("Creator", meta.get("artist") or meta.get("uploader"))
            if meta.get("duration") is not None:
                status("Duration", f"{meta['duration']}s")
            if meta.get("subtitles_available"):
                status("Subtitles", ", ".join(meta["subtitles_available"]))
        return 0 if payload.get("ok") else 1
    if args.link and args.link[0] in {"retry", "recover"}:
        command, *identifiers = args.link
        if len(identifiers) > 1:
            parser.error("Use linkget " + command + " [TASK].")
        try:
            if command == "recover" and not identifiers:
                if args.verify or args.item:
                    parser.error("Select a task: linkget recover TASK.")
                pending_tasks = []
                code = recovery.listing(status, records=pending_tasks)
                if args.json:
                    agent_output.emit({
                        "schema_version": agent_output.SCHEMA_VERSION,
                        "ok": code == 0,
                        "command": "recover",
                        "tasks": pending_tasks,
                    })
                return code
            journal = jobs.select(identifiers[0] if identifiers else None)
            if command == "recover" and any(token.split("=", 1)[0] == "--audio-format" for token in tokens):
                raise ValueError("--audio-format cannot be used with recover; use linkget retry TASK --redownload --audio-format FORMAT.")
            restore_options(args, journal, tokens)
            if getattr(args, 'page_data', None) and len(journal.data['items']) != 1:
                raise ValueError("--page-data cannot be used with a multi-post task.")
            all_audio_recovery = False
            if command == "recover" and args.folder is None and not getattr(args, "_explicit_photos", False):
                target_indices = ([args.item] if args.item is not None
                                  else sorted(set(journal.incomplete() + (journal.needs_verification() if args.verify else []))))
                valid_indices = [i for i in target_indices if 1 <= i <= len(journal.data["items"])]
                if valid_indices and not args.verify:
                    all_audio_recovery = all(
                        (journal.data["items"][i - 1].get("ready") or {}).get("evidence", {}).get("audio_mode") is True
                        for i in valid_indices
                    )
            if not all_audio_recovery:
                if command == "recover":
                    preflight.destination(args, find_tool, require_audio_tools=False)
                else:
                    preflight.destination(args, find_tool)
            if command == "recover":
                recover_records = []
                code = recovery.restore(journal, args, status, save_folder, import_photos,
                                        audio_folder_resolver=resolve_audio_folder, records=recover_records)
                if args.json:
                    agent_output.emit({
                        "schema_version": agent_output.SCHEMA_VERSION,
                        "ok": code == 0,
                        "command": "recover",
                        "task_id": journal.identifier,
                        "exit_code": code,
                        "items": recover_records,
                    })
                return code
            args._journal = journal
            args._indices = journal.incomplete()
            links = [journal.data["items"][i - 1]["input"] for i in args._indices]
            records = []
            code = batch_download(args, parser, links, records) if links else 0
            if args.report:
                write_report(args.report, VERSION, records, code)
            if args.json:
                batch_records = getattr(args, "_batch_records", {})
                items_payload = [
                    _agent_download_item(journal, idx, batch_records.get(idx))
                    for idx in args._indices
                ]
                agent_output.emit({
                    "schema_version": agent_output.SCHEMA_VERSION,
                    "ok": code == 0,
                    "command": "retry",
                    "task_id": journal.identifier,
                    "exit_code": code,
                    "items": items_payload,
                })
            return code
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            if args.json:
                agent_output.emit(agent_output.error_payload(command, error))
            else:
                report_failure(error)
            return 1
        except (KeyboardInterrupt, EOFError):
            if args.json:
                agent_output.emit(agent_output.error_payload(command, KeyboardInterrupt("Cancelled"), exit_code=130))
            else:
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
            if args.json:
                agent_output.emit({
                    "schema_version": agent_output.SCHEMA_VERSION,
                    "ok": True,
                    "command": "compare",
                    "rows": rows,
                })
                return 0
            for index, row in enumerate(rows, 1):
                status("Item", str(index))
                status("Comparison", row["result"])
                if "first" in row:
                    status("First", f"{row['first_count']} files · {row['first']}")
                    status("Second", f"{row['second_count']} files · {row['second']}")
            status("Quality", "Uploader maximum and watermark absence remain unverified.")
            return 0
        except (OSError, ValueError, KeyError, TypeError) as error:
            if args.json:
                agent_output.emit(agent_output.error_payload("compare", error))
            else:
                report_failure(error)
            return 1
    if args.link and args.link[0] == "config":
        try:
            return configure(args, parser)
        except (OSError, ValueError, RuntimeError) as error:
            if args.json:
                agent_output.emit(agent_output.error_payload("config", error))
            else:
                status("Error", redact(error), "31", stream=sys.stderr)
            return 1
    if args.link and args.link[0] == "login":
        parser.error("Browser connection is now part of the normal flow. Run linkget or linkget URL.")
    if args.link and args.link[0] in {"auth", "logout"}:
        try:
            return account_command(args, parser)
        except (OSError, ValueError, RuntimeError) as error:
            if args.json:
                agent_output.emit(agent_output.error_payload(args.link[0], error))
            else:
                status("Error", redact(error), "31", stream=sys.stderr)
            return 1
        except (KeyboardInterrupt, EOFError):
            if args.json:
                agent_output.emit(agent_output.error_payload(args.link[0], KeyboardInterrupt("Stopped"), exit_code=130))
            else:
                status("Cancelled", "Stopped", "33", stream=sys.stderr)
            return 130
    if is_clean_cmd:
        if len(args.link) > 1:
            parser.error("Use linkget clean [--dry-run | --apply] [--older-than AGE] [--json].")
        if (args.photos or args.date_now or args.live_photos or args.audio or args.article or args.no_assets
                or args.folder is not None or args.report is not None or args.page_data is not None or args.jobs is not None
                or args.browser != "auto" or args.profile is not None or args.rendered_backend is not None):
            parser.error("Use --dry-run, --apply, --older-than, --json, or --non-interactive with linkget clean.")
        try:
            age_spec = args.older_than if args.older_than is not None else cleanup.DEFAULT_OLDER_THAN
            media_limits.parse_age(age_spec)
        except ValueError as error:
            parser.error(str(error))
        try:
            result = cleanup.inspect_and_clean(older_than=args.older_than, apply=args.apply)
            payload = {
                "schema_version": agent_output.SCHEMA_VERSION,
                "command": "clean",
                **result,
            }
            if args.json:
                agent_output.emit(payload)
            else:
                mode_label = "Applied" if args.apply else "Dry run"
                status("Clean", f"{mode_label} (older than {result['older_than']})")
                status("Eligible", f"{result['eligible_count']} stages · {format_size(result['reclaimable_bytes'])}")
                if args.apply:
                    status("Removed", f"{result['deleted_count']} stages · {format_size(result['freed_bytes'])}")
                else:
                    status("Hint", "Pass --apply to remove eligible completed temporary stages.")
                status("Kept", str(result["kept_count"]))
                for err in result.get("errors", []):
                    status("Warning", err.get("message", "Could not clean stage."), "33", stream=sys.stderr)
            return 0 if result.get("ok", True) else 1
        except (OSError, ValueError, RuntimeError) as error:
            if args.json:
                agent_output.emit(agent_output.error_payload("clean", error))
            else:
                report_failure(error)
            return 1
    if is_mcp_cmd:
        if len(args.link) > 1:
            parser.error("Use linkget mcp.")
        if (args.json or args.photos or args.date_now or args.live_photos or args.audio or args.article or args.no_assets
                or args.folder is not None or args.report is not None or args.page_data is not None
                or args.jobs is not None or args.browser != "auto"
                or args.profile is not None or args.rendered_backend is not None):
            parser.error("linkget mcp takes no download flags; configure tool options per JSON-RPC call.")
        return mcp_server.serve()
    is_batch = bool(args.link and args.link[0] == "batch")
    batch_links = args.link[1:] if is_batch else []
    if args.link and not is_batch and not any("http://" in item or "https://" in item for item in args.link) and args.link not in (["doctor"], ["sites"]):
        parser.error("Unknown command. Use linkget sites, linkget auth, linkget config, or linkget help. To download, paste a post URL.")
    args.link = " ".join(args.link)
    if args.link == "doctor":
        try:
            doc_payload, doc_rows, code = collect_doctor(args)
            if not args.json:
                _print_doctor(doc_payload, doc_rows)
            if args.folder is not None or args.photos or args.live_photos or args.audio:
                try:
                    if args.audio:
                        args.folder = resolve_audio_folder(args.folder)
                    elif args.folder is True:
                        args.folder = Path.cwd()
                    preflight.destination(args, find_tool)
                    dest_path = str(args.folder or originals_directory())
                    doc_payload["destination"] = {"ok": True, "path": dest_path, "photos": bool(args.photos)}
                    if not args.json:
                        status("Destination", "Writable · " + dest_path)
                        if args.photos:
                            status("Photos", "Automation and managed-copy checks run at import time")
                except (OSError, ValueError, RuntimeError) as error:
                    code = 1
                    doc_payload["ok"] = False
                    doc_payload["destination"] = {"ok": False, "error": agent_output.classify_error(error)}
                    if not args.json:
                        report_failure(error)
            if args.json:
                agent_output.emit(doc_payload)
            return code
        except (OSError, ValueError, RuntimeError) as error:
            if args.json:
                agent_output.emit(agent_output.error_payload("doctor", error))
            else:
                report_failure(error)
            return 1
    if args.link == "sites":
        toutiao_entry = ("Toutiao", "Article downloads & reading, plus local video transcription via linkget read; Chrome and Neo supported")
        wechat_entry = ("WeChat Official Accounts", "Article downloads & reading (direct HTTP with optional Chrome/Neo /s/<id> fallback)")
        web_entry = ("Web articles", "General public HTTPS/HTTP article downloads and Markdown/HTML/plain reads via linkget read")
        entries = (*SUPPORTED_SITES, toutiao_entry, wechat_entry, web_entry)
        if args.json:
            agent_output.emit({
                "schema_version": agent_output.SCHEMA_VERSION,
                "ok": True,
                "command": "sites",
                "sites": [
                    {
                        "name": site,
                        "content": content,
                        "audio_only": is_audio_only_site(site),
                        "supports_read": site != "Pixiv",
                    }
                    for site, content in entries
                ],
            })
            return 0
        columns = shutil.get_terminal_size((80, 24)).columns
        label_width = max(cell_width(site) for site, _ in entries)
        content_width = columns - label_width - 3
        for site, content in entries:
            if content_width >= 30:
                status(site, wrap_cells(content, content_width), label_width=label_width)
            elif not runtime.quiet_status():
                print(f"  {site}")
                print("    " + wrap_cells(content, columns - 4).replace("\n", "\n    "))
        print()
        for note in (
            "Use --video for any single public video link yt-dlp can download as MP4.",
            "Use --audio (or --audio-format mp3|m4a|flac|wav|opus) to save audio from video posts to a folder.",
            "Use linkget URL [--article] --folder DIR to archive offline articles (Markdown, HTML, JSON, local images/videos).",
            "Use linkget read for video speech/keyframes, Toutiao, WeChat Official Accounts, or web articles.",
            "Pixiv is image-only. Availability varies by URL; read --help shows options.",
        ):
            print("  " + wrap_cells(note, columns - 2).replace("\n", "\n  "))
        return 0
    if args.profile and args.browser in {"auto", "none"}:
        parser.error("--profile requires an explicit --browser, such as chrome or firefox.")
    try:
        if args.report and args.report.expanduser().exists():
            raise ValueError("The report already exists. Choose a new path to preserve the comparison reference.")
        if args.report and not args.report.expanduser().parent.is_dir():
            raise ValueError("The report's parent folder does not exist. Choose an existing folder.")
        args._explicit_folder = args.folder is not None
        args._explicit_photos = bool(args.photos)
        args.folder_from_default = False
        is_article_run = bool(getattr(args, "article", False)) or (
            not (args.audio or args.video) and (
                (not is_batch and args.link and _is_article_link_text(args.link))
                or (is_batch and batch_links and all(_is_article_link_text(link) for link in batch_links))
            )
        )
        if is_article_run:
            if args.photos:
                raise ValueError("Articles cannot be imported into Photos. Use --folder or linkget config --article-folder.")
            if args.date_now:
                raise ValueError("--date-now applies to Photos imports only, not article downloads.")
            if args.live_photos:
                raise ValueError("--live-photos cannot be used with article downloads.")
            if args.audio:
                raise ValueError("Article pages do not contain downloadable audio.")
        if not (is_article_run or args.audio or args.video) and (
            (not is_batch and args.link and _is_audio_only_link_text(args.link))
            or (is_batch and batch_links and all(_is_audio_only_link_text(link) for link in batch_links))
        ):
            if args.photos:
                raise ValueError("Audio files cannot be imported into Photos. Use --folder or linkget config --audio-folder.")
            if args.date_now:
                raise ValueError("--date-now applies to Photos imports only, not audio downloads.")
            if args.live_photos:
                raise ValueError("--live-photos cannot be used with audio downloads.")
            args.audio = True
        if args.audio:
            args.folder = resolve_audio_folder(args.folder)
        elif is_article_run:
            args.folder = resolve_article_folder(args.folder)
            if not args._explicit_folder:
                args.folder_from_default = True
        elif args.photos:
            args.folder = None
        elif args.folder is True:
            args.folder = Path.cwd()
        elif args.folder is None:
            setting = configured_destination()
            if setting["mode"] == "cwd":
                args.folder = Path.cwd()
                args.folder_from_default = True
            elif setting["mode"] == "folder":
                args.folder = Path(setting["path"])
                args.folder_from_default = True
        if args.folder is not None and args.date_now:
            if args.json:
                raise ValueError("--date-now applies to Photos imports. Use --photos --date-now to override a folder default.")
            parser.error("--date-now applies to Photos imports. Use --photos --date-now to override a folder default.")
        preflight.destination(args, find_tool)
        records = []
        if is_batch:
            code = batch_download(args, parser, batch_links, records)
            if args.report:
                write_report(args.report, VERSION, records, code)
                status("Report", str(args.report.expanduser().resolve()))
            if args.json:
                batch_journal = getattr(args, "_batch_journal", None)
                batch_records = getattr(args, "_batch_records", {})
                if batch_journal is not None:
                    items_payload = [
                        _agent_download_item(batch_journal, pos + 1, batch_records.get(pos + 1))
                        for pos in range(len(batch_journal.data["items"]))
                    ]
                else:
                    items_payload = records
                agent_output.emit({
                    "schema_version": agent_output.SCHEMA_VERSION,
                    "ok": code == 0,
                    "command": "batch",
                    "task_id": getattr(args, "_batch_journal_id", None),
                    "exit_code": code,
                    "completed_count": sum(1 for item in items_payload if item.get("status") == "completed"),
                    "failed_count": sum(1 for item in items_payload if item.get("status") != "completed"),
                    "items": items_payload,
                })
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
            if args.json:
                item_payload = _agent_download_item(journal, 1, records[0] if records else None)
                agent_output.emit({
                    "schema_version": agent_output.SCHEMA_VERSION,
                    "ok": code == 0,
                    "command": "download",
                    "task_id": journal.identifier,
                    "exit_code": code,
                    **item_payload,
                })
        return code
    except (OSError, ValueError, RuntimeError) as error:
        if args.json:
            agent_output.emit(agent_output.error_payload("batch" if is_batch else "download", error))
        else:
            report_failure(error)
        return 1
    except (KeyboardInterrupt, EOFError):
        if args.json:
            agent_output.emit(agent_output.error_payload("batch" if is_batch else "download", KeyboardInterrupt("Download stopped"), exit_code=130))
        else:
            status("Cancelled", "Download stopped", "33", stream=sys.stderr)
        return 130


def restore_options(args, journal, tokens):
    saved = journal.data["options"]
    args._rendered_backend_override = any(token.split("=", 1)[0] == "--rendered-backend" for token in tokens)
    args._reader_browser_override = any(token.split("=", 1)[0] == "--browser" for token in tokens)
    args._explicit_photos = any(token.split("=", 1)[0] == "--photos" for token in tokens)
    args._explicit_folder = any(token.split("=", 1)[0] == "--folder" for token in tokens)
    args._explicit_audio_format = any(token.split("=", 1)[0] == "--audio-format" for token in tokens)
    args._explicit_clip = any(token.split("=", 1)[0] == "--clip" for token in tokens)
    args.folder_from_default = False
    for name in jobs.OPTIONS:
        flags = {"--" + name.replace("_", "-")}
        if name == "folder":
            flags.add("--photos")
        if not any(token.split("=", 1)[0] in flags for token in tokens) and name in saved:
            setattr(args, name, saved[name])
    if args._explicit_folder or args._explicit_photos:
        args.folder_from_default = False
    if getattr(args, "audio_format", None) is not None:
        args.audio = True
    validate_video_options(args)
    if args._reader_browser_override and not args._rendered_backend_override:
        args.rendered_backend = None
    if args.photos:
        args.folder = None
    elif args.folder is True:
        args.folder = Path.cwd()
    elif args.folder is not None:
        args.folder = Path(args.folder).expanduser().resolve()
    if getattr(args, "audio", False):
        if args.photos:
            raise ValueError("Audio files cannot be imported into Photos. Use --folder.")
        if args.date_now:
            raise ValueError("--date-now applies to Photos imports only, not --audio.")
        if args.live_photos:
            raise ValueError("--live-photos cannot be combined with --audio.")
        if args.folder is None:
            args.folder = resolve_audio_folder()
    if getattr(args, "article", False):
        if args.photos:
            raise ValueError("Articles cannot be imported into Photos. Use --folder or linkget config --article-folder.")
        if args.date_now:
            raise ValueError("--date-now applies to Photos imports only, not --article.")
        if args.live_photos:
            raise ValueError("--live-photos cannot be combined with --article.")
        if getattr(args, "audio", False):
            raise ValueError("Do not combine --article with --audio.")
        if args.folder is None:
            args.folder = resolve_article_folder()
            args.folder_from_default = True
    if args.folder is not None and args.date_now:
        raise ValueError("--date-now applies to Photos imports only.")
    if args.profile and args.browser in {"auto", "none"}:
        if not any(token.split("=", 1)[0] == "--profile" for token in tokens):
            args.profile = None
    if args.profile and args.browser in {"auto", "none"}:
        raise ValueError("--profile requires an explicit browser.")
    if getattr(args, 'page_data', None) and args.browser != 'none':
        raise ValueError("--page-data requires --browser none, including on retry.")
    if getattr(args, 'page_data', None):
        args.page_data = Path(args.page_data).expanduser().resolve()
    if args.report and (args.report.expanduser().exists() or not args.report.expanduser().parent.is_dir()):
        raise ValueError("Choose a new report path in an existing folder.")


def save_folder(files, folder):
    return destinations.save_folder(files, folder)


def import_photos(files, date_now, live_pairs=None):
    return destinations.import_photos(files, date_now, live_pairs, retain_originals, verify_library_copies)


def _agent_download_item(journal, index, evidence=None):
    row = journal.data["items"][index - 1]
    ev = dict(evidence or {})
    dest = row.get("destination_folder") or journal.data["options"].get("folder") or "Photos"
    dest_files = [
        entry["path"] for entry in row.get("destination_files", [])
        if isinstance(entry, dict) and entry.get("path")
    ]
    status_val = ev.get("status") or row.get("status")
    item = {
        "item": index,
        "status": status_val,
        "site": ev.get("site"),
        "reference": ev.get("reference"),
        "destination": str(dest),
        "audio_mode": bool(ev.get("audio_mode") or journal.data["options"].get("audio")),
        "saved_count": ev.get("saved_count", 0),
        "skipped_count": ev.get("skipped_count", 0),
        "saved_files": dest_files,
        "files": ev.get("files", []),
    }
    for opt_key in ("article_folder", "title", "author", "assets_downloaded", "assets_failed"):
        if ev.get(opt_key) is not None:
            item[opt_key] = ev[opt_key]
    failure_info = row.get("failure")
    if status_val != "completed":
        if isinstance(failure_info, dict):
            cat = failure_info.get("category", "unknown")
            item["error"] = {
                "category": cat,
                "message": failure_info.get("message", "Unknown"),
                "next_step": failure_info.get("next_step", ""),
                "phase": failure_info.get("phase"),
                "retryable": bool(failure_info.get("retryable", cat in {"rate_limit", "network"})),
            }
        elif ev.get("failure"):
            item["error"] = {"category": ev["failure"], "retryable": ev["failure"] in {"rate_limit", "network"}}
    return item


def batch_download(args, parser, links, records=None):
    return run_batch(args, parser, links, records, download, first_run, status, jobs.Journal.create)


def download(args, setup_results, records=None, media_consumer=None):
    phase = "download"
    args._retry_failure = None
    args._retry_after = None
    args._last_failure = None
    stage = None
    session_dir = None
    info_path = None
    metadata_proof = None
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
                if (getattr(args, "_explicit_audio_format", False)
                        and args.audio_format != journal.data["options"].get("audio_format")):
                    raise ValueError(
                        f"--audio-format cannot be changed without --redownload; "
                        f"use linkget retry {journal.identifier} --redownload --audio-format {args.audio_format}."
                    )
                if (getattr(args, "_explicit_clip", False)
                        and args.clip != journal.data["options"].get("clip")):
                    raise ValueError(
                        f"--clip cannot be changed without --redownload; "
                        f"use linkget retry {journal.identifier} --redownload --clip {args.clip}."
                    )
                # A failed save already has a checked complete set. Retry the save.
                phase = "save"
                restore_records = []
                result = recovery.restore(journal, args, status, save_folder, import_photos,
                                          claimed=True, indices=[index],
                                          audio_folder_resolver=resolve_audio_folder,
                                          records=restore_records)
                evidence.update(ready.get("evidence", {}), status="completed" if result == 0 else "failed")
                if restore_records:
                    for key in ("saved_count", "skipped_count", "audio_mode"):
                        if key in restore_records[0]:
                            evidence[key] = restore_records[0][key]
                    if result != 0 and restore_records[0].get("error"):
                        err = RuntimeError(restore_records[0]["error"])
                        record_failure(args, err, phase)
                        evidence["failure"] = classify(err, phase=phase).category
                return result
        if args.link:
            url = args.link
        elif runtime.is_interactive():
            url = input("  Paste a link (no quotes needed): ")
        elif not sys.stdin.isatty():
            url = sys.stdin.read()
        else:
            raise ValueError("A link is required in non-interactive mode.")
        if current:
            staging.remember_earlier(status)
            previous = journal.data["items"][index - 1]
            if previous.get("stage"):
                stages = previous.get("previous_stages", []) + [previous["stage"]]
                jobs.record(previous_stages=list(dict.fromkeys(stages)))
        if getattr(args, 'redownload', False):
            transfers.reset_progress()
        jobs.record(input=url, status="running", failure=None, ready=None, receipt=None, stage=None, stage_identity=None)
        started = time.monotonic()
        video_mode = getattr(args, "video", False)
        if video_mode:
            url, site, domain = route_video(single_url(url))
            engine = "yt-dlp"
        else:
            with activity("Resolving") if any(host in url for host in ("v.douyin.com", "vm.tiktok.com", "vt.tiktok.com", "tiktok.com/t/", "xhslink.com", "xhslink.cn", "t.cn/", "on.soundcloud.com")) else nullcontext():
                url = normalize_link(url)
            if media_consumer is None:
                article_target = article_archive.classify_article_target(url, force_article=bool(getattr(args, "article", False)))
                if article_target is not None:
                    domain = article_target.get("domain")
                    url = article_target["url"]
                    return download_article_target(args, article_target, setup_results, evidence, started)
                if getattr(args, "article", False):
                    raise ValueError("--article supports X posts/articles, WeChat, Toutiao articles, Zhihu articles, or public web article URLs.")
            site, engine, domain = route(url)
        evidence.update(reference=reference(url), site=site)
        audio_mode = (media_consumer is None) and (getattr(args, "audio", False) or is_audio_only_site(site))
        clip_range = media_limits.parse_clip(args.clip) if (media_consumer is None and getattr(args, "clip", None)) else None
        max_dur_s = media_limits.parse_duration(args.max_duration) if (media_consumer is None and getattr(args, "max_duration", None)) else None
        max_size_b = media_limits.parse_size(args.max_size) if (media_consumer is None and getattr(args, "max_size", None)) else None
        if clip_range is not None:
            if engine == "pixiv":
                raise ValueError("--clip applies to audio and video media, not Pixiv image artworks.")
            if getattr(args, "live_photos", False):
                raise ValueError("--clip cannot be combined with --live-photos.")
        if audio_mode:
            if getattr(args, "photos", False):
                raise ValueError("Audio files cannot be imported into Photos. Use --folder or linkget config --audio-folder.")
            if getattr(args, "live_photos", False):
                raise ValueError("--live-photos cannot be used with audio downloads.")
            if getattr(args, "date_now", False):
                raise ValueError("--date-now applies to Photos imports only, not audio downloads.")
            if engine == "pixiv":
                raise ValueError("Pixiv artworks are images only and contain no audio.")
        page_data = getattr(args, 'page_data', None)
        if page_data and (engine != 'douyin' or args.browser != 'none'):
            raise ValueError("--page-data supports only Douyin with --browser none.")
        provided_item = load_douyin_page(page_data, url) if page_data else None
        if not audio_mode and not args.folder and sys.platform != "darwin":
            raise ValueError("Photos is available on macOS only. Use --folder to choose a destination.")
        required = ([] if engine in {"douyin", "xiaohongshu", "kuaishou", "pixiv"} else [engine]) + (
            ["ffmpeg", "ffprobe"] if (engine == "yt-dlp" or audio_mode or clip_range is not None)
            else (["ffprobe"] if max_dur_s is not None else [])
        )
        if domain == "reddit.com" and engine == "gallery-dl":
            required.extend(["yt-dlp", "ffmpeg"])
        if domain == "youtube.com":
            required.append("deno")
        supports_auth = domain in accounts.SITES.values()
        cookie_spec = browser_spec(args, "gallery-dl", domain) if supports_auth and engine != "douyin" and args.browser not in {"auto", "none"} else None
        if cookie_spec and "gallery-dl" not in required:
            required.append("gallery-dl")
        tools = {name: find_tool(name) for name in required}
        missing = [name for name, path in tools.items() if not path]
        if missing:
            raise RuntimeError(installation_hint(missing))
        existing_dest = (journal.data["items"][index - 1].get("destination_folder")
                         if current else None)
        if audio_mode:
            if getattr(args, "_explicit_folder", False) and args.folder is not None:
                folder = args.folder.expanduser().resolve()
            elif existing_dest:
                folder = Path(existing_dest).expanduser().resolve()
            elif not args.folder or getattr(args, "folder_from_default", False):
                folder = resolve_audio_folder(None)
            else:
                folder = args.folder.expanduser().resolve()
            preflight.writable_directory(folder)
        else:
            folder = args.folder.expanduser().resolve() if args.folder else None
        if folder:
            folder.mkdir(parents=True, exist_ok=True)
            jobs.record(destination_folder=str(folder))
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
        browser_item = provided_item
        tried_douyin_page = False
        attempt = 0
        log_writer = None
        while True:
            check_batch_cancelled()
            try:
                source_notes = []
                download_info = DownloadResult()
                media_dir = stage / ("media" if attempt == 0 else f"signed-in-media-{attempt}")
                media_dir.mkdir()
                if not getattr(_batch, "state", None) and not runtime.quiet_status():
                    print()
                log_path = stage / 'download.log'
                if log_path.exists():
                    # This local proof also survives non-journal reading retries.
                    proof = log_writer.proof if log_writer is not None else None
                    if proof is None or proof.path != ownership.canonical(log_path):
                        raise RuntimeError('An existing log has no writer evidence; files were kept.')
                    proof.remove()
                log_writer = ownership.Writer(log_path, external=True, register=True)
                with log_writer as log:
                    log.write(f"Source: {url}\n")
                    command = None
                    if engine not in {"douyin", "xiaohongshu", "kuaishou", "pixiv"}:
                        command = (command_for(args, url, engine, domain, media_dir, tools, cookie_path, audio_only=True)
                                   if audio_mode else
                                   command_for(args, url, engine, domain, media_dir, tools, cookie_path))
                    if engine == "yt-dlp":
                        info_path = stage / "video-info.json"
                        if info_path.exists() and metadata_proof is not None:
                            metadata_proof.remove()
                        with activity("Preparing"):
                            command = prepare_video(command, info_path, log, domain)
                        metadata_proof = getattr(command, 'metadata_proof', None)
                        if getattr(_batch, "state", {}).get("panel"):
                            batch_media([], json.loads(info_path.read_text()))
                        reader = getattr(args, 'read_metadata_consumer', None)
                        if reader and reader(json.loads(info_path.read_text()), url, site):
                            evidence.update(status='completed')
                            return 0
                        if media_consumer is None and (max_dur_s is not None or max_size_b is not None or clip_range is not None):
                            media_limits.check_metadata_limits(
                                json.loads(info_path.read_text()),
                                max_duration=max_dur_s,
                                max_size=max_size_b,
                                clip=clip_range,
                            )
                        quality = selected_quality(info_path, audio_only=True) if audio_mode else selected_quality(info_path)
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
                                if engine == "douyin" and not audio_mode and (args.live_photos or not args.folder):
                                    notes = downloader(url, media_dir, cookie_path,
                                                       browser_item=browser_item, require_motion=True)
                                else:
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
                            result = run_process(command, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                if result is not None and result.returncode:
                    tail = (stage / "download.log").read_text(errors="replace").splitlines()[-8:]
                    log_text = "\n".join(tail)
                    if needs_login(log_text):
                        raise LoginRequired(f"{site} authentication response:\n" + log_text)
                    raise RuntimeError("Download failed.\n" + log_text)
                if engine == "gallery-dl" and domain == "x.com" and not audio_mode and x_article.is_x_status_url(url):
                    x_log_text = (stage / "download.log").read_text(errors="replace")
                    x_article_detected = "Error while processing article data" in x_log_text
                    probed_x_article = None
                    if not x_article_detected:
                        m_tid = re.search(r"/status/(\d+)", url)
                        target_tid = m_tid.group(1) if m_tid else None
                        for sidecar in sorted(media_dir.rglob("*.json")):
                            if sidecar.is_symlink() or not sidecar.is_file():
                                continue
                            try:
                                sidecar_data = json.loads(sidecar.read_text(errors="replace"))
                            except (ValueError, OSError):
                                continue
                            if (isinstance(sidecar_data, dict)
                                    and isinstance(sidecar_data.get("article"), dict)
                                    and sidecar_data["article"]
                                    and (target_tid is None or str(sidecar_data.get("tweet_id") or "") in {"", target_tid})):
                                x_article_detected = True
                                break
                    if (not x_article_detected and cookie_path is not None and not needs_login(x_log_text)
                            and not any(p.is_file() and not p.is_symlink() and p.suffix.lower() in (MEDIA | AUDIO)
                                        for p in media_dir.rglob("*"))):
                        try:
                            probed_x_article = x_article.fetch_via_gallery_dl(
                                url, tools["gallery-dl"], cookie_path=cookie_path
                            )
                            if probed_x_article is not None:
                                x_article_detected = True
                        except x_article.XArticleLoginRequired:
                            x_article_detected = True
                        except x_article.XArticleError:
                            pass
                    if x_article_detected:
                        if stage is not None and stage.name.startswith("linkget-") and not stage.is_symlink():
                            shutil.rmtree(stage, ignore_errors=True)
                            jobs.record(stage=None, stage_identity=None)
                            stage = None
                        read_article_consumer = getattr(args, "read_article_consumer", None)
                        if read_article_consumer is not None:
                            if probed_x_article is None:
                                probed_x_article = _acquire_x_article(
                                    url, args, setup_results, find_tool, cookie_path=cookie_path
                                )
                            read_article_consumer(probed_x_article)
                            evidence.update(status="completed")
                            return 0
                        x_target = {
                            "kind": "x_article",
                            "url": x_article.canonical_x_url(url),
                            "site": "X Article",
                            "engine": "x_article",
                            "domain": "x.com",
                            "supports_auth": True,
                        }
                        return download_article_target(
                            args,
                            x_target,
                            setup_results,
                            evidence,
                            started,
                            prefetched_article=probed_x_article,
                            cookie_path=cookie_path,
                        )
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
                    if domain == "weibo.com" and not audio_mode:
                        download_info = gallery_evidence(media_dir, domain, remove=False)
                        with activity("Checking sources"):
                            source_notes = prefer_clean_images(media_dir, find_tool("ffmpeg"))
                        download_info.live_pairs = [(image if (media_dir / image).exists() else str(Path(image).with_name(Path(image).stem + "_preferred" + Path(image).suffix)), video)
                                                    for image, video in download_info.live_pairs]
                    else:
                        download_info = gallery_evidence(media_dir, domain, remove=False)
                for note in source_notes:
                    status("Media", note)
                accepted = MEDIA | (AUDIO if (media_consumer or audio_mode) else set())
                files = sorted(path for path in media_dir.iterdir() if path.is_file() and path.suffix.lower() in accepted)
                files = ownership.FileList(files, transfers.proofs_for(files))
                unfinished = [path for path in media_dir.iterdir() if path.suffix.lower() not in accepted | {".json"}]
                if unfinished:
                    raise RuntimeError("The download contains unsupported or incomplete files. Files have been kept for review.")
                if not files:
                    report = (stage / "download.log").read_text(errors="replace")
                    if needs_login(report):
                        raise LoginRequired(f"{site} requires a signed-in account to download this post.")
                    if domain == "tiktok.com":
                        raise RuntimeError("TikTok returned no downloadable media. Open the post in your browser and check its availability or website verification, then retry.")
                    raise RuntimeError("No audio was downloaded from this post." if audio_mode else "No photos or videos were downloaded from this post.")
                if video_mode and (len(files) != 1 or files[0].suffix.lower() not in VIDEO):
                    raise RuntimeError("--video requires a single video file. The downloaded files have been kept for review.")
                if download_info.expected_count is not None and len(files) != download_info.expected_count:
                    raise RuntimeError(f"Incomplete media set: {len(files)} / {download_info.expected_count} files. Files have been kept.")
                break
            except (DouyinMediaUnavailable, DouyinDownloadInterrupted):
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
                if (not tried_douyin_page and args.browser in {"auto", "chrome"}
                        and domain not in accounts.preferences().get("blocked", [])):
                    tried_douyin_page = True
                    frozen = journal.data["items"][index - 1].get("rendered_backend") if current else None
                    choice = getattr(args, "rendered_backend", None)
                    if frozen and not (getattr(args, "_rendered_backend_override", False) or getattr(args, "_reader_browser_override", False)):
                        choice = frozen
                    backend = rendered_page.selected_backend(choice, browser=args.browser)
                    if current:
                        journal.update(index, rendered_backend=backend)
                    provenance = rendered_page.selection(backend=choice, browser=args.browser,
                        backend_source='retained checkpoint' if (frozen and choice == frozen or getattr(args,'_journal',None)) and not (getattr(args,'_rendered_backend_override',False) or getattr(args,'_reader_browser_override',False)) else 'command option',
                        browser_source='retained checkpoint' if getattr(args,'_journal',None) and not getattr(args,'_reader_browser_override',False) else 'command browser option')
                    status("Browser", "Reading the post in " + ("Neo" if backend == "neo" else "Chrome") + " · " + provenance['source'])
                    browser_item = batch_interaction(lambda: read_douyin_browser(url, backend=backend))
                    attempt += 1
                    continue
                raise
            except LoginRequired as failure:
                if args.browser == "none" or not supports_auth:
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
                if not tried_browser and runtime.is_interactive():
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
        transfers.verify(files)
        if clip_range is not None and engine != "yt-dlp" and media_consumer is None:
            phase = 'postprocess'
            with activity("Clipping"):
                files = media_limits.clip_media_files(
                    files,
                    download_info.live_pairs,
                    stage / "clipped",
                    clip=clip_range,
                    ffmpeg=tools.get("ffmpeg"),
                    ffprobe=tools.get("ffprobe"),
                )
            download_info.live_pairs = []
            download_info.expected_count = len(files)
            phase = 'download'
        panel_metadata = dict(download_info.metadata)
        if audio_mode and media_consumer is None:
            metadata_by_file = {}
            for sidecar in media_dir.glob("*.json"):
                try:
                    parsed_sidecar = json.loads(sidecar.read_text())
                except (ValueError, OSError):
                    continue
                if isinstance(parsed_sidecar, dict):
                    metadata_by_file[sidecar.name[:-5]] = parsed_sidecar
            default_metadata = json.loads(info_path.read_text()) if info_path is not None else download_info.metadata
            if isinstance(default_metadata, dict):
                panel_metadata = default_metadata
            phase = 'postprocess'
            with activity("Preparing audio"):
                files, audio_notes = prepare_audio(
                    files,
                    download_info.live_pairs,
                    metadata_by_file,
                    default_metadata,
                    url,
                    site,
                    stage / "audio",
                    tools,
                    target_format=getattr(args, "audio_format", None),
                )
            for note in audio_notes:
                status("Audio", note)
            had_fallback = any(v == "fallback" for v in download_info.variants.values())
            download_info.live_pairs = []
            download_info.expected_count = len(files)
            download_info.variants = {path.name: "fallback" for path in files} if had_fallback else {}
            phase = 'download'
        batch_media(files, panel_metadata)
        with activity("Checking") if getattr(_batch, "state", None) else nullcontext():
            media_records = inspect_files(files, download_info.variants)
        transfers.verify(files)
        evidence.update(files=media_records, expected_count=download_info.expected_count,
                        missing_motion=download_info.missing_motion, live_pairs=download_info.live_pairs,
                        highest_quality="not_verified", watermark="not_verified")
        if audio_mode:
            evidence['audio_mode'] = True
        if 'motion_metadata_complete' in download_info.metadata:
            evidence['motion_metadata_complete'] = download_info.metadata['motion_metadata_complete']
        if any(record.get('probe_ok') is False for record in media_records):
            transfers.invalidate([path for path, record in zip(files, media_records) if record.get('probe_ok') is False])
            raise RuntimeError("The downloaded media did not pass format validation. Files have been kept; nothing was saved or imported.")
        if media_consumer is None and (max_dur_s is not None or max_size_b is not None):
            media_limits.check_file_limits(files, media_records, max_duration=max_dur_s, max_size=max_size_b)
        status("Quality", quality_summary(media_records))
        count_text = f"{len(files)} / {download_info.expected_count} files" if download_info.expected_count is not None else f"{len(files)} files · source count unknown"
        status("Media count", count_text)
        if any(r['source'] == "fallback" for r in media_records):
            status("Source", "Fallback media included", "33")
        if media_consumer is not None:
            metadata = json.loads(info_path.read_text()) if info_path is not None else read_metadata
            phase = 'postprocess'
            media_consumer(files, media_records, download_info, url, site, metadata)
            evidence.update(status="completed")
            return 0
        ready = {"files": jobs.snapshot(files),
                 "live_pairs": download_info.live_pairs, "prepared": [], "evidence": dict(evidence)}
        jobs.record(ready=ready)
        prepared = []
        if download_info.live_pairs:
            if args.live_photos:
                phase = 'pair'
                with activity("Pairing Live Photos"):
                    prepared = prepare_pairs(files, download_info.live_pairs, stage / "live-photos")
                ready["prepared"] = [jobs.snapshot(pair) for pair in prepared]
                jobs.record(ready=ready)
                status("Live Photos", f"{len(prepared)} source pairs recognized by macOS")
            else:
                status("Live Photos", f"{len(download_info.live_pairs)} pairs saved separately · use --live-photos to pair")
        transfers.verify(files)
        phase = 'save' if folder else 'import'
        with saving_activity("Saving" if folder else "Importing"):
            jobs.record(destination_files=[])
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
            transfers.verify(files)
            transfers.verify_outputs()
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
        record_failure(args, error, "login")
        category = classify(error).category
        evidence['failure'] = category if category in {'login_required', 'login_expired'} else 'login_required'
        login_guidance(args, domain, url, str(error))
        return 1
    except subprocess.TimeoutExpired as error:
        record_failure(args, error, phase)
        evidence['failure'] = report_failure(error, phase=phase)
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        record_failure(args, error, phase)
        evidence['failure'] = report_failure(error, phase=phase)
        return 1
    except (KeyboardInterrupt, EOFError):
        evidence['status'] = "cancelled"
        status("Cancelled", "Download stopped", "33", stream=sys.stderr)
        return 130
    finally:
        if records is not None:
            records.append(evidence)
        if metadata_proof is not None:
            try:
                if metadata_proof.path.exists():
                    metadata_proof.remove()
            except (OSError, ValueError, RuntimeError):
                pass  # A metadata replacement is retained, never re-observed.
        if session_dir is not None:
            session_dir.cleanup()
        if stage is not None:
            # Uninstrumented engine/helper outputs and sidecars have no writer
            # receipt. Observing them cannot authorize recursive deletion.
            if media_consumer is not None:
                status("Files kept", str(stage), "33", stream=sys.stderr)
            if media_consumer is None:
                if current:
                    try:
                        progress = transfers.summary(journal.data['items'][index - 1])
                        if progress:
                            status("Partial", progress, "33", stream=sys.stderr)
                    except (ValueError, TypeError):
                        pass  # A corrupt receipt must not replace the original failure.
                try:
                    staging.remember(stage, journal.data["items"][index - 1].get("stage_identity") if current else None)
                except (OSError, ValueError, RuntimeError) as error:
                    status("Files kept", f"Temporary files could not be recorded for cleanup: {error}", "33", stream=sys.stderr)
                status("Files kept", str(stage), "33", stream=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
