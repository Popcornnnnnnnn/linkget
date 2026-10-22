# Changelog

## Unreleased

- Explain effective page-controller provenance separately from login-browser sources; add layered metadata-only diagnostics and explicit single-target checks.
- Add detailed local build identity, focused command help, retained-file reasons and a strictly read-only cleanup preview. Preserve existing overrides, checkpoints and all user files; media/results and browser materials are never cleanup candidates.

- Confirm asynchronous Neo tab closure with bounded read-only physical-identity observations; never replay the close, creation or content read.

- Add a shared user page-reading preference (`config --reader-browser auto|neo|chrome`) across Douyin fallback, scoped reads, batches and workers. Auto follows saved/system browser choices, unsupported controllers fail clearly, and retained tasks preserve their choice unless explicitly overridden.

- Provide the bounded Neo controller for the user-selected Douyin rendered fallback; preserve explicit Chrome, public-only and logout boundaries. Report incomplete browser state separately from unsupported websites.

- Integrate the local compact batch panel, continuous elapsed times, checked ASR chunk recovery, bounded Toutiao transfers, scoped rendered/Neo reader and Neo tab ownership fixes with the dev22 writer/checkpoint safeguards.
- Bound batches to three workers by default and one task per known website, with a one-second gap; reuse the existing read-only policy for at most two temporary network/rate-limit retries before saving. Save, import and pairing failures are never automatically retried.
- Add `linkget tasks [TASK]` to inspect download and background reading status, results, failures, observed retained sizes and existing recovery commands without showing source links.

## 0.2.1.dev22 (prerelease)

- Classify explicit download/save failure evidence and provide next steps without guessing unknown causes; redact structured, encoded and malformed credential diagnostics.
- Checkpoint native Xiaohongshu/Douyin media resources and reuse verified successful resources on retry. Refuse shrinking or changed plans without explicit redownload; protect omitted resources even after redownload. Other engines retain their existing retry behavior.
- Carry creation evidence from native writers through registration, duplicate handling, folder copies, retained Photos sources and cleanup. Preserve replaced, shared, legacy and uncertain files; report actual removed/retained counts. This conservative policy can retain more staging data.
- Add a manual offline compatibility checker with generated fixtures, engine versions and required extractor execution checks. Website availability and actual Photos playback are not established by these tests.
- Add disabled extractor-fallback interfaces and a read-only retry-delay policy for future work. Neither is connected to normal download execution; no third-party fallback extractor is integrated.
- Refresh installation, recovery and optional reading prerequisites. Include the previously unreleased dev21 Douyin motion safeguards below.

## 0.2.1.dev21

- Preserve explicit per-image motion in Douyin posts and pair each motion track with its still, including lazy-loaded galleries. Exclude post background music from motion pairs.
- Refuse Photos imports and Live Photo packaging when public image metadata cannot establish motion completeness. Ordinary folder downloads retain an explicit incomplete-motion warning.
- Accept a content-only Douyin snapshot from an authorized browser connector with `--page-data FILE --browser none`. Check the exact post identity before downloading; preserve that input path for retry.

## 0.2.1.dev20

- After a same-item retry/recovery has saved and verified complete media, remove its unchanged tracked earlier staging directories, including partial downloads, metadata and logs. Keep files on failure/cancellation, content changes, symbolic links, destination overlap or uncertain ownership.
- Verify folder and Live Photo copies before cleanup; retain the existing managed Photos original verification requirement.

## 0.2.1.dev19

- Fix Instagram Reel and gallery completeness when gallery-dl counts optional standalone audio that is not saved. Count the source photos/videos before downloading, and still reject missing source media or incomplete downloaded sets.
- Run the real Instagram extractor regression cases in CI, and find Node.js on PATH for the offline DOM test on both Linux and macOS.

## 0.2.1.dev17

