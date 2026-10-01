# Real media acceptance corpus

The opt-in corpus in `tests/fixtures/media_cases.json` covers all 13 channel entries. It contains 29 public post URLs and three explicit coverage boundaries. A local, ignored extension adds two Xiaohongshu cases whose share links contain access tokens. Do not commit those tokens, cookies or downloaded media.

## Reproduce

Install the normal downloader dependencies, then run from the repository root:

```sh
PYTHONPATH=src python3 scripts/validate_media.py --compare-saved
# Restrict a rerun to selected channels:
PYTHONPATH=src python3 scripts/validate_media.py --sites instagram x --compare-saved
# Include privately held share links; the file has the same {"cases": [...]} schema:
PYTHONPATH=src python3 scripts/validate_media.py --extra .local/media-validation/private-cases.json --compare-saved
# Regenerate a report after recording review findings in results.json:
PYTHONPATH=src python3 scripts/validate_media.py --report-only --output .local/media-validation/RUN
```

Every download uses the actual CLI, folder mode, `--browser none`, isolated settings and ignored engine configuration. It never opens Photos or a browser. With `--compare-saved`, an existing per-site cookie snapshot is copied temporarily and forced into the downloader for the same URL. This is a controlled comparison, not the normal CLI's automatic session selection. No browser cookie store is read, no session is refreshed, and user accounts/preferences are unchanged. A missing saved session is recorded as missing evidence, not a successful comparison.

Results go into an owner-only directory under `.local/media-validation/`, which Git ignores. Use a new output directory for each run. The runner records tool versions, exit status, logs, original media, SHA-256 hashes, dimensions, codec, duration, audio and video bitrate, and counts when the source count is known. Image bitrate is not meaningful. Videos have five preview frames at 5%, 25%, 50%, 75% and 95%; the report links the originals for complete review. Partial files from failed CLI runs are retained as diagnostics and excluded from successful outputs. Downloads over the test limit or timeout are inconclusive, not authentication failures.

## Separate acceptance criteria

1. **Anonymous availability:** exit success, valid media and the expected item count where known. Failure must be classified from the actual error: authentication, network/TLS, deleted or unavailable post, challenge, unsupported route, or incomplete media.
2. **Quality:** the selected resolution may match the maximum exposed by that request. Equal dimensions or identical anonymous/signed files do not establish the platform or uploader ceiling. Different codecs cannot be ranked solely by bitrate. Keep absolute highest quality unproven without a trusted original or platform/account quality reference.
3. **Watermarks:** record visible platform marks, author/source marks, no mark seen in inspected previews, or inconclusive. Five clean frames cannot establish a completely clean video. Marks burned into the upload can remain in an original stream. A failed download has no watermark verdict.
4. **Coverage:** video-only platforms, unsupported gallery/live routes and missing fresh links must remain explicit gaps. Test names and older sample notes are hypotheses, not current evidence.

Record manual findings in each row's `review`, each mode's `review`/`watermark`, and each file's `review`/`watermark`. `--report-only` preserves these fields. Human visual judgments should identify the reviewed file/mode, preview scope, observer and date. Keep full-resolution and full-video acceptance separate from preview inspection.

## 2026-10-01 local audit

27 real posts were attempted, with anonymous downloads succeeding for 19; three additional entries describe unsupported/not-applicable routes. Results depend on this network, these posts and the installed engine versions. Corrected gallery and Bilibili Opus runs replace initial test-harness argument failures. All successful output media passed ffprobe. All absolute highest-quality claims remain unproven.

