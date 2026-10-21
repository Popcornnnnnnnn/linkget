"""Immutable file evidence shared by writers, consumers and cleanup.

A pathname never grants ownership. New copies/native downloads carry the
exclusive writer descriptor's identity and the hash of bytes actually written.
Existing inputs/duplicates are observed once with identity surrounding the hash
read, then that evidence is carried forward instead of being re-established.
"""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import secrets
import shutil
import stat

def _jobs():
    # Persistence is an upper layer; importing it while defining Writer creates
    # a cycle through accounts -> adapters -> transfer evidence.
    if __package__:
        from . import jobs
    else:
        import jobs
    return jobs


def canonical(path):
    path = Path(path)
    return path.parent.resolve() / path.name


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def file_identity(path):
    info = Path(path).lstat()
    if not stat.S_ISREG(info.st_mode):
        raise RuntimeError('Media is no longer a regular file; files were kept.')
    return identity(info)


@dataclass(frozen=True)
class FileProof:
    path: Path
    identity: tuple
    sha256: str
    created: bool = False

    @classmethod
    def observe(cls, path, expected_hash=None):
        """Bind an existing input/duplicate before its first content read."""
        path = canonical(path)
        before = file_identity(path)
        checksum = _jobs().digest(path)
        proof = cls(path, before, checksum)
        if expected_hash is not None and checksum != expected_hash:
            raise RuntimeError('A file failed its content check; originals were kept.')
        proof.verify()
        return proof

    @classmethod
    def from_record(cls, row):
        value = row.get('file_identity')
        if (not isinstance(row.get('path'), str) or not isinstance(value, (list, tuple))
                or len(value) != 5 or any(type(part) is not int for part in value)
                or not re.fullmatch(r'[a-f0-9]{64}', str(row.get('sha256', '')))):
            raise ValueError('This retained file has no valid ownership evidence; files were kept.')
        return cls(canonical(row['path']), tuple(value), row['sha256'], row.get('created') is True)

    def record(self):
        return dict(path=str(self.path), file_identity=list(self.identity), sha256=self.sha256, created=self.created)

    def verify(self, *, fresh=False):
        if (file_identity(self.path) != self.identity
                or _jobs().digest(self.path, fresh=fresh) != self.sha256
                or file_identity(self.path) != self.identity):
            raise RuntimeError('A verified file was replaced or changed; originals were kept.')
        return self

    @contextmanager
    def open(self):
        self.verify()
        with self.path.open('rb') as stream:
            if identity(os.fstat(stream.fileno())) != self.identity:
                raise RuntimeError('A verified file changed before opening; originals were kept.')
            yield stream
            if identity(os.fstat(stream.fileno())) != self.identity:
                raise RuntimeError('A verified file changed while reading; originals were kept.')
            self.verify()

    def remove(self):
        """Claim the checked inode, preserving a replacement at either boundary."""
        if not self.created:
            raise ValueError('An observed file has no creation-time cleanup authority; files were kept.')
        self.verify(fresh=True)
        quarantine = self.path.parent / ('.linkget-cleanup-' + secrets.token_hex(16))
        quarantine.mkdir(mode=0o700)
        claimed = quarantine / 'claimed'
        try:
            os.rename(self.path, claimed)
            actual = file_identity(claimed)
            if (actual[:4] != self.identity[:4] or _jobs().digest(claimed, fresh=True) != self.sha256
                    or file_identity(claimed) != actual):
                raise RuntimeError('A file changed while being claimed; files were kept.')
            claimed.unlink()
        except BaseException:
            if claimed.exists():
                try:
                    os.link(claimed, self.path, follow_symlinks=False)
                except OSError:
                    pass  # Keep claimed bytes as well if the pathname is occupied.
                else:
                    claimed.unlink()
            raise
        finally:
            try:
                quarantine.rmdir()
            except OSError:
                pass


