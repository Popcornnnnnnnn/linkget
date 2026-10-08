# Toutiao content reading

`linkget read` produces source content for a summarizer. It does not run a hosted AI model, import into Photos or send messages. This is a separate read command; ordinary media downloads retain their existing behavior.

```sh
linkget read 'https://www.toutiao.com/video/7693844463388655644/' \
  --folder /tmp/toutiao-new-content \
  --transcribe --whisper-model '/path/to/existing/ggml-small.bin'
```

The default reader uses the same Chrome page controller as the existing Douyin fallback. It reuses an open matching post, or creates and closes a temporary tab while restoring the previous tab. It does not read browser cookies, storage, credentials or profile databases, and does not enable scripting permission. Chrome must already allow its existing page controller. This route may temporarily select a browser tab; it is not a guarantee of completely invisible browser processing.

An authorized browser connector can provide only content fields instead:

```sh
linkget read URL --page-data /tmp/chrome-content.json \
  --folder /tmp/toutiao-new-content \
  --transcribe --whisper-model '/path/to/existing/ggml-small.bin'
```

For a video, the supplied JSON has `url`, `kind: "video"`, `title`, `media_url` from the rendered player's `currentSrc`, `duration`, and optional `captions` with `text/start/end`. URLs are canonicalized across mobile/desktop and stripped of share tracking; the requested post must match. Input page evidence may contain a temporary CDN playback URL and should be kept private. Output `content.json` excludes that URL and selects only known content fields, excluding account state.

Videos always require local transcription. The command downloads only the rendered player's Toutiao CDN media, checks its audio and duration against the player, processes the whole audio, and saves both recognized speech and timed segments. `description` is separate from `text`. `content_complete` means complete video audio was processed, not that every word was recognized accurately; `recognition_verified` remains false. Review uncertain words and claims before automated publication. Failures do not report summary-ready content. Downloaded video, audio and duplicate transcript files are cleaned on success and failure; the durable `content.json` retains speech and timed segments. Output folders are create-only.

Use `--keep-media` only when checking uncertain recognition against the video. After review call `linkget.toutiao.cleanup_media(Path(folder))`; it removes only `source.mp4`, `audio.wav`, `transcript.txt` and `transcript.json`, refuses symlink substitutions, and preserves summaries, text evidence and models. The caller remains responsible for its temporary page-input JSON and review frames. Browser `blob:` players are currently unsupported; do not summarize their titles as if the video had been read.

For an article, `kind: "article"`, `title`, `text` and `content_complete: true` can be supplied after reading the rendered `article` element. Title-only and very short snippets are refused. Article support has unit coverage, but a live article has not been validated in this checkpoint.

## Verified 2026-10-08

- The daily Chrome connector visibly showed the existing Toutiao login; no new login or browser credential export was performed.
- The group-shared video `7693844463388655644` was read through that connector. Automatic playback later navigated to another post, confirming the need for exact post-identity checks; it was returned to the requested post before capturing the final evidence. The temporary task tab was closed.
- The installed CLI `read --page-data` path was exercised on the requested video, including a fresh media fetch, ffprobe checks, full local Chinese transcription, and a source-based concise summary prepared in the WeChat project. Runtime results and command versions are recorded there. The default AppleScript reader is covered by the existing controller pattern and parser tests; its Toutiao live invocation has not been exercised here.
- Neither LinkGet nor the WeChat summary sample enabled unattended processing or sent a summary into the group. The previously verified group sender remains a separate component.
