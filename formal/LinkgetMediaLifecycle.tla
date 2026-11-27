\* TLA+ specification of linkget's critical media, batch, and detached read-task lifecycles.
\*
\* Code mapping (verified at sync_commit 305b025):
\*   - Photos shared original staging, receipt recording, redownload preservation, and cleanup:
\*       src/linkget/ownership.py    (copy, FileProof.remove)
\*       src/linkget/photos.py       (originals_lock, referenced_originals, retain_originals)
\*       src/linkget/live.py         (_retention_lock, retain_pairs)
\*       src/linkget/destinations.py (_import_photos)
\*       src/linkget/recovery.py     (remove_verified, restore)
\*       src/linkget/cli.py          (_has_retained_receipt, download)
\*   - Batch coordinator exception handling and worker interaction wake-up:
\*       src/linkget/batch.py        (_run)
\*       src/linkget/runtime.py      (batch_interaction, check_batch_cancelled)
\*   - Detached read-task monotonicity, worker lock check, and post-commit job durability:
\*       src/linkget/read_tasks.py   (update, worker_claimed, _refresh_locked, refresh, cancel, submit, worker)
\*       src/linkget/read_store.py   (save_job, unfinished)
\*       src/linkget/content.py      (read_videos, finish, .toutiao-download cleanup)
\*       src/linkget/read_formats.py (write_text)

---- MODULE LinkgetMediaLifecycle ----
EXTENDS Naturals

VARIABLES
    mode,            \* "idle", "photos", "batch", "read"
    \* Photos shared-original and cross-task receipt state
    orig,            \* "absent", "present", "deleted"
    ownerA,          \* BOOLEAN: Task A holds creation-time FileProof (created=True)
    ownerB,          \* BOOLEAN: Task B holds creation-time FileProof (created=True)
    recA,            \* "none", "unverified", "verified_kept", "cleared"
    recB,            \* "none", "unverified", "cleared"
    redownloadTried, \* BOOLEAN: Task A attempted retry --redownload
    \* Batch coordinator and worker interaction state
    batchCoord,      \* "running", "failed", "stopped"
    workerWait,      \* "idle", "waiting_interaction", "exited"
    batchCancelled,  \* BOOLEAN: state["cancelled"] event
    \* Detached read-task and read-job monotonicity state
    readStatus,      \* "queued", "running", "completed", "cancelled", "failed", "interrupted"
    readTerminal,    \* "none", "completed", "cancelled", "failed", "interrupted"
    workerLock,      \* BOOLEAN: .worker.lock held by live detached worker
    cancelReq,       \* BOOLEAN: cancel_requested persisted under .lock
    jobStatus,       \* "running", "completed", "failed", "cancelled"
    jobEverDone,     \* BOOLEAN: .read-job.json ever committed status="completed"
    postCommitHit    \* BOOLEAN: post-commit interrupt observed after completion

vars == <<
    mode,
    orig, ownerA, ownerB, recA, recB, redownloadTried,
    batchCoord, workerWait, batchCancelled,
    readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit
>>

TerminalRead == {"completed", "cancelled", "failed", "interrupted"}

Init ==
    /\ mode = "idle"
    /\ orig = "absent"
    /\ ownerA = FALSE
    /\ ownerB = FALSE
    /\ recA = "none"
    /\ recB = "none"
    /\ redownloadTried = FALSE
    /\ batchCoord = "running"
    /\ workerWait = "idle"
    /\ batchCancelled = FALSE
    /\ readStatus = "queued"
    /\ readTerminal = "none"
    /\ workerLock = FALSE
    /\ cancelReq = FALSE
    /\ jobStatus = "running"
    /\ jobEverDone = FALSE
    /\ postCommitHit = FALSE

\* ---- Mode selection ----

StartPhotos ==
    /\ mode = "idle"
    /\ mode' = "photos"
    \* Task A stages via ownership.copy and persists receipt atomically under originals_lock().
    /\ orig' = "present"
    /\ ownerA' = TRUE
    /\ recA' = "unverified"
    /\ UNCHANGED <<ownerB, recB, redownloadTried, batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

