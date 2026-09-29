"""Opt-in live acceptance: read one browser and verify selected website sessions.

Run with PYTHONPATH=src. This reads real cookies and can request OS permission;
it never opens a browser, downloads media, or changes saved linkget accounts.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import tempfile

from linkget import accounts, session


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", required=True, choices=["chrome", "firefox", "edge", "safari"])
    parser.add_argument("--profile", help="Browser profile name or path")
    parser.add_argument("--sites", nargs="+", choices=list(accounts.SITES), default=list(accounts.SITES))
    args = parser.parse_args()
    tool = shutil.which("gallery-dl")
    if not tool:
        parser.error("gallery-dl is required")
    specification = args.browser + (":" + args.profile if args.profile else "")
    with tempfile.TemporaryDirectory(prefix="linkget-live-check-") as tmp:
        root = Path(tmp)
        jar, error = session.export_browser(tool, specification, root / "cookies.txt")
        if jar is None:
            print(error.detail)
            return 1
        if error:
            print("Browser read: " + error.detail)
        sites = list(dict.fromkeys(args.sites))
        def check(site):
            domain = accounts.SITES[site]
            scoped = session.site_cookies(jar, domain, root / (site + ".txt"))
            return session.check_session(scoped, domain)
        with ThreadPoolExecutor(max_workers=len(sites)) as executor:
            results = list(executor.map(check, sites))
        for site, state in zip(sites, results):
            print(f"{site:<12} {state.state:<12} {state.detail}")
        return 0 if all(state.state == "valid" for state in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
