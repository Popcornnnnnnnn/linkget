# Changelog

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
