# Trial validation scope

Automated tests use temporary directories, synthetic sessions and mocked Photos calls. They never read personal browser stores or open Photos.

Covered behaviors include:

- first-use connection, skip, denied access, explicit browser/profile selection, and connecting a site before a download;
- missing/expired/rejected sessions versus network errors, parallel checks, logout and explicit reconnection;
- automatic refresh for all eight channels, grouping by browser/profile, explicit browser overrides, failed-refresh snapshot preservation, and no refresh for unknown states or noninteractive checks;
- malformed account payloads, interrupted HTTP responses, saving cookies renewed during verification, and rotated-cookie warnings after anonymous video metadata fallback;
- public downloads, quality restrictions, using an existing Bilibili login before format selection, and media routing;
- Xiaohongshu complete image sets, original video selection, HEIC/HEIF detection, per-item display fallback with notices, preserved share tokens, invalid media and interrupted downloads;
- Weibo source comparison, refusal of any width/height reduction even when watermark comparison passes, retained clean/uncertain originals, encoding noise, distributed image changes, marked previews of clean originals, missing/timed-out decoders, invalid/truncated alternatives and metadata cleanup; Douyin clean-source rejection with native-media fallback;
- Weibo mixed media, YouTube video/Shorts routing, runtime guidance, quality display and playlist/live rejection;
- new-site session classification, alternate YouTube login cookies and excluding Google account cookies;
- persistent Photos originals, content-based filenames, repeated content, same-name different content, and import/copy failures;
- folder collisions and duplicate handling, progress cleanup, bare `--folder`, and invalid command guidance;
- saved destinations, dynamic current-directory defaults, fixed relative-path resolution, per-run overrides, and retaining account settings;
- isolated source/wheel installation and Homebrew formula smoke tests.

Local live checks include actual media inspection for all eight channels; see [watermark findings and limitations](WATERMARKS.md). These checks are samples, not a guarantee for every post or network. Xiaohongshu original images and video were downloaded and visually sampled. On 2026-09-29, opt-in live checks verified all eight sites with an authorized Chrome session; an invalid Weibo cookie was rejected. This does not verify other browsers or all account configurations. Ordinary automated tests do not read personal browser sessions.

## Manual acceptance needed

Real OS permission prompts, a brand-new Mac installation, browser profile combinations, and actual Photos imports vary by machine. The automated suite does not prove these UI flows. Photos mode keeps the originals specifically so a referenced library or a failed import does not lose the source media. Check the imported item, audio, dimensions and duplicate handling before relying on the trial for an archive.

## Live session acceptance before release

Mocked responses do not prove that a site's endpoint exists or still returns the expected viewer data. When adding or changing a channel's authentication, run the opt-in live check against a browser already signed in to that site:

```sh
PYTHONPATH=src python3 scripts/check_sessions.py --browser chrome --sites weibo
# Check all eight sites with one browser export:
PYTHONPATH=src python3 scripts/check_sessions.py --browser chrome
```

This reads real browser cookies and can request OS permission. Use it only with the browser owner's authorization. It prints states without account identifiers or credentials, removes its temporary cookie snapshot on exit, and leaves saved accounts unchanged. A nonzero exit means at least one selected site was not verified, including sites not signed in. It is deliberately separate from unattended CI.

Browser extraction and website verification are separate acceptance layers: verify extraction/profile/OS permissions per supported browser; verify each website's authenticated identity against the shared cookie interface. A channel parser fix does not require repeating every browser/site combination. Browser-specific real access remains unverified until tested on that browser. For channel changes, also test an anonymous or invalid-cookie request, and add a sanitized regression case based on the observed response. Record actual live results separately from synthetic test results.

Weibo regressions cover the observed homepage `window.$CONFIG` viewer identity, feature-only config responses, visitor/login redirects, and refusal of profile/album URLs before invoking a downloader. Short links that resolve to a profile must also be refused.

For 0.2.0b10, live checks again verified all eight channels from Chrome. An isolated temporary account store containing an expired synthetic YouTube cookie then ran the real automatic-refresh path: Chrome export, website verification, saved-session replacement and a second live verification all passed. Temporary exports and test account stores were removed. No real user account was expired or logged out for this test.

## Reproduce

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -q
uv build
```

GitHub Actions runs source and installed-wheel tests on Linux/Python 3.10 and macOS/Python 3.14, and compiles the AppleScript on the isolated macOS runner. The tap's `brew test linkget` does not request browser or Photos permissions.
