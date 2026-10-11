"""Stage Photos imports and verify managed copies before removing source files."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

if __package__:
    from . import accounts
    from .hashing import digest
else:
    import accounts
    from hashing import digest


def originals_directory():
    return accounts.directory().parent / "originals"


def retain_originals(files):
    """Keep stable import sources until managed library copies are verified."""
    root = originals_directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    retained = []
    for source in files:
        checksum = digest(source)
        target = root / ("linkget-" + checksum + source.suffix.lower())
        if target.exists():
            if digest(target) != checksum:
                raise RuntimeError(f"An original failed its content check: {target}. Downloaded files have been kept.")
        else:
            with tempfile.NamedTemporaryFile(dir=root, prefix=".copy-", delete=False) as output:
                temporary = Path(output.name)
                try:
                    with source.open("rb") as input_file:
                        shutil.copyfileobj(input_file, output)
                    output.close()
                    if digest(temporary) != checksum:
                        raise RuntimeError("Original copy verification failed; downloaded files have been kept.")
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
        retained.append(target)
    return retained


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
    for source, asset in zip(files, assets):
        # Final cleanup still requires actual reads of both the staged source
        # and the library copy, even if either was inspected earlier.
        checksum = digest(source, fresh=True)
        size = source.stat().st_size
        if not any(not target.is_symlink() and target.is_file()
                   and target.stat().st_size == size
                   and digest(target, fresh=True) == checksum for target in candidates[asset]):
            raise RuntimeError("Photos has not saved a verified copy of " + source.name
                               + ". Enable Photos > Settings > General > Copy items to the Photos library;"
                               + " use File > Consolidate for referenced items, then retry.")
