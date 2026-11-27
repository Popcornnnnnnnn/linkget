export const title = "Linkget Media, Batch, and Read-Task Lifecycle";

export const colors = {
  idle: "blue",
  photos: "teal",
  batch: "purple",
  read: "orange",
  done: "tan",
  broken: "pink",
};

const classify = (v) => {
  const crossTaskLoss =
    ((v.recA === "unverified" || v.recB === "unverified") && v.orig1 !== "present") ||
    (Boolean(v.recAOrig2) && v.orig2 !== "present");
  const receiptDrop =
    v.mode === "photos" &&
    v.orig1 === "present" &&
    v.ownerA &&
    ((v.recA !== "unverified" && v.recA !== "verified_kept") || !v.recAId);
  const dualOwner = v.ownerA && v.ownerB;
  const coordDeadlock = v.batchCoord === "failed" && !v.batchCancelled;
  const readRegression =
    (v.readTerminal !== "none" && v.readStatus !== v.readTerminal) ||
    (v.jobEverDone && v.jobStatus !== "completed") ||
    (v.jobEverDone &&
      ["completed", "cancelled", "failed", "interrupted"].includes(v.readStatus) &&
      v.readStatus !== "completed");
  const lockedInterrupt = v.workerLock && v.readStatus === "interrupted";
  return {
    crossTaskLoss,
    receiptDrop,
    dualOwner,
    coordDeadlock,
    readRegression,
    lockedInterrupt,
  };
};

export const state = (v) => {
  const b = classify(v);
  if (b.crossTaskLoss) return "Shared original deleted while receipt unverified";
  if (b.receiptDrop) return "Unverified Photos receipt lost on redownload";
  if (b.dualOwner) return "Two tasks claim shared original ownership";
  if (b.coordDeadlock) return "Batch coordinator crashed without cancelling workers";
  if (b.readRegression) return "Completed read task or job regressed";
  if (b.lockedInterrupt) return "Live worker interrupted while holding lock";

  if (v.mode === "idle") return "Idle";

  if (v.mode === "photos") {
    if (v.recA === "cleared") return "Photos originals verified and cleaned";
    if (v.recA === "verified_kept" && v.recB === "unverified") {
      return "Task A cleaned unique original; shared kept";
    }
    if (v.recA === "verified_kept" && v.recB === "cleared") {
      return "Task B verified; Task A can clean shared";
    }
    if (v.recB === "unverified") return "Task A and Task B share original 1";
    if (v.recB === "cleared") return "Task B verified; Task A still unverified";
    return "Task A staged originals";
  }

  if (v.mode === "batch") {
    if (v.batchCoord === "stopped") return "Batch shutdown complete";
    if (v.batchCoord === "failed" && v.workerWait === "waiting_interaction") {
      return "Coordinator crashed; waking blocked worker";
    }
    if (v.batchCoord === "failed") return "Coordinator crashed; finishing shutdown";
    if (v.workerWait === "waiting_interaction") return "Worker waiting on coordinator interaction";
    if (v.workerWait === "exited") return "Batch worker finished";
    return "Batch running";
  }

  if (v.readStatus === "queued") {
    if (v.workerLock) return "Read worker holds lock before start";
    return v.cancelReq ? "Read queued with cancel requested" : "Read queued";
  }
  if (v.readStatus === "running" && v.workerLock) {
    if (v.jobEverDone) return "Read job committed; finalizing task";
    return v.cancelReq ? "Read worker running with cancel requested" : "Read worker running";
  }
  if (v.readStatus === "running" && !v.workerLock) {
    return v.jobEverDone
      ? "Read worker crashed after job commit"
      : "Read worker crashed; lock released";
  }
  if (v.workerLock) {
    return `Read ${v.readStatus}; releasing lock`;
  }
  if (v.readStatus === "completed") return "Read completed";
  if (v.readStatus === "cancelled") return "Read cancelled";
  if (v.readStatus === "interrupted") return "Read interrupted";
  return "Read failed";
};

export const group = (v) => {
  const b = classify(v);
  if (
    b.crossTaskLoss ||
    b.receiptDrop ||
    b.dualOwner ||
    b.coordDeadlock ||
    b.readRegression ||
    b.lockedInterrupt
  ) {
    return "broken";
  }
  if (v.mode === "idle") return "idle";
  if (v.mode === "photos") {
    return v.recA === "cleared" ? "done" : "photos";
  }
  if (v.mode === "batch") {
    return v.batchCoord === "stopped" ? "done" : "batch";
  }
  const terminal = ["completed", "cancelled", "failed", "interrupted"];
  return terminal.includes(v.readStatus) && !v.workerLock ? "done" : "read";
};

export const step = (action, from, to) => {
  if (action === "Done") return null;
  switch (action) {
    case "StartPhotos":
      return "start Photos import";
    case "StartBatch":
      return "start batch run";
    case "StartRead":
      return "submit background read";
    case "TaskBStage":
      return "Task B observes shared original";
    case "TaskARedownload":
      return "Task A retries with redownload";
    case "TaskAVerify":
      return to.recA === "verified_kept"
        ? "Task A cleans unique and keeps shared"
        : "Task A verifies and cleans";
    case "TaskBVerify":
      return "Task B verifies Photos copy";
    case "WorkerBlockInteraction":
      return "worker waits for interaction";
    case "CoordServeInteraction":
      return "coordinator serves interaction";
    case "CoordUnhandledCrash":
      return "coordinator hits unhandled error";
    case "WorkerWakeOnCancel":
      return "cancel wakes waiting worker";
    case "CoordFinishShutdown":
      return "executor shutdown finishes";
    case "ReadPreSpawnFail":
      return "worker spawn fails";
    case "ReadCancelRequest":
      return "user requests cancel";
    case "ReadWorkerAcquireLock":
      return "worker acquires lock";
    case "ReadWorkerBegin":
      return to.readStatus === "cancelled"
        ? "worker sees cancel before start"
        : "worker transitions to running";
    case "ReadCommitJob":
      return "read job commits completed";
    case "ReadWorkerFinalizeSuccess":
      return "worker finalizes completed task";
    case "ReadPostCommitInterrupt":
      return "post-commit interrupt reconciles completed";
    case "ReadWorkerCrash":
      return "worker crashes and drops lock";
    case "ReadRefresh":
      if (to.readStatus === "completed") return "refresh reconciles completed job";
      return to.readStatus === "cancelled"
        ? "refresh marks stopped task cancelled"
        : "refresh marks stopped task interrupted";
    case "ReadWorkerCancelFinish":
      return "worker handles cancel and stops";
    case "ReadWorkerReleaseLock":
      return "worker releases lock";
    default:
      return action;
  }
};
