"""Save complete retained downloads or verify Photos receipts without downloading."""
from pathlib import Path
from contextlib import nullcontext
import subprocess
from urllib.parse import urlsplit

if __package__:
    from . import jobs, media_limits, staging, transfers, ownership
    from .failures import redact
    from .photos import originals_directory, verify_library_copies, digest
    from .media import inspect_files
    from .live import prepare_pairs, save_pairs
    from .output import saving_activity, batch_media
else:
    import jobs, media_limits, staging, transfers, ownership
    from failures import redact
    from photos import originals_directory, verify_library_copies, digest
    from media import inspect_files
    from live import prepare_pairs, save_pairs
    from output import saving_activity, batch_media


def listing(status, records=None):
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
            if records is not None:
                records.append({
                    "task_id": journal.identifier,
                    "pending_count": len(pending),
                    "ready_count": ready,
                    "pending_items": pending,
                })
            status("Task", f"{journal.identifier} · {len(pending)} need attention · {ready} ready to save")
            for index in pending:
                row = journal.data["items"][index - 1]
                try:
                    progress = transfers.summary(row)
                    if progress:
                        status("Media", f"Item {index}: {progress}")
                except (ValueError, TypeError):
                    status("Media", f"Item {index}: retained media manifest needs repair; use retry --redownload")
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
    if records == []:
        return 0
    jobs.verified_files(records, root)
    transfers.verify_outputs()
    removed = 0
    for record in records:
        # A matching old hash is a read check, never fresh cleanup authority.
        if record.get('created') is not True or 'file_identity' not in record:
            continue
        proof = ownership.FileProof.from_record(record)
        if proof.path.exists():
            transfers.verify_outputs()
            proof.remove()
            removed += 1
    return removed