StartBatch ==
    /\ mode = "idle"
    /\ mode' = "batch"
    /\ UNCHANGED <<orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

StartRead ==
    /\ mode = "idle"
    /\ mode' = "read"
    /\ UNCHANGED <<orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

\* ---- Photos shared-original and cross-task receipt transitions ----

TaskBStage ==
    /\ mode = "photos"
    /\ orig = "present"
    /\ recB = "none"
    \* Task B observes the existing shared original (created=False) and persists its receipt under originals_lock().
    /\ ownerB' = FALSE
    /\ recB' = "unverified"
    /\ UNCHANGED <<mode, orig, ownerA, recA, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

TaskARedownload ==
    /\ mode = "photos"
    /\ recA = "unverified"
    /\ ~redownloadTried
    \* cli.py _has_retained_receipt preserves recA when staged original still exists on disk.
    /\ redownloadTried' = TRUE
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

TaskAVerify ==
    /\ mode = "photos"
    /\ recA \in {"unverified", "verified_kept"}
    /\ orig = "present"
    \* Under originals_lock(), check referenced_originals(exclude=taskA) before proof.remove().
    /\ IF recB = "unverified"
       THEN /\ orig' = "present"
            /\ recA' = "verified_kept"
       ELSE /\ orig' = IF ownerA THEN "deleted" ELSE orig
            /\ recA' = "cleared"
    /\ UNCHANGED <<mode, ownerA, ownerB, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

TaskBVerify ==
    /\ mode = "photos"
    /\ recB = "unverified"
    /\ orig = "present"
    \* Task B verifies Photos copy; because ownerB=FALSE it never deletes orig, and clears its own receipt.
    /\ recB' = "cleared"
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

\* ---- Batch coordinator and worker interaction transitions ----

WorkerBlockInteraction ==
    /\ mode = "batch"
    /\ batchCoord = "running"
    /\ workerWait = "idle"
    /\ workerWait' = "waiting_interaction"
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

CoordServeInteraction ==
    /\ mode = "batch"
    /\ batchCoord = "running"
    /\ workerWait = "waiting_interaction"
    /\ workerWait' = "exited"
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

CoordUnhandledCrash ==
    /\ mode = "batch"
    /\ batchCoord = "running"
    \* batch.py except BaseException sets state["cancelled"] before executor.shutdown(wait=True).
    /\ batchCoord' = "failed"
    /\ batchCancelled' = TRUE
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   workerWait,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

WorkerWakeOnCancel ==
    /\ mode = "batch"
    /\ workerWait = "waiting_interaction"
    /\ batchCancelled = TRUE
    /\ workerWait' = "exited"
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

CoordFinishShutdown ==
    /\ mode = "batch"
    /\ batchCoord \in {"running", "failed"}
    /\ (workerWait = "exited" \/ (batchCoord = "failed" /\ workerWait = "idle"))
    /\ batchCoord' = "stopped"
    /\ workerWait' = "exited"
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

\* ---- Detached read-task and read-job monotonicity transitions ----

ReadPreSpawnFail ==
    /\ mode = "read"
    /\ readStatus = "queued"
    /\ ~workerLock
    /\ ~cancelReq
    \* Pre-spawn Popen error marks receipt failed before any worker exists.
    /\ readStatus' = "failed"
    /\ readTerminal' = "failed"
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

ReadCancelRequest ==
    /\ mode = "read"
    /\ readStatus \in {"queued", "running"}
    /\ ~cancelReq
    \* cancel() re-checks terminal status under .lock; never mutates terminal receipts.
    /\ cancelReq' = TRUE
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, jobStatus, jobEverDone, postCommitHit>>

ReadWorkerStart ==
    /\ mode = "read"
    /\ readStatus = "queued"
    /\ ~workerLock
    /\ IF cancelReq
       THEN /\ readStatus' = "cancelled"
            /\ readTerminal' = "cancelled"
            /\ workerLock' = FALSE
       ELSE /\ readStatus' = "running"
            /\ readTerminal' = readTerminal
            /\ workerLock' = TRUE
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   cancelReq, jobStatus, jobEverDone, postCommitHit>>

