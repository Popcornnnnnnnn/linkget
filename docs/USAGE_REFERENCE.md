# linkget Detailed Usage & Reference

Detailed command behavior, recovery rules, media heuristics, and historical notes moved out of `README.md`.

## Save type and destination

- `--folder [PATH]` saves to `PATH` (or the current working directory if omitted).
- `--photos` imports to macOS Photos; `--date-now` sets the date of new Photos items (use `--photos --date-now` when your saved default is a folder).
- `--as auto` detects the output from the link and preserves mixed photo and video posts. This is the default.
- `--as photo` saves still images from a supported post. Use Auto with `--live-photos` to preserve motion in paired Live Photos.
- `--as video` (or `--video`) uses yt-dlp for a single video that has a usable MP4 format. Use a processed replay for live streams. Links outside dedicated video routes use public access.
- `--as article` (or `--article`) saves an offline archive to a folder (set `linkget config --article-folder ~/path/to/Vault/Clippings` to save Markdown bundles directly into an Obsidian vault or notes folder). Use `--no-assets` to skip embedded media.
- **X Articles → Apple Photos:** On macOS, `linkget 'X_ARTICLE_URL' --photos` (or `--as article --photos`, or permanently via `linkget config --x-article-photos`) renders the offline X Article locally as a high-resolution PNG and imports it into Apple Photos (requiring Node.js, Chrome/BrowserOS Neo, and FFmpeg for very tall articles; explicit `--folder` saves an offline bundle instead, and `--photos` cannot be combined with `--no-assets` or non-X articles).
- Downloads, `batch`, and `inspect` accept `--as`. Retry uses the original task type.
- `--audio` extracts audio-only from any supported video link to a folder; `--audio-format {mp3,m4a,flac,wav,opus}` converts when needed (implies `--audio`).
- Music-only links (`music.youtube.com/watch`, `bilibili.com/audio/au...`, SoundCloud single tracks, Bandcamp `/track/...`) automatically run in audio mode.
- **Audio default folder precedence:** explicit `--folder` → `linkget config --audio-folder [PATH]` → saved global `--folder` default → error (audio cannot be imported into macOS Photos; use `linkget config --clear-audio-folder` to clear).
- **Audio quality & metadata:** Prefers original `.m4a` (AAC) when offered or keeps the best original stream without re-encoding (`--audio-format` skips re-encoding when the source codec already matches; otherwise uses `ffmpeg`: MP3 320k, M4A 256k, Opus 160k, lossless FLAC/WAV). Filenames use `Artist - Title.ext` with non-overwriting hash deduplication and best-effort embedded tags & cover art (cover art omitted on `.opus`/`.wav`).
- Existing files are never overwritten. Download options override `linkget config` for that run only.
- Relative paths given to `linkget config --folder PATH`, `--audio-folder PATH`, or `--article-folder PATH` are resolved when saved; bare `--folder` / `--audio-folder` / `--article-folder` follows your working directory on each run.
- `--article` forces an X `/status/ID` post into offline article archive mode (or explicitly requests article mode on a public web page); `--no-assets` skips downloading embedded images and videos. X Articles (`/article/ID` or auto-detected `/status/ID`), WeChat (`mp.weixin.qq.com`), Toutiao `/article/`, Zhihu `zhuanlan.zhihu.com`, Raspberry Pi, and public HTTP(S) web articles enter article mode automatically without `--article`.
- For Auto downloads, `--article-folder PATH` picks a folder just for articles in this run. Other media use the usual destination. Folder precedence for articles is explicit `--folder`, then the one-time article folder, then the saved article or global folder.
- **Article archive layout:** each download creates a non-overwriting `YYYY-MM-DD-<site-or-handle>-<title-slug>/` folder containing `article.md` (with YAML frontmatter and local asset links), standalone responsive light/dark `article.html`, `content.txt`, `content.json`, `assets/` (cover, inline images/videos, posters, avatar), and `source/x-article.json` or `source/original.html`.

## Batch Execution & Panel

```sh
linkget batch                         # paste one URL or share text per line; blank line starts
linkget batch 'URL1' 'URL2' --folder ~/Downloads
linkget batch --folder ~/Downloads < links.txt
```

