\* TLA+ specification of linkget's critical media, batch, and detached read-task lifecycles.
\*
\* Code mapping (verified at sync_commit 9f871e49f1a5e8f5c126163696e2d2472e00e2a9):
\*   - Photos shared original staging, receipt recording, redownload preservation, and cleanup:
\*       src/linkget/ownership.py    (copy, FileProof.remove)
\*       src/linkget/photos.py       (originals_lock, referenced_originals, retain_originals, prune_receipt)
\*       src/linkget/live.py         (_retention_lock, retain_pairs)
\*       src/linkget/destinations.py (_import_photos)
\*       src/linkget/recovery.py     (remove_verified, restore)
\*       src/linkget/cli.py          (_has_retained_receipt, download)
\*   - Batch coordinator exception handling and worker interaction wake-up:
\*       src/linkget/batch.py        (_run)
\*       src/linkget/runtime.py      (batch_interaction, check_batch_cancelled)
\*   - Detached read-task monotonicity, worker lock check, and post-commit job durability:
\*       src/linkget/read_tasks.py   (_try_claim_worker, _job_completed, _refresh_locked, refresh, cancel, submit, worker)
\*       src/linkget/read_store.py   (save_job, unfinished)
\*       src/linkget/content.py      (read_videos, finish, .toutiao-download cleanup)
\*       src/linkget/read_formats.py (write_text)

---- MODULE LinkgetMediaLifecycle ----
EXTENDS Naturals

VARIABLES
    mode,            \* "idle", "photos", "batch", "read"
    \* Photos shared-original and cross-task receipt state (orig1 shared, orig2 Task A only)
    orig1,           \* "absent", "present", "deleted"
    orig2,           \* "absent", "present", "deleted"
    ownerA,          \* BOOLEAN: Task A holds creation-time FileProof (created=True)
    ownerB,          \* BOOLEAN: Task B holds creation-time FileProof (created=True)
    recA,            \* "none", "unverified", "verified_kept", "cleared"
    recAOrig2,       \* BOOLEAN: Task A receipt still lists orig2
    recAId,          \* BOOLEAN: Task A recoverable receipt retains its Photos import identifier
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
    orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
    batchCoord, workerWait, batchCancelled,
    readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit
>>

TerminalRead == {"completed", "cancelled", "failed", "interrupted"}

Init ==
    /\ mode = "idle"
    /\ orig1 = "absent"
    /\ orig2 = "absent"
    /\ ownerA = FALSE
    /\ ownerB = FALSE
    /\ recA = "none"
    /\ recAOrig2 = FALSE
    /\ recAId = FALSE
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
    \* Task A stages orig1 (later shared) and orig2 (unique) with a recoverable receipt.
    /\ orig1' = "present"
    /\ orig2' = "present"
    /\ ownerA' = TRUE
    /\ recA' = "unverified"
    /\ recAOrig2' = TRUE
    /\ recAId' = TRUE
    /\ UNCHANGED <<ownerB, recB, redownloadTried, batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

StartBatch ==
    /\ mode = "idle"
    /\ mode' = "batch"
    /\ UNCHANGED <<orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

StartRead ==
    /\ mode = "idle"
    /\ mode' = "read"
    /\ UNCHANGED <<orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

\* ---- Photos shared-original, --redownload receipt preservation, and partial cleanup transitions ----

TaskBStage ==
    /\ mode = "photos"
    /\ orig1 = "present"
    /\ recB = "none"
    \* Task B observes existing shared orig1 (created=False) and persists its receipt under originals_lock().
    /\ ownerB' = FALSE
    /\ recB' = "unverified"
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, recA, recAOrig2, recAId, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

TaskARedownload ==
    /\ mode = "photos"
    /\ recA \in {"unverified", "verified_kept"}
    /\ ~redownloadTried
    \* _import_photos keeps existing recoverable receipt when identifiers exist until retry succeeds.
    /\ redownloadTried' = TRUE
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

TaskARemoveOrig2 ==
    /\ mode = "photos"
    /\ recA \in {"unverified", "verified_kept"}
    /\ orig1 = "present"
    /\ orig2 = "present"
    /\ recAOrig2 = TRUE
    /\ ownerA = TRUE
    \* Cleanup removes orig2 and immediately prunes recAOrig2 before attempting orig1 (safe under transient error).
    /\ orig2' = "deleted"
    /\ recAOrig2' = FALSE
    /\ recA' = "verified_kept"
    /\ UNCHANGED <<mode, orig1, ownerA, ownerB, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

