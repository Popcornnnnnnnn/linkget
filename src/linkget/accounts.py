"""Local per-site browser authorizations. No passwords or cookie values in output."""

from http.cookiejar import MozillaCookieJar
import json
import os
import plistlib
from pathlib import Path
import shutil
import sys
import tempfile

if __package__:
    from .session import AUTH_COOKIE, Session, check_session, prepare_session, export_browser, site_cookies
else:
    from session import AUTH_COOKIE, Session, check_session, prepare_session, export_browser, site_cookies

SITES = {"instagram": "instagram.com", "x": "x.com", "bilibili": "bilibili.com",
         "tiktok": "tiktok.com", "douyin": "douyin.com"}


def directory():
    if os.environ.get("LINKGET_HOME"):
        return Path(os.environ["LINKGET_HOME"]).expanduser() / "sessions"
    base = Path.home() / "Library/Application Support" if sys.platform == "darwin" else Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "linkget" / "sessions"


def preferences():
    path = directory().parent / "preferences.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("Could not read linkget preferences")
    return data


def set_preferences(**changes):
    data = preferences()
    data.update(changes)
    path = directory().parent / "preferences.json"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            json.dump(data, output)
            output.close()
            temporary.chmod(0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def default_browser():
    if sys.platform != "darwin":
        return None
    path = Path.home() / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"
    try:
        with path.open("rb") as source:
            handlers = plistlib.load(source).get("LSHandlers", [])
        names = {"com.google.chrome": "chrome", "org.mozilla.firefox": "firefox",
                 "com.microsoft.edgemac": "edge", "com.apple.safari": "safari"}
        for scheme in ("https", "http"):
            for item in handlers:
                if item.get("LSHandlerURLScheme") == scheme:
                    return names.get(item.get("LSHandlerRoleAll", "").lower())
    except (OSError, ValueError, plistlib.InvalidFileException):
        pass
    return None


def remember_source(domain, specification):
    settings = preferences()
    sources = settings.get("sources", {})
    sources[domain] = specification
    blocked = [item for item in settings.get("blocked", []) if item != domain]
    set_preferences(sources=sources, blocked=blocked)


def connect_browser(tool, browser, profile=None):
    """One browser export, only verified supported-site sessions are retained."""
    specification = browser + (":" + str(Path(profile).expanduser()) if profile else "")
    results = {}
    with tempfile.TemporaryDirectory(prefix="linkget-connect-") as temp:
        root = Path(temp)
        jar, error = export_browser(tool, specification, root / "browser.txt")
        if jar is None:
            return {domain: error for domain in SITES.values()}
        for cookie in list(jar):
            host = cookie.domain.lstrip(".")
            if not any(host == domain or host.endswith("." + domain) for domain in SITES.values()):
                jar.clear(cookie.domain, cookie.path, cookie.name)
        jar.save(ignore_discard=True, ignore_expires=True)
        for domain in SITES.values():
            path = root / (domain + ".txt")
            filtered = site_cookies(jar, domain, path)
            state = check_session(filtered, domain)
            if state.state == "missing" and error:
                state = error
            if state.state == "valid":
                filtered.save(ignore_discard=True, ignore_expires=True)
                save(path, domain)
                remember_source(domain, browser + "/" + domain +
                                (":" + str(Path(profile).expanduser()) if profile else ""))
            results[domain] = state
    return results


def path_for(domain):
    if domain not in SITES.values():
        raise ValueError("Unsupported login site")
    return directory() / (domain + ".txt")


def read_session(path, domain):
    jar = MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    for cookie in list(jar):
        if cookie.expires == 0:
            cookie.expires = None
    candidates = [c for c in jar if c.name == AUTH_COOKIE[domain] and c.value]
    if not candidates:
        return jar, Session("missing", "No saved login")
    if all(c.is_expired() for c in candidates):
        return jar, Session("invalid", "Expired")
    return jar, Session("unverified", "Saved; validity not confirmed")


def save(source, domain):
    """Only store an actual login cookie, not an anonymous visitor cookie."""
    _, state = read_session(source, domain)
    if state.state != "unverified":
        return state
    target = path_for(domain)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".login-", delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(source.read_bytes())
            output.close()
            temporary.chmod(0o600)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return state


def import_browser(tool, specification, domain):
    with tempfile.TemporaryDirectory(prefix="linkget-login-") as temp:
        source = Path(temp) / "cookies.txt"
        state = prepare_session(tool, specification, source, domain)
        if state.state != "valid":
            return state
        saved = save(source, domain)
        if saved.state == "unverified":
            remember_source(domain, specification)
            return state
        return saved


def available_browsers():
    """Discover local browser data directories without opening cookie stores."""
    home = Path.home()
    if sys.platform == "darwin":
        base = home / "Library/Application Support"
        roots = {"chrome": base / "Google/Chrome", "firefox": base / "Firefox",
                 "edge": base / "Microsoft Edge", "safari": home / "Library/Safari"}
    elif sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", home))
        roaming = Path(os.environ.get("APPDATA", home))
        roots = {"chrome": local / "Google/Chrome/User Data", "firefox": roaming / "Mozilla/Firefox",
                 "edge": local / "Microsoft/Edge/User Data"}
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        roots = {"chrome": base / "google-chrome", "firefox": home / ".mozilla/firefox",
                 "edge": base / "microsoft-edge"}
    browsers = []
    for name, root in roots.items():
        try:
            if root.is_dir() or (name == "safari" and ((home / "Library/Containers/com.apple.Safari").is_dir()
                                                      or Path("/Applications/Safari.app").is_dir())):
                browsers.append(name)
        except OSError:
            # Existing but protected browser data still deserves a permissions diagnosis.
            browsers.append(name)
    return browsers


def import_auto(tool, domain):
    """Refresh only the browser the user previously connected; never cascade prompts."""
    settings = preferences()
    if domain in settings.get("blocked", []):
        return Session("missing", "Browser access disabled for this site"), None
    specification = settings.get("sources", {}).get(domain)
    browser = settings.get("browser")
    if not specification and browser:
        specification = browser + "/" + domain
        if settings.get("profile"):
            specification += ":" + settings["profile"]
    if not specification:
        return Session("missing", "No browser connected"), None
    browser = specification.split("/", 1)[0]
    return import_browser(tool, specification, domain), browser


def copy_saved(domain, target):
    source = path_for(domain)
    if not source.is_file():
        return False
    try:
        _, state = read_session(source, domain)
        if state.state != "unverified":
            return False
        shutil.copyfile(source, target)
        target.chmod(0o600)
        return True
    except (OSError, ValueError):
        return False


def status(domain):
    path = path_for(domain)
    if not path.is_file():
        return Session("missing", "Not saved")
    try:
        jar, state = read_session(path, domain)
        return check_session(jar, domain) if state.state == "unverified" else state
    except (OSError, ValueError):
        return Session("unavailable", "Saved authorization could not be read")


def logout(site):
    domains = SITES.values() if site == "all" else [SITES[site]]
    settings = preferences()
    blocked = set(settings.get("blocked", []))
    for domain in domains:
        path_for(domain).unlink(missing_ok=True)
        blocked.add(domain)
    set_preferences(blocked=sorted(blocked))
