# Trial validation scope

Automated tests use temporary directories, synthetic sessions and mocked Photos calls. They never read personal browser stores or open Photos.

Covered behaviors include:

- first-use connection, skip, denied access, explicit browser/profile selection, and connecting a site before a download;
- missing/expired/rejected sessions versus network errors, parallel checks, logout and explicit reconnection;
- public downloads, quality restrictions, using an existing Bilibili login before format selection, and media routing;
- Xiaohongshu complete image sets, video selection, preserved share tokens, invalid media and interrupted downloads;
- Weibo mixed media, YouTube video/Shorts routing, runtime guidance, quality display and playlist/live rejection;
- new-site session classification, alternate YouTube login cookies and excluding Google account cookies;
- persistent Photos originals, content-based filenames, repeated content, same-name different content, and import/copy failures;
- folder collisions and duplicate handling, progress cleanup, bare `--folder`, and invalid command guidance;
- isolated source/wheel installation and Homebrew formula smoke tests.

Local live checks include Bilibili downloads, a public Weibo video, and a public YouTube video with merged audio. These checks are samples, not a guarantee for every post or network. A current xhslink.cn share was downloaded anonymously as three JPEG images through its mobile share page. Xiaohongshu video selection remains covered by simulated responses and needs live user acceptance. The new sites' signed-in endpoint checks also need user acceptance; tests do not read personal browser sessions.

## Manual acceptance needed

Real OS permission prompts, a brand-new Mac installation, browser profile combinations, and actual Photos imports vary by machine. The automated suite does not prove these UI flows. Photos mode keeps the originals specifically so a referenced library or a failed import does not lose the source media. Check the imported item, audio, dimensions and duplicate handling before relying on the trial for an archive.

## Reproduce

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -q
uv build
```

GitHub Actions runs source and installed-wheel tests on Linux/Python 3.10 and macOS/Python 3.14, and compiles the AppleScript on the isolated macOS runner. The tap's `brew test linkget` does not request browser or Photos permissions.
