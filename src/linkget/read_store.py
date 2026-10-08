"""Private settings, checkpoints and bounded text caches for content reads."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

if __package__:
    from . import accounts
else:
    import accounts

SCHEMA = 2


def root():
    return accounts.directory().parent


def read_json(path):
    if path.is_symlink():
        raise ValueError('Refusing a substituted read record.')
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Invalid read record.')
    return value


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('Refusing a substituted read record.')
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.read-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            temporary.chmod(0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


@contextmanager
def lock(path, wait=False):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            raise RuntimeError('This read record is already in use.') from None
        yield
    finally:
        os.close(fd)


def settings(changes=None):
    path = root() / 'read-settings.json'
    with lock(root() / '.read-settings.lock'):
        current = read_json(path) if path.exists() else {}
        if changes is not None:
            for key, value in changes.items():
                if value is None:
                    current.pop(key, None)
                else:
                    current[key] = value
            write_json(path, current)
        return current


def model_default(config):
    if config.get('model'):
        return Path(config['model']).expanduser()
    candidate = Path.home() / 'Library/Application Support/PhotoSearch/models/whisper/ggml-small.bin'
    return candidate if candidate.is_file() else None


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        while data := stream.read(1024 * 1024):
            h.update(data)
    return h.hexdigest()


def cache_key(source, language, model, subtitles, ocr, chunk_seconds=600, vad_model=None):
    model_hash = digest(model) if model is not None and model.is_file() else None
    return hashlib.sha256(json.dumps([SCHEMA, source, language, model_hash, subtitles, ocr, chunk_seconds,
                                      digest(vad_model) if vad_model else None], sort_keys=True).encode()).hexdigest()


def cached(key, max_age=86400):
    path = root() / 'read-cache' / (key + '.json')
    if not path.exists():
        return None
    try:
        value = read_json(path)
        if value.get('schema') != SCHEMA or time.time() - value['created'] > max_age:
            return None
        result = value['content']
        if not isinstance(result, dict) or result.get('content_complete') is not True or not result.get('text'):
            return None
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None


def cache(key, content):
    write_json(root() / 'read-cache' / (key + '.json'), {'schema': SCHEMA, 'created': time.time(), 'content': content})


def checkpoint(folder, source, options):
    value = {'schema': SCHEMA, 'id': uuid.uuid4().hex, 'folder': str(folder), 'source': source,
             'options': options, 'status': 'running', 'created': time.time()}
    save_job(folder, value)
    return value


def save_job(folder, value):
    write_json(folder / '.read-job.json', value)
    write_json(root() / 'read-jobs' / (value['id'] + '.json'),
               {'folder': str(folder), 'status': value['status'], 'created': value['created']})


def unfinished():
    values = []
    for path in (root() / 'read-jobs').glob('*.json'):
        try:
            value = read_json(path)
            if value.get('status') != 'completed' and (Path(value.get('folder', '')) / '.read-job.json').is_file():
                values.append(value)
        except (OSError, ValueError):
            continue
    return sorted(values, key=lambda v: v.get('created', 0), reverse=True)
