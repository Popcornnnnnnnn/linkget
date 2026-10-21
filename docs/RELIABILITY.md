# Reliability in 0.2.1.dev22 (prerelease)

This release preserves the existing CLI while strengthening transfer recovery, diagnostics and file ownership. The boundaries below distinguish enabled behavior from preparatory interfaces.

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
resources are fetched again. Final save, deduplication and full-set checks remain in the existing pipeline;
ownership checks also guard its save and cleanup boundaries. Successful partial transfers remain in staging;
no incomplete set is imported into Photos by this feature.

The source identity hashes the exact planned URL, media kind and position. A
refreshed signed URL or changed order can omit previously known identities. Such a
refresh is refused without replacing the old plan. Retry later, or explicitly
accept a new plan with `retry TASK --redownload`; old stages containing omitted
resources remain protected even after that reset. No
claim is made that different URLs identify the same original. Source URLs and raw
error strings are not stored in the new per-media manifest. Existing task inputs
still retain private share tokens needed for retries, as before.

Every reuse checks that the file belongs to this task's recorded staging tree,
that the stage identity matches, that no path component is a symbolic link, and
that the recorded content hash still matches. Invalid manifests fail safely;
`retry TASK --redownload` bypasses reuse while keeping unverified old files.
The common [file-evidence model](FILE_OWNERSHIP.md) starts at an exclusive
writer descriptor, before completion registration. Its immutable identity/hash
continues through normal and duplicate saves, retained Photos copies, managed
copy verification and recovery. Observation is separate from creation authority;
unknown files, older receipts, shared originals and uninstrumented engine/tool
outputs are kept. Explicit redownload protects old stages before clearing the old
manifest, even without an earlier ordinary retry. This intentionally retains more
temporary files, including content-reading stages; it adds no automatic purge.
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

- Local guarded source/Python 3.14 and isolated-wheel validation: 440 cases each, 432
  successful, eight skipped. The wheel is tested in a task-local environment; the user installation is unchanged.
- Five skips are optional Instagram extractor cases: ordinary Python cannot
  import gallery-dl. Three PhotosScriptTests are deliberately excluded by the
  guarded acceptance runner to avoid any AppleScript execution. No actual Photos
  acceptance is claimed. The existing Homebrew gallery-dl 1.32.15 interpreter
  separately passes all six reused tests and the seven generated fixtures.
- The compatibility report requires five named extractor tests to be collected,
  executed and successful. Empty, command-only, skipped, duplicated or missing
  execution cannot satisfy `--require-extractor`.
- Sensitive diagnostics are parsed recursively (including arrays and multiline
  credential values and bounded complete encoding layers). Ambiguous/malformed
  structures are omitted. Classification distinguishes affirmative boolean response flags from false/configuration values.
- Generated fixtures cover repeated partial failures, cancellation, disk-full
  copies, edited sources, rejected format probes, changed source identities,
  damaged manifests, outside/symlink paths, shrinking plans and valid generated PNG
  replacements at writer publication/completion, duplicate and retention handoffs,
  managed-copy verification and cleanup claims. Mock Photos
  boundaries verify importer prevention and retained-copy preservation.
  Concurrently replaced temporary copies are preserved, and their stages are
  excluded from later automatic cleanup.
- Offline sdist/wheel builds and modified-document link/fence checks passed.
  There was no website download, cookie read, actual Photos access, third-party
  candidate installation/execution, external CI configuration change. Release CI additionally tests Linux/Python 3.10 and macOS/Python 3.14; consult the release-linked runs for their results.