| Channel | Current sample evidence |
|---|---|
| Instagram | Both image-gallery and Reel anonymous requests explicitly required login; saved-session downloads succeeded. Four gallery images, Reel 480×854. |
| X | Image 5568×3712 and video 1280×720 downloaded anonymously; both byte-identical to saved-session outputs. Video has NASA publisher branding, not an X overlay. |
| Bilibili | Opus still/live video and 720p video succeeded anonymously and matched saved bytes. Another anonymous request failed with TLS EOF, so login causality is unproven; saved output is 1080p and visibly retains a bilibili mark. |
| TikTok | Two-image gallery and 1080×1920 video succeeded anonymously. Gallery matches saved bytes; signed video hit a 403 challenge. No TikTok mark seen in inspected public previews. |
| Douyin | All ten photos and two videos downloaded anonymously. Video outputs match saved bytes; saved gallery failed to expose media. No Douyin mark seen in public image previews/five video frames. |
| Xiaohongshu | Using the same token-bearing share link without cookies, video downloaded at 1920×1080 from the original route; saved mode fell back to a much smaller display stream. Gallery was incomplete in both modes. Anonymous link discovery and absolute original ceiling were not verified. |
| Weibo | Gallery and 720p video downloaded anonymously and matched saved bytes. All three image previews show a Weibo account mark; all five video frames show 视频号. |
| YouTube | All three videos succeeded anonymously and matched request-exposed resolution ceilings (240p, 1080p, 2160×2964). Saved outputs differ; Shorts VP9 versus AV1 needs quality review and retains author branding. Images are not applicable. |
| Reddit | Both photo/video requests were blocked; no saved-session control. Anonymous availability remains unverified. |
| Kuaishou | The tested video exposed no downloadable web-state media. No saved-session control. Gallery route is not implemented; a fresh link is needed. |
| LOFTER | All seven images and 720p video downloaded anonymously. Video visibly retains LOFTER branding. No saved-session or highest-quality control. |
| Pixiv | Still-image case requires an OAuth refresh token not integrated with browser authorization. Ugoira output is unsupported. |
| Pinterest | Image 290×249 and video 1080×1920 downloaded anonymously. Small NASA image may itself be the original small logo; no original/saved reference proves quality. |

Local evidence: `.local/media-validation/2026-10-01-audit/report.md`, `review.html`, `results.json` and per-mode originals/logs. These artifacts are local and are not shipped with the package. The public manifest and runner are reproducible; media and private share links stay local.

## 2026-10-01 Reddit repair acceptance

The installed `0.2.0b16.dev5` CLI downloaded the two user-provided regression posts through its ordinary session flow. The image (`1ws8p4o`) used `--browser chrome` and produced a 1100×1429 JPEG. The video (`1wurwev`) used default automatic session selection, reusing that login in isolated settings, and produced a 1280×720 H.264 MP4 with audio, duration 309.568438 seconds. No engine injection or forced-cookie override was used.

The video is byte-identical to the previously inspected workaround output. A ten-second audio sample decoded successfully and was non-silent. The five inspected frames retain source-program branding; no Reddit-added watermark was observed. The user previously accepted the image as matching the post. Anonymous availability, absolute highest quality and full-video visual acceptance remain unproven.

Actual user settings were unchanged. All full downloaded media (65,937,767 bytes), temporary session snapshots and test settings were deleted; only small previews, metadata and logs remain in `.local/media-validation/2026-10-01-reddit-fixed-224834/`.

## Pixiv web-original follow-up

The normal CLI downloaded public artwork `966412` as one 655×640 PNG and `148094125` as three 1000×1409 JPEGs. The three-page artwork also passed `--browser chrome` using ordinary session export and verification; all three signed-in files are byte-identical to their anonymous counterparts. Previews show no observed Pixiv-added mark. These results establish access to the web API's original URLs for these samples, not the uploader's absolute quality ceiling or all account-restricted works. Ugoira remains unsupported.

Pixiv login checking now uses `/ajax/user/extra`, which returned private viewer counters with the authorized Chrome session. The anonymous control returned HTTP 400 and an invalid-cookie control returned HTTP 401. The old homepage-text check incorrectly rejected this same signed-in browser session.

The older eleven-page artwork `56360615` failed when an original image returned HTTP 500. It was not counted as a complete download and no lower-resolution preview was substituted. Full successful media, partial files and temporary cookie/settings snapshots were removed after metadata and preview inspection. Evidence remains in `.local/media-validation/2026-10-01-pixiv-web/`.

## Clean macOS VM installation and upgrade

The Parallels `macOS 27` VM (27.0, build 26A428, arm64) initially had no Homebrew, command-line developer tools or linkget. Homebrew 7.0.7 and CLT 27.0 were installed in the guest, followed by the public `0.2.0b13` tap formula. Dependency checks and `brew test` passed. Upgrading through a temporarily substituted generated formula to the unpublished `0.2.0b16.dev5` archive succeeded; a saved folder preference remained byte-identical. The public tap formula was restored afterward. Source and isolated installed-wheel suites each passed 120 tests, and the installed candidate downloaded a valid 5568×3712 public X image. Full test media and temporary installation/test files were cleaned.

This validates clean guest installation and the package upgrade mechanism, not a public release fetch for the unpublished candidate. Browser permission flows and real Photos imports remain separate manual acceptance items. Local evidence: `.local/media-validation/2026-10-01-vm-release/`.

## 2026-10-02 Kuaishou and Photos follow-up

