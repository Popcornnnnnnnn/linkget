---
name: verify-media-lifecycle
description: Formally verify linkget's shared Photos original protection, redownload receipt preservation, batch coordinator shutdown liveness, and detached read-task monotonicity invariants with TLA+ and TLC. Run before major updates or changes to destinations.py, photos.py, live.py, ownership.py, recovery.py, batch.py, runtime.py, cli.py, read_tasks.py, read_store.py, content.py, or read_formats.py.
---

# Verify Linkget Media, Batch, and Read-Task Lifecycle

Checks the TLA+ model in `formal/LinkgetMediaLifecycle.tla` against the safety and liveness invariants governing content-addressed Photos originals across tasks, `--redownload` receipt preservation, batch coordinator exception shutdown, and detached read-task monotonicity.

## Coverage and Sync Record

- `sync_commit`: `9f871e49f1a5e8f5c126163696e2d2472e00e2a9`
- **Covered files and symbols:**
  - `src/linkget/photos.py`: `originals_lock`, `referenced_originals`, `retain_originals`, `prune_receipt`
  - `src/linkget/jobs.py`: `Journal.update` (`pending_receipt`, `receipt`)
  - `src/linkget/live.py`: `_retention_lock`, `retain_pairs`
  - `src/linkget/destinations.py`: `_carry_created_proofs`, `_import_photos`
  - `src/linkget/recovery.py`: `remove_verified` (`on_removed`), `restore`
  - `src/linkget/cli.py`: `_has_retained_receipt`, `download`
  - `src/linkget/batch.py`: `_run`
  - `src/linkget/runtime.py`: `batch_interaction`, `check_batch_cancelled`
  - `src/linkget/read_tasks.py`: `update`, `_try_claim_worker`, `_job_completed`, `_refresh_locked`, `refresh`, `cancel`, `submit`, `worker`
  - `src/linkget/read_store.py`: `save_job`, `unfinished`
  - `src/linkget/content.py`: `read_videos`, `finish`, `.toutiao-download` cleanup
  - `src/linkget/read_formats.py`: `write_text`
- **Check for code drift since last sync:**
  ```sh
  git diff 9f871e49f1a5e8f5c126163696e2d2472e00e2a9..HEAD -- src/linkget/photos.py src/linkget/jobs.py src/linkget/live.py src/linkget/destinations.py src/linkget/recovery.py src/linkget/cli.py src/linkget/batch.py src/linkget/runtime.py src/linkget/read_tasks.py src/linkget/read_store.py src/linkget/content.py src/linkget/read_formats.py
  ```

If covered files changed after `sync_commit`, update `formal/LinkgetMediaLifecycle.tla` and confirm it accurately models `HEAD` (including a mutation check) before moving `sync_commit` forward.

## Verified Properties (Invariants)

1. **`NoCrossTaskOriginalLoss`:** Neither a shared content-addressed file in `originals/` (`orig1`) nor an unremoved receipt entry (`recAOrig2 => orig2 = "present"`) is deleted while still referenced by an active Photos receipt; per-file pruning immediately updates the persisted receipt after each unlinked file (`TaskARemoveOrig2`).
2. **`NoUnverifiedReceiptLoss`:** Retrying a task with `--redownload` never drops an existing recoverable Photos receipt or overwrites its verified import identifiers on a duplicate skip (`saved_positions` check + `pending_receipt`) while staged originals remain on disk.
3. **`SingleOriginalOwner`:** Concurrent staging of the same original grants creation-time cleanup ownership (`created=True`) to at most one task.
4. **`NoCoordShutdownDeadlock` (and `NoDeadlock`):** An unhandled exception in the batch coordinator always sets `state["cancelled"]` before `executor.shutdown(wait=True)`, waking any worker blocked in `batch_interaction()`; per-file Photos cleanup pruning ensures follow-up verification reaches completion cleanly after partial failures or shared-original holds.
5. **`MonotonicReadTerminal`:** Once a detached read task reaches a terminal status (`completed`, `cancelled`, `failed`, `interrupted`) or a read job commits `completed` in `.read-job.json`, neither concurrent cancellation, refresh, post-commit interrupts (`worker` `finally`), nor hard-crash `refresh()` reconciliation regress the status away from `completed`.
6. **`NoStaleInterruptWhileLocked`:** `read_tasks.refresh()` acquires `.worker.lock` across its terminal status write (`_try_claim_worker`), never marking a task `interrupted` while a live detached worker holds `.worker.lock`.