- Expand top-level and read command help with translation, summaries, task management, examples, background/foreground behavior and fallback configuration guidance.
- Run content reads, recognition, OCR, translations, summaries, retries and read batches in detached background workers by default; return a task ID immediately and retain `--foreground` for scripts.
- Add read task status, private logs and verified process-group cancellation, preserving source text and recognition checkpoints on interruption.
- Show cloud/local inference phases and serialize local service/model startup across concurrent tasks; retain ten-minute idle unload for models loaded by LinkGet.

## 0.2.1.dev16

- Add an opt-in automatic AI fallback on provider outages, rate limits or transport timeouts; preserve successful batches, stop after backup failure and record the actual models used.
- Keep Gemini Flash Lite as primary and configure independent local LM Studio inference as backup, with startup and downloaded-model loading on demand. Never forward the primary endpoint's credentials to another route.
- Remove the installed LinkGet desktop companion and Share extension at the user's request; keep the CLI, saved settings and existing content.

## 0.2.1.dev15

- Accept real translation through the existing loopback CPA service using `gemini-3.1-flash-lite`; add explicit client-auth reuse without copying keys or changing proxy configuration.
- Preserve older/user-edited exports by citing matching source snapshots, and restore source timeline order if the model reorders translated units.
- Strengthen uncertainty handling for garbled recognition; validate English/Chinese/Japanese, exact source-unit coverage, numbers, negation and multi-batch output.

## 0.2.1.dev14

- Add subtitle-first reading, saved defaults, doctor, Markdown/TXT/SRT/VTT exports, local media and macOS Vision OCR.
- Add full-audio chunk checkpoints, sequential read batches, failure retry and a 24-hour text cache; request audio-only where available.
- Add explicit configurable summaries/translations with validated source references and original text preservation.
- Add native macOS Read/Download host, Share extension and Services.
- Validate real Bilibili, YouTube, Weibo, Douyin and Xiaohongshu samples; preserve channel and recognition limitations in read documentation.


## 0.2.1.dev13 — video content reading across channels (unreleased)

- Extend `linkget read` to existing video post channels: Bilibili (including a selected part), YouTube, Instagram, X, TikTok, Douyin, Kuaishou, Xiaohongshu, Weibo, Reddit, LOFTER and individual Pinterest pins. Reuse the existing download, format validation and configured login flow; Pixiv remains static-image only.
- Transcribe each ordinary video locally with the existing whisper.cpp model, save speech and independent timed segments in `content.json`, and optionally select `--language auto`, `zh` or `en`. Keep publisher descriptions separate from speech, ignore pictures and explicit Live Photo motion, and refuse missing audio, over-30-minute videos or incomplete results.
- Clean temporary downloads and recognition files by default, preserve optional review media with `--keep-media`, and commit content only after every video succeeds. Existing content folders are refused. See [reading options and validation limits](docs/READ.md).

## 0.2.1.dev12 — clean Toutiao intermediates (unreleased)

- Remove downloaded video, audio and duplicate transcript files after durable content capture, including partial files on failure. Preserve text and timed segments in `content.json`.
- Add `--keep-media` for temporary recognition review; `cleanup_media` removes only known generated filenames and refuses substituted paths.

## 0.2.1.dev11 — Toutiao content reading (unreleased)

- Add `linkget read URL --folder NEW_DIR` for content intended for AI summarization. Read the current rendered Chrome page without exporting browser credentials, or accept content-only evidence from an authorized browser connector with `--page-data`.
- Process Toutiao videos with explicit `--transcribe --whisper-model PATH`: verify complete downloaded audio/duration and use the existing local whisper.cpp runtime. Keep publisher descriptions separate from recognized speech. No hosted model, Photos import or message sending is enabled by this command.
- Reject a different post after automatic playback, unsupported media hosts, title-only content and existing output folders. See [Toutiao validation and limits](docs/TOUTIAO.md).

## 0.2.1.dev10 — hide routine task identifiers (unreleased)

- Stop displaying task IDs before single and batch downloads. Keep private recovery receipts, retry commands on failure/cancellation, and the explicit `recover` task listing.

## 0.2.1.dev9 — faster repeated lookups (unreleased)

