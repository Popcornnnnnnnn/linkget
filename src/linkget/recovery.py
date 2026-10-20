"""Save complete retained downloads or verify Photos receipts without downloading."""
from pathlib import Path
from contextlib import nullcontext
import subprocess
from urllib.parse import urlsplit

if __package__:
    from . import jobs, staging
    from .photos import originals_directory, verify_library_copies, digest
    from .media import inspect_files
    from .live import prepare_pairs, save_pairs
else:
    import jobs, staging
    from photos import originals_directory, verify_library_copies, digest
    from media import inspect_files
    from live import prepare_pairs, save_pairs


def listing(status):
    found = False
    for journal in jobs.all_journals():
        for index, row in enumerate(journal.data["items"], 1):
            if row["status"] == "completed" and row.get("stage") and Path(row["stage"]).is_dir():
                status("Diagnostics", f"Task {journal.identifier} · item {index}: {row['stage']}")
            for path in row.get("previous_stages", []):
                if Path(path).is_dir():
                    status("Earlier files", f"Task {journal.identifier} · item {index}: {path}")
        pending = sorted(set(journal.incomplete() + journal.needs_verification()))
        if pending:
            found = True
            ready = sum(bool(journal.data["items"][i - 1].get("ready")) for i in pending)
            status("Task", f"{journal.identifier} · {len(pending)} need attention · {ready} ready to save")
            for index in pending:
                row = journal.data["items"][index - 1]
                if row.get("stage"):
                    status("Files kept", f"Item {index}: {row['stage']}")
                if row.get("receipt"):
                    status("Photos", f"Item {index} · retained import receipt available")
    root = originals_directory()
    count = sum(p.is_file() and not p.is_symlink() for p in root.iterdir()) if root.is_dir() else 0
    if count:
        status("Originals", f"{count} retained files · {root}")
        status("Safety", "Untracked originals are kept. Re-download their link to verify the Photos copy.")
    if not found:
        status("Recovery", "No unfinished task receipts")
    else:
        status("Next", "linkget retry TASK · linkget recover TASK · linkget recover TASK --verify")
    return 0


def remove_verified(records, root):
    jobs.verified_files(records, root)
    for record in records:
        path = Path(record["path"])
        if path.exists():
            if path.is_symlink() or digest(path) != record["sha256"]:
                raise RuntimeError("A retained file changed; it was kept: " + str(path))
            path.unlink()


def restore(journal, args, status, save_folder, import_photos, claimed=False, indices=None):
    failures = 0
    with nullcontext() if claimed else journal.claim():
        indices = (sorted(set(journal.incomplete() + (journal.needs_verification() if args.verify else [])))
                   if indices is None else indices)
        if args.item is not None:
            if args.item not in indices:
                raise ValueError("That item is not unfinished in this task.")
            indices = [args.item]
        if not indices:
            status("Recovery", "No unfinished items")
            return 0
        for index in indices:
            row = journal.data["items"][index - 1]
            try:
                with jobs.item(journal, index):
                    staging.remember_earlier(status)
                    if args.verify:
                        receipt = row.get("receipt")
                        if not receipt or not receipt.get("identifiers"):
                            raise ValueError("No Photos import identifiers; use recover without --verify to save again.")
                        files = jobs.verified_files(receipt["files"], originals_directory())
                        staged = jobs.verified_files(receipt["staged"], originals_directory())
                        verify_library_copies(files, receipt["identifiers"])
                        ready = row.get("ready")
                        stage = Path(row.get("stage", ""))
                        if ready:
                            if not stage.name.startswith("linkget-") or stage.is_symlink() or not stage.is_dir():
                                raise ValueError("The staging folder changed; retained files have been kept.")
                            jobs.verified_files(ready["files"], stage)
                            for pair in ready.get("prepared", []):
                                jobs.verified_files(pair, stage)
                        remove_verified(receipt["staged"], originals_directory())
                        if ready:
                            remove_verified(ready["files"], stage)
                            for pair in ready.get("prepared", []):
                                remove_verified(pair, stage)
                        status("Verified", f"Item {index} · Photos originals match · {len(staged)} staged copies removed")
                        if row.get("stage"):
                            status("Files kept", row["stage"])
                    else:
                        ready = row.get("ready")
                        if not ready:
                            raise ValueError("The download was incomplete; use linkget retry " + journal.identifier)
                        evidence = ready.get('evidence', {})
                        reference = evidence.get('reference', row.get('input', ''))
                        source = urlsplit(reference if '://' in reference else 'https://' + reference)
                        douyin = (evidence.get('site') == 'Douyin'
                                  or source.hostname in {'douyin.com', 'www.douyin.com', 'v.douyin.com'})
                        media = evidence.get('files', [])
                        ordinary_video = bool(media) and all(record.get('kind') == 'video' for record in media)
                        if (douyin and not ordinary_video and (args.folder is None or args.live_photos)
                                and evidence.get('motion_metadata_complete') is not True):
                            raise ValueError("Retained Douyin motion completeness is unknown. Refresh connector page data "
                                             "and retry --redownload before Photos import or Live Photo packaging; "
                                             "separate files can still be recovered with --folder.")
                        stage = Path(row.get("stage", ""))
                        if not stage.name.startswith("linkget-") or stage.is_symlink() or not stage.is_dir():
                            raise ValueError("The recorded staging folder is unavailable; use retry --redownload.")
                        files = jobs.verified_files(ready["files"], stage)
                        inspected = inspect_files(files)
                        if any(record.get("probe_ok") is False for record in inspected):
                            raise RuntimeError("Retained media failed format validation; files have been kept.")
                        pairs = []
                        if args.live_photos and ready.get("live_pairs"):
                            prepared = ready.get("prepared", [])
                            if prepared:
                                pairs = [tuple(jobs.verified_files(pair, stage)) for pair in prepared]
                            else:
                                folder = Path(ready["files"][0]["path"]).parent.parent
                                # A previous failed packaging attempt may occupy the original directory.
                                import tempfile
                                directory = Path(tempfile.mkdtemp(prefix="live-recovery-", dir=folder))
                                directory.rmdir()
                                pairs = prepare_pairs(files, ready["live_pairs"], directory)
                                ready["prepared"] = [jobs.snapshot(pair) for pair in pairs]
                                jobs.record(ready=ready)
                        if args.folder is not None:
                            saved, skipped = save_folder(files, args.folder)
                            if pairs:
                                save_pairs(pairs, args.folder / "Live Photos")
                        elif pairs:
                            saved, skipped = import_photos(files, args.date_now, dict(zip(map(tuple, ready["live_pairs"]), pairs)))
                        else:
                            saved, skipped = import_photos(files, args.date_now)
                        # All output saves passed; remove only the exact checked downloads.
                        if not staging.contains(stage, args.folder):
                            remove_verified(ready["files"], stage)
                            for pair in ready.get("prepared", []):
                                remove_verified(pair, stage)
                        status("Recovered", f"Item {index} · {len(saved)} saved · {skipped} duplicates")
                    staging.cleanup(status, args.folder)
                    jobs.record(status="completed")
            except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
                failures += 1
                status("Kept", f"Item {index}: {error}", "33")
    return 1 if failures else 0
