"""Remove only unchanged, item-owned temporary trees after a verified save."""
from pathlib import Path
import os
import secrets
import tempfile

if __package__:
    from . import jobs
else:
    import jobs


def identity(path):
    info = Path(path).lstat()
    return [info.st_dev, info.st_ino]


def contains(root, path):
    return path is not None and Path(path).resolve().is_relative_to(Path(root).resolve())


def _root(path):
    root = Path(path)
    if (not root.is_absolute() or not root.name.startswith("linkget-")
            or root.is_symlink() or root.parent.resolve() != Path(tempfile.gettempdir()).resolve()
            or not root.is_dir()):
        raise ValueError("The recorded temporary folder is unavailable or unsafe")
    return root.resolve()


def _inventory(root):
    if __package__:
        from .transfers import file_identity
    else:
        from transfers import file_identity
    files, directories, identities = {}, [], {}
    for path in root.rglob("*"):
        name = str(path.relative_to(root))
        if path.is_symlink():
            raise ValueError("The temporary folder contains a symbolic link")
        if path.is_dir():
            directories.append(name)
        elif path.is_file():
            before = file_identity(path)
            files[name] = jobs.digest(path, fresh=True)
            if file_identity(path) != before:
                raise ValueError('The temporary file changed during its content check')
            identities[name] = before
        else:
            raise ValueError("The temporary folder contains an unrecognized file")
    return files, sorted(directories), identities


def remember(path, expected_identity=None):
    current = jobs.current()
    if current is None:
        return
    journal, index = current
    row = journal.data["items"][index - 1]
    key = str(Path(path).resolve())
    if not isinstance(row.get("stage_snapshots", {}), dict):
        raise ValueError("The temporary cleanup record is invalid")
    snapshots = dict(row.get("stage_snapshots", {}))
    if key in snapshots:
        return
    if expected_identity is None:
        raise ValueError("The older temporary folder has no creation-time ownership record")
    root = _root(path)
    before = identity(root)
    if expected_identity is not None and before != expected_identity:
        raise ValueError("The temporary folder was replaced")
    # A complete set must still match the media hashes recorded before saving.
    if row.get("ready") and row.get("stage") == key:
        jobs.verified_files(row["ready"]["files"], root)
    if __package__:
        from . import transfers
    else:
        import transfers
    transfers.verify([Path(item['path']) for item in (row.get('media_progress') or {}).get('items', [])
                      if item.get('path') and contains(root, item['path'])])
    files, directories, identities = _inventory(root)
    if identity(root) != before:
        raise ValueError("The temporary folder changed during its content check")
    snapshots[key] = {"identity": before, "files": files, "directories": directories, "file_identities": identities}
    jobs.record(stage_snapshots=snapshots)


def paths(row):
    previous, current = row.get('previous_stages', []), row.get('stage')
    if (not isinstance(previous, list) or any(not isinstance(path, str) for path in previous)
            or current is not None and not isinstance(current, str)):
        raise ValueError('The temporary stage list is invalid; retained files have been kept.')
    return list(dict.fromkeys(previous + ([current] if current else [])))


def remember_earlier(status):
    journal, index = jobs.current()
    row = journal.data["items"][index - 1]
    for path in paths(row):
        if not Path(path).exists():
            continue
        try:
            remember(path, row.get("stage_identity") if path == row.get("stage") else None)
        except (OSError, ValueError, RuntimeError) as error:
            status("Files kept", f"Earlier temporary files: {error}", "33")