- Query Photos once per ordinary asset or Live Photo pair, reuse existing identifiers, and match new import results by filename rather than return order. Group source filenames once and preserve per-resource IDs for verification.
- Enumerate each relevant managed-originals directory once per verification, indexing only requested assets. Continue checking both Live Photo components, rejecting symlinks and mismatched bytes.
- Reuse unchanged SHA-256 results within one download/recovery item. Invalidate on file identity, size, modification or change time; detect changes during reads. Final Photos verification and recovery content checks still force actual reads.
- Share exact short-link resolutions across concurrent and queued items in one batch. Keep query tokens separate, retry failures, respect cancellation and platform redirect boundaries, and discard the cache after each run.

## 0.2.1.dev8 — task recovery and bounded batch execution (unreleased)

- Add private task receipts and `retry [TASK]`, preserving original item numbers and skipping completed downloads. Reuse a complete retained set after save failure; `--redownload` fetches again while keeping earlier files.
- Add `recover [TASK]`, per-item recovery, destination overrides and `--verify` cleanup using recorded Photos asset identifiers and byte checks. Refuse incomplete, changed or out-of-stage files; retain referenced and untracked originals.
- Check writable destinations, ExifTool and the selected Swift compiler before download/browser work; extend destination-aware `doctor` checks without opening Photos or reading a browser.
- Add optional `--jobs N` without changing default concurrency. Show active phases, elapsed time and transfer size. Cancel external engine process groups and check cancellation between native adapter chunks; preserve active-save safety.
- Extract batch coordination, engine routing/format selection, output, destination saves, cancellation and recovery from the main CLI. Preserve direct Homebrew script execution and packaged entry points.
- Add an opt-in generated-media Live Photo acceptance script, checking source preservation, encoded stream hashes, repeat packaging, and named-library import/deduplication/verified cleanup. Actual playback still requires UI acceptance; offline recognition alone is insufficient.

## 0.2.1.dev7 — Douyin browser-assisted recovery (unreleased)

- When public metadata is missing or a transfer is interrupted, use daily Chrome's normal post page once, without exporting browser credentials. Reuse an existing matching page or temporarily select a new page so Chrome initializes its player; restore the previous tab and close only newly created tabs. Browser reads remain coordinated during concurrent batches.
- Select the highest exposed player quality, validate the complete gallery count, and reuse normal downloads, duplicate handling and verified Photos cleanup. Explain missing Chrome scripting permission and record the canonical source link in failure logs. Clarify Douyin's Public access status; backport to local `0.2.0+douyin-browser.1`.

## 0.2.1.dev6 — preserve Douyin failure reasons (unreleased)

- Replace the generic `download interrupted` message with the underlying timeout, connection, transfer or local file error. Do not tell users to check their connection for every local I/O failure. Backport to the installed `0.2.0+douyin-errors.1` build.

## 0.2.1.dev5 — Douyin cold share-page initialization (unreleased)

- Handle a requested post's empty SSR shell before its anonymous visitor cookie is issued. Allow up to three share-page requests during this observed initialization sequence; stop immediately for unrecognized pages or another post, and retain the existing failure after the bounded initialization attempt.
- Reproduce the old failure and verify the corrected path without browser login data. Backport the fix to the installed `0.2.0+douyin-bootstrap.1` build, retaining concurrent batch downloads and verified Photos cleanup.

## 0.2.1.dev4 — concurrent batch downloads (unreleased)

- Start one download worker per supplied link. Keep input numbers on output and report failures independently; accept one sharing text per line, with a blank line starting interactive downloads.
- Coordinate browser/login requests on the calling thread and serialize final Photos/folder saves. Keep duplicate handling and verified Photos cleanup; cancellation preserves unfinished files and waits for active requests to finish.
- Backport batch downloads to the local installed stable build `0.2.0+parallel-batch.1`; retain its existing download behavior and Photos cleanup without adding unrelated development features.

## 0.2.1.dev3 — verified Photos copy cleanup (unreleased)

