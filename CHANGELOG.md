# Changelog

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
