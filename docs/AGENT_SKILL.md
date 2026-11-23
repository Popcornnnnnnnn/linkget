---
name: linkget
description: Non-interactive, JSON-structured media inspection, download, audio extraction, and content transcription/reading via the local linkget CLI.
---

# Using `linkget` from an AI Agent

Always pass `--non-interactive --json` (or set `LINKGET_NON_INTERACTIVE=1`) when running `linkget` from an agent shell. Many agent runners attach a PTY (`isatty() == True`); without `--non-interactive`, missing inputs or login prompts can wait for terminal input. `--json` automatically implies `--non-interactive` and suppresses status text and spinners on stdout/stderr so stdout holds one JSON object (`schema_version: 1`). `--json` is supported on `doctor`, `inspect`, `download`, `batch`, `retry`, `recover`, `tasks`, `compare`, `sites`, `config`, `logout`, and `read` (`read`, `read batch`, `read retry`, `read recover`, `read status`, `read wait`, `read show`). Set `LINKGET_NON_INTERACTIVE=1` for helper subcommands without `--json` (`auth`, `read config`, `read doctor`, `read logs`, `read cancel`, `read translate`, `read summarize`).

## Standard Agent Workflow

1. **Check environment once:**

   ```sh
   linkget doctor --non-interactive --json
   ```

   Verifies installed tools (`gallery-dl`, `yt-dlp`, `ffmpeg`, `ffprobe`, `deno`), configured page controller, and whether site sessions exist (never reads or outputs cookies).

2. **Inspect a URL or local file before acting:**

   ```sh
   linkget inspect 'URL_OR_PATH' --json
   # Purely offline check (no short-link HTTP or remote metadata probe):
   linkget inspect 'URL_OR_PATH' --no-network --json
   ```

   Inspect creates no task receipts and downloads no media. Check:
   - `ok` / `supported`
   - `site`, `engine`, `canonical_url`
   - `audio_only` / `effective_audio_mode`
   - `destination_preview` / `destination_error`
   - `metadata.duration`, `metadata.is_live`, `metadata.filesize_approx`, `metadata.subtitles_available`
   - `tools_missing`, `session_state`, `login_required`

3. **Download media or audio/music:**

   ```sh
   # Normal download to an explicit folder (recommended on headless/Linux or repeatable scripts):
   linkget 'URL' --folder ./downloads --non-interactive --json

   # Audio-only extraction or music track download:
   linkget 'URL' --audio --audio-format mp3 --folder ./music --non-interactive --json

   # Concurrent batch:
   linkget batch 'URL1' 'URL2' --folder ./downloads --non-interactive --json
   ```

   On success (`ok: true`, exit `0`), read `task_id`, `destination`, `saved_files` (for single download), or `items` (for batch). On failure, read `error.category`, `error.message`, `error.next_step`, and `error.retryable`.

4. **Read / transcribe content into agent context:**

   ```sh
   # Synchronous read directly to stdout as Markdown, plain text, or JSON:
   linkget read 'URL_OR_PATH' --stdout --stdout-format md --non-interactive
   linkget read 'URL_OR_PATH' --foreground --json

   # Read a video with keyframes (--frames / --max-frames / --frame-ocr):
   linkget read 'VIDEO_URL_OR_PATH' --frames --max-frames 12 --foreground --json

   # Read general web articles or WeChat Official Account articles (mp.weixin.qq.com/s/...):
   linkget read 'https://mp.weixin.qq.com/s/ARTICLE_ID' --stdout --non-interactive

   # Asynchronous submit + wait + fetch:
   linkget read 'URL_OR_PATH' --json         # returns task_id immediately
   linkget read wait TASK_ID --timeout 300 --json
   linkget read show TASK_ID --format md     # or --json for raw content.json
   ```

5. **Inspect tasks and recover safely:**

   ```sh
   linkget tasks --json
   linkget retry TASK_ID --json
   linkget recover TASK_ID --json
   ```

   Never pass `--photos`, `--date-now`, or `--live-photos` with `--audio` or music-only URLs (`music.youtube.com/watch`, `bilibili.com/audio/au...`, SoundCloud tracks, Bandcamp `/track/`).

## Exit Codes & Error Categories

| Exit code | Meaning |
| --- | --- |
| `0` | Succeeded |
| `1` | Command/network/validation failure (`error` object emitted when `--json` is set) |
| `2` | Invalid CLI options (`argparse`) |
| `124` | `linkget read wait --timeout` expired (`timed_out: true`) |
| `130` | Cancelled |

Common `error.category` values: `rate_limit` (`retryable: true`), `network` (`retryable: true`), `login_required`, `login_expired`, `website_check`, `access_refused`, `unavailable`, `unsupported`, `browser_state`, `postprocessing`, `extractor_failure`, `unknown`.

## Guardrails

- Wrap every URL in single quotes (`'...'`) so shell does not expand `&`, `?`, or `;`.
- Never automate logins, bypass CAPTCHAs, or scrape browser cookies directly; if `login_required` or `website_check` is returned, surface `error.next_step` to the user.
- Do not run `rm -rf` on staging directories (`~/Library/Application Support/linkget/` or `/tmp/linkget-*`).