- Remove staged download originals and Live Photo resources after comparing their bytes with the managed originals belonging to the saved Photos item IDs. Verify existing duplicates too; preserve failed, referenced or unverified imports and report their location. Successful Photos saves no longer report an extra Originals folder. Locate the running Photos app's open library instead of using caller-specific Photos preferences or accessing the protected container. Ignore its internal Syndication library; preserve files if the active library cannot be identified. Folder saving is unchanged.

## 0.2.1.dev2 — media evidence, gallery checks and Live Photo pairing (unreleased)

- Verify supported exposed gallery counts and probe downloaded media before saving; preserve failures for review. Show actual dimensions, codec, video frame rate/bitrate/audio, fallback choices and unknown source counts.
- Add opt-in `--report PATH` and `linkget compare FIRST.json SECOND.json`, recording token-free references and byte/property comparisons without claiming absolute maximum quality or watermark absence. Add a baseline regression gate to the opt-in real-media corpus.
- Add opt-in `--live-photos` for explicit Xiaohongshu, Weibo and Bilibili source pairs on macOS. Preserve original downloads, create separate paired copies without picture/video/audio re-encoding, validate offline with PhotoKit, and retain stable copies for Photos. ExifTool and developer tools are required. Real Photos library pairing/playback still needs manual acceptance.
- Drain audio/video tracks together after a real audio-bearing remux test reproduced a stall. Keep paired filenames aligned through folder collisions, normalize generated container dates for duplicate detection, and protect existing retained components from copy failures/races.

## 0.2.1.dev1 — batch downloads and clearer failures (unreleased)

- Add `linkget batch`: ordered command-line links, one sharing text per pasted line, or piped input. Reuse the ordinary downloader and destination options; continue after item failures and report failed item numbers. Cancel stops remaining items and preserves partial downloads.
- Distinguish known network failures, website checks, access refusal, rate limits, unsupported media and unavailable posts, with relevant next steps. Generic HTTP errors do not initiate login recovery.
- Keep the accepted 0.2.0 release artifacts and installed stable CLI separate from this development version.

## 0.2.0 — public mobile Kuaishou extraction and release acceptance

- Recover Kuaishou videos when desktop HTML contains only site configuration by reading the same post's public mobile page. Match the requested post ID before selecting media; reject invalid MP4 responses and incomplete downloads.
- Select the largest exposed Kuaishou resolution, preserving the HEVC preference at equal resolution. Codec preference is not a claim of absolute highest quality. Full `/short-video/` links are supported; profiles and image galleries are rejected.
- If Douyin's public response omits media, try an existing authorized login once in automatic mode. Respect public-only mode and logout; do not infer expiry, refresh a browser or prompt for sign-in from this failure alone.
- Keep Xiaohongshu fallback media tied to the same gallery item, including Live Photos without exposed video tracks. Never substitute another item's video when a live track is unavailable.
- Include native Pixiv extraction in the saved-session corpus comparison. Static Pixiv originals, Reddit image/video download, clean macOS installation/upgrades and user-reviewed Photos image/video imports have separate acceptance evidence.
- Keep account status compact and distinguish public access from quality and watermark guarantees. Website restrictions, unsupported media routes and unverified browser/architecture combinations remain documented.

## 0.2.0b16.dev7 — verified public Kuaishou sample

- Mark Kuaishou as `Public access` in the compact account overview after an ordinary anonymous download passed on both the host and clean macOS VM. Image galleries remain unsupported.
- Add the user-provided video to the opt-in corpus: 720×1280 HEVC, 114.2 seconds, audible audio and complete decode. Five sampled frames show no observed Kuaishou-added mark; uploader maximum quality remains unproven.
- Record the user's successful Pixiv image import into Photos separately from pending video import/playback acceptance.

## 0.2.0b16.dev6 — Pixiv web originals and browser login

