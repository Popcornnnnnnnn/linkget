"""Check browser sessions without treating readable cookies as authenticated."""

from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
import json
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPCookieProcessor, Request, build_opener


ENDPOINTS = {
    "instagram.com": "https://www.instagram.com/api/v1/accounts/edit/web_form_data/",
    "x.com": "https://x.com/i/api/graphql/_8ClT24oZ8tpylf_OSuNdg/Viewer?" + urlencode({
        "variables": json.dumps({"withCommunitiesMemberships": False}),
        "features": json.dumps({
            "subscriptions_upsells_api_enabled": False,
            "profile_label_improvements_pcf_label_in_post_enabled": True,
            "responsive_web_profile_redirect_enabled": False,
            "rweb_tipjar_consumption_enabled": False,
            "verified_phone_label_enabled": False,
            "creator_subscriptions_tweet_preview_api_enabled": True,
            "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
            "responsive_web_graphql_timeline_navigation_enabled": True,
        }),
        "fieldToggles": json.dumps({"isDelegate": False, "withAuxiliaryUserLabels": False}),
    }),
    "bilibili.com": "https://api.bilibili.com/x/web-interface/nav",
    "tiktok.com": "https://www.tiktok.com/passport/web/account/info/?aid=1988&app_name=tiktok_web&device_platform=web_pc",
    "douyin.com": "https://www.douyin.com/passport/account/info/v2/?aid=6383",
}
LOGIN_URLS = {
    "instagram.com": "https://www.instagram.com/accounts/login/",
    "x.com": "https://x.com/i/flow/login",
    "bilibili.com": "https://passport.bilibili.com/login",
    "tiktok.com": "https://www.tiktok.com/login",
    "douyin.com": "https://www.douyin.com/",
}
AUTH_COOKIE = {"instagram.com": "sessionid", "x.com": "auth_token", "bilibili.com": "SESSDATA", "tiktok.com": "sessionid", "douyin.com": "sessionid"}


@dataclass(frozen=True)
class Session:
    state: str
    detail: str


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def classify_response(domain, code, data):
    """Only explicit authenticated identity or auth rejection is decisive."""
    if code == 429:
        return Session("unverified", "Rate limited")
    if code >= 500:
        return Session("unverified", "Website unavailable")
    if not isinstance(data, dict):
        return Session("unverified", f"Website login check returned HTTP {code} without account data")
    if domain == "instagram.com":
        user = data.get("form_data")
        if code == 200 and data.get("status") == "ok" and isinstance(user, dict) and user.get("username"):
            return Session("valid", "Valid")
        if data.get("message") in {"login_required", "login_again", "session_invalid"}:
            return Session("invalid", "Session rejected")
        if data.get("message") in {"challenge_required", "checkpoint_required", "consent_required"} or data.get("checkpoint_url"):
            return Session("attention", "Verification required")
    elif domain == "x.com":
        account = data.get("data")
        for field in ("viewer", "user_results", "result"):
            account = account.get(field) if isinstance(account, dict) else None
        if (code == 200 and isinstance(account, dict) and account.get("__typename") == "User"
                and account.get("rest_id") and not data.get("errors")):
            return Session("valid", "Valid")
        errors = data.get("errors", [])
        codes = {item.get("code") for item in errors if isinstance(item, dict)} if isinstance(errors, list) else set()
        if codes & {32, 89}:
            return Session("invalid", "Session rejected")
        if codes & {64, 326}:
            return Session("attention", "Account needs attention")
    elif domain == "bilibili.com":
        account = data.get("data")
        account = account if isinstance(account, dict) else {}
        if code == 200 and data.get("code") == 0 and account.get("isLogin") is True and account.get("mid"):
            return Session("valid", "Valid")
        if data.get("code") == -101 or (data.get("code") == 0 and account.get("isLogin") is False):
            return Session("invalid", "Session rejected")
    elif domain in {"tiktok.com", "douyin.com"}:
        account = data.get("data")
        account = account if isinstance(account, dict) else {}
        if code == 200 and data.get("message") == "success" and (account.get("user_id") or account.get("user_id_str")):
            return Session("valid", "Valid")
        if code == 200 and data.get("message") == "error" and account.get("error_code") == 13:
            return Session("invalid", "Session rejected")
    return Session("unverified", "Website check inconclusive")