The ordinary installed CLI downloaded user video `3xz5t5ber85wq7g` anonymously on both the host and the clean macOS VM: 720×1280 HEVC, 30 fps, 114.2 seconds, 6,583,060 bytes, with audio. Full host video/audio decoding passed; a ten-second audio sample was non-silent. The anonymous page exposed H.264 and HEVC renditions, both at 720×1280. This establishes the maximum exposed resolution for this request, not an uploader ceiling or codec quality ranking. Five inspected frames show no observed Kuaishou-added mark; full-video visual acceptance remains pending. The new sample is in the corpus; the older blocked sample remains historical evidence. Full test downloads are cleaned after inspection. Local evidence: `.local/media-validation/2026-10-02-kuaishou-user/`.

The user reported that the Pixiv image imported into Photos without problems. Record this as human image-import acceptance.

The Kuaishou video also imported through the installed Photos AppleScript when run from the guest Terminal (`1:0:1`), and the user inspected playback, reported no problems and deleted the imported sample. Video import/playback acceptance passed for this retained HEVC file. SSH import was blocked separately: the guest macOS 27.0 (26A428) Automation settings failed to authorize the path-based `sshd-keygen-wrapper` with `Missing subject bundle identifier` and `Could not create identity dict for subject`. This does not establish that all macOS versions have the same issue. Use a local Terminal for Photos permission acceptance; do not modify the permission database.

## 0.2.0 Kuaishou regression repair

A later anonymous recheck failed for both the complete and shortened user URL on the host and VM. The desktop page returned only Apollo site configuration, while the public mobile page exposed the matching photo in `window.INIT_STATE` with both H.264 and HEVC manifests. The adapter now reads that representation only when desktop post data is absent, verifies the requested post identity, and validates MP4 response bytes and length. No login, challenge solver or new dependency was added.

The repaired source downloaded both corpus videos anonymously: the user's 720×1280 HEVC file is byte-identical to the accepted Photos video, and the older blocked sample now yields 1080×1440 HEVC with audio. These are sample/request results, not an unconditional availability guarantee. Local evidence: `.local/media-validation/2026-10-02-kuaishou-repair/`.

## 0.2.0 release audit

The complete corpus was repeated on 2026-10-02: 31 active URLs including two private Xiaohongshu shares, plus three unsupported/not-applicable boundaries. Of 31 anonymous requests, 24 downloaded successfully; of 19 forced saved-session comparisons, 16 downloaded successfully. All 110 successful media outputs passed ffprobe, with no known-count mismatch on a successful request. This is a sampled audit, not a guarantee for every post.

Instagram's two anonymous requests required login and both saved-session controls passed. Reddit's four anonymous requests were blocked; the ordinary Chrome-login image command and installed CLI's automatic saved-login video command passed separately. TikTok's two anonymous samples passed while both forced saved-session controls hit HTTP 403 website verification. One Douyin anonymous request omitted media but its saved-session control passed; another failed only in the forced saved comparison. Automatic Douyin downloads now try an existing allowed saved login after absent public metadata, without treating it as expiry or requesting sign-in. Public-only mode and logout remain respected.

Both private Xiaohongshu shares completed in anonymous and saved modes: the gallery produced 18 images per mode and the video one item per mode. X, Bilibili, Weibo and YouTube sample downloads passed in both modes. Kuaishou, LOFTER, Pixiv and Pinterest public samples passed; missing saved controls for those channels remain missing evidence. The user-reviewed Kuaishou file has the same bytes after the mobile repair. In the VM, the installed 0.2.0 CLI passed the full user URL, shortened URL and older video; complete video/audio decode passed for all three.

Homebrew upgrade and `brew test` passed on the host (macOS 27.0.1, arm64) and VM (27.0, arm64), preserving user preferences and restoring the public tap formula after local candidate checks. Tests also passed on the declared minimum Python 3.10 and source/installed-wheel environments. Intel/older-macOS and other-browser live permission combinations remain unverified; those are outside this physical acceptance. Public release fetching still requires publishing the prepared artifacts. Evidence: `.local/media-validation/2026-10-02-release-corpus/` and `.local/media-validation/2026-10-02-release-0.2.0/`.

Final review reproduced two Xiaohongshu Live Photo fallback defects: a live item with no video shifted a later image's source index, and a rejected later live track could be replaced by an earlier item's video. Downloads now retain the exact source item for each output. Both regressions passed after the repair; the final automated suite contains 136 tests. The real Xiaohongshu gallery/video controls were repeated after this change.
