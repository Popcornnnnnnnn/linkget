# Optional extractor fallback policy

No third-party fallback is installed, imported, registered or invoked by the normal CLI in this branch. `extractor_fallback.py` supplies a small programmatic policy and an empty default adapter list. Its tests use a fake in-process metadata extractor only. This is an extension boundary, not a claim that XHS-Downloader, DouK or cobalt fixes a current linkget failure.

## Evidence before integration

The current baseline already has site-specific source alternatives: Xiaohongshu can retain exposed display sources when an original is unavailable; Douyin can use an explicitly permitted browser media snapshot. Those are linkget implementations, not integrations with dedicated third-party downloaders.

The public [Douyin motion report #9](https://github.com/Popcornnnnnnnn/linkget/issues/9) was fixed by the current main baseline, including per-image motion identity and incomplete-motion refusal. The public [Instagram count report #2](https://github.com/Popcornnnnnnnn/linkget/issues/2) was fixed with the existing gallery-dl extension. Neither report demonstrates that another downloader is needed now.

Existing offline evidence includes:

- `test_new_sites.py::test_xhs_incomplete_gallery_challenge_and_truncation_do_not_import`: incomplete metadata, verification content and truncated transfers remain failures.
- `test_short_video.py::test_browser_fallback_recovers_missing_metadata_and_interrupted_transfer`: the existing allowed Douyin browser route, using synthetic metadata.
- `test_douyin_browser.py::test_unknown_or_incomplete_metadata_keeps_polling`: ambiguous/current-post motion evidence is not accepted as complete.

These fixtures establish required safety behavior. They do not establish that any candidate extractor succeeds on the same real failing post. This review found no authorized reproducible baseline-failure/candidate-success comparison that justifies shipping a concrete adapter.

## Public candidate review, 2026-10-21

Only public metadata, source manifests, examples, licenses and API documentation were read. No candidate was installed or executed, and no website media was downloaded.

| Candidate | Verified source facts | Integration/maintenance cost and current decision |
| --- | --- | --- |
| XHS-Downloader | Its [manifest](https://github.com/JoeanAmier/XHS-Downloader/blob/master/pyproject.toml) reports version 2.8, Python >=3.12 and GPL-3.0; the [license file](https://github.com/JoeanAmier/XHS-Downloader/blob/master/LICENSE) contains GPL v3. The [example](https://github.com/JoeanAmier/XHS-Downloader/blob/master/example.py) exposes Python metadata extraction and a local API, alongside optional download/browser-cookie features. | linkget supports Python >=3.10 and otherwise keeps its core in the standard library. This candidate brings curl-cffi, FastAPI, GUI/MCP/database and other dependencies, plus source-schema adaptation. A future adapter would need explicit no-download/no-cookie configuration and independent identity/count/format checks. Not registered. |
| DouK / TikTokDownloader | The [manifest](https://github.com/JoeanAmier/TikTokDownloader/blob/master/pyproject.toml) names DouK-Downloader 5.8, Python >=3.12 and GPL-3.0. Its [license](https://github.com/JoeanAmier/TikTokDownloader/blob/master/license) contains GPL v3. | Adds a separate async/network stack and dependencies including curl-cffi, FastAPI and a JavaScript bridge. No verified coverage advantage for the current image/motion failure cases was established. Not registered. |
| cobalt | The [API manifest](https://github.com/imputnet/cobalt/blob/main/api/package.json) reports version 11.7.1, Node >=18.17 and AGPL-3.0, with its own web/network/FFmpeg stack. Its [license](https://github.com/imputnet/cobalt/blob/main/LICENSE) contains AGPL v3. [API documentation](https://github.com/imputnet/cobalt/blob/main/docs/api.md) requires permission for third-party use of hosted instances and describes self-hosting as an alternative. | Adds a local/remote service lifecycle, optional API authentication and response/tunnel mapping. Public hosted APIs are not a default fallback. No hosted request or self-hosted service was started. Not registered. |

Public repository metadata reported non-archived repositories and last push timestamps of 2026-10-18 (XHS-Downloader), 2026-09-22 (DouK), and 2026-04-06 (cobalt). A push timestamp is not proof of endpoint health, maintenance responsiveness or compatibility with linkget.

The GPL/AGPL labels are review inputs, not a legal conclusion about every integration mode. Copying or linking code, redistributing a separately installed tool, and calling a separately operated API have different obligations and need review of the actual license, packaging and deployment. This branch copies none of these projects' source and makes no assertion that a future adapter is legally compatible merely because its SPDX field is filled in. A concrete adapter requires a documented compatibility decision before `compatibility_reviewed=True` is appropriate. Third-party installation/execution still requires separate authorization.

## Programmatic contract

`Fallback(Policy(), adapters=())` is disabled. Enabling it also requires an exact `adapter_name`; there is no auto-discovery, importing, executable search, installer or cascade through every registered adapter. One instance belongs to one post attempt and invokes at most one selected callback once, even if two threads call it. A failed callback is not retried. User-initiated later attempts may create a fresh instance; the module does not schedule them.

The caller must supply a `FailureContext` with a verified `unsupported` or `metadata_unavailable` reason, extraction phase, and `artifacts_started=False`. Arbitrary error text is not evidence. Authentication, HTTP refusal, rate limits, website verification, network failures, unknown causes, transfer failures and save/postprocessing failures cannot trigger this fallback. If media transfer or saving has started, use the original recovery path instead.

An `Adapter` declares exact sites/reasons, a license identifier, a documented compatibility review and evidence references. These declarations are a registration review record, not a sandbox or a replacement for reviewing trusted adapter code. An adapter receives only a post request; there is no cookie/profile/destination argument. The callback contract permits metadata extraction only, with no media files, Photos imports, settings changes or private-store reads. No real adapter satisfying that contract is included.

An `ExtractedPost` must bind the requested site and post ID. `Outcome.status == 'extracted'` means only that nonempty metadata was returned for that identity. It does **not** prove media completeness, source quality, supported media hosts, valid formats, or a successful download/save. The caller must pass the result through existing site parsers, source allowlists, gallery/motion completeness checks, format inspection and normal verified save/recovery logic. A metadata result with an empty gallery still needs to fail in that pipeline; the policy cannot infer a source count.

Raw request URLs, returned metadata and callback exception text are omitted from outcome representations. This avoids echoing signed query strings or adapter credential errors. The caller retains the original primary failure and reports only the bounded fallback status; it must not log the raw payload or callback exception separately.

## Offline validation and next evidence needed

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_extractor_fallback.py -v
```

The ten tests cover default-off/unregistered behavior, reason/phase/artifact gates, registration review and scope, exact selected adapter, bounded/concurrent invocation, cancellation, wrong post identity, invalid metadata, credential-safe representations and refusal of credential/custom-port URLs. They make no network or Photos requests and install nothing.

Before adding a real candidate: retain a permitted, reproducible current failure; reproduce it with pinned engine/linkget versions; review a candidate's exact version, license and dependencies; obtain separate permission for its installation/execution; compare the same source without expanding account access; then add a sanitized, redistributable regression. Until that comparison exists, this interface remains unconnected to the CLI and real-world candidate success is unverified.