TaskARemoveOrig1 ==
    /\ mode = "photos"
    /\ recA \in {"unverified", "verified_kept"}
    /\ ~recAOrig2
    /\ recB /= "unverified"
    /\ orig1 = "present"
    \* Only remove shared orig1 once orig2 is pruned and no other task references orig1.
    /\ orig1' = IF ownerA THEN "deleted" ELSE orig1
    /\ recA' = "cleared"
    /\ UNCHANGED <<mode, orig2, ownerA, ownerB, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

TaskBVerify ==
    /\ mode = "photos"
    /\ recB = "unverified"
    /\ orig1 = "present"
    \* Task B verifies Photos copy; ownerB=FALSE never deletes orig1 and clears its own receipt.
    /\ recB' = "cleared"
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

\* ---- Batch coordinator and worker interaction transitions ----

WorkerBlockInteraction ==
    /\ mode = "batch"
    /\ batchCoord = "running"
    /\ workerWait = "idle"
    /\ workerWait' = "waiting_interaction"
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

CoordServeInteraction ==
    /\ mode = "batch"
    /\ batchCoord = "running"
    /\ workerWait = "waiting_interaction"
    /\ workerWait' = "exited"
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

CoordUnhandledCrash ==
    /\ mode = "batch"
    /\ batchCoord = "running"
    \* batch.py except BaseException sets state["cancelled"] before executor.shutdown(wait=True).
    /\ batchCoord' = "failed"
    /\ batchCancelled' = TRUE
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   workerWait,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

WorkerWakeOnCancel ==
    /\ mode = "batch"
    /\ workerWait = "waiting_interaction"
    /\ batchCancelled = TRUE
    /\ workerWait' = "exited"
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

CoordFinishShutdown ==
    /\ mode = "batch"
    /\ batchCoord \in {"running", "failed"}
    /\ (workerWait = "exited" \/ (batchCoord = "failed" /\ workerWait = "idle"))
    /\ batchCoord' = "stopped"
    /\ workerWait' = "exited"
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
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
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

ReadCancelRequest ==
    /\ mode = "read"
    /\ readStatus \in {"queued", "running"}
    /\ ~cancelReq
    \* cancel() re-checks terminal status under .lock; never mutates terminal receipts.
    /\ cancelReq' = TRUE
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, jobStatus, jobEverDone, postCommitHit>>

ReadWorkerAcquireLock ==
    /\ mode = "read"
    /\ readStatus = "queued"
    /\ ~workerLock
    \* Worker opens and flocks .worker.lock before updating queued -> running.
    /\ workerLock' = TRUE
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, cancelReq, jobStatus, jobEverDone, postCommitHit>>

ReadWorkerBegin ==
    /\ mode = "read"
    /\ readStatus = "queued"
    /\ workerLock = TRUE
    /\ IF cancelReq
       THEN /\ readStatus' = "cancelled"
            /\ readTerminal' = "cancelled"
       ELSE /\ readStatus' = "running"
            /\ readTerminal' = readTerminal
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

ReadCommitJob ==
    /\ mode = "read"
    /\ readStatus = "running"
    /\ workerLock = TRUE
    /\ jobStatus = "running"
    \* content.finish writes export, content.json, and .read-job.json (status="completed").
    /\ jobStatus' = "completed"
    /\ jobEverDone' = TRUE
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, workerLock, cancelReq, postCommitHit>>

ReadWorkerFinalizeSuccess ==
    /\ mode = "read"
    /\ readStatus = "running"
    /\ workerLock = TRUE
    /\ jobEverDone = TRUE
    \* Normal worker exit persists terminal completed on task receipt under .lock.
    /\ readStatus' = "completed"
    /\ readTerminal' = "completed"
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

ReadPostCommitInterrupt ==
    /\ mode = "read"
    /\ readStatus = "running"
    /\ workerLock = TRUE
    /\ jobEverDone = TRUE
    /\ ~postCommitHit
    \* Interrupt after .read-job.json commit: worker.finally checks _job_completed and reconciles to completed.
    /\ postCommitHit' = TRUE
    /\ readStatus' = "completed"
    /\ readTerminal' = "completed"
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   workerLock, cancelReq, jobStatus, jobEverDone>>

