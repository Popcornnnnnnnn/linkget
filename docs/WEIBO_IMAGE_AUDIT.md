# Weibo image rendition batch audit — 2026-09-30

## Findings

Tested 19 JPEG images from 7 posts by 7 distinct authors, spanning 2018–2026. Subjects included cycling, a television photograph, screenshots, a long text image, cats, cosplay, food and game hardware. Samples were selected for accessible public posts and variety, not randomly; this cannot establish platform-wide frequencies.

- All 17 images wider than 690 pixels in the `large` rendition became **690 pixels wide** in `oslarge`, with proportionally scaled height (integer rounding).
- The two narrower images (440×6958 and 550×309) kept their dimensions. This is a width limit in these samples, not a longest-edge limit.
- All 19 `original` responses were byte-for-byte identical to `large`; changing that path did not provide a better original.
- `mw690` had the same dimensions as `oslarge` in all 19 cases, but only one pair was byte-for-byte identical. Visual controls show that ordinary `mw690` can still carry the current author's watermark, while `oslarge` omits that added layer.
- In 13 marked `large` images, the current publishing account's added watermark was absent from `oslarge`. Two of those images still contained an older watermark from the image's earlier source. An `oslarge` response is therefore not proof that the image contains no watermark.
- Six `large` images had no visible platform watermark in inspection; five of these still lost resolution under the current unconditional replacement. This is a confirmed limitation of linkget 0.2.0b11, not an unavoidable requirement for removing watermarks.

**Product implication:** do not describe every replacement as a successful watermark removal. Version 0.2.0b11 prioritized an alternate rendition without detecting whether the larger image already lacked a mark. It could unnecessarily downgrade clean originals. See the subsequent fix verification below.

For example, a 3024×4032 cat photo already lacked a visible platform watermark, yet its selected alternate was 690×920: only about 5.2% as many pixels. Pixel count is not a percentage measure of perceived quality.

## Method

Public post metadata was read from Weibo's own `ajax/statuses/show` endpoint with the existing scoped saved Weibo session. CDN media requests used no account cookie. Nine candidate post URLs were queried; two returned no usable author/image metadata and were excluded rather than counted as passes. Each accessible post contributed up to five JPEGs, for 19 distinct images.

For each image, `large`, `oslarge`, `mw690` and `original` were downloaded from the same CDN host and image identifier. All 76 responses decoded successfully; dimensions, byte lengths and SHA-256 equality were checked. Full-image thumbnails and native-pixel bottom-right crops of every `large`/`oslarge` pair were inspected. The 440×6958 text image's interior was not inspected at native scale throughout. This is a visible-watermark review, not detection of invisible marks or proof about every pixel.

