---
name: verify-media-lifecycle
description: Formally verify linkget's shared Photos original protection, redownload receipt preservation, batch coordinator shutdown liveness, and detached read-task monotonicity invariants with TLA+ and TLC. Run before major updates or changes to destinations.py, photos.py, live.py, ownership.py, recovery.py, batch.py, runtime.py, cli.py, read_tasks.py, read_store.py, content.py, or read_formats.py.
---

# Verify Linkget Media, Batch, and Read-Task Lifecycle

Checks the TLA+ model in `formal/LinkgetMediaLifecycle.tla` against the safety and liveness invariants governing content-addressed Photos originals across tasks, `--redownload` receipt preservation, batch coordinator exception shutdown, and detached read-task monotonicity.

## Coverage and Sync Record

- `sync_commit`: `305b025`
- **Covered files and symbols:**
  - `src/linkget/photos.py`: `originals_lock`, `referenced_originals`, `retain_originals`
  - `src/linkget/live.py`: `_retention_lock`, `retain_pairs`
  - `src/linkget/destinations.py`: `_import_photos`
  - `src/linkget/recovery.py`: `remove_verified`, `restore`
  - `src/linkget/cli.py`: `_has_retained_receipt`, `download`
  - `src/linkget/batch.py`: `_run`
  - `src/linkget/runtime.py`: `batch_interaction`, `check_batch_cancelled`
  - `src/linkget/read_tasks.py`: `update`, `worker_claimed`, `_refresh_locked`, `refresh`, `cancel`, `submit`, `worker`
  - `src/linkget/read_store.py`: `save_job`, `unfinished`
  - `src/linkget/content.py`: `read_videos`, `finish`, `.toutiao-download` cleanup
  - `src/linkget/read_formats.py`: `write_text`
- **Check for code drift since last sync:**
  ```sh
  git diff 305b025..HEAD -- src/linkget/photos.py src/linkget/live.py src/linkget/destinations.py src/linkget/recovery.py src/linkget/cli.py src/linkget/batch.py src/linkget/runtime.py src/linkget/read_tasks.py src/linkget/read_store.py src/linkget/content.py src/linkget/read_formats.py
  ```

If covered files changed after `sync_commit`, update `formal/LinkgetMediaLifecycle.tla` and confirm it accurately models `HEAD` (including a mutation check) before moving `sync_commit` forward.

## Verified Properties (Invariants)

1. **`NoCrossTaskOriginalLoss`:** A shared content-addressed file in `originals/` is never unlinked while another task still holds an unverified Photos receipt referencing it.
2. **`NoUnverifiedReceiptLoss`:** Retrying a task with `--redownload` never drops an existing unverified Photos receipt whose staged files still exist on disk.
3. **`SingleOriginalOwner`:** Concurrent staging of the same original grants creation-time cleanup ownership (`created=True`) to at most one task.
4. **`NoCoordShutdownDeadlock` (and `NoDeadlock`):** An unhandled exception in the batch coordinator always sets `state["cancelled"]` before `executor.shutdown(wait=True)`, waking any worker blocked in `batch_interaction()`.
5. **`MonotonicReadTerminal`:** Once a detached read task reaches a terminal status (`completed`, `cancelled`, `failed`, `interrupted`) or a read job commits `completed` in `.read-job.json`, neither concurrent cancellation, refresh, worker cleanup, nor post-commit interrupts regress its status.
6. **`NoStaleInterruptWhileLocked`:** `read_tasks.refresh()` never marks a task `interrupted` while its detached worker holds `.worker.lock`.

## Bounds, Assumptions, and Out of Scope

- **Bounds:** Three disjoint protocol branches selected from `Idle` (`photos`, `batch`, `read`): 2 tasks sharing 1 content-addressed original with at most 1 `--redownload` retry; 1 batch coordinator with 1 worker entering `batch_interaction()` and at most 1 unhandled crash; 1 detached read task with `.worker.lock`, 1 cancel request, 1 worker crash, and 1 post-commit interrupt.
- **Assumptions:** `fcntl.flock` on `.originals.lock`, `.lock`, and `.worker.lock` provides mutual exclusion on local POSIX filesystems; atomic rename/link (`os.replace` / `os.link`) is indivisible; `FileProof.verify()` detects in-place tampering.
- **Out of scope:** Network/extractor retries, cookie/session recovery flows, AppleScript/Photos internal database locking, and ffmpeg/whisper subprocess internals.

## Verified Status at `305b025`

- **Model check (`LinkgetMediaLifecycle`):** TLC explores **37 states / 60 steps** (22 chart states, 30 arrows). All 6 invariants and `NoDeadlock` **hold** (`exit 0`).
- **Mutation proofs:** Removing the cross-task receipt check in `TaskAVerify` breaks `NoCrossTaskOriginalLoss` in 3 steps (`exit 1`); removing `batchCancelled' = TRUE` in `CoordUnhandledCrash` breaks `NoCoordShutdownDeadlock` and `NoDeadlock` (`exit 1`); clearing `recA` in `TaskARedownload` breaks `NoUnverifiedReceiptLoss` (`exit 1`); removing `~workerLock` in `ReadRefresh` breaks `NoStaleInterruptWhileLocked` (`exit 1`).
- **Regression tests:** Replayed offline in `tests/test_lifecycle_invariants.py`.

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