- **Concurrency:** Runs up to 3 posts at once by default (`--jobs N` overrides). Known share/mobile/desktop aliases for the same site share a single per-website slot with a 1-second gap after completion.
- **Automatic retries:** Explicit network or rate-limit errors before saving retry at most twice (1s, then 2s, or numeric server `Retry-After` up to 30s). Login, certificate, validation, pairing, and Photos import errors stop for manual action.
- **Interactive panel:** Keeps each item's input number in place with phase, current-attempt elapsed time, and size (`Waiting` means queued for serialized Photos/folder save). Redirected output and `TERM=dumb` use plain event logs; `NO_COLOR` disables ANSI color.
- **Cancellation & exit codes:** `Ctrl-C` cancels queued items, terminates child engines, and keeps unfinished files. Exit codes: `0` all succeeded, `1` any item failed, `2` invalid CLI options or empty batch, `130` cancelled.
- **Deduplication:** Identical short URLs in a single batch share resolution (keeping distinct access tokens separate).

## Tasks, Retry & Recovery

```sh
linkget tasks [TASK] [--kind download|read] [--limit N]
linkget tasks TASK --cleanup-preview  # read-only candidate check; deletes nothing
linkget retry [TASK] [--jobs 3]
linkget retry TASK --redownload       # re-fetch while protecting old staging files
linkget recover                       # list retained staging files and tasks
linkget recover TASK [--folder PATH | --photos] [--item N]
linkget recover TASK --verify         # verify Photos managed originals without importing
```

- Receipts live in `~/Library/Application Support/linkget/jobs/` with user-only permissions. Unfinished tasks retain share links/tokens for retry (zero browser cookies); completed items drop share text.
- On retry, completed items are skipped and partial native Xiaohongshu/Douyin media sets reuse verified resources. Incomplete, missing, or mismatched media are never silently imported.
- Cleanup of staging files (`~/Library/Application Support/linkget/originals/`) occurs only when writer-origin evidence and verified saved copies (byte-checked against folder outputs or Photos managed library originals) both pass. See [RELIABILITY.md](RELIABILITY.md).
- Preflight checks destination writability and (with `--live-photos`) ExifTool and Swift availability (`linkget doctor --folder PATH` or `--photos --live-photos`).

## Machine / Agent Mode (`--non-interactive`, `--json`, `inspect`)

- `--non-interactive` (or `LINKGET_NON_INTERACTIVE=1`) disables all interactive prompts and progress spinners even on a PTY, failing fast if input is missing.
- `--json` emits one structured JSON object (`schema_version: 1`) on stdout, implies `--non-interactive`, and suppresses aligned status text on stdout/stderr (`sites`, `config`, `doctor`, `inspect`, `tasks`, `download`, `batch`, `retry`, `recover`, `clean`, `compare`, `logout`, `read`).
- `linkget inspect URL_OR_PATH [--as TYPE] [--no-network] [--json]` checks routing, tools, saved sessions, destination and optional remote metadata without downloading media. See [AGENT_SKILL.md](AGENT_SKILL.md).
- Download clipping & safety caps: `--clip START-END` (e.g. `0:30-2:00`), `--max-duration DURATION` (e.g. `10m`, `90s`), and `--max-size SIZE` (e.g. `100MB`, `1.5GB`). `yt-dlp` sites enforce caps before downloading when metadata provides duration/size and download clipped ranges via `--download-sections`; all sites enforce post-download caps and `ffmpeg` clipping before saving.
- Safe stage cleanup & MCP server: `linkget clean [--dry-run | --apply] [--older-than AGE] [--json]` previews (`--dry-run` default) or deletes only completed, unlocked, verified temporary `linkget-*` staging folders older than `AGE` (default `7d`), preserving Photos unverified receipts, incomplete tasks, symlinks, and destination folders. `linkget mcp` starts a stdlib JSON-RPC 2.0 stdio MCP server exposing `inspect_url` (`article`), `download_media` (`article`, `no_assets`), `batch_download`, `read_content` (`no_assets`), `get_task`, `retry_task`, `clean_stages`, and `doctor`.
- `linkget read` also extracts timestamped video keyframes (`--frames`, `--max-frames N`, `--frame-ocr`) into `frames/`, reads general public web articles via stdlib HTML extraction, and reads WeChat Official Account articles (`mp.weixin.qq.com/s/...`) via direct HTTP with optional `/s/<id>` browser fallback. See [READ.md](READ.md).

## Evidence Reports & Live Photos