def cleanup(status, folder=None):
    """Called only after complete media have passed destination verification."""
    current = jobs.current()
    if current is None:
        return
    journal, index = current
    row = journal.data["items"][index - 1]
    receipt = row.get("receipt")
    # Folder recovery must not discard evidence for an unverified Photos import.
    if receipt and any(Path(item["path"]).exists() for item in receipt.get("staged", [])):
        status("Files kept", "Photos originals still need verification", "33")
        return
    stage_paths = paths(row)
    protected = [jobs.directory().parent.resolve()]
    if folder is not None:
        protected.append(Path(folder).resolve())
    # Keep any other task's sources and any destination nested inside a stage.
    try:
        for other in jobs.all_journals():
            if other.data.get("options", {}).get("folder"):
                protected.append(Path(other.data["options"]["folder"]).resolve())
            for number, item in enumerate(other.data["items"], 1):
                if other.identifier == journal.identifier and number == index:
                    continue
                protected.extend(Path(path).resolve() for path in paths(item))
    except (OSError, ValueError, RuntimeError, TypeError):
        status("Files kept", "Could not check other task receipts before cleanup", "33")
        return
    removed = 0
    if not isinstance(row.get("stage_snapshots", {}), dict):
        status("Files kept", "The temporary cleanup record is invalid", "33")
        return
    snapshots = dict(row.get("stage_snapshots", {}))
    for path in stage_paths:
        if not Path(path).exists() and not Path(path).is_symlink():
            snapshots.pop(str(Path(path).resolve()), None)
            continue
        try:
            root = _root(path)
            protected_stages = row.get('protected_stages', [])
            if not isinstance(protected_stages, list) or any(not isinstance(value, str) for value in protected_stages):
                raise ValueError('The protected stage list is invalid; files are kept')
            if str(root) in protected_stages:
                raise ValueError('A temporary copy was replaced concurrently; this stage is kept for review')
            if any(target == root or root in target.parents for target in protected):
                raise ValueError("The temporary folder contains a save destination or another task's files")
            record = snapshots.get(str(root))
            if (not isinstance(record, dict) or identity(root) != record.get("identity")
                    or not isinstance(record.get("files"), dict) or not isinstance(record.get("directories"), list)):
                raise ValueError("The temporary folder has no matching ownership record")
            files, directories, identities = _inventory(root)
            allowed_directories = set(record["directories"])
            # Recovery can create new paired exports. Their checked media are
            # removed before this call, leaving known empty output directories.
            for pair in (row.get("ready") or {}).get("prepared", []):
                for item in pair:
                    target = Path(item["path"]).resolve()
                    if root in target.parents:
                        allowed_directories.update(str(parent) for parent in target.relative_to(root).parents if str(parent) != ".")
            recorded_ids = record.get('file_identities')
            if not isinstance(recorded_ids, dict):
                raise ValueError('The temporary folder has no file identity record; files are kept')
            if (any(recorded_ids.get(name) != value for name, value in identities.items())
                    or any(record["files"].get(name) != checksum for name, checksum in files.items())
                    or any(name not in allowed_directories for name in directories)):
                raise ValueError("The temporary folder contains changed or newly added files")
            if identity(root) != record["identity"]:
                raise ValueError("The temporary folder was replaced")
            # Claim each checked inode before deleting it. Never recursively
            # delete the original tree: a concurrent replacement must survive.
            for name, checksum in files.items():
                remove_owned(root / name, checksum, recorded_ids[name])
            for name in sorted(directories, key=lambda value: len(Path(value).parts), reverse=True):
                (root / name).rmdir()
            root.rmdir()
            snapshots.pop(str(root), None)
            removed += 1
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            status("Files kept", f"{Path(path).name}: {error}", "33")
    remaining = [path for path in row.get("previous_stages", []) if Path(path).exists() or Path(path).is_symlink()]
    jobs.record(previous_stages=remaining, stage_snapshots=snapshots)
    if removed and (len(stage_paths) > 1 or row.get("ready")):
        status("Cleanup", f"{removed} temporary folder{'s' if removed != 1 else ''} removed")


def remove_owned(path, checksum, expected):
    """Atomically claim a checked file; restore and retain external replacements."""
    if __package__:
        from .transfers import file_identity, matches
    else:
        from transfers import file_identity, matches
    path = Path(path)
    if not matches(path, expected, checksum):
        raise RuntimeError('A temporary file was replaced or changed; files are kept')
    quarantine = path.parent / ('.linkget-cleanup-' + secrets.token_hex(16))
    quarantine.mkdir(mode=0o700)
    claimed = quarantine / 'claimed'
    try:
        os.rename(path, claimed)
        actual = file_identity(claimed)
        # rename changes ctime, but not inode, size or modification time.
        if (actual[:4] != expected[:4] or jobs.digest(claimed, fresh=True) != checksum
                or file_identity(claimed) != actual):
            raise RuntimeError('A temporary file changed while being claimed; files are kept')
        claimed.unlink()
    except BaseException:
        if claimed.exists():
            try:
                os.link(claimed, path, follow_symlinks=False)
            except OSError:
                # Original pathname occupied: retain the claimed file as well.
                pass
            else:
                claimed.unlink()
        raise
    finally:
        try:
            quarantine.rmdir()
        except OSError:
            pass