Four controls (#1, #7, #9 and #17) also tested `bmiddle`, `mw2000`, `orj360`, `orj1080` and an intentionally nonexistent rendition name. The first four returned usable images; the nonexistent name failed for all four, rather than silently returning `oslarge`. These checks do not reveal Weibo's internal implementation or establish that no other full-size clean source exists.

Post candidates came from the user's reported example and the public [gallery-dl Weibo extractor fixtures](https://github.com/mikf/gallery-dl/blob/master/test/results/weibo.py). The downloaded CDN files, not fixture expectations, supplied the results below.

## Per-image results

`large` means the downloaded native large rendition, not a verified copy of the author's upload. Sizes are width × height. Residual marks in #5–6 refer to the earlier source's mark, not the current posting account's added mark.

| # | Author / post | Image | large | oslarge | Visible result |
| --- | --- | --- | --- | --- | --- |
| 1 | [塔库米Hg](https://weibo.com/5595931769/RklUpAkt0) | 1 | 896×1120 | 690×863 | Current account mark absent |
| 2 | [塔库米Hg](https://weibo.com/5595931769/RklUpAkt0) | 2 | 824×1136 | 690×951 | Current account mark absent |
| 3 | [塔库米Hg](https://weibo.com/5595931769/RklUpAkt0) | 3 | 896×792 | 690×610 | Current account mark absent |
| 4 | [麻衣样一途](https://weibo.com/detail/4323047042991618) | 1 | 1334×750 | 690×388 | Already no visible platform mark; unnecessarily smaller |
| 5 | [凤凰网科技](https://weibo.com/detail/4600167083287033) | 1 | 1125×966 | 690×592 | Current account mark absent; earlier source mark remains |
| 6 | [凤凰网科技](https://weibo.com/detail/4600167083287033) | 2 | 440×6958 | 440×6958 | Current account mark absent; earlier source mark remains |
| 7 | [喵呜不停](https://weibo.com/3194672795/OuxSwgUrC) | 1 | 3024×4032 | 690×920 | Already no visible platform mark; unnecessarily smaller |
| 8 | [喵呜不停](https://weibo.com/3194672795/OuxSwgUrC) | 2 | 3024×4032 | 690×920 | Already no visible platform mark; unnecessarily smaller |
| 9 | [优雅Yoya-](https://weibo.com/2909128931/4409545658754086) | 1 | 4032×2690 | 690×460 | Current account mark absent |
| 10 | [优雅Yoya-](https://weibo.com/2909128931/4409545658754086) | 2 | 2690×4032 | 690×1034 | Current account mark absent |
| 11 | [优雅Yoya-](https://weibo.com/2909128931/4409545658754086) | 3 | 4032×2268 | 690×388 | Current account mark absent |
| 12 | [优雅Yoya-](https://weibo.com/2909128931/4409545658754086) | 4 | 2690×4032 | 690×1034 | Current account mark absent |
| 13 | [优雅Yoya-](https://weibo.com/2909128931/4409545658754086) | 5 | 4032×2074 | 690×355 | Current account mark absent |
| 14 | [我妻洛醬](https://weibo.com/1919017185/4246199458129705) | 1 | 960×1280 | 690×920 | Current account mark absent |
| 15 | [我妻洛醬](https://weibo.com/1919017185/4246199458129705) | 2 | 1280×960 | 690×518 | Current account mark absent |
| 16 | [我妻洛醬](https://weibo.com/1919017185/4246199458129705) | 3 | 960×1280 | 690×920 | Current account mark absent |
| 17 | [游侠网](https://weibo.com/1893905030/Q9yKt97ID) | 1 | 550×309 | 550×309 | Already no visible platform mark; unchanged dimensions |
| 18 | [游侠网](https://weibo.com/1893905030/Q9yKt97ID) | 2 | 1080×607 | 690×388 | Already no visible platform mark; unnecessarily smaller |
| 19 | [游侠网](https://weibo.com/1893905030/Q9yKt97ID) | 3 | 2384×1334 | 690×386 | Already no visible platform mark; unnecessarily smaller |

## Interpretation and limits

The repeated `lower resolution` notices are explained by the observed maximum width of 690 pixels on the selected `oslarge` rendition. They do not mean that linkget compressed the files after download. Watermark handling and resize rules belong to the rendition: `mw690` and `oslarge` can have equal dimensions and different marks.

Weibo has not been shown to document the purpose or internal generation pipeline of `oslarge`. The suggestion that it serves a particular client or a historical pipeline remains an inference, not a verified explanation. This test concerns JPEG post images, not PNG, GIF, video, every CDN host or every account setting.

Only this written report is retained. Temporary downloads, contact sheets and raw metadata were removed after inspection; no Photos imports, browser UI changes or saved-session edits were performed.

## 0.2.0b12 fix verification

The same 19 images were fetched again and passed through the production comparison with the installed ffmpeg decoder. All six images previously judged visually free of platform marks retained their large rendition. All 13 images with the current account's added mark selected the alternate. The two images with earlier source marks still retain those marks; no general erasure is claimed.

The comparison decodes `mw690` and `oslarge` at their matching dimensions, discounts small differences, and requires strong changes to be localized in a short horizontal band. It then checks whether the actual large image, resized only in memory for comparison, resembles the marked reference at those changed pixels. This prevents a marked preview alone from downgrading a clean large image. Actual output files are never re-encoded. Missing comparison tools, invalid renditions, incompatible geometry, timeouts and ambiguous changes retain original quality.

Two live CLI checks also verified end-to-end source selection: the cat post retained both 3024×4032 JPEGs, and the reported cycling post selected the three alternate images at 690×863, 690×951 and 690×610. Downloads were directed to temporary folders, not Photos.

This is a conservative heuristic verified against these samples and synthetic regression scenarios. It can miss faint or unusual watermark layouts; ambiguous cases retain the large image with a notice instead of sacrificing detail. Passing this set is not proof of accuracy for all Weibo images.

## 0.2.0b13 requirement correction: full resolution AND watermark improvement

The user clarified that reduced resolution is not an acceptable substitute for removing a watermark. The b12 policy therefore did not satisfy the request, even when its image-comparison decisions matched the visual review.

The output selection now refuses any candidate whose width or height is below the native large image's dimensions, even if it has a demonstrated watermark improvement. No upscaling, synthesis, patching or output re-encoding is used. The smaller rendition can be used for comparison only. If no eligible alternative is found, the native file is retained and the CLI explicitly states that watermark removal was unavailable at that resolution. This fixes the incorrect downgrade; it does not claim to solve full-resolution watermark removal for the reported post.

Additional direct-source checks on the reported first cycling image:

| Request | Returned size | Result |
| --- | --- | --- |
| Native `wx4.sinaimg.cn/large/…` | 896×1120 | Marked baseline |
| Alternate `lz.sinaimg.cn/large/…` | 896×1120 | Byte-for-byte identical to marked baseline |
| Weibo `ajax/common/download?pid=…`, with existing scoped login | 896×1120 | Byte-for-byte identical to marked baseline |
| Native `wx4.sinaimg.cn/oslarge/…` | 690×863 | Smaller alternative, ineligible for output |
| Alternate `lz.sinaimg.cn/oslarge/…` | 690×863 | Byte-for-byte identical to the smaller native alternative |

The inspected [public Weibo script](https://github.com/fmz200/wool_scripts/blob/main/Scripts/weibo/weibo_main.js) also labels its `oslarge` substitution as lower quality. The inspected [image parser](https://github.com/5ime/images_spider/blob/master/src/images_spider.php) returns `lz.sinaimg.cn/oslarge/…`, not evidence of a separate full-resolution source. No third-party session credentials were used. These checks found no full-resolution clean alternative for this image; they do not prove that none exists anywhere.

The public [SnapAny Weibo service](https://snapany.com/weibo), which advertises full-size unmarked photos, was also queried anonymously with the public post URL. It returned direct Weibo `osj1080` URLs. Actual decoded dimensions were still 690×863, 690×951 and 690×610. The first direct request using the service's specified empty User-Agent likewise remained 690×863. A rendition name containing `1080` is not evidence of a 1080-pixel file; this route did not satisfy the requirement. The service was not integrated into linkget, and no personal cookies were sent to it.

The b13 live CLI check retained the original three image dimensions (896×1120, 824×1136, 896×792), verified the first file byte-for-byte against the native source, and printed that watermark removal was unavailable at that resolution. This validates refusal of the downgrade, not successful full-resolution removal for that post.

## Further recovery experiments — 2026-09-30

The user requested further attempts before accepting the original-quality fallback. No production behavior was changed in this round.

### Alternative full-size images and complementary pixels

Two marked samples were checked: the reported 896×1120 cycling photo and a 4032×2690 photo from the existing audit. Six requests per sample compared `large`, `orj1080`, `mw2000`, `oslarge`, and the same `large` identifier with `.png`/`.webp` extensions. The extension alternatives returned HTTP errors for both samples. The larger photo's `orj1080` and `mw2000` renditions were smaller, at 1619×1080 and 2000×1334.

For the cycling photo, `large`, `orj1080` and `mw2000` all decoded at 896×1120, but had different JPEG sizes (1,038,043; 249,338; 428,837 bytes). Native-pixel crops showed the watermark at the same location in all three. Of 451 high-contrast watermark pixels identified against the aligned smaller clean reference, all still differed strongly from that reference in both additional full-size renditions. These copies do not expose complementary clean pixels for that tested region; their equal dimensions do not imply equal compression quality.

An additional open-source parser was inspected: [CurrentCortex's Weibo parser](https://github.com/backrooms-yrc/astrbot_plugin_currentcortex/blob/main/media_parser.py) returns the mobile API's `pics[].large.url`, not a documented separate clean master. A live mobile-API request for the reported post did not return parseable JSON, so that route remains unverified rather than being counted as proof that no source exists.

### One actual generative repair attempt: rejected

A built-in image editing model was given the 896×1120 native photo plus the genuine 690×863 clean reference, instructed to repair only the bottom-right watermark and preserve the native canvas and all other pixels. The watermark was visually removed, but the output was 1122×1402 and altered the image beyond the requested region. After resizing the result only in memory for comparison, mean absolute RGB error outside the requested edit region was 38.04/255; 62.8% of those pixels had mean channel error greater than 10. These figures are a pixel-comparison diagnostic, not a perceptual quality score. Visual inspection also showed changes to subject and equipment detail.

This result fails the preservation requirement and was not integrated into linkget or imported into Photos. It is an edited reconstruction, not a recovered original. The rejected generated file and all temporary source/comparison files were removed after inspection.

### Research directions are not verified fixes

[Google's multi-image watermark study](https://research.google/pubs/on-the-effectiveness-of-visible-watermarks/) demonstrates estimation and inversion of consistent semi-transparent marks across image collections. That supports treating some watermark recovery as a research possibility, not asserting that all cases are impossible. It does not establish successful recovery of these Weibo photos. A public [Weibo-specific neural restoration project](https://github.com/ming19956/n2n-weibo-watermark-remove-master) was inspected but not executed or integrated; its published training instructions and checkpoints alone do not establish fidelity on the current samples.

**Current conclusion:** no method verified in these experiments meets both full original quality and watermark removal for the reported photo. Retain the native high-quality file under b13. This is a practical validation result, not a claim of mathematical impossibility or that every possible source has been exhausted.
