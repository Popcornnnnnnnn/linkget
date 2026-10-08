# Reading content

`linkget read` reads a single supported post, or a local audio/video/image file. It saves complete source text, timestamps and exports without importing into Photos. Reading is local; only an explicit `summarize` or `translate` command sends saved text to your configured model service.

```sh
linkget read 'https://www.bilibili.com/video/BV1bK411W797?p=1'
linkget read ~/Downloads/recording.m4a --language zh
linkget read ~/Downloads/screenshot.png
linkget read 'POST_URL' --formats md,txt,srt,vtt --ocr
```

The default output is a new folder under `~/Downloads/linkget-read/`. `--folder NEW_DIR` selects a new folder. Existing content and edited exports are preserved. `content.json` is always saved; Markdown, TXT and SRT are the defaults. Multiple videos get separate subtitle files and independent timelines. `--formats md,txt,srt,vtt` selects all exports.

## Saved defaults and prerequisites

```sh
linkget read config --model '/path/to/ggml-small.bin' --language auto \
  --output ~/Downloads/linkget-read --formats md,txt,srt,vtt
linkget read config --clear language --clear output
linkget read doctor
```

Settings are kept in the private linkget application-support directory, separate from download preferences. The existing multilingual Photo Search small model is discovered on this Mac if no model is configured. No tools or models are downloaded automatically. Local recognition needs `ffmpeg`, `ffprobe`, `whisper-cli` and a compatible existing model. OCR uses macOS Vision, compiling a small local helper with the existing Swift toolchain. `read doctor` checks the recognition tools and model.

Language defaults to `auto`; Toutiao keeps `zh` unless you supply or save another language. Use a multilingual model for Chinese/Japanese; an English-only model cannot recognize these languages.

## Subtitles and channels

The reader first checks the selected video's publisher subtitles, then platform automatic captions, then full local audio recognition. `content_basis` records `publisher_subtitles`, `platform_auto_subtitles` or `full_video_local_asr`. It checks caption language, timestamps and first/last coverage against source duration. That check does not prove captions contain every spoken word. If automatic language cannot be established, it uses recognition rather than choosing an unrelated translated subtitle track.

`--subtitles never` forces recognition. `--subtitles only --language zh` requires complete available Chinese captions and fails otherwise. Subtitle-only success does not download video or require a Whisper model. Recognition requests an audio-only source when the existing channel engine supports one (`ba/b` for yt-dlp); custom channel adapters can still require a whole video. `--keep-media` instead retains media for review.

Video routes: Bilibili, YouTube, Instagram, X/Twitter, TikTok, Douyin, Kuaishou, Xiaohongshu, Weibo, Reddit, LOFTER and individual Pinterest pins. Bilibili `p=N` reads and retains the selected part. Short links and sharing text follow download normalization. Pinterest requires a `/pin/ID/` link; LOFTER requires a `/post/ID` link. Availability and account restrictions follow each existing downloader. Livestreams, playlists, boards, profiles and Pixiv artwork URLs are not supported by this reader.

Toutiao uses daily Chrome to read rendered article text or obtain the player video, with the existing channel transfer bound (200 MiB / 60 seconds). `--page-data` accepts content-only Toutiao page JSON; see [Toutiao](TOUTIAO.md). It does not switch to another browser through `--browser`.

Other channels retain the publisher title/body when the adapter exposes them. Image-only posts use local OCR; mixed video/image posts add OCR with `--ocr`. JSON separates publisher descriptions, recognized speech and image text. Markdown includes image text and source anchors. Text-only social posts without downloadable media are not universally supported. OCR and recognition accuracy remain unverified; they do not describe visual-only events. Known Live Photo motion companions are excluded and counted.

`--browser auto` reuses the existing public/saved-login flow. `--browser none` uses public access only. Explicit browser/profile selection follows ordinary download login behavior. Reading skips first-run bulk browser setup.

## Long recordings, batches, cache and recovery

```sh
linkget read ~/Downloads/long-recording.mp4 --chunk-seconds 600
linkget read batch 'URL_1' 'URL_2' ~/Downloads/recording.wav
linkget read batch --folder ~/Downloads/read-batch < links.txt
linkget read recover
linkget read retry '/path/to/unfinished/read-folder'
```