```sh
# Compare two runs (e.g. anonymous vs signed-in)
linkget 'URL' --browser none --folder ~/Downloads/public --report public.json
linkget 'URL' --folder ~/Downloads/connected --report connected.json
linkget compare public.json connected.json

# Package explicit picture/motion pairs (macOS; requires exiftool + xcode-select --install)
linkget 'URL' --folder ~/Downloads --live-photos
linkget 'URL' --photos --live-photos

# Use a BrowserOS neo Douyin snapshot without Chrome access
linkget 'https://www.douyin.com/note/POST_ID' --page-data post.json --browser none --folder ~/Downloads/check --live-photos
```

- When `ffprobe` is available, `linkget` inspects dimensions, codecs, frame rate, bitrate, and audio, and validates gallery completeness against exposed source counts.
- `--report` files exclude share query tokens, cookies, and raw request logs, and never overwrite existing files.
- `--live-photos` supports explicit picture/motion pairs exposed by Xiaohongshu, Weibo, Bilibili, and Douyin without re-encoding image pixels or video streams. Folder mode keeps originals and places paired copies in `Live Photos/`.

## Browser Connection, Reading & Auth

- **Page reading preference:** `linkget config --reader-browser auto|neo|chrome` (priority: `--rendered-backend` > `--browser chrome` > `LINKGET_RENDERED_BACKEND` > saved reader preference > `auto`).
- **Browser cookies/sessions:** Interactive runs can read Chrome, Firefox, Edge, or Safari once (Chrome/Edge may ask macOS Keychain for `Safe Storage`; Safari needs Terminal privacy permission). `--browser none` disables all browser reads; `--browser firefox --profile PATH` targets a specific profile.
- **Auth management:** `linkget auth` lists all 13 channels without login prompts; `linkget auth SITE` connects an optional login; `--check-session SITE` checks a saved snapshot without re-exporting browser profiles; `linkget logout SITE|all` removes saved sessions.
- **Diagnostics:** `linkget doctor --probe-browser neo|chrome` checks MCP handshake or OS non-prompt Chrome Automation access without opening pages. `linkget version --json` reports `BUILD_IDENTITY.json` metadata when present.

## Site-Specific Notes & Watermark Heuristics

- **X / Twitter & X Articles:** Normal photo/video posts download via `gallery-dl`; X Articles (`/article/ID` or `/status/ID` posts promoted automatically or with `--article`) extract the full GraphQL Draft.js `content_state` via `gallery-dl` with `x.com` cookies (with Chrome/BrowserOS Neo fallback), preserving UTF-16 formatting, headings, lists, code blocks, links, cover/inline media (`name=orig`), engagement metrics, and Community Notes.
- **Bilibili / YouTube:** Keeps best available MP4 streams (including HEVC/AV1) and merges audio without re-encoding. Playlist-only URLs and live/upcoming streams are out of scope.
- **Douyin / Xiaohongshu:** Prefers clean image/playback variants (`video.bit_rate` ranking for Douyin; original images/HEIC and video for Xiaohongshu). Keep full Xiaohongshu `xsec_token` links. If Douyin blocks public access, allowed fallback uses BrowserOS neo (temporary task tab) or Chrome AppleScript (`Allow JavaScript from Apple Events`).
- **Weibo:** Compares alternate JPEGs with `ffmpeg` only when dimensions are not reduced and watermark heuristics improve ([WEIBO_IMAGE_AUDIT.md](WEIBO_IMAGE_AUDIT.md)).
- **Kuaishou / Pixiv / Reddit / LOFTER / Pinterest:** Trial channels; see [MEDIA_VALIDATION.md](MEDIA_VALIDATION.md) and [COMPATIBILITY.md](COMPATIBILITY.md).
- **Photos library:** Ensure **Photos → Settings → General → Copy items to the Photos library** is enabled. Unsupported codecs remain staged rather than being silently transcoded.

## Development & Historical Handoff Notes (2026-09-29)

- Source validation uses `PYTHONPATH=src python3 -m unittest discover -s tests -q` and `uv build` (see [TESTING.md](TESTING.md)).
- The 2026-09-29 handoff verified all 8 website checks with Chrome export, automatic YouTube expired-cookie refresh in an isolated test store, and preserved the CLI-only design (`auth` for sessions, `doctor` for checks, release version in `src/linkget/version.py`).
- Historical workspace snapshot preserved under tag `archive/workspace-backup-20261010`.