ReadWorkerCrash ==
    /\ mode = "read"
    /\ readStatus = "running"
    /\ workerLock = TRUE
    \* Hard worker crash releases OS .worker.lock while receipt still shows running.
    /\ workerLock' = FALSE
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, cancelReq, jobStatus, jobEverDone, postCommitHit>>

ReadRefresh ==
    /\ mode = "read"
    /\ readStatus = "running"
    /\ ~workerLock
    \* _refresh_locked checks worker_claimed() before marking stopped worker cancelled/interrupted.
    /\ readStatus' = IF cancelReq THEN "cancelled" ELSE "interrupted"
    /\ readTerminal' = IF cancelReq THEN "cancelled" ELSE "interrupted"
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

ReadWorkerCommit ==
    /\ mode = "read"
    /\ readStatus = "running"
    /\ workerLock = TRUE
    /\ jobStatus = "running"
    \* Atomic export + save_job(completed) + terminal worker write under .lock.
    /\ jobStatus' = "completed"
    /\ jobEverDone' = TRUE
    /\ readStatus' = "completed"
    /\ readTerminal' = "completed"
    /\ workerLock' = FALSE
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   cancelReq, postCommitHit>>

ReadWorkerCancelFinish ==
    /\ mode = "read"
    /\ readStatus = "running"
    /\ workerLock = TRUE
    /\ cancelReq = TRUE
    /\ jobStatus' = "cancelled"
    /\ readStatus' = "cancelled"
    /\ readTerminal' = "cancelled"
    /\ workerLock' = FALSE
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   cancelReq, jobEverDone, postCommitHit>>

ReadPostCommitInterrupt ==
    /\ mode = "read"
    /\ jobEverDone = TRUE
    /\ ~postCommitHit
    \* Post-commit interrupt (content.py + read_store.save_job) preserves completed status.
    /\ postCommitHit' = TRUE
    /\ UNCHANGED <<mode, orig, ownerA, ownerB, recA, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone>>

\* ---- Terminal stuttering so TLC NoDeadlock holds in legitimate end states ----

Done ==
    /\ ( \/ (mode = "photos" /\ recA = "cleared" /\ recB \in {"none", "cleared"})
         \/ (mode = "batch" /\ batchCoord = "stopped" /\ workerWait = "exited")
         \/ (mode = "read" /\ readStatus \in TerminalRead /\ ~workerLock) )
    /\ UNCHANGED vars

Next ==
    \/ StartPhotos
    \/ StartBatch
    \/ StartRead
    \/ TaskBStage
    \/ TaskARedownload
    \/ TaskAVerify
    \/ TaskBVerify
    \/ WorkerBlockInteraction
    \/ CoordServeInteraction
    \/ CoordUnhandledCrash
    \/ WorkerWakeOnCancel
    \/ CoordFinishShutdown
    \/ ReadPreSpawnFail
    \/ ReadCancelRequest
    \/ ReadWorkerStart
    \/ ReadWorkerCrash
    \/ ReadRefresh
    \/ ReadWorkerCommit
    \/ ReadWorkerCancelFinish
    \/ ReadPostCommitInterrupt
    \/ Done

\* ---- Safety Invariants ----

NoCrossTaskOriginalLoss ==
    (recA = "unverified" \/ recB = "unverified") => orig = "present"

NoUnverifiedReceiptLoss ==
    (mode = "photos" /\ orig = "present" /\ ownerA) => recA \in {"unverified", "verified_kept"}

SingleOriginalOwner ==
    ~(ownerA /\ ownerB)

NoCoordShutdownDeadlock ==
    batchCoord = "failed" => batchCancelled = TRUE

MonotonicReadTerminal ==
    /\ (readTerminal /= "none" => readStatus = readTerminal)
    /\ (jobEverDone => jobStatus = "completed")

NoStaleInterruptWhileLocked ==
    workerLock => readStatus = "running"

====
