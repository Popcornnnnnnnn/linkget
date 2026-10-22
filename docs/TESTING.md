# Trial validation scope

Automated tests use temporary directories, synthetic sessions and mocked Photos calls. They never read personal browser stores or open Photos.

Covered behaviors include:

- Same-item retry cleanup after repeated partial failures, complete-set recovery and verified Photos originals; cancelled/failed saves, corrupt destination copies, edited/added files, symbolic links, replaced/outside roots, another task's sources and nested destinations retain their files;
- Instagram visual-source counts exclude optional standalone music; Reel and mixed-gallery tests use the real gallery-dl extractor, and missing source/downloaded media still fail completeness checks;
- first-use connection, skip, denied access, explicit browser/profile selection, and connecting a site before a download;
- missing/expired/rejected sessions versus network errors, parallel checks, logout and explicit reconnection;
- automatic refresh for all eight channels, grouping by browser/profile, explicit browser overrides, failed-refresh snapshot preservation, and no refresh for unknown states or noninteractive checks;
- malformed account payloads, interrupted HTTP responses, saving cookies renewed during verification, and rotated-cookie warnings after anonymous video metadata fallback;
- public downloads, quality restrictions, using an existing Bilibili login before format selection, and media routing;
- Xiaohongshu complete image sets, original video selection, HEIC/HEIF detection, per-item display fallback with notices, preserved share tokens, invalid media and interrupted downloads;
- Weibo source comparison, refusal of any width/height reduction even when watermark comparison passes, retained clean/uncertain originals, encoding noise, distributed image changes, marked previews of clean originals, missing/timed-out decoders, invalid/truncated alternatives and metadata cleanup; Douyin clean-source rejection with native-media fallback;
- Weibo mixed media, YouTube video/Shorts routing, runtime guidance, quality display and playlist/live rejection;
- new-site session classification, alternate YouTube login cookies and excluding Google account cookies;
- Douyin share-page initialization when the requested post's SSR shell precedes its anonymous visitor cookie; bounded failure for persistent shells, website checks and another post;
- Douyin image-scoped motion downloads and explicit still/video pairs, exclusion of image-post background music, actual fallback filenames, interrupted or missing motion, and refusal of stripped public metadata in `--live-photos` mode; browser fixtures cover current-post metadata, lazy motion, missing or ambiguous metadata, and extensionless/blob background audio;
- staged Photos originals, content-based filenames, repeated content, same-name different content, verified-copy cleanup, referenced/mismatched/symlink copy rejection, and import/copy failures;
- folder collisions and duplicate handling, progress cleanup, bare `--folder`, and invalid command guidance;
- batch input numbering, simultaneous download start for every link, independent failure continuation, partial-file preservation, invalid input, multiline sharing text, one-time setup, duplicate handling, cancellation, serialized Photos saves and login prompts on the calling thread without browser reads;
- compact batch terminal rows in original order, Chinese title clipping by display width, width/height limits, resize cleanup, queued/cancelled items, serialized-import waiting, prompt suspension, full failure/recovery notices, retry numbering, `NO_COLOR` and plain `TERM=dumb`/redirected output;
- exposed gallery-count completeness, exact per-item Live Photo pairs, missing motion, invalid format refusal, opt-in token-free reports and inconclusive quality comparisons;
- Live Photo shared collision suffixes, repeated-export deduplication, partial-copy cleanup, retained-component races/content mismatches, persistent raw/paired resources and Photos file-count bookkeeping without accessing Photos;
- saved destinations, dynamic current-directory defaults, fixed relative-path resolution, per-run overrides, and retaining account settings;
- isolated source/wheel installation and Homebrew formula smoke tests.

The current [13-channel media corpus and acceptance protocol](MEDIA_VALIDATION.md) separates anonymous availability, quality references and visual watermark findings. Historical [eight-channel watermark findings](WATERMARKS.md) include authenticated samples and do not prove anonymous access. These checks are samples, not a guarantee for every post or network. On 2026-09-29, opt-in live checks verified eight sites with an authorized Chrome session; an invalid Weibo cookie was rejected. This does not verify other browsers or all account configurations. Ordinary automated tests do not read personal browser sessions.