Recognition processes the full downloaded audio in 10-minute chunks by default; the old 30-minute recognition cap is removed. `--chunk-seconds` accepts 30–1800. Each completed chunk is saved with media/model/language identity and absolute per-video timestamps. Failure or cancellation keeps selected media and completed chunks. Retry checks retained file hashes, resumes completed work and does not need the original local file if retained media is intact. Successful reads clean the generated `media` folder unless `--keep-media` was used. Checkpoint metadata remains for completion history.

Read batches run sequentially, continue after individual failures and return a failure exit code if any item failed. A batch folder has `item-001`, etc. Only fully successful items get `content.json`; there is no partial summary-ready result.

Complete text is cached privately for 24 hours. Source identity (including Bilibili part), language, model contents, chunk size, OCR/subtitle choices and optional VAD model identify the cache. Local files also use a content hash. `--refresh` bypasses the cache; remote posts changed within 24 hours require this flag. Cache files store text and metadata, not media URLs or login information. Use `--vad-model PATH` only with an existing compatible whisper.cpp VAD model. Music/silence labels alone are rejected; repeated phrases are flagged in `suspicious_segments`, not silently deleted.

`content_complete` means the chosen text source or the full selected audio finished processing. It does not guarantee word accuracy or that a channel exposed every item. `source_count_verified` records whether the adapter supplied an independent gallery count; `recognition_verified` remains false. Check names, numbers, lyrics and flagged phrases against the original.

## Explicit summary and translation

```sh
linkget read config --ai-provider compatible --ai-model YOUR_MODEL \
  --ai-endpoint https://YOUR_SERVICE/v1 --ai-key-env YOUR_KEY_ENV
# Alternatively: --ai-provider openai, or --ai-provider ollama with a local model.
linkget read summarize '/path/to/read-folder' --language zh
linkget read translate '/path/to/read-folder' --language en
```

Keep keys in the named environment variable; do not put them in URLs or configuration files. OpenAI uses Responses with `store: false`; compatible services use Chat Completions; Ollama uses its local `/api/chat`. HTTPS is required except loopback HTTP. Redirects are refused and unknown request outcomes are not automatically retried. The command submits all speech units, exposed publisher descriptions and OCR text in bounded batches. Summary points require valid source IDs; translation requires every source unit exactly once. Outputs link to source timestamps or original text and preserve `content.json`. This validates reference structure, not whether the model's claims are accurate.

A service and model must be explicitly configured. Source acquisition never invokes a cloud model automatically. Real summary/translation acceptance requires a working service configuration; local HTTP fixtures only validate request/response integration.

## Mac Share menu

[Mac application and Share extension](MAC_SHARE.md) provide Read / Download and an explicit Start button. They use the installed CLI.

## Validation, 2026-10-08

245 automated tests passed, including existing download/recovery tests, selected-part reads, subtitle priority/coverage, multi-video exports, >30-minute recognition checkpoints, failure recovery after deleting the original local file, modified-media rejection, batch continuation, cache invalidation, image/description references and all three AI HTTP protocol paths. These fixtures do not establish website or model availability.

Fresh real samples succeeded on Bilibili `BV1bK411W797?p=1`, YouTube `jNQXAC9IVRw`, Weibo `RkpghmW5K`, Douyin `7675556744459143529` and Xiaohongshu `6abb97e1000000000200fd74`, through existing configured login/public paths. All produced Markdown/TXT/SRT/VTT from full local ASR. Xiaohongshu's sample contains song lyrics. These results verify acquisition/export, not manual transcription accuracy. Other routes have fixture coverage, not fresh live acceptance here.

The installed command passed local Chinese/English image OCR and a generated 65-second local audio fixture divided into three chunks. A generated 768-second desktop task was cancelled and then successfully recovered through the installed CLI. Long audio beyond 30 minutes is validated with fixtures; no >30-minute real recording was manually reviewed. YouTube `dQw4w9WgXcQ` also passed publisher-caption-only acquisition in English, without downloading media or running ASR. An actual AI service remains unverified. Raw sample logs and signed URLs stay in ignored local evidence, outside Git.