- Download every original static page of a single Pixiv artwork through the website API, without requiring gallery-dl OAuth authorization. Reject incomplete page lists, non-image responses and Ugoira covers; retain partial files on an interrupted original download.
- Verify Pixiv browser sessions through its viewer-only JSON endpoint instead of searching homepage text for login links. Live Chrome verification passed; anonymous and invalid-cookie controls were rejected.
- Show `Public access` for Pixiv static artworks without optional-login clutter. Public single-image and three-page samples downloaded; the three-page artwork also passed the normal Chrome-login flow.

## 0.2.0b16.dev5 — Reddit video engine connection

- Connect gallery-dl to the yt-dlp package in its separate Homebrew/pipx installation when available. Reddit videos request the best exposed video/audio streams and merge to MP4 using the installed ffmpeg; Reddit image posts still use gallery-dl.
- Check Reddit's video dependencies before downloading, instead of failing later with `Cannot import yt-dlp or youtube-dl`.
- Verify the ordinary installed CLI against a user-provided image and native video: 1100×1429 JPEG and 1280×720 MP4 with audible audio. Keep both posts in the opt-in regression corpus; remove full test downloads and temporary login snapshots after acceptance.

## 0.2.0b16.dev4 — real media corpus and evidence-based access labels

- Replace the overbroad `No login needed` claim with `Public access`. Instagram's tested posts required login; Reddit and Kuaishou anonymous access remain unverified. No general sign-in prompts are added.
- Add an opt-in corpus covering all 13 channels, image/video routes and explicit unsupported slots, plus isolated anonymous downloads and existing-session comparisons. Preserve originals, hashes, metadata and five video frames for review.
- Record maximum quality separately from request-exposed resolution and visible watermarks. Weibo image/video, LOFTER video and one Bilibili video retain visible platform marks; successful downloading does not prove a clean original.

## 0.2.0b16.dev3 — compact account overview

- Show `No login needed` for public channels instead of optional account states and repeated explanations. Retain Bilibili's higher-quality login state and mark Pixiv as `Not ready`.
- Remove the general note from `auth`; explicit `auth SITE` requests still show that website's login state.

## 0.2.0b16.dev2 — optional logins for public access

- Annotate account status with each adapter's public access capability, Bilibili's higher-quality login requirement and Pixiv's incomplete downloader authorization.
- Ordinary `auth` checks exit without sign-in prompts. `auth SITE` explicitly connects an optional website login; first-run browser checks no longer invite sign-in for public channels.
- Downloads continue to request a login when a specific post requires it.

## 0.2.0b16.dev1 — local channel synchronization

- Synchronize the local command with all 13 channel entries and document the five trial integrations. This development build is not a published Homebrew release.
- Clarify that account connection does not guarantee maximum quality or watermark-free media; correct the Kuaishou gallery and Pixiv Ugoira listings.
- Handle Kuaishou login requests through the existing saved-session recovery and reject an explicit `isLogin: false` response during account checks.

## 0.2.0b16 — Reddit and Kuaishou support

- Reddit images, galleries, and videos with audio are now supported natively. You can use `--browser` or `linkget auth` to authorize an active Reddit session and download age-restricted or quarantined content.
- Initial Kuaishou (快手) video support reads exposed sources from `window.__APOLLO_STATE__` and prefers HEVC (H.265). This did not establish the uploader's highest quality or universal watermark absence; 0.2.0 adds mobile-page recovery.

## 0.2.0b15 — Live Photos support for Xiaohongshu and Weibo

- Xiaohongshu Live Photos (`livePhoto: true`) save the original still image (HEIC/JPEG) and an exposed MP4 motion track when available. Track resolution depends on the post; the files remain separate rather than being paired as a native Photos Live Photo.
- Weibo extraction arguments now set `extractor.weibo.livephoto=true` instead of `false`, enabling the simultaneous download of Weibo Live Photos (Dynamic Photos) as paired images and videos.

## 0.2.0b14 — best available Douyin quality, audited source selection

