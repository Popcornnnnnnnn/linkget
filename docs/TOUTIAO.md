# Toutiao content reading

`linkget read` produces source content for a summarizer. It does not run a hosted AI model, import into Photos or send messages. This is a separate read command; ordinary media downloads retain their existing behavior.

```sh
linkget read 'https://www.toutiao.com/video/7693844463388655644/' \
  --folder /tmp/toutiao-new-content \
  --transcribe --whisper-model '/path/to/existing/ggml-small.bin'
```

The page reader uses the shared user preference `linkget config --reader-browser auto|neo|chrome`. Product default `auto` follows the saved browser choice or system default. Only Chrome and Neo DOM controllers exist; unsupported defaults produce a clear error instead of switching browsers. Explicit `--rendered-backend` takes precedence over `--browser chrome`, then `LINKGET_RENDERED_BACKEND`, then the saved preference. The Neo controller uses its already-running local MCP connection, creates a task-owned tab, binds content to the exact requested post and verifies cleanup, including cancellation. Neither controller silently falls back to the other. No cookies, storage, credentials or profile databases are exported and no browser permissions are enabled. `--browser none` disables rendered access; `--page-data` remains an offline connector path.

An authorized browser connector can provide only content fields instead:

```sh
linkget read URL --page-data /tmp/chrome-content.json \
  --folder /tmp/toutiao-new-content \
  --transcribe --whisper-model '/path/to/existing/ggml-small.bin'
```

For a video, the supplied JSON has `url`, `kind: "video"`, `title`, `media_url` from the rendered player's `currentSrc`, `duration`, and optional `captions` with `text/start/end`. URLs are canonicalized across mobile/desktop and stripped of share tracking; the requested post must match. Input page evidence may contain a temporary CDN playback URL and should be kept private. Output `content.json` excludes that URL and selects only known content fields, excluding account state.

Videos always require local transcription. The command downloads only the rendered player's Toutiao CDN media, checks its audio and duration against the player, processes the whole audio, and saves both recognized speech and timed segments. `description` is separate from `text`. `content_complete` means complete video audio was processed, not that every word was recognized accurately; `recognition_verified` remains false. Review uncertain words and claims before automated publication. Failures do not report summary-ready content. Downloaded video, audio and duplicate transcript files are cleaned on success and failure; the durable `content.json` retains speech and timed segments. Output folders are create-only.

Use `--keep-media` only when checking uncertain recognition against the video. After review call `linkget.toutiao.cleanup_media(Path(folder))`; it removes only `source.mp4`, `audio.wav`, `transcript.txt` and `transcript.json`, refuses symlink substitutions, and preserves summaries, text evidence and models. The caller remains responsible for its temporary page-input JSON and review frames. Blob-backed players require exposed direct or paired media resources with the existing transfer and timing evidence; a title or player surface alone is not readable video content.

For an article, `kind: "article"`, `title`, `text` and `content_complete: true` can be supplied after reading the rendered `article` element. Title-only and very short snippets are refused. A live public article was validated on 2026-10-22 through the installed default Neo reader; this is sampled acceptance, not a guarantee for every post.

## Verified 2026-10-22

The installed default Neo reader completed one publicly sourced article. The existing public video sample exposed a delayed close-confirmation race; after the bounded read-only cleanup-confirmation fix, the normal CLI completed its 38.383991-second video with a full 3,439,671-byte HTTP response, full local audio transcription and 12 timed segments. The reader confirmed closed cleanup without credential export. `recognition_verified` remains false: transfer/audio coverage does not prove word accuracy. Test media were retained with `--keep-media`; no Photos import was involved.

## Historical verification 2026-10-08

- The daily Chrome connector visibly showed the existing Toutiao login; no new login or browser credential export was performed.
- The group-shared video `7693844463388655644` was read through that connector. Automatic playback later navigated to another post, confirming the need for exact post-identity checks; it was returned to the requested post before capturing the final evidence. The temporary task tab was closed.
- The installed CLI `read --page-data` path was exercised on the requested video, including a fresh media fetch, ffprobe checks, full local Chinese transcription, and a source-based concise summary prepared in the WeChat project. Runtime results and command versions are recorded there. The default AppleScript reader is covered by the existing controller pattern and parser tests; its Toutiao live invocation has not been exercised here.
- Neither LinkGet nor the WeChat summary sample enabled unattended processing or sent a summary into the group. The previously verified group sender remains a separate component.