def check_session(jar, domain):
    candidates = [cookie for cookie in jar if cookie.name == AUTH_COOKIE[domain] and cookie.value]
    if not candidates:
        return Session("missing", "Not signed in")
    if all(cookie.is_expired() for cookie in candidates):
        return Session("invalid", "Expired")
    values = {cookie.name: cookie.value for cookie in jar if not cookie.is_expired()}
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://www." + domain + "/" if domain != "x.com" else "https://x.com/",
    }
    if domain == "instagram.com":
        headers.update({"X-IG-App-ID": "936619743392459", "X-Requested-With": "XMLHttpRequest", "X-CSRFToken": values.get("csrftoken", "")})
    elif domain == "x.com":
        if not values.get("ct0"):
            return Session("unverified", "CSRF cookie missing")
        # Public X web-client identifier, also used by gallery-dl; not a user token.
        headers.update({"authorization": "Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA",
                        "x-csrf-token": values["ct0"], "x-twitter-auth-type": "OAuth2Session", "x-twitter-active-user": "yes"})
    opener = build_opener(HTTPCookieProcessor(jar), NoRedirect())
    try:
        try:
            response = opener.open(Request(ENDPOINTS[domain], headers=headers), timeout=8)
        except HTTPError as error:
            response = error
        with response:
            code = response.code
            if domain == "instagram.com" and code in {301, 302, 303, 307, 308}:
                destination = urlsplit(response.headers.get("Location", ""))
                if (destination.hostname in {None, "www.instagram.com", "instagram.com"}
                        and destination.path.rstrip("/") == "/accounts/login"):
                    return Session("invalid", "Session rejected")
            try:
                data = json.loads(response.read(1_048_576))
            except (ValueError, UnicodeError):
                data = None
        return classify_response(domain, code, data)
    except (URLError, OSError, TimeoutError):
        return Session("unverified", "Network check failed")


def export_browser(gallery_dl, browser_spec, cookie_path):
    """Read a browser once. The caller owns the private temporary directory."""
    cookie_path.write_text("# Netscape HTTP Cookie File\n")
    cookie_path.chmod(0o600)
    try:
        result = subprocess.run([gallery_dl, "--config-ignore", "--cookies-from-browser", browser_spec,
                                 "--cookies-export", str(cookie_path)], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return None, Session("unavailable", "Browser access timed out; confirm the system permission and retry")
    except OSError:
        return None, Session("unavailable", "Could not read browser data")
    if result.returncode:
        return None, Session("unavailable", "Browser access denied or profile unavailable")
    cookie_path.chmod(0o600)
    jar = MozillaCookieJar(str(cookie_path))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
    except (OSError, ValueError):
        return None, Session("unavailable", "Could not read browser data")
    for cookie in jar:
        if cookie.expires == 0:
            cookie.expires = None
    report = (result.stdout + result.stderr).lower()
    if any(marker in report for marker in ("could not be decrypted", "unable to decrypt", "failed to decrypt")):
        return jar, Session("unavailable", "Could not decrypt browser data; check Keychain permission")
    return jar, None


def site_cookies(jar, domain, path):
    filtered = MozillaCookieJar(str(path))
    for cookie in jar:
        host = cookie.domain.lstrip(".")
        if host == domain or host.endswith("." + domain):
            filtered.set_cookie(cookie)
    return filtered


def prepare_session(gallery_dl, browser_spec, cookie_path, domain):
    """Export once to a private temporary directory; downloader reuses this snapshot."""
    jar, error = export_browser(gallery_dl, browser_spec, cookie_path)
    if jar is None:
        return error
    jar = site_cookies(jar, domain, cookie_path)
    jar.save(ignore_discard=True, ignore_expires=True)
    if error and not any(c.name == AUTH_COOKIE[domain] and c.value for c in jar):
        return error
    return check_session(jar, domain)
