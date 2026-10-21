# File evidence and cleanup (0.2.1.dev22)

This change fixes a pipeline invariant, rather than treating every successful hash
read as new permission to trust or delete its current pathname.

## The invariant

1. Native downloads and copies exclusively create an output descriptor before
   writing. `ownership.Writer` hashes the bytes written and carries that
   descriptor's device/inode, size and timestamps. Native publication does not
   overwrite an existing final filename. `TransferSet.complete` requires that
   writer evidence; it cannot establish identity from a later path.
2. Existing inputs and duplicate destinations receive a fixed, non-owning
   `FileProof`: identity is captured before the hash read and checked afterwards.
   Later stages verify that proof; they cannot silently replace its identity or
   expected hash. A duplicate is a saved-output witness, not a deletion right.
3. Folder and Live Photo saves record both created and duplicate output proofs in
   `destination_files`. Sources, outputs and receipts retain the same evidence
   across validation, saving and recovery. Derived pairs retain their `FileList`
   evidence instead of being converted to a plain tuple of paths.
4. Photos retention returns its copy writer's proof. Existing shared copies are
   observed, never newly owned. Managed-library verification returns fixed target
   proofs as well. Retained proofs are checked before import; the same retained
   proofs and verified managed-target proofs are checked before removal. A verification callback without file evidence authorizes
   no removal. These are code boundaries; this round invokes only mock importers
   and synthetic library directories.
5. Cleanup requires durable creation evidence. It atomically claims the checked
   inode, retains/restores a replacement, and removes only known empty
   directories. Inventory does not turn unknown files or empty directories into owned resources. Legacy
   hash-only receipts may authorize a checked copy, but cannot acquire deletion
   authority during recovery. Explicit `--redownload` protects old stages before
   discarding their plan, including when no ordinary retry was attempted first.

`FileProof` is immutable. `created=False` remains false when serialized or passed
through `FileList`; reading a file successfully cannot promote it to true. The
private journal remains an internal receipt, not an access-control boundary
against another program running as the same user.

## Producer boundaries and deliberate retention

| Producer | Evidence | Automatic source cleanup |
| --- | --- | --- |
| Native Xiaohongshu/Douyin transfer | Exclusive writer descriptor and written-byte hash | Only its unchanged, recorded files |
| Folder/Photos retention/Live Photo copy | Pinned source proof and exclusive output writer | Only created, unchanged outputs |
| Existing duplicate or shared retained file | Fixed identity and expected content | No new deletion right |
| CLI log and generated video-info JSON | Exclusive writer; process log descriptor stays pinned | Only the recorded output |
| gallery-dl/yt-dlp or other uninstrumented adapter output | Fixed observation at the consumer boundary | Kept; observation does not prove creation |
| Swift/ExifTool transformed Live Photo output | Source evidence checked throughout; fixed transformed-output observation | Kept; tool transformation does not grant creation evidence |
| Unknown sidecar, helper, user-added file or old receipt | No writer evidence | Kept |

Consequently, successful saves can now leave temporary stages for external engine
outputs, transformed Live Photo components, shared originals or unknown files.
Content-reading stages also remain instead of being recursively deleted. This
uses more disk space; the existing task/status paths identify retained stages.
There is no new automatic purge. Metadata produced by linkget's own writer still
has safe cleanup evidence; arbitrary external metadata is retained.

Digest caching remains per item, keyed consistently across macOS `/var` and
`/private/var` aliases. Reuse requires the entire identity signature to match;
final output, managed-copy and removal checks still request fresh reads where
needed. There is no global content cache or background watch of saved files.

## Offline verification

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p test_file_ownership.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -p test_live_ownership.py -v
PYTHONPATH=src python3 -m unittest discover -s tests -p test_failures.py -v
```

Generated PNGs cover different-byte and same-byte inode replacements, fresh
native completion, partial publication, final-name collision, duplicate targets,
retention return boundaries, managed-copy verification, prepared-pair recovery,
legacy/shared receipts and files inserted before the first inventory. Reading
retry tests use fake adapters and session callbacks; they read no browser store.
The tests do not establish website availability, actual Photos import/playback,
or safety against arbitrary code that can tamper with the process or its journal.

Escaped diagnostic handling uses one bounded interpretation boundary for display,
keys and classification. Up to four complete JSON encoding layers are decoded;
ambiguous, malformed or over-bound diagnostics become opaque. Sensitive values
remain unavailable to classification even when their text says "login required"
or contains another error signal. This can reduce displayed detail or yield
Unknown, which is preferable to inferring a cause from credential contents.
