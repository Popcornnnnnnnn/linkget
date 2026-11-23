"""Automatic daily release check and `linkget upgrade` command (stdlib only)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

if __package__:
    from .version import VERSION
else:
    from version import VERSION

REPO = "Popcornnnnnnnn/linkget"
LATEST_RELEASE_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECK_INTERVAL_SECONDS = 86400
ENV_DISABLE = "LINKGET_NO_UPDATE_CHECK"

_VERSION_RE = re.compile(
    r"^v?(?P<core>\d+(?:\.\d+)*)(?:[.\-_]?(?P<pre>(?:dev|a|alpha|b|beta|rc)\d*.*))?$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ReleaseInfo:
    tag_name: str
    version: str
    release_url: str
    wheel_url: str | None = None


def default_cache_path(env: dict[str, str] | None = None) -> Path:
    env_map = os.environ if env is None else env
    if env_map.get("LINKGET_HOME"):
        return Path(env_map["LINKGET_HOME"]).expanduser() / "update-check.json"
    base = (
        Path.home() / "Library/Application Support"
        if sys.platform == "darwin"
        else Path(env_map.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    )
    return base / "linkget" / "update-check.json"


def normalize_version(version: str) -> str:
    return version.strip().lstrip("vV")


def _parse_version(version: str) -> tuple[tuple[int, ...], int, tuple[int, str]] | None:
    cleaned = version.strip()
    match = _VERSION_RE.match(cleaned)
    if not match:
        return None
    core = tuple(int(part) for part in match.group("core").split("."))
    pre = match.group("pre")
    if not pre:
        return (core, 1, (0, ""))
    num_match = re.search(r"(\d+)", pre)
    pre_num = int(num_match.group(1)) if num_match else 0
    return (core, 0, (pre_num, pre.lower()))


def is_newer_version(remote: str, current: str) -> bool:
    parsed_remote = _parse_version(remote)
    parsed_current = _parse_version(current)
    if parsed_remote is None or parsed_current is None:
        return False
    r_core, r_final, r_pre = parsed_remote
    c_core, c_final, c_pre = parsed_current
    width = max(len(r_core), len(c_core))
    r_core_padded = r_core + (0,) * (width - len(r_core))
    c_core_padded = c_core + (0,) * (width - len(c_core))
    return (r_core_padded, r_final, r_pre) > (c_core_padded, c_final, c_pre)


def _is_truthy(value: str | None) -> bool:
    return bool(value and value.strip().lower() in {"1", "true", "yes", "on"})


def should_check_updates(
    tokens: list[str],
    *,
    env: dict[str, str] | None = None,
    stderr=None,
) -> bool:
    env_map = os.environ if env is None else env
    if _is_truthy(env_map.get(ENV_DISABLE)):
        return False
    if env_map.get("CI"):
        return False
    err_stream = sys.stderr if stderr is None else stderr
    if not (hasattr(err_stream, "isatty") and err_stream.isatty()):
        return False
    token_set = set(tokens)
    if token_set & {"--json", "--non-interactive", "-h", "--help", "--version"}:
        return False
    if tokens:
        cmd = tokens[0]
        if cmd in {"upgrade", "version", "help"}:
            return False
        if len(tokens) >= 2 and cmd == "read" and tokens[1] == "show":
            return False
    return True


def read_cache(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_cache(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def fetch_latest_release(
    *,
    timeout: float = 10.0,
    url: str = LATEST_RELEASE_URL,
) -> ReleaseInfo:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": f"linkget/{VERSION}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = json.loads(response.read().decode("utf-8"))
    if data.get("draft") or data.get("prerelease"):
        raise ValueError("Latest release is marked as draft or prerelease.")
    tag_name = str(data.get("tag_name") or "").strip()
    if not tag_name:
        raise ValueError("GitHub release payload missing tag_name.")
    wheel_url = None
    for asset in data.get("assets") or []:
        name = str(asset.get("name") or "")
        if name.endswith(".whl"):
            wheel_url = asset.get("browser_download_url")
            break
    release_url = str(data.get("html_url") or f"https://github.com/{REPO}/releases/latest")
    return ReleaseInfo(
        tag_name=tag_name,
        version=normalize_version(tag_name),
        release_url=release_url,
        wheel_url=wheel_url,
    )


def refresh_cache(
    cache_path: Path | None = None,
    *,
    timeout: float = 5.0,
    fetch_fn=None,
    now: float | None = None,
) -> dict | None:
    target = cache_path or default_cache_path()
    fetcher = fetch_latest_release if fetch_fn is None else fetch_fn
    info = fetcher(timeout=timeout)
    payload = {
        "checked_at": time.time() if now is None else float(now),
        "latest_version": info.version,
        "tag_name": info.tag_name,
        "release_url": info.release_url,
        "wheel_url": info.wheel_url,
    }
    write_cache(target, payload)
    return payload


def _spawn_background_refresh(cache_path: Path) -> None:
    if "unittest" in sys.modules or "pytest" in sys.modules:
        return
    try:
        subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--refresh-cache", "--cache-path", str(cache_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def maybe_notify_update(
    current_version: str,
    tokens: list[str],
    *,
    cache_path: Path | None = None,
    env: dict[str, str] | None = None,
    stderr=None,
    spawn_refresh=None,
    now: float | None = None,
) -> str | None:
    err_stream = sys.stderr if stderr is None else stderr
    if not should_check_updates(tokens, env=env, stderr=err_stream):
        return None
    target = cache_path or default_cache_path(env)
    current_time = time.time() if now is None else float(now)
    cached = read_cache(target)

    notice = None
    stale = True
    if cached:
        checked_at = cached.get("checked_at")
        if isinstance(checked_at, (int, float)) and (current_time - float(checked_at)) < CHECK_INTERVAL_SECONDS:
            stale = False
        latest = str(cached.get("latest_version") or "").strip()
        if latest and is_newer_version(latest, current_version):
            notice = (
                f"\nUpdate available: linkget v{latest} "
                f"(current v{normalize_version(current_version)}). "
                "Run: linkget upgrade\n"
            )
            try:
                err_stream.write(notice)
                err_stream.flush()
            except OSError:
                pass

    if stale:
        spawner = _spawn_background_refresh if spawn_refresh is None else spawn_refresh
        spawner(target)
    return notice


def detect_install_method(file_path: str | Path | None = None) -> str:
    resolved = str(Path(file_path or __file__).resolve())
    if (
        "/Cellar/linkget/" in resolved
        or "/opt/homebrew/" in resolved
        or "/usr/local/Cellar/linkget/" in resolved
    ):
        return "homebrew"
    if "/uv/tools/" in resolved:
        return "uv"
    if "/pipx/venvs/" in resolved:
        return "pipx"
    return "source"


def recommended_upgrade_command(install_method: str, wheel_url: str | None = None) -> str:
    if install_method == "homebrew":
        return "brew update && brew upgrade Popcornnnnnnnn/tap/linkget"
    if install_method == "uv" and wheel_url:
        return f"uv tool install --force {wheel_url}"
    if install_method == "pipx" and wheel_url:
        return f"pipx install --force {wheel_url}"
    return "git pull && pip install -e ."


def run_upgrade(
    argv: list[str] | None = None,
    *,
    current_version: str = VERSION,
    cache_path: Path | None = None,
    fetch_fn=None,
    runner=None,
    stdout=None,
    stderr=None,
    file_path: str | Path | None = None,
) -> int:
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr

    parser = argparse.ArgumentParser(
        prog="linkget upgrade",
        description="Check for new linkget releases and upgrade the installed tool.",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true", help="Show this help message and exit")
    parser.add_argument("--check", action="store_true", help="Check for updates without installing")
    parser.add_argument("--json", action="store_true", help="Print update check result as JSON")
    args = parser.parse_args([] if argv is None else argv)

    if args.help:
        parser.print_help(out)
        return 0

    try:
        info = refresh_cache(
            cache_path=cache_path,
            timeout=10.0,
            fetch_fn=fetch_fn,
        )
    except (OSError, urllib.error.URLError, ValueError, json.JSONDecodeError) as exc:
        if args.json:
            out.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        else:
            err.write(f"Could not check for updates: {exc}\n")
        return 1

    latest = info["latest_version"]
    release_url = info["release_url"]
    wheel_url = info.get("wheel_url")
    method = detect_install_method(file_path)
    available = is_newer_version(latest, current_version)
    cmd_hint = recommended_upgrade_command(method, wheel_url)

    payload = {
        "current": normalize_version(current_version),
        "latest": latest,
        "update_available": available,
        "install_method": method,
        "release_url": release_url,
        "upgrade_command": cmd_hint,
    }

    if args.json:
        out.write(json.dumps(payload) + "\n")
        return 0

    if not available:
        out.write(f"linkget {normalize_version(current_version)} is already up to date.\n")
        return 0

    if args.check:
        out.write(
            f"Update available: linkget v{latest} "
            f"(current v{normalize_version(current_version)}).\n"
            f"Install method: {method}\n"
            f"Upgrade with: {cmd_hint}\n"
            f"Release: {release_url}\n"
        )
        return 0

    run_fn = subprocess.run if runner is None else runner
    if method == "homebrew":
        out.write(f"Upgrading linkget to v{latest} via Homebrew...\n")
        for cmd in (["brew", "update"], ["brew", "upgrade", f"Popcornnnnnnnn/tap/linkget"]):
            res = run_fn(cmd, check=False)
            if res.returncode != 0:
                err.write(f"Command failed ({res.returncode}): {' '.join(cmd)}\n")
                return int(res.returncode)
        return 0

    if method == "uv" and wheel_url:
        out.write(f"Upgrading linkget to v{latest} via uv...\n")
        res = run_fn(["uv", "tool", "install", "--force", wheel_url], check=False)
        return int(res.returncode)

    if method == "pipx" and wheel_url:
        out.write(f"Upgrading linkget to v{latest} via pipx...\n")
        res = run_fn(["pipx", "install", "--force", wheel_url], check=False)
        return int(res.returncode)

    out.write(
        f"Update available: linkget v{latest} (current v{normalize_version(current_version)}).\n"
        f"Installed from source. Update with:\n  {cmd_hint}\n"
        f"Or download from: {release_url}\n"
    )
    return 0


def _cli_entry() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--refresh-cache", action="store_true")
    parser.add_argument("--cache-path", type=Path, default=None)
    args, _ = parser.parse_known_args()
    if args.refresh_cache:
        try:
            refresh_cache(cache_path=args.cache_path, timeout=5.0)
        except Exception:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_entry())
