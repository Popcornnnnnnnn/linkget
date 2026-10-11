"""Reuse unchanged file digests within one item; verification can force a read."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import threading

_context = threading.local()


@contextmanager
def digest_session():
    previous = getattr(_context, "cache", None)
    if previous is None:
        _context.cache = {}
    try:
        yield
    finally:
        _context.cache = previous


def _signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def digest(path, *, fresh=False):
    path = Path(path)
    key = str(path.absolute())
    cache = getattr(_context, "cache", None)
    signature = _signature(path.stat())
    if not fresh and cache is not None and key in cache and cache[key][0] == signature:
        return cache[key][1]
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        before = _signature(os.fstat(source.fileno()))
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
        after = _signature(os.fstat(source.fileno()))
    if before != after or after != _signature(path.stat()):
        raise RuntimeError("Media changed during its content check; files have been kept: " + path.name)
    value = checksum.hexdigest()
    if cache is not None:
        cache[key] = (after, value)
    return value
