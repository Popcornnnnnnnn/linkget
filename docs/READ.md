# Reading content

`linkget read` saves source text for a summarizer. For videos it downloads the post through the existing channel adapter, extracts audio and transcribes speech using an existing local whisper.cpp model. It does not generate the summary itself. It never imports into Photos or sends messages.

```sh
linkget read 'https://www.bilibili.com/video/BV_ID?p=2' \
  --folder ~/Downloads/bilibili-content \
  --transcribe --whisper-model '/path/to/existing/ggml-small.bin' \
  --language zh

linkget read 'https://youtu.be/VIDEO_ID' \
  --folder ~/Downloads/youtube-content \
  --transcribe --whisper-model '/path/to/existing/ggml-small.bin' \
  --language auto
```

Replace the example IDs with real video IDs. The output folder must be new; repeat runs never overwrite content. Both `--transcribe` and `--whisper-model` are required for videos. The model and `ffmpeg`, `ffprobe`, `whisper-cli` must already exist; this command does not download tools or models. The model must support the selected language; English-only models cannot read Chinese speech.

## Channels

Video posts use the existing downloader for Bilibili, YouTube, Instagram, X/Twitter, TikTok, Douyin, Kuaishou, Xiaohongshu, Weibo, Reddit, LOFTER and individual Pinterest pins. Bilibili `p=N` reads the selected part and retains the part number in its source reference. Short links and quoted sharing text follow the same normalization as downloads; Pinterest requires a full `/pin/ID/` link and LOFTER requires a `/post/ID` link. Channel availability and account restrictions remain those of the ordinary downloader. Pinterest boards, static Pixiv artwork, livestreams and whole-account downloads are outside this reader.

Toutiao uses its existing rendered Chrome page reader for article text and video speech; see [Toutiao reading](TOUTIAO.md). `--page-data` is exclusive to Toutiao. Other channels read video speech, not article bodies or photo captions, and do not perform OCR or describe visual-only events.

`--browser auto` (default) reuses the ordinary public/saved-login flow, including browser assistance when needed. `--browser none` uses public access only. An explicit browser and optional `--profile` select the existing download login flow. The reader skips first-run bulk browser setup. Toutiao uses daily Chrome rather than the ordinary download login flow; browser/profile choices do not switch its page controller.

## Output and completeness

`content.json` contains `text`, `videos` (one entry per ordinary video with `text`, `segments`, `media_evidence`), a token-free source `reference`, and available publisher `title`/`description`. Each video's segment times start at its own zero. Titles and descriptions are separate from recognized speech. Multiple video texts appear in download filename order, with an explicit index and source filename. Images and known Live Photo motion are ignored and counted.

Every ordinary video must pass media validation, contain audio, and be at most 30 minutes long. The reader transcribes the whole audio rather than a preview. If independent source duration is available, it also checks that downloaded duration matches. It commits `content.json` only after all ordinary videos succeed. A failed or cancelled read does not report summary-ready content.

`content_complete: true` means the downloaded videos' full audio was processed. It does not prove every word is correct, that a website exposed every video in a post, or that visual information was read. `source_count_verified` identifies whether the adapter exposed a checked gallery count. `recognition_verified` remains false; check names, numbers and uncertain claims against the source. Audio without recognizable speech is rejected rather than substituting a title. Automatic recognition can still misrecognize music or silence.

Language defaults to automatic detection on ordinary video channels, while Toutiao retains its Chinese default. Use `--language zh` or `--language en` to fix the spoken language. Recognition timeouts scale with video length.

Temporary downloaded media and duplicate recognition files are removed on success or failure. `--keep-media` retains selected review videos, audio and transcripts under `media/video-001/`, etc.; remove that generated `media` folder after review. Toutiao retains its existing root-level intermediate layout and cleanup method. Ordinary media downloads retain their normal failure recovery behavior; read failures clean their private temporary download stages.

## Validation

Automated coverage exercises every existing video channel route through the downloader-to-reader boundary, selected Bilibili parts, independent multi-video segments, language forwarding, image/Live Photo exclusion, missing audio, preserving existing folders, failure cleanup and preventing partial content commits. These use fixtures and are not live website acceptance.

On 2026-10-08, Bilibili `BV1bK411W797?p=1` was fetched with an existing configured login: 1280×720 HEVC with audio, 90.302 seconds. The small multilingual local model with automatic language detection produced 33 Japanese segments (687 text characters), preserving the selected part in the reference and cleaning intermediate media. This verifies the download-to-text pipeline, not manual transcription accuracy. The same sample's public-only request returned no formats and stopped without content. A music-only sample exposed `(歌詞)` labels; final recognition validation rejects results made only of such labels. Other video channels have routing/reader fixture coverage, not fresh live speech-transcription acceptance at this checkpoint.
