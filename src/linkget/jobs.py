"""Private local task receipts. Share tokens never enter public evidence reports."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid

if __package__:
    from .hashing import digest, digest_session
else:
    from hashing import digest, digest_session

_context = threading.local()
OPTIONS = ("folder", "date_now", "live_photos", "browser", "profile", "jobs", "page_data", "rendered_backend")
INCOMPLETE = {"pending", "running", "failed", "cancelled"}


def directory():
    if __package__:
        from . import accounts
    else:
        import accounts
    return accounts.directory().parent / "jobs"


class Journal:
    def __init__(self, path, data):
        self.path, self.data = path, data
        self.mutex = threading.RLock()

    @classmethod
    def create(cls, links, args):
        if getattr(args, "_journal", None) is not None:
            return args._journal
        root = directory()
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        identifier = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12]
        options = {name: str(value) if isinstance(value, Path) else value
                   for name in OPTIONS if (value := getattr(args, name, None)) is not None}
        if options.get("folder"):
            options["folder"] = str(Path(options["folder"]).expanduser().resolve())
        journal = cls(root / (identifier + ".json"), {
            "schema": 1, "id": identifier, "created_ns": time.time_ns(), "options": options,
            "items": [{"input": link, "status": "pending"} for link in links],
        })
        journal.write()
        return journal

    @classmethod
    def load(cls, identifier):
        if not re.fullmatch(r"\d{8}T\d{6}-[a-f0-9]{12}", identifier):
            raise ValueError("Use the task ID shown by linkget recover.")
        path = directory() / (identifier + ".json")
        if path.is_symlink():
            raise ValueError("A task receipt must not be a symbolic link.")
        data = json.loads(path.read_text())
        if (not isinstance(data, dict) or data.get("schema") != 1 or data.get("id") != identifier
                or not isinstance(data.get("items"), list) or not isinstance(data.get("options"), dict)
                or any(not isinstance(row, dict) or row.get("status") not in INCOMPLETE | {"completed"}
                       or not isinstance(row.get("input"), str) for row in data["items"])):
            raise ValueError("The task receipt is invalid; retained files have not been touched.")
        return cls(path, data)

    @property
    def identifier(self):
        return self.data["id"]

    @contextmanager
    def claim(self):
        """Prevent two commands from retrying or cleaning the same task."""
        fd = os.open(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError("This task is already running in another command.") from None
            # Reload after locking: a previous command may have finished since selection.
            self.data = self.load(self.identifier).data
            yield self
        finally:
            os.close(fd)

    def write(self):
        with tempfile.NamedTemporaryFile(mode="w", dir=self.path.parent, prefix=".task-", delete=False) as output:
            temporary = Path(output.name)
            try:
                json.dump(self.data, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
                output.close()
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)

    def update(self, index, **changes):
        with self.mutex:
            row = self.data["items"][index - 1]
            row.update(changes)
            if row["status"] == "completed":
                # Successful tasks do not need share tokens or import identifiers anymore.
                receipt = row.get("receipt")
                if not receipt or not any(Path(resource["path"]).exists() for resource in receipt.get("staged", [])):
                    receipt = None
                # A folder recovery can leave sources used by a referenced Photos import.
                stage = row.get("stage")
                if not stage or not Path(stage).is_dir():
                    stage = None
                row.update(input="", ready=None, receipt=receipt, stage=stage)
            self.write()

    def incomplete(self):
        return [index for index, row in enumerate(self.data["items"], 1) if row["status"] in INCOMPLETE]

    def needs_verification(self):
        return [index for index, row in enumerate(self.data["items"], 1) if row.get("receipt")]


def all_journals():
    if not directory().is_dir():
        return []
    journals = [Journal.load(path.stem) for path in directory().glob("*.json")]
    return sorted(journals, key=lambda task: task.data.get("created_ns", task.path.stat().st_mtime_ns), reverse=True)


def select(identifier=None):
    if identifier:
        return Journal.load(identifier)
    for journal in all_journals():
        if journal.incomplete():
            return journal
    raise ValueError("No unfinished tasks. Run linkget recover to inspect retained files.")


@contextmanager
def item(journal, index):
    previous = getattr(_context, "item", None)
    _context.item = (journal, index) if journal is not None else None
    try:
        with digest_session():
            yield
    finally:
        _context.item = previous


def record(**changes):
    current = getattr(_context, "item", None)
    if current:
        current[0].update(current[1], **changes)


def active():
    return getattr(_context, "item", None) is not None


def current():
    return getattr(_context, "item", None)


def snapshot(files):
    if __package__:
        from . import transfers
    else:
        import transfers
    return [proof.record() for proof in transfers.proofs_for(files)]


def verified_files(records, root=None):
    if not isinstance(records, list) or not records:
        raise ValueError("This item has no complete media set; use linkget retry instead.")
    if __package__:
        from . import ownership
    else:
        import ownership
    files, proofs = [], []
    for row in records:
        if (not isinstance(row, dict) or not isinstance(row.get("path"), str)
                or not re.fullmatch(r"[a-f0-9]{64}", str(row.get("sha256", "")))):
            raise ValueError("A retained file receipt is invalid; nothing was removed.")
        path = Path(row["path"])
        if root is not None and not path.resolve().is_relative_to(Path(root).resolve()):
            raise ValueError("A retained file is outside its recorded staging folder; nothing was removed.")
        try:
            if 'file_identity' in row:
                proof = ownership.FileProof.from_record(row).verify()
            else:
                # Legacy receipts can authorize a checked read/copy, not deletion.
                proof = ownership.FileProof.observe(path, row['sha256'])
        except (OSError, ValueError, RuntimeError) as error:
            raise RuntimeError('A retained file is missing or changed; nothing was removed: ' + str(path)) from error
        files.append(path)
        proofs.append(proof)
    return ownership.FileList(files, proofs)
