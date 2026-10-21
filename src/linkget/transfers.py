"""Per-resource native transfer receipts, scoped to one existing download task."""
import copy
import hashlib
from contextlib import contextmanager
from pathlib import Path
import re
import shutil
import threading

if __package__:
    from . import jobs, staging, ownership
else:
    import jobs, staging, ownership

_HASH = re.compile(r'[a-f0-9]{64}')


class TransferSet:
    """Reuse only an exact planned URL's checked bytes from this item's stages.

    URLs are hashed, not persisted here. A changed signed URL is deliberately a
    new resource: there is no inference that two URLs identify identical bytes.
    No task context means ordinary direct-adapter behavior without persistence.
    """
    def __init__(self, directory, site, post_id, plan):
        self.directory = Path(directory)
        self.current = jobs.current()
        self.lock = threading.RLock()
        scope = hashlib.sha256((site + ':' + str(post_id)).encode()).hexdigest()
        old = None
        if self.current:
            journal, index = self.current
            old = journal.data['items'][index - 1].get('media_progress')
        previous = self.validate(old) if old is not None else {}
        if old and old['scope'] != scope:
            raise ValueError('The retained media plan belongs to another post; files were kept. Use retry --redownload.')
        rows = []
        for index, (kind, target) in enumerate(plan, 1):
            key = hashlib.sha256((scope + ':' + str(index) + ':' + kind + ':' + target).encode()).hexdigest()
            row = copy.deepcopy(previous.get(key, {}))
            row.update(key=key, index=index, kind=kind)
            if row.get('state') != 'complete':
                row = dict(key=key, index=index, kind=kind, state='pending')
            rows.append(row)
        if set(previous) - {row['key'] for row in rows}:
            # Metadata refresh is not proof that omitted resources disappeared.
            protected = [row['stage'] for row in previous.values() if row.get('stage')]
            protect(protected)
            raise ValueError('The refreshed media plan omits known resources; the previous plan and files were kept. '
                             'Retry later, or use retry --redownload to explicitly accept a new plan.')
        self.data = dict(schema=1, scope=scope, planned=len(rows), source_total=None, items=rows)
        self.persist()

    @staticmethod
    def validate(data):
        invalid = 'The retained media manifest is invalid; files were kept. Use retry --redownload.'
        if (not isinstance(data, dict) or data.get('schema') != 1 or not _HASH.fullmatch(str(data.get('scope', '')))
                or type(data.get('planned')) is not int or not isinstance(data.get('items'), list)
                or data['planned'] != len(data['items']) or data.get('source_total') is not None):
            raise ValueError(invalid)
        seen, result = set(), {}
        for number, row in enumerate(data['items'], 1):
            if (not isinstance(row, dict) or not _HASH.fullmatch(str(row.get('key', '')))
                    or type(row.get('index')) is not int or row['index'] != number
                    or row.get('kind') not in {'image', 'video'}
                    or row.get('state') not in {'pending', 'complete', 'failed', 'cancelled'}
                    or row['key'] in seen):
                raise ValueError(invalid)
            if row['state'] == 'complete':
                if (not all(isinstance(row.get(name), str) for name in ('path', 'stage', 'sha256'))
                        or not _HASH.fullmatch(row['sha256']) or type(row.get('fallback')) is not bool
                        or not isinstance(row.get('stage_identity'), list) or len(row['stage_identity']) != 2
                        or any(type(value) is not int for value in row['stage_identity'])):
                    raise ValueError(invalid)
            seen.add(row['key'])
            result[row['key']] = row
        return result

    def persist(self):
        if self.current:
            journal, index = self.current
            journal.update(index, media_progress=copy.deepcopy(self.data))

    def reuse(self, index):
        if not self.current:
            return None
        with self.lock:
            row = self.data['items'][index - 1]
            if row['state'] != 'complete':
                return None
            journal, number = self.current
            task = journal.data['items'][number - 1]
            source, recorded_stage = Path(row['path']), Path(row['stage'])
            roots = staging.paths(task)
            if str(recorded_stage) not in roots:
                raise ValueError('Retained media is outside this task; files were kept. Use retry --redownload.')
            root = staging._root(recorded_stage)
            snapshots = task.get('stage_snapshots', {})
            if not isinstance(snapshots, dict):
                raise ValueError('The retained media ownership manifest is invalid; files were kept. Use retry --redownload.')
            snapshot = snapshots.get(str(root), {})
            if not isinstance(snapshot, dict):
                raise ValueError('The retained media ownership record is invalid; files were kept. Use retry --redownload.')
            expected = task.get('stage_identity') if str(recorded_stage) == task.get('stage') else snapshot.get('identity')
            if expected != row['stage_identity'] or staging.identity(root) != expected:
                raise ValueError('A retained media stage was replaced; files were kept. Use retry --redownload.')
            if (not source.is_absolute() or not source.resolve().is_relative_to(root)
                    or any(p.is_symlink() for p in (source, *source.parents) if p == root or root in p.parents)):
                raise ValueError('Retained media path is unsafe; files were kept. Use retry --redownload.')
            if not row.get('file_identity') or not matches(source, row['file_identity'], row['sha256']):
                # An edited/missing file is not reusable, and is never removed.
                row.update(state='pending')
                self.persist()
                return None
            target = self.directory / source.name
            try:
                source_proof = ownership.FileProof.from_record(row)
                copied = ownership.copy(source_proof, target, copier=shutil.copyfileobj)
                self.complete(index, target, row['fallback'], proof=copied)
            except BaseException:
                protect([self.directory.parent], self.current)
                raise
            return target.name, row['fallback']

    def complete(self, index, path, fallback=False, *, proof):
        # Evidence must come from the exclusive writer. No fallback observes
        # the post-download pathname, including calls without a task context.
        if not isinstance(proof, ownership.FileProof) or not proof.created or proof.path != ownership.canonical(path):
            protect([self.directory.parent], self.current)
            raise ValueError('The writer supplied no valid creation evidence; files were kept.')
        try:
            proof.verify()
        except (OSError, ValueError, RuntimeError):
            protect([self.directory.parent], self.current)
            raise
        if not self.current:
            return
        with self.lock:
            stage = self.directory.parent
            row = self.data['items'][index - 1]
            row.update(proof.record(), state='complete', stage=str(stage.resolve()),
                       stage_identity=staging.identity(stage), fallback=bool(fallback))
            row.pop('error_type', None)
            self.persist()
            ownership.register_created([proof], self.current)
            try:
                proof.verify()
            except (OSError, ValueError, RuntimeError):
                protect([stage], self.current)
                raise

    def failed(self, index, error):
        if not self.current:
            return
        with self.lock:
            row = self.data['items'][index - 1]
            row.update(state='cancelled' if isinstance(error, (KeyboardInterrupt, SystemExit)) else 'failed',
                       error_type=type(error).__name__)
            self.persist()