class Writer:
    """Exclusive writer whose evidence never comes from a later pathname."""
    def __init__(self, path, *, external=False, register=False):
        self.proof = None
        self.external = external
        self.register = register
        self.path = Path(path)
        self.stream = self.path.open('x+b' if external else 'xb', buffering=0)
        os.fchmod(self.stream.fileno(), 0o600)
        self.last_identity = identity(os.fstat(self.stream.fileno()))
        self.checksum = hashlib.sha256()

    @property
    def name(self):
        return self.stream.name

    def fileno(self):
        return self.stream.fileno()

    def write(self, data):
        if identity(os.fstat(self.fileno())) != self.last_identity:
            raise RuntimeError('An active output changed; files were kept.')
        if isinstance(data, str):
            data = data.encode()
        view = memoryview(data)
        written = 0
        while written < len(view):
            count = self.stream.write(view[written:])
            if not count:
                raise OSError('Output write made no progress')
            self.checksum.update(view[written:written + count])
            written += count
            self.last_identity = identity(os.fstat(self.fileno()))
        return written

    def flush(self):
        self.stream.flush()

    def seal(self):
        self.flush()
        if self.external:
            # Trusted child processes write the descriptor we exclusively created.
            # Read that pinned descriptor, never reopen its mutable pathname.
            before = identity(os.fstat(self.fileno()))
            if before[:2] != self.last_identity[:2]:
                raise RuntimeError('An output descriptor changed; files were kept.')
            self.stream.seek(0)
            checksum = hashlib.sha256()
            for chunk in iter(lambda: self.stream.read(1024 * 1024), b''):
                checksum.update(chunk)
            if identity(os.fstat(self.fileno())) != before:
                raise RuntimeError('An output changed during completion; files were kept.')
            self.stream.seek(0, os.SEEK_END)
            return FileProof(canonical(self.path), before, checksum.hexdigest(), True).verify()
        if identity(os.fstat(self.fileno())) != self.last_identity:
            raise RuntimeError('An active output changed before completion; files were kept.')
        return FileProof(canonical(self.path), self.last_identity, self.checksum.hexdigest(), True).verify()

    def _own_metadata_change(self):
        current = identity(os.fstat(self.fileno()))
        if current[:4] != self.last_identity[:4]:
            raise RuntimeError('An output changed during publication; files were kept.')
        self.last_identity = current

    def publish(self, target):
        """Publish without overwriting a pre-existing target; keep the fd pinned."""
        partial = self.seal()
        os.link(self.path, target, follow_symlinks=False)
        self._own_metadata_change()  # Our new hard link changes ctime.
        linked = FileProof(canonical(target), self.last_identity, partial.sha256, True).verify()
        # Remove only the exact partial inode, never a replacement pathname.
        FileProof(canonical(self.path), linked.identity, linked.sha256, True).remove()
        self._own_metadata_change()  # Removing our partial link changes ctime.
        self.path = Path(target)
        return self.seal()

    def discard(self):
        try:
            FileProof(canonical(self.path), self.last_identity, self.checksum.hexdigest(), True).remove()
        except (OSError, ValueError, RuntimeError):
            pass  # Failed/uncertain outputs are retained, never re-authorized.

    def __enter__(self):
        return self

    def __exit__(self, kind, error, traceback):
        try:
            if self.register:
                try:
                    self.proof = self.seal()
                    register_created([self.proof])
                except (OSError, ValueError, RuntimeError):
                    if kind is None:
                        raise
        finally:
            self.stream.close()


def copy(source, target, *, copier=shutil.copyfileobj):
    """Copy a pinned, proven source and return the exclusive writer's proof."""
    writer = Writer(target)
    try:
        with writer:
            with source.open() as stream:
                copier(stream, writer)
            result = writer.seal()
            if result.sha256 != source.sha256:
                raise RuntimeError('Saved file verification failed; originals were kept.')
        result.verify()
        return result
    except BaseException:
        writer.discard()
        raise


class FileList(list):
    """Preserve the existing list-of-Paths API while carrying immutable proofs."""
    def __init__(self, paths=(), proofs=()):
        super().__init__(paths)
        self.proofs = {proof.path: proof for proof in proofs}

    def proof(self, path, expected_hash=None):
        proof = self.proofs.get(canonical(path))
        if proof is None:
            raise ValueError('The file producer supplied no matching evidence; files were kept.')
        if expected_hash is not None and proof.sha256 != expected_hash:
            raise RuntimeError('Produced bytes do not match the expected source; originals were kept.')
        return proof.verify()


def register_created(proofs, current=None):
    """Record only writer proofs; observation never authorizes stage cleanup."""
    current = current or _jobs().current()
    if current:
        journal, index = current
        with journal.mutex:
            old = journal.data['items'][index - 1].get('owned_files', [])
            if not isinstance(old, list):
                raise ValueError('The created-file receipt is invalid; files were kept.')
            records = {row['path']: row for row in old}
            for proof in proofs:
                if not proof.created:
                    raise ValueError('An observed file cannot be registered as created.')
                proof.verify()
                records[str(proof.path)] = proof.record()
            journal.update(index, owned_files=list(records.values()))
