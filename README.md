# linkget

Save photos and videos from a post link to **macOS Photos** or a folder.
A small command-line tool with English prompts, browser login reuse and no hosted service.

**macOS command-line tool · MIT license**

## Install

With [Homebrew](https://brew.sh/):

```sh
brew install Popcornnnnnnnn/tap/linkget
linkget
```

The current release is **0.2.1.dev22**, a prerelease. The Homebrew tap follows these development releases; unreleased `main` changes may differ. Homebrew installs the download engines, Python and Deno (needed for YouTube). See [installation, updates and removal](docs/INSTALL.md) for details and the wheel alternative.

## Use

The simplest way avoids shell quoting entirely:

```sh
linkget                  # paste a link; use your default (initially Photos)
linkget --photos         # save to Photos this time
linkget --folder         # paste a link; save to the current folder
linkget --folder ~/Downloads
```

Or pass a URL on the command line:

```sh
linkget 'https://www.bilibili.com/video/BV1bK411W797/' --folder
```

Use straight ASCII quotes (`'` or `"`) around command-line URLs. Shells interpret `&`, `?` and other characters before linkget runs; curly quotes (`“ ”`) do not protect a URL. Shared post text containing one supported URL is also accepted at the paste prompt.

| Command | Purpose |
| --- | --- |
| `linkget help` | Commands, options and examples |
| `linkget sites` | Supported websites and post types |
| `linkget read URL --folder NEW_DIR` | Read subtitles, local speech and post/image text in the background; [reading options](docs/READ.md) |
| `linkget read batch URL1 URL2` | Read multiple posts; retry unfinished reads |
| `linkget read summarize FOLDER` | Summarize saved text with an explicitly configured model |
| `linkget read translate FOLDER --language zh` | Translate saved text in the background, with configured local fallback |
| `linkget read status [TASK]` | Show progress, completion and result paths |
| `linkget read logs TASK` / `linkget read cancel TASK` | Inspect or cancel a background read task |
| `linkget doctor` | Check download tools, without account requests |
| `linkget config` | View or change the default save destination |
| `linkget batch URL1 URL2` | Download all supplied posts concurrently |
| `linkget batch` | Paste one link or sharing text per line; a blank line starts downloads |
| `linkget tasks [TASK]` | View download and background read tasks together; add `--kind download` or `--kind read` |
| `linkget retry [TASK]` | Retry only unfinished items from the latest or specified task |
| `linkget recover` | List retained files and tasks needing recovery |
| `linkget recover TASK` | Save a complete retained download without downloading again |
| `linkget recover TASK --verify` | Verify recorded Photos originals before cleaning staged copies |
| `linkget auth` | Check logins and public access without sign-in prompts |
| `linkget auth SITE` | Explicitly connect one optional login, e.g. `auth bilibili` |
| `linkget logout bilibili` | Remove Bilibili login and disable its automatic browser access |
| `linkget logout all` | Disconnect all sites |

`--folder [PATH]` selects a folder; omit the path for the current directory. `--photos` selects Photos. Without either option, linkget uses your saved default, initially Photos. The two options cannot be combined. `--date-now` changes the date of new Photos items; use `--photos --date-now` when your default is a folder. Files are not overwritten.

## Batch downloads

Use `linkget batch` to paste several links, one per line. Each line can contain sharing text around one URL. A blank line starts the downloads. Batch downloads support task recovery, optional concurrency limits and repeated-lookup reuse. You can also pass individually quoted links or pipe a text file:

```sh
linkget batch 'URL1' 'URL2' --folder ~/Downloads
linkget batch --folder ~/Downloads < links.txt
```

Up to three posts run at once by default. Add `--jobs N` to change the global limit. Known share, mobile and desktop aliases share a website slot: only one post per website runs at once, with a one-second gap after it finishes. Other websites can use free slots while that site waits. Native adapters may still transfer several resources within one post concurrently. Explicit network or rate-limit failures before saving get at most two automatic retries (one and two seconds, respecting a numeric server retry time up to 30 seconds). Longer, invalid or date-form retry instructions stop automatic retry. Certificate, login, access, website-check, unknown, validation, pairing, saving and Photos import failures require manual action. Successful native resources reuse the existing checked checkpoints; no save/import is automatically repeated. In an interactive terminal, a compact panel keeps each item's original input number in a fixed row and updates its phase, elapsed time and downloaded size in place. The rightmost time is the current attempt's total elapsed time from processing start, including time waiting to save; it excludes time queued before starting, never resets between phases and freezes when the item finishes. The header shows the whole batch's elapsed time and freezes at batch completion. Available post titles are clipped to the terminal width; narrow windows show the item number, phase and details. `Waiting` means the item is waiting for the serialized Photos import or folder save. Completed rows stay in input order, with warnings and full failure/recovery guidance printed after the panel. Redirected output and `TERM=dumb` retain plain event logs; `NO_COLOR` disables color without disabling the panel. Large batches show only the rows that fit, plus the hidden-item count and overall totals. Browser/login requests and final Photos or folder saves run one at a time. A failed item keeps its diagnostics and does not stop other links; the summary identifies failed item numbers. Existing duplicate handling and save options still apply. Many links share your bandwidth and may encounter website rate limits. Ctrl-C stops queued work, terminates external download engines and their child processes, and preserves unfinished files. Native adapters stop at their next transfer boundary; an active network read may still wait for its existing timeout. Saving or importing finishes at a safe boundary. Exit status is 0 when all items complete, 1 when any item fails, 130 when cancelled; invalid command options or an empty batch exit with 2.

Identical short URLs in one batch share their resolution, including concurrent requests. Each item still runs its own download and normal duplicate handling. Different access tokens stay separate; failed resolutions can retry, and nothing is cached across runs. Photos reuses filename lookup results and scans each relevant originals directory once per verification. File digests are reused only within an item while its file metadata is unchanged; final Photos cleanup still reads and checks both the staged source and the managed library copy.

### View tasks

`linkget tasks` lists the latest 20 download and background read tasks together. Use `linkget tasks TASK` for one task, `--kind download|read` to filter, or `--limit N` to change the list length. It shows status, destination/result locations, structured failures when available, observed retained file sizes and the existing retry/recovery/log/cancel commands. Source links and sharing tokens are omitted. Size observations do not verify media or authorize cleanup; unavailable paths and older receipts can remain uncertain. A download receipt left running without its process lock is shown as interrupted. A queued background read is still pending, even though submission succeeded. If a read worker cannot be confirmed (including receipts from another installation), the view reports that uncertainty and preserves its recorded state; `read status TASK` retains its existing refresh behavior. Existing `retry`, `recover` and `read status/logs/cancel` commands remain available.

### Retry and recover

Downloads keep their task ID in a local receipt without displaying it during normal use. A failed or cancelled download shows a retry command with that ID; `linkget recover` also lists tasks needing attention. The receipt remembers the resolved destination, download options and unfinished items. Completed items are skipped on retry, and original item numbers are retained. If a download was complete but saving failed, retry saves the checked local files first. For native Xiaohongshu/Douyin downloads, matching successful media resources are checked and reused; only missing or rejected resources are fetched again. A changed or smaller exposed plan is refused unless explicitly redownloaded. Other engines retain their existing whole-item retry behavior. This does not provide byte-level resume or prove that the source exposed every resource.

```sh
linkget retry                         # latest unfinished task
linkget retry TASK --jobs 3           # one selected task
linkget recover                      # retained locations and recovery status
linkget recover TASK --folder ~/Downloads
linkget recover TASK --photos --item 2
linkget recover TASK --verify         # no import; verify existing Photos copies
linkget retry TASK --redownload       # fetch again; protect uncertain old staging
```

Replace `TASK` with the printed ID. Recovery refuses incomplete, missing or changed media; it does not silently import a partial set. A folder or Photos override applies to that recovery command. In `0.2.1.dev22`, cleanup requires creation evidence carried from the writer and a verified saved copy. Folder copies are checked against source bytes; Photos imports must pass managed-original verification. Failed/cancelled retries keep older files. Externally replaced or newly added files, symbolic links, shared/legacy originals and outputs from uninstrumented external engines or transformation tools are retained. Explicit redownload protects old stages before resetting progress. More temporary data can remain, including reading stages; there is no automatic purge. Older receipts without creation evidence cannot authorize deletion, even after a new successful download. See [reliability scope and recovery limits](docs/RELIABILITY.md). Saving a previously failed Photos task to a folder does not delete resources that Photos may still reference; its import receipt remains available to `recover TASK --verify`.

Receipts are kept in `~/Library/Application Support/linkget/jobs/` on macOS, with user-only directory/file permissions. Unfinished tasks retain their original share text or link, including access tokens needed to retry, plus a selected browser profile path if supplied. They contain no browser cookies. Successful items drop their share text; do not share unfinished task receipts. Optional `--report` files continue to exclude share tokens and local recovery paths. Retrying with `--report` reports only the retried subset.

Before network/browser work, linkget checks whether the selected destination or its existing parent is writable and, with `--live-photos`, whether ExifTool and the selected Swift compiler are available. `linkget doctor --folder PATH` or `linkget doctor --photos --live-photos` includes these checks. Photos Automation permission and managed-library copies still need to be checked during import; the preflight does not grant permissions or prove library acceptance.

Download errors distinguish known network failures, website checks, HTTP access refusal, rate limits and unavailable posts, with a next step. A generic HTTP 403 does not trigger login recovery. Explicit login requirements keep the existing browser flow.

## Download evidence and Live Photos (development version)

The development version checks downloaded formats with ffprobe when available and verifies gallery completeness against exposed source counts where supported. An invalid or incomplete set is kept for review and is not imported. When the source count is unknown, the output says so. Actual dimensions, codec and available video frame rate, bitrate and audio presence are shown; fallback sources are identified.

Use an optional report to compare two downloads of the same links in the same order, for example public access versus an already-connected login:

```sh
linkget 'URL' --browser none --folder ~/Downloads/public --report public.json
linkget 'URL' --folder ~/Downloads/connected --report connected.json
linkget compare public.json connected.json
```

Reports include post references, filenames, hashes, file properties, source counts, fallback choices, exposed Live Photo pairs and failure categories. They exclude share query tokens, cookies and raw request logs. They are saved only when requested and never overwrite an existing report. They still contain post references and should be reviewed before sharing. Matching bytes establish identical downloads; equal dimensions or different encodings do not establish the uploader's highest quality or absence of watermarks.

On macOS, `--live-photos` packages explicit picture/motion pairs exposed by Xiaohongshu, Weibo, Bilibili and Douyin. It needs ExifTool and Apple's command-line developer tools (`brew install exiftool`; `xcode-select --install` if missing). Without this option the picture and video stay separate.

```sh
linkget 'URL' --folder ~/Downloads --live-photos
linkget 'URL' --photos --live-photos
```

Folder mode saves unchanged downloaded originals and puts paired copies in `Live Photos/`. Photos mode stages original downloads and paired copies and submits the paired resources together. Cleanup additionally requires identical managed originals for the saved Photos item IDs and writer-origin evidence for each source; shared, transformed or uncertain sources can remain staged. Picture pixels and encoded video/audio streams are preserved; only pairing/container metadata is changed on copies. Packaging dates are normalized for repeat-export deduplication. The still-frame marker is placed at the start; the source camera's original key-frame timing is not recovered. A social-network motion track may have lower resolution than its still picture. Missing exposed motion is reported. macOS offline pair recognition is checked before saving, but actual Photos library pairing/playback needs manual acceptance; it remains unverified for this development version.

Douyin's public share response can omit per-image motion information. Photos imports and `--live-photos` stop when that information is incomplete; ordinary folder downloads report `motion_metadata_complete=false` and a warning. A browser connector such as BrowserOS neo can run the packaged `douyin_browser.js` on the exact post and save a content-only JSON object containing `aweme_id` plus its `images` or `video`. Pass that snapshot to the normal download pipeline:

```sh
linkget 'https://www.douyin.com/note/POST_ID' --page-data post.json --browser none --folder ~/Downloads/check --live-photos
```

The page-data input checks the exact post ID and requires `--browser none`; it never opens Chrome or reads browser credentials. It accepts one post per command, retains the input path for retry, and rejects unrelated/raw browser state. Refresh expired media URLs through the same connector. Keep snapshots private because media URLs can be signed. Folder mode performs download and native pair checks without importing into Photos.

## Default save destination

```sh
linkget config                       # show the current default
linkget config --photos              # default to Photos
linkget config --folder              # default to the current folder on each run
linkget config --folder ~/Downloads  # default to a fixed folder
```

Relative folder paths supplied to `config --folder PATH` are resolved when saved. Bare `config --folder` follows your working directory on each subsequent download. Configuration does not create the destination folder; it is created when downloading.

Download options override the saved default for that run only. The `Destination` line shows the actual resolved destination. Updating the default preserves your browser choice and saved website sessions.

## Supported posts

| Website | Media |
| --- | --- |
| Instagram | Post photos, videos and Reels |
| X / Twitter | Post photos and videos |
| Bilibili | BV/av videos and Opus media; one selected part per URL |
| TikTok | Photo posts and videos, including supported share links |
| Douyin | Photo posts and videos, including copied share text and short links |
| Xiaohongshu (trial) | Note image sets and videos; full links, `xhslink.com` and `xhslink.cn` shares |
| Weibo | Post photos and videos; mobile links, video pages and `t.cn` shares |
| YouTube | Single videos and Shorts; `youtu.be` shares |
| Reddit (trial) | Post photos, galleries and videos through gallery-dl |
| Kuaishou (trial) | Videos from full `/short-video/` post links; image galleries are not implemented |
| LOFTER (trial) | Post photos and videos through gallery-dl; video sample retains a LOFTER mark |
| Pixiv (trial) | Original static pages of a single artwork; Ugoira is not supported |
| Pinterest (trial) | Pins and boards through gallery-dl |

Live streams, playlists and whole-account downloads are outside this release. Post text and photo-post background music are not saved. Website availability and account access still apply.

Keep the full Xiaohongshu share link, including `xsec_token` when present. Removing or reusing an expired access token can make an otherwise visible note unavailable to the downloader. Xiaohongshu image sets preserve their order and original formats, including HEIC/HEIF; Exposed Live Photo motion tracks are saved as separate video files; background music is not included. This adapter still needs live user acceptance with current video share links.

YouTube links with a playlist parameter download only the explicitly selected video. Playlist-only URLs and active/upcoming streams are rejected. A browser login does not bypass membership, geographic restrictions or website verification.

## Browser connection

On first interactive use, choose Chrome, Firefox, Edge or Safari; a supported default browser is preselected. You can skip this. The chosen browser is read once and supported website sessions are checked concurrently before being saved. Failed verification is not reported as a successful connection.

On macOS, Chrome/Edge may need Keychain access. The system request may name `security` and the browser's **Safe Storage** item. Safari may need terminal access under System Settings > Privacy & Security. A denied request does not trigger prompts for every other browser.

Normal downloads use public access first. Bilibili uses an already-connected login before selecting formats so anonymous quality restrictions do not hide available quality. When a download needs a login, linkget can refresh the previously chosen browser; if you need to sign in, the same command provides the website URL and waits for Enter. There is no separate login/import/refresh command.

`linkget auth` lists all 13 integrated channels, including sites with no saved login. It verifies access to an account, not maximum quality or watermark-free output. Public posts may download without login; Bilibili may expose higher-quality formats after login, while account permissions still apply. The five newly connected channels remain trial integrations with post-specific coverage in the acceptance corpus. Pixiv static artworks use the website's original-page URLs and browser sessions rather than the downloader's OAuth flow. Profiles, collections and Ugoira animations are not supported.

`linkget auth` displays a compact list: tested public channels (including Kuaishou videos and Pixiv static artworks) show `Public access`, Instagram shows its connection or `Login for tested posts`, Reddit shows `Public access unverified`, and Bilibili shows its higher-quality login status. Optional login states are shown only when explicitly requesting `auth SITE`. It exits without a general note or sign-in prompts. Public access varies by post and does not prove maximum quality or watermark-free media. Use `linkget auth SITE` to explicitly connect a login. First-run browser connection skips unsolicited sign-in links for public routes. Network check failures do not remove saved logins. After logout, reconnecting requires your explicit choice. See the [real media corpus and current sample findings](docs/MEDIA_VALIDATION.md).

In an interactive terminal, `auth` automatically refreshes expired or rejected saved logins from the previously connected browser/profile. Sites using the same browser/profile share one read. Only newly verified sessions replace saved data; network errors, rate limits and website verification challenges do not trigger automatic refresh. `--browser none` and noninteractive account checks never read browsers automatically.

`--browser none` disables all login use. `--browser firefox --profile '/path/to/profile'` selects a specific browser and account profile. Otherwise the download engine chooses its recently used profile. Noninteractive commands do not run setup or automatically read browsers; explicitly requesting `--browser` still requests that browser.

## Quality and Photos originals

- Bilibili and YouTube preserve the best available MP4 video stream, including HEVC/AV1 where offered, and merge audio without re-encoding. Resolution, codec and frame rate are shown when available. Login and paid-tier restrictions still apply; linkget cannot grant higher account permissions.
- Prefer sources without platform watermarks. When no usable clean alternative is available, keep the available media. There is no crop, blur, re-encoding or AI watermark repair; marks embedded in an upload may remain.
- Douyin prefers recognized clean image variants and ordinary video playback, ranks the exposed `video.bit_rate` gears, and requests the best ratio the playback endpoint serves (the watermarked distribution variant is only a fallback). Xiaohongshu prefers original images and the exposed original video. If these are unavailable, exposed fallback media are retained with a notice; one unavailable original does not downgrade the rest of a gallery.
- Douyin image motion is kept with its associated still, excluding background music. Use `--live-photos` to package these explicit pairs. Public sharing metadata can omit motion entirely: ordinary downloads warn and report `motion_metadata_complete=false`; `--live-photos` instead requires complete motion metadata or the existing allowed browser fallback. With `--browser none`, incomplete public metadata stops before saving or importing. The flag does not enable browser permissions or change your selected browser.
- If Douyin's public request is blocked by its JavaScript check or a transfer fails, Linkget reads the normal post in your running daily Chrome once. Chrome must be your default browser, or selected with `--browser chrome`, and permit **View → Developer → Allow JavaScript from Apple Events** plus macOS Automation access. Existing pages are reused; a new page is briefly selected and then closed, with the previous tab restored. The highest exposed player quality and complete gallery are used; browser credentials are not exported. Single-picture posts without a gallery counter require a fully loaded single-slide panel; galleries with a counter must match its total. `--browser none` disables this fallback. A CAPTCHA or login still requires your action.
- Kuaishou reads the public desktop or mobile post state and selects the largest exposed resolution. HEVC is preferred at equal resolution; that preference does not prove better visual quality than an H.264 alternative. Website verification and post availability still apply.
- Weibo JPEGs use an alternate rendition only when comparison supports a localized watermark improvement **and neither image dimension is reduced**. A smaller `oslarge` image is never used as the output. If no qualifying source is available, the large file is kept unchanged and the CLI states that watermark removal was unavailable at that resolution. The comparison uses ffmpeg; if missing or unable to decode the image, original quality is retained with a notice. This is a heuristic, not a universal watermark detector, and earlier embedded marks may remain. See the [19-image batch audit and follow-up verification](docs/WEIBO_IMAGE_AUDIT.md). Videos retain the best exposed source.
- Instagram, X and TikTok use playback/original media from the download engine. Bilibili and YouTube keep the best available stream; embedded logos are retained when no clean alternative is available. These are source-selection strategies, not an automatic watermark detector or a guarantee for every post. See the [current media acceptance corpus](docs/MEDIA_VALIDATION.md).
- **Photos mode keeps only the library copy after a verified import.** Downloads are temporarily staged unchanged in `~/Library/Application Support/linkget/originals/`. linkget checks the saved Photos item IDs and compares the bytes of their managed library originals before deleting the staged files, including on duplicate imports. If the import or verification fails, sources remain available at the reported location. Enable Photos > Settings > General > Copy items to the Photos library; referenced imports must be consolidated before their external sources can be removed. A downloaded original can be a smaller clean rendition, not necessarily the largest platform image. Previously imported marked copies are not modified; download the link again for the new source selection.
- Staged originals use a SHA-256-derived filename: different contents with the same download name stay distinct; identical contents are reused. Imports made by older versions may be imported once again because they did not use these filenames.
- Some codecs may not be supported by your Photos/macOS version. If importing fails, the original is retained and linkget reports its location; `--folder` avoids the Photos importer. Files are never transcoded to hide this limitation.

For leftovers from older versions or failed imports, verify the library copies or consolidate referenced items before removing external originals. See [data retention and uninstall](docs/INSTALL.md#user-data).

## Privacy and support

No website passwords are collected and no credentials are sent to the project author. Saved sessions and temporary browser exports are local, with user-only file permissions; temporary exports are removed. Website checks send the relevant session only to its website. There is no telemetry.

Include `linkget --version`, `linkget doctor`, the website and the visible error in a [bug report](https://github.com/Popcornnnnnnnn/linkget/issues). Do not attach cookie files or private account data. Network errors, expired sessions and unavailable posts are different failures.

## Development

Python 3.10+; external engines are gallery-dl, yt-dlp and ffmpeg, with Deno for YouTube's JavaScript challenges. Core code uses only the Python standard library.

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -q
uv build
```

See [validation scope](docs/TESTING.md). Actual Photos and OS permission dialogs require manual acceptance; unit tests never open personal apps or read real browser sessions.

### Development handoff — 2026-09-29

- The local checkout is `/Users/forge/Workspace/linkget`, registered as a dedicated Codex project with the original conversation. The development environment was recreated after relocation and no longer references the old checkout. Use `PYTHONPATH=src .venv/bin/python` for source validation.
- This is an independent public MIT repository with its own Homebrew tap. The release version is maintained in `src/linkget/version.py`.
- Preserve the CLI-only design, saved destination settings, original media preference and single-post restriction. `auth` manages website connections; `doctor` checks dependencies. Do not introduce a separate login command.
- Verified: all eight website checks passed with an authorized Chrome export. An expired YouTube snapshot automatically recovered using real Chrome cookies in an isolated temporary account store. Other browsers' real OS access is not thereby verified. See the validation document for automated coverage.
- YouTube diagnosis: the old saved snapshot returned `LOGGED_IN=false` and `loggedOut=true`, while a fresh Chrome export returned the opposite. Seven cookie values differed although local expiry checks passed. Refreshing from Chrome restored the saved connection. No credential values were retained in development evidence.
- Implemented: interactive `auth` refreshes explicitly rejected or expired snapshots once per authorized browser/profile, preserves sessions after failed validation, and respects logout and noninteractive checks. Cookies renewed during validation are included in newly saved snapshots.
- Before further work, verify the working directory, this README and Git status. Real permission prompts, other browser profiles and long-term behavior under upstream website changes remain acceptance limits.


<!-- workspace-maintenance:start -->
## 维护与问题入口

代码与必要文档沿用 [GitHub 仓库](https://github.com/Popcornnnnnnnn/linkget)；需跟进的问题统一从 [Issues](https://github.com/Popcornnnnnnnn/linkget/issues) 接续，先查重，再记录进展与完成条件。
未完成开发的快照保存在 `archive/workspace-backup-20261010` 归档标签，便于恢复与接续；该标签不代表功能已验收。
项目功能与验收沿用已有记录。
<!-- workspace-maintenance:end -->