def summary(row):
    data = row.get('media_progress')
    if data is None:
        return None
    TransferSet.validate(data)
    complete = sum(item['state'] == 'complete' for item in data['items'])
    return f'{complete} / {data["planned"]} exposed resources recorded complete; total source count unknown'


def invalidate(files):
    """A failed format probe makes only those transfers ineligible for reuse."""
    current = jobs.current()
    if not current:
        return
    journal, index = current
    data = journal.data['items'][index - 1].get('media_progress')
    if data is None:
        return
    TransferSet.validate(data)
    data = copy.deepcopy(data)
    rejected = {str(Path(path).resolve()) for path in files}
    for row in data['items']:
        if row.get('path') in rejected:
            row.update(state='failed', error_type='FormatValidationError')
    journal.update(index, media_progress=data)


def file_identity(path):
    return list(ownership.file_identity(path))


def matches(path, expected, checksum):
    try:
        ownership.FileProof(ownership.canonical(path), tuple(expected), checksum).verify()
        return True
    except (OSError, ValueError, RuntimeError, TypeError):
        return False


def protect(roots, current=None):
    current = current or jobs.current()
    if current:
        journal, index = current
        row = journal.data['items'][index - 1]
        old = row.get('protected_stages', [])
        if not isinstance(old, list) or any(not isinstance(value, str) for value in old):
            old = []
        journal.update(index, protected_stages=list(dict.fromkeys(old + [str(Path(p).resolve()) for p in roots])))


def record_for(path):
    current = jobs.current()
    if current:
        journal, index = current
        data = journal.data['items'][index - 1].get('media_progress')
        if data:
            TransferSet.validate(data)
            for row in data['items']:
                if row.get('path') == str(Path(path).parent.resolve() / Path(path).name):
                    return row


def verify(files):
    if isinstance(files, ownership.FileList):
        for path in files:
            files.proof(path)
    for path in files:
        row = record_for(path)
        if row:
            try:
                ownership.FileProof.from_record(row).verify()
            except (OSError, ValueError, RuntimeError):
                protect([row['stage']])
                raise


def proofs_for(files):
    if isinstance(files, ownership.FileList):
        return [files.proof(path) for path in files]
    return [proof_for(path) for path in files]


def proof_for(path):
    row = record_for(path)
    if row:
        try:
            return ownership.FileProof.from_record(row).verify()
        except (OSError, ValueError, RuntimeError):
            protect([row['stage']])
            raise
    current = jobs.current()
    if current:
        journal, index = current
        records = journal.data['items'][index - 1].get('owned_files', [])
        if not isinstance(records, list):
            raise ValueError('The created-file receipt is invalid; files were kept.')
        for record in records:
            if record.get('path') == str(ownership.canonical(path)):
                return ownership.FileProof.from_record(record).verify()
    return ownership.FileProof.observe(path)


@contextmanager
def open_verified(path):
    with proof_for(path).open() as stream:
        yield stream


def record_outputs(proofs):
    proofs = list(proofs)
    for proof in proofs:
        proof.verify()
    current = jobs.current()
    if current:
        journal, index = current
        with journal.mutex:
            old = journal.data['items'][index - 1].get('destination_files', [])
            if not isinstance(old, list):
                raise ValueError('The destination receipt is invalid; originals were kept.')
            records = {row['path']: row for row in old}
            for proof in proofs:
                records[str(proof.path)] = proof.record()
            journal.update(index, destination_files=list(records.values()))
    verify_outputs()


def verify_outputs():
    current = jobs.current()
    if current:
        journal, index = current
        row = journal.data['items'][index - 1]
        proofs = row.get('destination_files', [])
        if not isinstance(proofs, list):
            raise ValueError('The destination receipt is invalid; originals were kept.')
        try:
            for proof in proofs:
                ownership.FileProof.from_record(proof).verify(fresh=True)
        except (OSError, ValueError, RuntimeError):
            protect(staging.paths(row))
            raise


def reset_progress():
    """Explicit reset can change a plan, never its old resources' deletion rights."""
    current = jobs.current()
    if current:
        journal, index = current
        row = journal.data['items'][index - 1]
        # Protect before clearing even a malformed manifest. Do not infer that
        # the next response will include every previously completed resource.
        protect(staging.paths(row), current)
        journal.update(index, media_progress=None)
