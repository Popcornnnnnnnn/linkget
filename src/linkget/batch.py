"""Batch scheduling; browser and destination operations remain serialized."""
import argparse
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import queue
import sys
import threading
import time
PROGRESS_INTERVAL = 5
if __package__:
    from .runtime import _batch, check_batch_cancelled
    from . import jobs
    from .output import downloaded_bytes, format_size
    from .links import ResolutionCache
else:
    from runtime import _batch, check_batch_cancelled
    import jobs
    from output import downloaded_bytes, format_size
    from links import ResolutionCache


def run_batch(args, parser, links, records, download, first_run, status, journal_factory):
    if not links:
        if sys.stdin.isatty():
            print("\n  Paste one link or sharing text per line. Blank line starts downloads.\n")
            while line := input("  > ").strip():
                links.append(line)
        else:
            links = [line.strip() for line in sys.stdin.read().splitlines() if line.strip()]
    if not links:
        parser.error("The batch is empty. Supply links or paste one per line.")
    journal = journal_factory(links, args)
    with journal.claim():
        indices = getattr(args, "_indices", list(range(1, len(links) + 1)))
        # Selection is refreshed under the task lock.
        pairs = [(index, link) for index, link in zip(indices, links)
                 if journal.data["items"][index - 1]["status"] != "completed"]
        if not pairs:
            status("Retry", "No unfinished items")
            return 0
        return _run(args, pairs, records, download, first_run, status, journal)


def _run(args, pairs, records, download, first_run, status, journal):
    links = [link for _, link in pairs]
    setup_results = (first_run(args) or {}) if any(not journal.data["items"][i - 1].get("ready") for i, _ in pairs) else {}
    state = {"cancelled": threading.Event(), "interactions": queue.Queue(),
             "output": threading.RLock(), "saving": threading.Lock(), "progress": {},
             "resolution_cache": ResolutionCache()}
    item_records = {index: [] for index, _ in pairs}
    results = {}

    def run_item(index, link):
        _batch.state = state
        _batch.item = (index, len(journal.data["items"]))
        try:
            item_args = argparse.Namespace(**vars(args))
            item_args.link = link
            with jobs.item(journal, index):
                jobs.record(status="running")
                result = download(item_args, setup_results, item_records[index])
                if result == 130:
                    state["cancelled"].set()
                jobs.record(status="completed" if result == 0 else "cancelled" if result == 130 else "failed")
            if result == 130:
                state["cancelled"].set()
            return result
        finally:
            del _batch.state
            del _batch.item

    workers = min(args.jobs or len(links), len(links))
    status("Task", journal.identifier)
    status("Batch", f"{workers} downloads running in parallel" + (f" · {len(links) - workers} queued" if workers < len(links) else ""))
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="linkget-download")
    futures = {}
    try:
        futures = {executor.submit(run_item, index, link): index for index, link in pairs}
        pending = set(futures)
        next_progress = time.monotonic() + PROGRESS_INTERVAL
        while pending:
            try:
                request = state["interactions"].get(timeout=0.05)
            except queue.Empty:
                request = None
            if request:
                _batch.state = state
                _batch.item = request["item"]
                try:
                    with state["output"]:
                        check_batch_cancelled()
                        request["result"] = request["callback"]()
                except BaseException as error:
                    request["error"] = error
                    if isinstance(error, (KeyboardInterrupt, EOFError)):
                        state["cancelled"].set()
                finally:
                    request["done"].set()
                    del _batch.state
                    del _batch.item
            if time.monotonic() >= next_progress:
                with state["output"]:
                    for index, entry in list(state["progress"].items()):
                        _batch.state, _batch.item = state, (index, len(journal.data["items"]))
                        try:
                            size = ""
                            if entry["directory"] is not None:
                                try:
                                    size = format_size(downloaded_bytes(entry["directory"])) + " · "
                                except OSError:
                                    pass
                            status("Progress", f"{entry['phase']} · {size}{time.monotonic() - entry['started']:.0f}s")
                        finally:
                            del _batch.state
                            del _batch.item
                next_progress = time.monotonic() + PROGRESS_INTERVAL
            done, pending = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
            for future in done:
                if not future.cancelled():
                    results[futures[future]] = future.result()
            if state["cancelled"].is_set():
                for future in pending:
                    future.cancel()
    except KeyboardInterrupt:
        state["cancelled"].set()
        for pending in futures:
            pending.cancel()
        status("Stopping", "Waiting for active requests to finish; downloaded files will be kept.", "33")
    finally:
        executor.shutdown(wait=True, cancel_futures=state["cancelled"].is_set())
        for future, index in futures.items():
            if future.done() and not future.cancelled() and index not in results:
                results[index] = future.result()
        if records is not None:
            records.extend(record for item in item_records.values() for record in item)
        for index, _ in pairs:
            if index not in results:
                journal.update(index, status="cancelled" if state["cancelled"].is_set() else "failed")
    completed = sum(result == 0 for result in results.values())
    failed = sorted(index for index, result in results.items() if result not in {0, 130})
    if state["cancelled"].is_set():
        status("Batch", f"{completed} completed · {len(failed)} failed · {len(links) - completed - len(failed)} not completed", "33")
        status("Retry", "linkget retry " + journal.identifier, "33")
        return 130
    status("Batch", f"{completed} completed · {len(failed)} failed", "33" if failed else "32")
    if failed:
        status("Failed items", ", ".join(map(str, failed)), "33")
        status("Retry", "linkget retry " + journal.identifier, "33")
    return 1 if failed else 0
