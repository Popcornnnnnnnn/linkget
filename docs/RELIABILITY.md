# Local reliability work (unreleased)

This branch starts at public main `67a174b`, independently of the earlier local
`start` entrance commits `c6c21ed` and `6a40458`. No entrance changes, system
installation, publication, personal library or account access are part of it.

## Plan and initial gaps

1. Errors: existing text matching misses explicit expiry, processing failures and
   uncertain causes; raw engine errors can include signed URLs. Add verified
   classification, safe messages and bounded read-only retry policy.
2. Gallery recovery: whole-post retries and complete-set save recovery already
   exist. Add per-resource receipts for native Xiaohongshu/Douyin transfers so a
   retry can reuse checked successful resources and fetch missing resources only.
   Exposed-plan counts must remain distinct from independently known source totals.
3. Fallback extractors: existing original/display and browser-content fallbacks
   are already implemented. Evaluate public candidates without installation;
   provide a disabled policy/interface and offline fake adapters, accepting a real
   adapter only with compatible licensing and matching failure evidence.
4. Compatibility: reuse synthetic fixtures, record engine versions and distinguish
   parser/engine/platform evidence. Default checks remain offline, with no scheduled
   checks or changes to external CI.

Each area is verified independently, then integrated and tested together. Actual
Photos playback remains the user's own check; it is not an assistant prerequisite.

## Implemented boundaries

### 1. Error evidence and safe recovery hints

`src/linkget/failures.py` classifies explicit HTTP/auth/network/extractor signals,
unknown errors and caller-owned save/import/pair phases. A generic 403 is not
login expiry; an extraction error is not proof of an unsupported URL. CLI error
and login guidance plus recovery failure messages redact credentials and signed
URL query data. Native adapters retain their existing RuntimeError contracts,
with bounded typed-cause inspection for disk/network evidence.

`retry_delay` is a pure, opt-in read-only policy: at most two retries with 1/2-second
backoff, respecting bounded Retry-After values. It does not run requests. No new
automatic retry of an engine, download, save or Photos import has been enabled.
Existing bounded site bootstrap/source alternatives remain separate behavior.

### 2. Per-resource native gallery recovery

`src/linkget/transfers.py` is used by the existing Xiaohongshu and Douyin adapters.
It adds `media_progress` to the existing private task receipt, recording each
exposed resource's transfer state, hash, staging ownership and source identity.
Retry refreshes the post metadata and copies only matching, byte-checked complete
resources into the new attempt; missing, cancelled, edited or probe-rejected
resources are fetched again. Final save, deduplication, full-set checks and cleanup
still use the original pipeline. Successful partial transfers remain in staging;
no incomplete set is imported into Photos by this feature.

The source identity hashes the exact planned URL, media kind and position. A
refreshed signed URL or changed order can therefore cause extra downloads. No
claim is made that different URLs identify the same original. Source URLs and raw
error strings are not stored in the new per-media manifest. Existing task inputs
still retain private share tokens needed for retries, as before.

Every reuse checks that the file belongs to this task's recorded staging tree,
that the stage identity matches, that no path component is a symbolic link, and
that the recorded content hash still matches. Invalid manifests fail safely;
`retry TASK --redownload` bypasses reuse while keeping unverified old files.
Changed/untracked files remain protected by existing cleanup ownership checks.
Manifest/disk-write failure may leave an unrecorded successful transfer; that file
is retained but cannot be safely reused without a durable valid receipt.

`planned` counts only exposed resources. `source_total` is explicitly unknown;
the progress output says "recorded complete" and does not assert independent
source completeness or playback. A complete transfer still needs format checks.
This incremental transfer feature does not cover gallery-dl/yt-dlp or other native
adapters; their existing task retry/recovery behavior is unchanged. It is not
byte-range resume and does not reconstruct an absent or unexposed resource.

### 3. Optional extractor fallback

`src/linkget/extractor_fallback.py` provides a disabled metadata-only interface,
empty default registry, explicit adapter choice and one eligible attempt before
media side effects. No normal CLI route invokes it. [Candidate review](FALLBACKS.md)
records public failure evidence, GPL/AGPL and dependency review inputs, and the
absence of a matching current-failure/candidate-success comparison. No third-party
candidate was installed, executed or integrated. Fake adapters validate policy,
identity, cancellation and concurrency; their success is not real-site acceptance.

### 4. Offline upstream compatibility

`scripts/check_compatibility.py` runs seven generated contract fixtures and reuses
the existing Instagram extractor suite while blocking network/process attempts.
[Compatibility instructions](COMPATIBILITY.md) describe version visibility,
optional local version probes and attribution limits. Website availability remains
`not_checked`; no online mode, schedule or external CI changes were added.

## Reproduce without accounts or Photos

From this checkout, using existing trusted tools only:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_failures.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -p test_failure_reporting.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -p test_transfer_recovery.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -p test_extractor_fallback.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -p test_compatibility.py -v
PYTHONPATH=src python3 scripts/check_compatibility.py
PYTHONPATH=src python3 -m unittest discover -s tests -q
```

The broad suite starts isolated loopback fixture servers and local child processes;
a restricted sandbox may need to permit those operations. It never needs a real
Photos library, private browser stores or website downloads. With an existing
Homebrew gallery-dl Python environment, run its already documented compatibility
command with `--require-extractor` to cover the optional five extractor cases.

## Local verification, 2026-10-21

- Source/Python 3.14: 373 cases, 368 successful and five optional extractor skips.
- Isolated installed wheel/Python 3.14 from `/tmp`: the same 373 cases,
  368 successful and five skips. The user's installed command was not replaced.
- The five skips are the existing Instagram extractor cases when the ordinary
  interpreter cannot import gallery-dl. The existing Homebrew gallery-dl 1.32.15
  Python environment separately passed all six reused tests (five extractors and
  one command contract), plus all seven compatibility fixtures.
- Generated fixtures cover repeated partial failures, cancellation, disk-full
  copies, edited sources, rejected format probes, changed source identities,
  damaged manifests, outside/symlink paths and concurrent target replacement.
  Concurrently replaced temporary copies are preserved, and their stages are
  excluded from later automatic cleanup.
- Offline sdist/wheel builds and modified-document link/fence checks passed.
  There was no website download, cookie read, actual Photos access, third-party
  candidate installation/execution, external CI change or publication.

The earlier `start` commits are not ancestors of this branch; its module and CLI
command are absent. The original checkout remains on `ccdafe2` with its existing
user edits. This branch intentionally starts at public main rather than importing
that checkout's later local Neo/reading work; future integration must reconcile
those independent commits before installation. Nothing is merged or deployed by
this local handoff.
