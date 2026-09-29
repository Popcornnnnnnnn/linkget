"""Stable, content-addressed originals for Photos imports and referenced libraries."""

import hashlib
import os
from pathlib import Path
import shutil
import tempfile

if __package__:
    from . import accounts
else:
    import accounts


def originals_directory():
    return accounts.directory().parent / "originals"


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def retain_originals(files):
    """Never point Photos at disposable download files. Do not delete retained originals."""
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
