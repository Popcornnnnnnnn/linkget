"""Batch scheduling; browser and destination operations remain serialized."""
import argparse
from contextlib import nullcontext
import os
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import queue
import sys
import threading
import time
import re
from urllib.parse import urlsplit
PROGRESS_INTERVAL = 5
if __package__:
    from .runtime import _batch, check_batch_cancelled
    from .failures import retry_delay
    from . import jobs
    from .output import downloaded_bytes, format_size, BatchPanel
    from .links import ResolutionCache
else:
    from runtime import _batch, check_batch_cancelled
    from failures import retry_delay
    import jobs
    from output import downloaded_bytes, format_size, BatchPanel
    from links import ResolutionCache


DEFAULT_JOBS = 3
SITE_INTERVAL = 1.0


def site_key(text):
    """Group known share/mobile aliases without making a network request."""
    match = re.search(r'https?://[^\s<>"，。]+', text)
    try:
        host = (urlsplit(match[0]).hostname or '').lower() if match else ''
    except ValueError:
        host = ''  # The downloader reports malformed links as an item failure.
    aliases = {'xhslink.com': 'xiaohongshu.com', 'xhslink.cn': 'xiaohongshu.com',
               't.cn': 'weibo.com', 'weibo.cn': 'weibo.com', 'b23.tv': 'bilibili.com',
               'youtu.be': 'youtube.com', 'iesdouyin.com': 'douyin.com',
               'tiktokv.com': 'tiktok.com', 'twitter.com': 'x.com'}
    parts = host.split('.')
    base = '.'.join(parts[-2:])
    return aliases.get(base, base)


class SiteGate:
    def __init__(self):
        self.condition = threading.Condition()
        self.busy = set()
        self.next_start = {}

    def enter(self, key):
        with self.condition:
            if key in self.busy or time.monotonic() < self.next_start.get(key, 0):
                return False
            self.busy.add(key)
            return True

    def leave(self, key):
        with self.condition:
            self.busy.discard(key)
            self.next_start[key] = time.monotonic() + SITE_INTERVAL
            self.condition.notify_all()


def retry_wait(delay, state):
    if state['cancelled'].wait(delay):
        raise KeyboardInterrupt


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
    panel = (BatchPanel(pairs, str(args.folder.expanduser().resolve()) if args.folder else "Photos")
             if sys.stdout.isatty() and os.environ.get("TERM") != "dumb" else None)
    state["panel"] = panel
    item_records = {index: [] for index, _ in pairs}
    results = {}
    gate = SiteGate()

    def run_item(index, link, key):
        _batch.state = state
        _batch.item = (index, len(journal.data["items"]))
        result = 1
        try:
            if panel:
                with state["output"]:
                    panel.phase(index, "Preparing")
            item_args = argparse.Namespace(**vars(args))
            item_args.link = link
            with jobs.item(journal, index):
                jobs.record(status="running")
                for attempt in range(3):
                    item_args._retry_failure = None
                    item_args._retry_after = None
                    jobs.record(attempt=attempt + 1)
                    item_records[index].clear()
                    if attempt:
                        item_args.redownload = False
                    result = download(item_args, setup_results, item_records[index])
                    delay = retry_delay(item_args._retry_failure, attempt + 1, operation='read',
                                        retry_after=item_args._retry_after)
                    if result != 1 or delay is None:
                        break
                    with state['output']:
                        status('Retrying', f'Temporary failure · attempt {attempt + 2}/3 in {delay:g}s')
                    retry_wait(delay, state)
                if result == 130:
                    state["cancelled"].set()
                jobs.record(status="completed" if result == 0 else "cancelled" if result == 130 else "failed")
            if result == 130:
                state["cancelled"].set()
            return result
        except (KeyboardInterrupt, EOFError):
            state['cancelled'].set()
            journal.update(index, status='cancelled')
            result = 130
            return result
        finally:
            gate.leave(key)
            if panel:
                with state["output"]:
                    panel.complete(index, result)
            del _batch.state
            del _batch.item

    workers = min(args.jobs or DEFAULT_JOBS, len(links))
    if panel:
        panel.render()
    else:
        status("Batch", f"{workers} download slots · one per website · {len(links)} posts")
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="linkget-download")
    futures = {}
    try:
        queued = list(pairs)
        pending = set()
        next_progress = time.monotonic() + (0.2 if panel else PROGRESS_INTERVAL)
        while pending or queued:
            if state['cancelled'].is_set():
                queued.clear()
            for index, link in list(queued):
                if len(pending) >= workers:
                    break
                key = site_key(link)
                if not gate.enter(key):
                    continue
                future = executor.submit(run_item, index, link, key)
                futures[future] = index
                pending.add(future)
                queued.remove((index, link))
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
                        with panel.interaction() if panel else nullcontext():
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
                    if panel:
                        panel.render()
                    for index, entry in ([] if panel else list(state["progress"].items())):
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
                next_progress = time.monotonic() + (0.2 if panel else PROGRESS_INTERVAL)
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
        with state["output"]:
            if panel:
                panel.erase()
            status("Stopping", "Waiting for active requests to finish; downloaded files will be kept.", "33")
            if panel:
                panel.render()
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
                if panel:
                    panel.complete(index, 130 if state["cancelled"].is_set() else 1)
        if panel:
            panel.finish()
            if args.folder:
                status("Folder", panel.destination)
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