def restore(journal, args, status, save_folder, import_photos, claimed=False, indices=None, audio_folder_resolver=None, records=None):
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
            entry = {"item": index, "status": "failed"}
            try:
                with jobs.item(journal, index):
                    staging.remember_earlier(status)
                    if args.verify:
                        ready_ev = (row.get("ready") or {}).get("evidence", {})
                        media = ready_ev.get("files", [])
                        if (getattr(args, "audio", False) or ready_ev.get("audio_mode") is True
                                or (bool(media) and all(record.get("kind") == "audio" for record in media))):
                            raise ValueError("--verify applies to Photos imports only, not audio files.")
                        receipt = row.get("receipt")
                        if not receipt or not receipt.get("identifiers"):
                            raise ValueError("No Photos import identifiers; use recover without --verify to save again.")
                        files = jobs.verified_files(receipt["files"], originals_directory())
                        staged = (ownership.FileList() if receipt.get('staged') == [] else
                                  jobs.verified_files(receipt["staged"], originals_directory()))
                        managed = verify_library_copies(files, receipt["identifiers"])
                        if not isinstance(managed, ownership.FileList):
                            raise RuntimeError('Managed copy verification supplied no file evidence; originals were kept.')
                        jobs.record(destination_files=[])
                        transfers.record_outputs(managed.proofs.values())
                        ready = row.get("ready")
                        stage = Path(row.get("stage", ""))
                        if ready:
                            if not stage.name.startswith("linkget-") or stage.is_symlink() or not stage.is_dir():
                                raise ValueError("The staging folder changed; retained files have been kept.")
                            jobs.verified_files(ready["files"], stage)
                            for pair in ready.get("prepared", []):
                                jobs.verified_files(pair, stage)
                        removed = remove_verified(receipt["staged"], originals_directory())
                        if ready:
                            remove_verified(ready["files"], stage)
                            for pair in ready.get("prepared", []):
                                remove_verified(pair, stage)
                        kept = sum(path.exists() for path in set(staged))
                        retention = f" · {kept} kept" if kept else ""
                        status("Verified", f"Item {index} · Photos originals match · {removed} staged copies removed{retention}")
                        if row.get("stage"):
                            status("Files kept", row["stage"])
                        cleanup_folder = args.folder
                        entry.update(verified=True, removed_staged=removed,
                                     destination=str(cleanup_folder) if cleanup_folder else "Photos")
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
                        saved_audio = (evidence.get("audio_mode") is True
                                       or (bool(media) and all(record.get("kind") == "audio" for record in media)))
                        if getattr(args, "audio", False) and not saved_audio:
                            raise ValueError("The retained download is not audio; use linkget retry TASK --redownload --audio.")
                        is_audio_item = saved_audio
                        if is_audio_item:
                            if getattr(args, "_explicit_photos", False) or getattr(args, "photos", False):
                                raise ValueError("Audio files cannot be imported into Photos. Use --folder.")
                            if getattr(args, "live_photos", False):
                                raise ValueError("--live-photos cannot be used with audio downloads.")
                            target_folder = args.folder
                            if target_folder is None and row.get("destination_folder"):
                                target_folder = Path(row["destination_folder"])
                            if target_folder is None and audio_folder_resolver is not None:
                                target_folder = audio_folder_resolver()
                            if target_folder is None:
                                raise ValueError("Audio recovery requires a folder. Pass --folder PATH or set linkget config --audio-folder PATH.")
                            if __package__:
                                from . import preflight
                            else:
                                import preflight
                            target_folder = preflight.writable_directory(target_folder)
                        else:
                            target_folder = args.folder
                            ordinary_video = bool(media) and all(record.get('kind') == 'video' for record in media)
                            if (douyin and not ordinary_video and (target_folder is None or args.live_photos)
                                    and evidence.get('motion_metadata_complete') is not True):
                                raise ValueError("Retained Douyin motion completeness is unknown. Refresh connector page data "
                                                 "and retry --redownload before Photos import or Live Photo packaging; "
                                                 "separate files can still be recovered with --folder.")
                        stage = Path(row.get("stage", ""))
                        if not stage.name.startswith("linkget-") or stage.is_symlink() or not stage.is_dir():
                            raise ValueError("The recorded staging folder is unavailable; use retry --redownload.")
                        files = jobs.verified_files(ready["files"], stage)
                        transfers.verify(files)
                        inspected = inspect_files(files)
                        batch_media(files)
                        if any(record.get("probe_ok") is False for record in inspected):
                            raise RuntimeError("Retained media failed format validation; files have been kept.")
                        max_dur = media_limits.parse_duration(args.max_duration) if getattr(args, "max_duration", None) else None
                        max_size = media_limits.parse_size(args.max_size) if getattr(args, "max_size", None) else None
                        if max_dur is not None or max_size is not None:
                            media_limits.check_file_limits(files, inspected, max_duration=max_dur, max_size=max_size)
                        pairs = []
                        if not is_audio_item and args.live_photos and ready.get("live_pairs"):
                            prepared = ready.get("prepared", [])
                            if prepared:
                                pairs = [jobs.verified_files(pair, stage) for pair in prepared]
                            else:
                                folder = Path(ready["files"][0]["path"]).parent.parent
                                # A previous failed packaging attempt may occupy the original directory.
                                import tempfile
                                directory = Path(tempfile.mkdtemp(prefix="live-recovery-", dir=folder))
                                directory.rmdir()
                                pairs = prepare_pairs(files, ready["live_pairs"], directory)
                                ready["prepared"] = [jobs.snapshot(pair) for pair in pairs]
                                jobs.record(ready=ready)
                        jobs.record(destination_files=[])
                        with saving_activity("Saving" if target_folder is not None else "Importing"):
                            if target_folder is not None:
                                saved, skipped = save_folder(files, target_folder)
                                if pairs:
                                    save_pairs(pairs, target_folder / "Live Photos")
                            elif pairs:
                                saved, skipped = import_photos(files, args.date_now, dict(zip(map(tuple, ready["live_pairs"]), pairs)))
                            else:
                                saved, skipped = import_photos(files, args.date_now)
                        transfers.verify(files)
                        transfers.verify_outputs()
                        # All output saves passed; remove only the exact checked downloads.
                        if not staging.contains(stage, target_folder):
                            remove_verified(ready["files"], stage)
                            for pair in ready.get("prepared", []):
                                remove_verified(pair, stage)
                        cleanup_folder = target_folder
                        dest_files = [item.get("path") for item in journal.data["items"][index - 1].get("destination_files", []) if item.get("path")]
                        entry.update(verified=False, destination=str(target_folder) if target_folder is not None else "Photos",
                                     saved_count=len(saved), skipped_count=skipped, saved_files=dest_files, audio_mode=is_audio_item)
                        status("Recovered", f"Item {index} · {len(saved)} saved · {skipped} duplicates")
                    staging.cleanup(status, cleanup_folder, photos_verified=args.verify)
                    jobs.record(status="completed")
                    entry["status"] = "completed"
            except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
                failures += 1
                entry["error"] = redact(error)
                status("Kept", f"Item {index}: {redact(error)}", "33")
            finally:
                if records is not None:
                    records.append(entry)
    return 1 if failures else 0