- Douyin videos no longer stop at the share page's 720p default. Ask the public playback endpoint for the best ratio it serves (1080p, clamped down when the post has less), and rank `video.bit_rate` gears by pixels, then declared bitrate and size. The watermarked distribution variant remains only a fallback with a notice.
- Live check: sample 7675556744459143529 changed from H.264 720×1280 (5,162,997 bytes) to H.264 1080×1920 (6,396,748 bytes); the watermarked variant at the same ratio was still 720×1280. A horizontal sample whose public ceiling is 720p was unchanged, so the change only fills in missing gears.
- Xiaohongshu display fallback keeps the largest declared image rendition instead of the default URL; originals remain the primary source.
- yt-dlp format sort adds `vbr`, so Bilibili, Weibo and YouTube prefer the higher-bitrate stream when resolution, fps and HDR are equal.
- Re-checked all eight channels live and re-ran the Weibo no-downgrade audit (20 images, zero reductions); the Weibo video sample kept its best exposed 720p stream and its burned-in 视频号 mark is disclosed. See `docs/WATERMARKS.md` and `docs/validation.md`.

## 0.2.0b13 — never downgrade resolution for watermark removal

- Correct the requirement missed by 0.2.0b11/b12: a cleaner source must not reduce either image dimension. Keep the original bytes when only a smaller alternative exists and explicitly report that watermark removal at that resolution was unavailable.
- Do not upscale, crop, blur, synthesize or re-encode the output to disguise a missing full-resolution clean source.
- Checked Weibo's original-download endpoint and alternate `lz` CDN on the reported image: both full-size responses were identical to the marked native file; the unmarked alternative remained smaller. Full-resolution watermark removal for that example remains unresolved.

## 0.2.0b12 — preserve clean Weibo originals

- Stop unconditionally replacing Weibo JPEGs with smaller `oslarge` images. Compare same-size renditions and the actual downloaded large image; select the alternate only when localized changes support a watermark improvement.
- Preserve original bytes and dimensions when no improvement is detected, comparison is inconclusive, ffmpeg is unavailable, or comparison fails. No new dependency is added; image inspection uses the existing ffmpeg tool.
- Avoid claiming that an alternate is completely watermark-free: marks embedded by earlier sources can remain.
- Rechecked 19 images from seven authors: all six visually clean originals were retained, while 13 images with an added account watermark selected the alternate. Added regression coverage for compression noise, distributed changes, clean large images with marked previews and decoder failure.

## 0.2.0b11 — prefer clean media sources

- Prefer Weibo's `oslarge` JPEG rendition: all three images in the reported sample lost the platform watermark. Report when the clean rendition is smaller; retain the downloaded original if the alternative is unavailable or invalid.
- Keep available Douyin and Xiaohongshu fallback media with an explicit notice when a clean/original source is unavailable. Preserve originals for the other items in a mixed gallery.
- Remove temporary Weibo metadata on success, failure and cancellation.
- Document live photo/video checks across all eight channels, including embedded logos that remain and the limits of sampled video frames.

## 0.2.0b10 — session recovery and response handling

- Automatically refresh expired/rejected sessions during interactive `auth`, using one read per authorized browser/profile and saving only verified replacements. Respect logout, public-only mode and noninteractive checks.
- Preserve cookies renewed by website validation before saving a connection.
- Recognize rotated-cookie download errors and recover even when a video probe silently falls back to anonymous results.
- Handle malformed Xiaohongshu, Instagram and X responses and interrupted HTTP responses without crashing account checks. Recognize Xiaohongshu's login redirect as a rejected session.
- Verified all eight channels with real Chrome sessions and recovered an expired YouTube snapshot in an isolated store. Browser-specific OS permissions outside Chrome remain unverified.

## 0.2.0b9 — verified Weibo sessions

- Replace the incorrect Weibo config endpoint with the homepage's authenticated viewer configuration; require matching viewer IDs and an account name.
- Recognize Weibo visitor/login redirects as rejected sessions. Feature flags, public author data and unknown responses cannot prove login.
- Add opt-in live browser session checks for development, without changing saved accounts or retaining cookie snapshots. All eight channels passed with an authorized Chrome session; an invalid Weibo cookie was rejected.
- Verify that Weibo profile, album and profile-shortlink inputs never start the downloader, and that accepted posts select the installed downloader's single-status extractor.

