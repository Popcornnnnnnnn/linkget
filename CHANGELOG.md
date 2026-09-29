# Changelog

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