ReadWorkerCancelFinish ==
    /\ mode = "read"
    /\ readStatus = "running"
    /\ workerLock = TRUE
    /\ cancelReq = TRUE
    /\ jobStatus = "running"
    /\ jobStatus' = "cancelled"
    /\ readStatus' = "cancelled"
    /\ readTerminal' = "cancelled"
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   workerLock, cancelReq, jobEverDone, postCommitHit>>

ReadWorkerCrash ==
    /\ mode = "read"
    /\ readStatus \in {"queued", "running"}
    /\ workerLock = TRUE
    \* Hard worker crash releases OS .worker.lock before finalizing task receipt.
    /\ workerLock' = FALSE
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, cancelReq, jobStatus, jobEverDone, postCommitHit>>

ReadRefresh ==
    /\ mode = "read"
    /\ readStatus \in {"queued", "running"}
    /\ ~workerLock
    \* _refresh_locked holds _try_claim_worker across read-task status write and reconciles _job_completed.
    /\ LET next == IF jobEverDone THEN "completed" ELSE IF cancelReq THEN "cancelled" ELSE "interrupted"
       IN /\ readStatus' = next
          /\ readTerminal' = next
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   workerLock, cancelReq, jobStatus, jobEverDone, postCommitHit>>

ReadWorkerReleaseLock ==
    /\ mode = "read"
    /\ readStatus \in TerminalRead
    /\ workerLock = TRUE
    /\ workerLock' = FALSE
    /\ UNCHANGED <<mode, orig1, orig2, ownerA, ownerB, recA, recAOrig2, recAId, recB, redownloadTried,
                   batchCoord, workerWait, batchCancelled,
                   readStatus, readTerminal, cancelReq, jobStatus, jobEverDone, postCommitHit>>

\* ---- Terminal stuttering so TLC NoDeadlock holds in legitimate end states ----

Done ==
    /\ ( \/ (mode = "photos" /\ recA = "cleared" /\ ~recAOrig2 /\ recB \in {"none", "cleared"})
         \/ (mode = "batch" /\ batchCoord = "stopped" /\ workerWait = "exited")
         \/ (mode = "read" /\ readStatus \in TerminalRead /\ ~workerLock) )
    /\ UNCHANGED vars

Next ==
    \/ StartPhotos
    \/ StartBatch
    \/ StartRead
    \/ TaskBStage
    \/ TaskARedownload
    \/ TaskARemoveOrig2
    \/ TaskARemoveOrig1
    \/ TaskBVerify
    \/ WorkerBlockInteraction
    \/ CoordServeInteraction
    \/ CoordUnhandledCrash
    \/ WorkerWakeOnCancel
    \/ CoordFinishShutdown
    \/ ReadPreSpawnFail
    \/ ReadCancelRequest
    \/ ReadWorkerAcquireLock
    \/ ReadWorkerBegin
    \/ ReadCommitJob
    \/ ReadWorkerFinalizeSuccess
    \/ ReadPostCommitInterrupt
    \/ ReadWorkerCancelFinish
    \/ ReadWorkerCrash
    \/ ReadRefresh
    \/ ReadWorkerReleaseLock
    \/ Done

\* ---- Safety Invariants ----

NoCrossTaskOriginalLoss ==
    /\ ((recA = "unverified" \/ recB = "unverified") => orig1 = "present")
    /\ (recAOrig2 => orig2 = "present")

NoUnverifiedReceiptLoss ==
    (mode = "photos" /\ orig1 = "present" /\ ownerA) =>
        (recA \in {"unverified", "verified_kept"} /\ recAId)

SingleOriginalOwner ==
    ~(ownerA /\ ownerB)

NoCoordShutdownDeadlock ==
    batchCoord = "failed" => batchCancelled = TRUE

MonotonicReadTerminal ==
    /\ (readTerminal /= "none" => readStatus = readTerminal)
    /\ (jobEverDone => jobStatus = "completed")
    /\ ((jobEverDone /\ readStatus \in TerminalRead) => readStatus = "completed")

NoStaleInterruptWhileLocked ==
    workerLock => readStatus /= "interrupted"

====