## 0.2.0b8 — clearer account guidance

- Remove speculative browser permission notices; show permission troubleshooting only after a failed read.
- Emphasize website names in account results and connection prompts.
- Show clickable, copyable sign-in URLs for every supported website when login or verification is needed, including inconclusive checks and first-run setup.
- Tell users which browser to use and how to recheck after signing in. Redirected output and terminals without styling keep plain text.

## 0.2.0b7 — Xiaohongshu account check

- Handle empty JavaScript Maps in Xiaohongshu page state so unrelated page stores no longer prevent account verification.
- Continue requiring the website's logged-in flag and current user ID before saving a connection; never execute page JavaScript.
- Verified a real Chrome session successfully against Xiaohongshu after the parser fix.

## 0.2.0b6 — Xiaohongshu originals

- Download original Xiaohongshu image files instead of watermarked mobile display images; retain HEIC/HEIF when that is the original format.
- Request the exposed original video key instead of selecting a display stream. Stop if the original is unavailable; do not fall back to potentially watermarked media.
- Mark new download filenames with `_original` to distinguish them from earlier display copies. Existing files and Photos items are preserved.
- An image sample's original was visually checked without the central platform mark, at 1200×1600 versus the 1080×1440 marked display variant. Specific user video samples still need live verification.

## 0.2.0b5 — clearer config guidance

- `linkget config` displays the current default followed by all three ways to change it, with descriptions and a reminder about one-time overrides.

## 0.2.0b4 — default save destination

- `linkget config` shows the current default destination.
- `linkget config --photos`, `--folder`, or `--folder PATH` saves a default for future runs.
- `--photos` and `--folder [PATH]` override the saved default for a single download.
- Bare folder defaults follow the working directory at download time; explicit relative paths are saved as fixed absolute paths.
- Existing users retain Photos as the default until they change it. Browser preferences and website sessions are preserved.

## 0.2.0b3 — Xiaohongshu short-link fix

- Recognize xhslink.cn share links, including full copied share text and redirects between Xiaohongshu's short-link domains.
- Preserve post access tokens and show resolving progress for both .cn and .com short links.
- Read the mobile share page when the desktop page has no note data; download the complete H5 image set without requiring login when the share is public.

## 0.2.0b2 — more websites (trial)

- Xiaohongshu note image sets and best exposed video stream; share tokens and xhslink redirects are preserved. Live user acceptance is pending.
- Weibo photos and videos, mobile post links, standalone video links and t.cn redirects.
- YouTube single videos and Shorts, MP4 audio/video merging, quality display and Deno dependency guidance; playlists and ongoing streams are rejected.
- New sites participate in browser connection, auth checks and logout. Initial website checks now run concurrently.
- Existing defaults remain: Photos, bare --folder for the current directory, or --folder PATH.

Public Weibo and YouTube sample downloads succeeded locally. New account checks and Xiaohongshu media extraction still need user acceptance with current logged-in sessions and share links.

## 0.2.0b1 — first public trial

- Photos imports use verified, content-addressed originals in a persistent local folder. Source files remain available even when Photos references them instead of copying them.
- Different files with the same name no longer collide in Photos; identical originals are reused.
- Bilibili selects the best available MP4 video without forcing H.264, keeps the source encoding, displays the selected resolution, and uses an already-connected account before selecting quality.
- Interrupted folder copies remove their incomplete destination; repeated downloads also recognize numbered duplicates.
- First-use browser connection, in-command sign-in recovery, parallel account checks, and separate tool diagnostics.
- `--folder` without a path uses the current directory; no `--folder` imports into Photos.
- Homebrew tap with declared dependencies; isolated wheel installation remains available.
- No automatic watermark removal for Bilibili. Source quality takes priority.

## 0.1.0 — local prototype

Local-only development version; not a public release.
