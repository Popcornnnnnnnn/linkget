"""Stage Photos imports and verify managed copies before removing source files."""

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

if __package__:
    from .hashing import digest
    from . import accounts, ownership
else:
    from hashing import digest
    import accounts, ownership

_originals_lock_state = threading.local()


def originals_directory():
    return accounts.directory().parent / "originals"


@contextmanager
def originals_lock():
    """Serialize shared originals staging, receipt recording, and cleanup across tasks."""
    lock_path = originals_directory().parent / ".originals.lock"
    key = str(lock_path)
    depths = getattr(_originals_lock_state, "depths", None)
    if depths is None:
        depths = {}
        _originals_lock_state.depths = depths
    if depths.get(key, 0) > 0:
        depths[key] += 1
        try:
            yield
        finally:
            depths[key] -= 1
            if depths[key] == 0:
                depths.pop(key, None)
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        depths[key] = 1
        try:
            yield
        finally:
            depths.pop(key, None)
    finally:
        os.close(fd)


def referenced_originals(exclude=None):
    """Return canonical original paths referenced by any other task item's Photos receipt."""
    if __package__:
        from . import jobs
    else:
        import jobs
    excluded_id, excluded_index = (exclude[0].identifier, exclude[1]) if exclude else (None, None)
    referenced = set()
    for journal in jobs.all_journals():
        for number, row in enumerate(journal.data.get("items", []), 1):
            if journal.identifier == excluded_id and number == excluded_index:
                continue
            receipt = row.get("receipt")
            if not receipt:
                continue
            if not isinstance(receipt, dict):
                raise ValueError("Another task has an invalid Photos receipt; originals were kept.")
            for field in ("files", "staged"):
                entries = receipt.get(field, [])
                if not isinstance(entries, list):
                    raise ValueError("Another task has an invalid Photos receipt; originals were kept.")
                for entry in entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                        raise ValueError("Another task has an invalid Photos receipt; originals were kept.")
                    referenced.add(ownership.canonical(entry["path"]))
    return referenced


def retain_originals(files):
    """Keep source-bound writer evidence until managed copies are verified."""
    if __package__:
        from . import transfers
    else:
        import transfers
    root = originals_directory()
    with originals_lock():
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        retained, proofs = [], {}
        for path, source in zip(files, transfers.proofs_for(files)):
            target = root / ("linkget-" + source.sha256 + path.suffix.lower())
            key = ownership.canonical(target)
            if key in proofs:
                proof = proofs[key].verify()
            elif target.exists() or target.is_symlink():
                # Existing shared sources are validated, never newly owned.
                proof = ownership.FileProof.observe(target, source.sha256)
            else:
                proof = ownership.copy(source, target, copier=shutil.copyfileobj)
            source.verify()
            proofs[key] = proof
            retained.append(target)
        for proof in proofs.values():
            proof.verify()
        return ownership.FileList(retained, proofs.values())



def library_directory():
    # Inspect the running Photos process, rather than preferences that can
    # differ between Terminal and other callers. Read only open-file paths:
    # never open the library database or launch/activate Photos here.
    processes = subprocess.run(["/usr/bin/pgrep", "-u", str(os.getuid()), "-x", "Photos"],
                               text=True, capture_output=True, timeout=10)
    pids = processes.stdout.split()
    if processes.returncode or not pids or any(not pid.isdecimal() for pid in pids):
        raise RuntimeError("Could not identify the running Photos app to verify its library.")
    result = subprocess.run(["/usr/sbin/lsof", "-a", "-p", ",".join(pids), "-Fn"],
                            text=True, capture_output=True, timeout=10)
    if result.returncode:
        raise RuntimeError("Could not locate Photos' open library: " + result.stderr.strip())
    internal = Path.home() / "Library/Photos/Libraries/Syndication.photoslibrary"
    libraries = set()
    for line in result.stdout.splitlines():
        if not line.startswith("n"):
            continue
        path = Path(line[1:])
        if path.name == "Photos.sqlite" and path.parent.name == "database":
            library = path.parent.parent
            if library.suffix == ".photoslibrary" and library != internal:
                libraries.add(library)
    if len(libraries) != 1:
        raise RuntimeError("Could not identify a single open Photos library; original files have been kept.")
    library = libraries.pop()
    if not library.is_dir():
        raise RuntimeError("The open Photos library is unavailable or cannot be accessed: " + str(library))
    return library


def verify_library_copies(files, identifiers):
    """Require identical local originals belonging to the returned Photos items.

    An import result or an export alone can also come from a referenced file.
    Check the managed originals themselves; never remove files on uncertainty.
    """
    if len(files) != len(identifiers):
        raise RuntimeError("Photos did not identify every saved item.")
    root = library_directory().resolve() / "originals"
    assets = []
    for identifier in identifiers:
        asset = identifier.split("/")[0]
        if not re.fullmatch(r"[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}", asset):
            raise RuntimeError("Photos returned an invalid saved item identifier.")
        assets.append(asset.upper())
    # A Photos bucket can contain thousands of files. Enumerate each needed
    # bucket once, indexing only the requested assets (including Live pairs).
    candidates = {asset: [] for asset in assets}
    for bucket in {asset[0] for asset in assets}:
        try:
            for target in (root / bucket).iterdir():
                asset = target.name[:36]
                if asset in candidates:
                    candidates[asset].append(target)
        except FileNotFoundError:
            pass
    checked = []
    for source, asset in zip(files, assets):
        source_proof = (files.proof(source) if isinstance(files, ownership.FileList)
                        else ownership.FileProof.observe(source))
        matched = None
        for target in candidates[asset]:
            if target.is_symlink() or not target.is_file() or target.stat().st_size != source_proof.identity[2]:
                continue
            candidate = ownership.FileProof.observe(target)
            if candidate.sha256 == source_proof.sha256:
                candidate.verify(fresh=True)
                matched = candidate
                break
        source_proof.verify(fresh=True)
        if matched is None:
            raise RuntimeError("Photos has not saved a verified copy of " + source.name
                               + ". Enable Photos > Settings > General > Copy items to the Photos library;"
                               + " use File > Consolidate for referenced items, then retry.")
        checked.append(matched)
    for proof in checked:
        proof.verify()
    return ownership.FileList([proof.path for proof in checked], checked)
