# Media source and watermark audit

Checked locally for 0.2.0b11 on 2026-09-30. Download success and session verification do not establish whether a file contains a watermark. The checks below inspected actual downloaded media.

## Selection policy

Prefer usable sources without added platform marks. If none can be obtained, retain the available media instead of refusing the whole post. Douyin, Xiaohongshu and Weibo report known source fallbacks. This is not a visual watermark detector: logos burned into an upload can still appear, including on an otherwise clean rendition. There is no pixel erasure, cropping, blurring or re-encoding.

Weibo's alternate image rendition can be smaller than its marked image. Since 0.2.0b13, selection requires both a supported watermark improvement and no reduction in either dimension. Smaller alternatives are never saved as the output; the original file is kept with a notice when watermark removal at its resolution is unavailable. Version 0.2.0b12 still allowed smaller output and did not satisfy the requested quality requirement. Video downloads preserve source quality. Previously saved files and Photos items are not changed; redownload to apply new source selection.

## Actual samples

| Channel | Images | Video | Remaining limitation |
| --- | --- | --- | --- |
| Weibo | All three photos in the reported post checked; `oslarge` had no visible Weibo mark. First photo changed from 896×1120 to 690×863. | Downloaded a 53-second clip; sampled frames contain the embedded Miaopai/秒拍 logo. | No usable clean video alternative found for that sample. Retain it and disclose possible embedded marks. |
| Instagram | One 1080×1350 photo checked, no visible Instagram mark. | Reel sampled, no visible Instagram mark. | Other uploads may contain embedded logos. |
| X | One 5568×3712 NASA photo checked, no visible X mark. | NASA Artemis video sampled; no X mark, but the NASA logo remains. | Publisher branding is part of the sampled video. |
| Bilibili | One 4096×3072 opus photo and its live-photo video checked/sampled, no visible Bilibili mark. | A regular video sampled; no platform mark seen, credits/subtitles remain. | Use highest available native quality; embedded marks in other videos may remain. |
| TikTok | Two photos (1290×824 and 1440×2470) checked, no visible TikTok mark. | One 1080×1920 video sampled, no visible TikTok stamp. | Selects playback media, not the marked download variant; embedded captions remain. |
| Douyin | Ten photos from one gallery checked, no visible Douyin mark. | One video using ordinary `/play/` sampled, no visible Douyin stamp. | Earlier user-provided gallery was unavailable to the public request; no watermark conclusion for that unavailable post. |
| Xiaohongshu | All three images in the reported note checked (two original HEIC and one JPEG), no visible Xiaohongshu mark. | Reported cycling video downloaded from the exposed original source and sampled, no visible Xiaohongshu mark. | If originals are unavailable, display media may retain marks; a fallback notice is printed. |
| YouTube | Not applicable to the supported video route. | “Me at the zoo” downloaded and sampled, no visible YouTube mark. | Earlier extractor test video was unavailable and was not counted as a pass. |

Videos were inspected at approximately 10%, 50% and 90% of duration, not frame by frame. Results apply to these samples; they do not certify every post on a platform. HEIC images were converted only for temporary visual inspection; downloaded originals were not altered.

## Weibo investigation

**Batch follow-up:** [19-image comparison across seven authors](WEIBO_IMAGE_AUDIT.md) found that all 17 images wider than 690 pixels were reduced to 690 pixels wide by `oslarge`; two narrower images retained their dimensions. Five images that already lacked a visible platform mark were unnecessarily downsized by 0.2.0b11's unconditional replacement. Version 0.2.0b12 retained all six already-clean originals in the same sample set. Two alternate images retained older source watermarks. Do not interpret a successful `oslarge` request as proof of watermark removal or retained original quality.

For the reported JPEG, `large`, `original`, `mw2000` and `orj1080` still contained the platform mark. `oslarge` returned the usable clean rendition. Other attempted original-size variants did not return usable JPEGs. This does not establish that every Weibo image has a clean full-resolution source.

The video sample exposed SD/HD streams with the Miaopai logo. A separately exposed HEVC HLS source failed to download in this environment; it cannot be described as a verified clean alternative or proof that removal is impossible.

## Validation and cleanup

Automated regressions cover missing sources, HTTP rejection, invalid/truncated clean renditions, per-item fallback, reduced-resolution reporting and temporary metadata cleanup. Live checks used temporary folders and existing scoped sessions; they did not import into Photos or alter browser login state. Downloaded samples, inspection frames and parsing artifacts were removed after inspection. No cookies or account identifiers are included in this report.
