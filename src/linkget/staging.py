"""Remove only unchanged, item-owned temporary trees after a verified save."""
from pathlib import Path
import shutil
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
    files, directories = {}, []
    for path in root.rglob("*"):
        name = str(path.relative_to(root))
        if path.is_symlink():
            raise ValueError("The temporary folder contains a symbolic link")
        if path.is_dir():
            directories.append(name)
        elif path.is_file():
            files[name] = jobs.digest(path, fresh=True)
        else:
            raise ValueError("The temporary folder contains an unrecognized file")
    return files, sorted(directories)


def remember(path, expected_identity=None):
    current = jobs.current()
    if current is None:
        return
    journal, index = current
    row = journal.data["items"][index - 1]
    key = str(Path(path).resolve())
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
    files, directories = _inventory(root)
    if identity(root) != before:
        raise ValueError("The temporary folder changed during its content check")
    snapshots[key] = {"identity": before, "files": files, "directories": directories}
    jobs.record(stage_snapshots=snapshots)


def remember_earlier(status):
    journal, index = jobs.current()
    row = journal.data["items"][index - 1]
    for path in dict.fromkeys(row.get("previous_stages", []) + ([row["stage"]] if row.get("stage") else [])):
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
    paths = list(dict.fromkeys(row.get("previous_stages", []) + ([row["stage"]] if row.get("stage") else [])))
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
                protected.extend(Path(path).resolve() for path in item.get("previous_stages", [])
                                 + ([item["stage"]] if item.get("stage") else []))
    except (OSError, ValueError, RuntimeError):
        status("Files kept", "Could not check other task receipts before cleanup", "33")
        return
    removed = 0
    snapshots = dict(row.get("stage_snapshots", {}))
    for path in paths:
        if not Path(path).exists() and not Path(path).is_symlink():
            snapshots.pop(str(Path(path).resolve()), None)
            continue
        try:
            root = _root(path)
            if any(target == root or root in target.parents for target in protected):
                raise ValueError("The temporary folder contains a save destination or another task's files")
            record = snapshots.get(str(root))
            if record is None or identity(root) != record["identity"]:
                raise ValueError("The temporary folder has no matching ownership record")
            files, directories = _inventory(root)
            if (any(record["files"].get(name) != checksum for name, checksum in files.items())
                    or any(name not in record["directories"] for name in directories)):
                raise ValueError("The temporary folder contains changed or newly added files")
            if identity(root) != record["identity"]:
                raise ValueError("The temporary folder was replaced")
            shutil.rmtree(root)
            snapshots.pop(str(root), None)
            removed += 1
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            status("Files kept", f"{Path(path).name}: {error}", "33")
    remaining = [path for path in row.get("previous_stages", []) if Path(path).exists() or Path(path).is_symlink()]
    jobs.record(previous_stages=remaining, stage_snapshots=snapshots)
    if removed and (len(paths) > 1 or row.get("ready")):
        status("Cleanup", f"{removed} temporary folder{'s' if removed != 1 else ''} removed")