## Bounds, Assumptions, and Out of Scope

- **Bounds:** Three disjoint protocol branches selected from `Idle` (`photos`, `batch`, `read`): 2 tasks sharing `orig1` plus unshared `orig2` with `--redownload` retry and separate per-file cleanup/pruning steps (`TaskARemoveOrig2`, `TaskARemoveOrig1`); 1 batch coordinator with 1 worker entering `batch_interaction()` and at most 1 unhandled crash; 1 detached read task modeling separate lock acquisition (`ReadWorkerAcquireLock`), startup (`ReadWorkerBegin`), `.read-job.json` commit (`ReadCommitJob`), post-commit interrupt or hard crash, lock-held refresh (`ReadRefresh`), and lock release (`ReadWorkerReleaseLock`).
- **Assumptions:** `fcntl.flock` on `.originals.lock`, `.lock`, and `.worker.lock` provides mutual exclusion on local POSIX filesystems; atomic rename/link (`os.replace` / `os.link`) is indivisible; `FileProof.verify()` detects in-place tampering.
- **Out of scope:** Network/extractor retries, cookie/session recovery flows, AppleScript/Photos internal database locking, and ffmpeg/whisper subprocess internals.

## Verified Status at `9f871e49f1a5e8f5c126163696e2d2472e00e2a9`

- **Model check (`LinkgetMediaLifecycle`):** TLC explores **51 states / 86 steps** (27 chart states, 42 arrows). All 6 invariants and `NoDeadlock` **hold** (`exit 0`).
- **Mutation proofs (all exited `1`):**
  1. Clearing `recAId` on `TaskARedownload` breaks `NoUnverifiedReceiptLoss` in 2 steps (`exit 1`).
  2. Deleting `orig2` in `TaskARemoveOrig2` without immediately clearing `recAOrig2` breaks `NoCrossTaskOriginalLoss` in 2 steps and `NoDeadlock` in 5 steps (`exit 1`).
  3. Removing `recB /= "unverified"` in `TaskARemoveOrig1` breaks `NoCrossTaskOriginalLoss` and `NoDeadlock` in 4 steps (`exit 1`).
  4. Dropping `~workerLock` in `ReadRefresh` breaks `NoStaleInterruptWhileLocked` in 3 steps (`exit 1`).
  5. Regressing `readStatus` to `"cancelled"` in `ReadPostCommitInterrupt` breaks `MonotonicReadTerminal` in 5 steps (`exit 1`).
- **Regression tests:** Replayed offline in `tests/test_lifecycle_invariants.py` (full suite: 670 tests run, 6 skipped, 0 failures).

Treat any violation of these invariants or `NoDeadlock` as a release blocker.

## How to Run

Run `formal/LinkgetMediaLifecycle.tla` with the `/statechart` skill (writing output to a fresh folder under `/tmp`, never inside the repository):

- Model: `formal/LinkgetMediaLifecycle.tla`
- Config: `formal/LinkgetMediaLifecycle.cfg`
- View: `formal/linkget-view.mjs`
- Output: a new or empty folder under `/tmp` (for example `/tmp/linkget-statechart-run`)

*Contributor fallback (without `/statechart`):* run standard TLC with `formal/LinkgetMediaLifecycle.cfg` and `formal/LinkgetMediaLifecycle.tla`.

## Interpreting Results and Upkeep Rules

- **Never weaken an invariant** or shrink bounds solely to make TLC pass.
- **Replay every violation:** Before changing product code, replay the shortest counterexample path against the real Python modules in `tests/test_lifecycle_invariants.py` using isolated temporary directories. If the sequence is impossible in real code, fix the model drift instead.
- **Separate commits:** Keep formal spec and skill updates (`formal/`, `.agents/skills/`, `AGENTS.md`) separate from product bug fixes.