## Manual acceptance needed

### Compact batch terminal panel (2026-10-13)

Source/Python 3.13 ran 313 tests successfully with five optional tests skipped. The locally patched Homebrew installation passed all 72 batch, terminal-panel, CLI, recovery and runtime tests. A real PTY smoke check using the Homebrew Python 3.14 runtime saved 41 generated JPEGs from four simulated posts through real ffprobe and folder-copy verification; every saved file matched its source bytes. Panel tests cover Chinese titles, terminal width/height limits and resize cleanup, serialized-import waiting, failures, queued-item cancellation, coordinator Ctrl-C, prompt suspension and original retry numbers. Evidence and the four previous installed modules are under `.local/media-validation/compact-panel/`.

The local command remains version `0.2.1.dev20` with this presentation patch; no release was published. Applying the full source test suite to the installed package also found seven pre-existing reading-module errors. The same seven errors reproduced in 41 reading tests against the pre-patch installed baseline; `read_asr.py` and `toutiao.py` were not changed in this round. Follow-up is tracked in [Issue #6](https://github.com/Popcornnnnnnnn/linkget/issues/6). Ghostty screenshot inspection was unavailable through the UI tool. Actual website transfers and real Photos import were not run for this presentation change.

### Installed reading-module sync (2026-10-13)

The two installed reading modules were still at the revisions before `562b7ce` (Toutiao transfer bounds) and `40fb792` (ASR boundary clipping and checked checkpoint recovery). Only `toutiao.py` and `read_asr.py` were backed up and synchronized from the committed source. All installed package files now match the local source, including the compact batch panel; the local version remains `0.2.1.dev20` and nothing was published.

The full suite against the actual Homebrew installation and its Python 3.14 runtime passed 313 tests with five optional skips, eliminating the seven earlier reading errors. The installed `linkget read --foreground` command transcribed 8.404 seconds of locally generated English speech with the existing Whisper model. Reprocessing the retained audio reused an unchanged chunk checkpoint: only ffprobe ran, with no new ffmpeg or Whisper invocation. The completed read also returned safely from command-line retry. Backups, file fingerprints, test logs and generated-content evidence are under `.local/media-validation/reading-sync/`. [Issue #6](https://github.com/Popcornnnnnnnn/linkget/issues/6) is resolved. This is local installation/recognition validation; a fresh live Toutiao website transfer was not run.

### Batch elapsed-time correction (2026-10-13)

The initial compact panel reset the row timer on every phase, then switched to the single-download total at completion. A controlled-clock reproduction showed Downloading 20s → Importing 0s → Done 30.0s. The row now uses one cumulative clock per attempt, includes waiting to save, excludes time queued before starting, and freezes on completion, failure or cancellation. The rightmost column always keeps its elapsed time visible when details are clipped; elapsed times have one decimal place, and the batch header freezes when finished. Retried/retained saves use their current attempt's clock.

All 318 tests passed against the actual Homebrew package/Python 3.14, with five optional skips. Five controlled-clock regressions cover continuous stage timing, queue exclusion, terminal-result freezing, recovery saves and the header. A real PTY smoke with four simulated posts and 41 generated JPEGs confirmed nondecreasing row times and frozen completed rows. Only the installed `output.py` was backed up and replaced. Evidence is under `.local/media-validation/batch-timing/`; [Issue #7](https://github.com/Popcornnnnnnnn/linkget/issues/7) is resolved. The update remains local and unpublished.

### Task recovery and Live Photo follow-up (2026-10-07)

The recovery regression suite uses isolated settings/files and covers failed-only retries, original item numbers, saved destinations, public-only browser overrides, optional concurrency limits, stage/size progress, complete-set save recovery, changed/out-of-stage refusal, private token retention and token-free reports, task locking, and managed-byte verification before cleanup. Cancellation tests launch only local Python processes; they check bounded cancellation, timeout classification and large output-pipe draining. Ordinary tests do not access a personal Photos library or browser.

Run the generated-media offline acceptance separately:

```sh
PYTHONPATH=src python3 scripts/accept_live_photos.py --output .local/media-validation/live-offline-NEW
```

The script generates its own two-second moving pattern and tone. It checks macOS pair recognition, unchanged raw inputs, identical encoded motion/audio stream hashes and byte-identical repeated packaging. It never opens Photos and never downloads a social-network sample.

For actual import acceptance, manually open an isolated Photos test library in the existing test VM, then run the following from its local Terminal. Supply its exact path; the script refuses a different active library:

```sh
PYTHONPATH=src python3 scripts/accept_live_photos.py \
  --output .local/media-validation/live-library-NEW --photos \
  --library '/Users/jack/Pictures/Linkget Acceptance.photoslibrary'
```

This opts into a real import. The importer requires one asset for the two pair resources, verifies both managed originals before removing staged raw/paired copies, and verifies that importing again adds no asset. The generated sample remains in the named test library for inspecting the LIVE badge, motion and audio. `acceptance.json` keeps playback explicitly pending until that UI check is recorded. Repeat with a fresh real multi-item source gallery for platform acceptance; the generated pair establishes only the packaging/import path.

On 2026-10-07, all 196 tests passed on source/Python 3.10 and 3.14, and on an isolated installed wheel/Python 3.13. The direct Homebrew script entry point passed a generated-JPEG batch/retry smoke check using a local fixture engine and real ffprobe: the successful first item was skipped and the second retained its original input number. That smoke check made no website requests. The offline follow-up passed pair recognition, unchanged raw sources, identical encoded audio/video streams and byte-identical repeat packaging. The backed-up local command was updated to `0.2.1.dev8`; destination-aware `doctor` passed. The already-running older batch was kept running.

The host Mac and existing test VM were locked during UI work, so actual named-library import and playback have not been verified in this round. Local evidence, the installed-source backup and installation fingerprints are under `.local/media-validation/optimizations/`.

For the development `--live-photos` feature, macOS recognizes the generated resources in an offline PhotoKit check. This does not prove actual library import or playback. In a test library, import one pair from a local Terminal: it should become one item with a LIVE badge and playable motion; repeat the same import and confirm there is no additional item. Also test a fresh multi-item Live Photo share before claiming live gallery acceptance. Check that both raw and paired staging files are removed only after the library originals have been verified; keep them while Photos could reference them. Do not launch Photos for unattended tests or change the user's focus. The macOS VM was suspended during this round; real library acceptance remains pending.

Real OS permission prompts, a brand-new Mac installation, browser profile combinations, and actual Photos imports vary by machine. The automated suite does not prove these UI flows. Photos mode removes staged originals only after matching the bytes of the managed originals belonging to the returned Photos item IDs. Referenced, failed or unverified imports keep their source media. The check uses the running Photos process's open library paths, ignoring the internal Syndication library, and reads managed originals without reading or editing the library database. It does not depend on caller-specific Photos preferences. Missing or ambiguous open libraries and changed on-disk layouts fail safely. Check the imported item, audio, dimensions and duplicate handling before relying on the trial for an archive.

For 0.2.0, source tests passed on Python 3.10, 3.13 and the VM's 3.14; the isolated installed wheel passed on 3.14. Homebrew upgrade/test checks passed on the host and clean VM without changing preferences. The user accepted a Pixiv image and the Kuaishou HEVC video in Photos, including video playback. The installed CLI downloaded the full/shortened Kuaishou URL and a second video anonymously in the VM; all three decoded completely, and the user file matched the accepted Photos original. These concrete acceptances do not establish Intel/older-macOS compatibility or universal watermark/quality guarantees.

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

The `0.2.1.dev9` lookup regressions use isolated files and a fake Photos adapter. On macOS they execute the actual AppleScript grouping/import-result logic without opening Photos, including shuffled results, existing duplicates, motion-first Live pairs, unexpected IDs and incomplete pairs. Short-link tests cover shared concurrent resolution, task lifetime, token separation, failure retry, cancellation and redirect boundaries. Digest tests cover file replacement, same-size edits with restored modification times, changes during hashing and mandatory fresh reads before Photos cleanup.

An isolated 64-asset fixture with 512 entries in one originals directory reduced directory enumeration from 64 calls to 1. A one-file inspection/retention/two-receipt/final-verification fixture reduced SHA-256 reads from 7 to 5. These are operation counts, not a general download speed multiplier. Actual Photos import, playback and library acceptance for this build remain unverified.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -q
uv build
```

`test_instagram.py` runs its command checks without gallery-dl's Python package.
To also run its offline extractor integration cases with an isolated Homebrew
installation, use `PYTHONPATH=src /opt/homebrew/opt/gallery-dl/libexec/bin/python -m unittest discover -s tests -p test_instagram.py -v`.
These cases use synthetic post data and make no network or Photos requests.

GitHub Actions runs source and installed-wheel tests on Linux/Python 3.10 and macOS/Python 3.14, and compiles the AppleScript on the isolated macOS runner. The tap's `brew test linkget` does not request browser or Photos permissions.

## Reliability in 0.2.1.dev22

This prerelease adds evidence-based diagnostics,
per-resource native gallery recovery, a disabled fallback metadata interface and
an offline upstream compatibility command. See [scope, verification and limits](RELIABILITY.md),
[candidate review](FALLBACKS.md) and [compatibility commands](COMPATIBILITY.md).
These checks do not arrange a real Photos acceptance run; the user reviews actual
Photos results in normal use.

## Batch/task integration (source only)

The integration preserves all eight commits from the local batch/reader branch on top of main `4f85d68`. The installed dev21_1 package was compared byte for byte with `deeb111` / `67a174b`; every installed package file matched, so no installed-only implementation was omitted. The original dirty checkout remains separate. No release, tap, installation, live website or Photos change is part of this integration.

Offline coverage includes bounded default workers, independent-site scheduling, known share aliases, one-site exclusion and delay, finite network retries, long Retry-After refusal, certificate/login/access/save/import exclusions, cancellation, native resource reuse with one final save, task-lock interruption detection, mixed task views, retained-size observation, symlink avoidance and source-token redaction. Existing ownership, media completeness and Neo lifecycle tests remain required.

## Douyin automatic Neo routing

Offline regressions cover Neo selection independently of the system default browser, explicit Chrome compatibility, public-only/logout boundaries, exact Douyin URL/player scope and no AppleScript fallback on Neo errors. The original gallery/motion extractor and source-completeness requirements remain unchanged.

A controlled public-metadata miss exercised automatic Neo acquisition and real native transfers into an isolated folder: nine current-post images, zero exposed motion tracks, nine valid saved WebP files totaling 1,215,162 bytes. The existing Neo lifecycle reported verified tab closure and no credential export. No AppleScript or Photos import ran; the old failed Photos task was not retried. This validates that one accessible gallery; it does not establish every Douyin post or Photos playback. Signed media URLs and downloaded images remain in ignored local evidence.


### Personal page-reader preference (2026-10-22)

The complete source suite passed 560 tests with five optional extractor skips; wheel and source-distribution builds passed. Fourteen focused selection tests cover clean-user auto resolution, saved/system browser choices, personal Neo selection, explicit/environment priority, configuration preservation, download and read checkpoint freezing/override, batch/worker transport, browser-off and offline paths, cancellation, and no silent Neo-to-Chrome fallback. A live Neo alias with missing group metadata exposed an owned-tab cleanup failure; the regression now derives only the unique group on the same physical tab and preserves conflicting group/owner/navigation records. Browser controller support remains limited to Chrome and Neo. Live platform availability and account state are separate acceptance layers.
