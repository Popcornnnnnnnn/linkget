"""Detached read/AI workers with private receipts, results and safe cancellation."""
import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

if __package__:
    from . import read_store
else:
    import read_store

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
FAST = {'config', 'doctor', 'recover', 'status', 'logs', 'cancel', '_worker'}
_current = None


def directory():
    return read_store.root() / 'read-tasks'


def paths(identifier):
    if not re.fullmatch(r'\d{8}T\d{6}-[a-f0-9]{12}', identifier):
        raise ValueError('Use the task ID printed when submitting a read.')
    base = directory() / identifier
    return base.with_suffix('.json'), base.with_suffix('.lock'), base.with_suffix('.worker.lock'), base.with_suffix('.log')


def load(identifier):
    value = read_store.read_json(paths(identifier)[0])
    if value.get('schema') != 1 or value.get('id') != identifier or value.get('status') not in TERMINAL | {'queued', 'running'} or not isinstance(value.get('argv'), list) or not all(isinstance(v, str) for v in value['argv']):
        raise ValueError('Invalid background task receipt.')
    return value


def update(identifier, **changes):
    record, lock, _, _ = paths(identifier)
    with read_store.lock(lock, wait=True):
        value = load(identifier)
        value.update(changes)
        read_store.write_json(record, value)
        return value


def progress(**changes):
    if _current:
        update(_current, **changes)


def record_output(path):
    if not _current:
        return
    record, lock, _, _ = paths(_current)
    with read_store.lock(lock, wait=True):
        value = load(_current)
        value['outputs'] = list(dict.fromkeys([*value.get('outputs', []), str(Path(path).resolve())]))
        read_store.write_json(record, value)


def worker_file():
    return Path(__file__).resolve().with_name('cli.py')


def active(value):
    pid = value.get('pid')
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        if os.getpgid(pid) != pid:
            return False
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, timeout=2)
        return result.returncode == 0 and str(worker_file()) in result.stdout and result.stdout.rstrip().endswith(' read _worker ' + value['id'])
    except (OSError, subprocess.SubprocessError):
        return False


def refresh(identifier):
    record, lock, _, _ = paths(identifier)
    with read_store.lock(lock, wait=True):
        value = load(identifier)
        if value['status'] not in TERMINAL and not active(value) and (value['status'] == 'running' or time.time() - value['created'] > 10):
            value.update(status='cancelled' if value.get('cancel_requested') else 'interrupted', phase='worker stopped; source/checkpoints retained', finished=time.time())
            read_store.write_json(record, value)
        return value


def show(value):
    print(value['id'] + '  ' + value['status'] + '  ' + value.get('phase', value['kind']))
    if value.get('model'):
        print('Model: ' + value['model'])
    for output in value.get('outputs', []):
        print('Result: ' + output)
    if value['status'] in {'failed', 'interrupted', 'cancelled'}:
        print('Log: ' + str(paths(value['id'])[3]))


def status(argv):
    parser = argparse.ArgumentParser(prog='linkget read status', description='Show phase, model and result paths. Failed, cancelled or interrupted tasks return a nonzero exit code.')
    parser.add_argument('task', nargs='?', help='Submitted task ID; omit to list the latest 20 tasks')
    args = parser.parse_args(argv)
    if args.task:
        value = refresh(args.task)
        show(value)
        return int(value['status'] in {'failed', 'cancelled', 'interrupted'})
    records = sorted(directory().glob('*.json'), reverse=True)[:20]
    if not records:
        print('No background read tasks.')
    for record in records:
        show(refresh(record.stem))
    return 0


def logs(argv):
    parser = argparse.ArgumentParser(prog='linkget read logs', description='Show the private log of a background read task.')
    parser.add_argument('task', help='Submitted task ID')
    parser.add_argument('--tail', type=int, default=50, help='Last 1–1000 lines; default: 50')
    args = parser.parse_args(argv)
    if not 1 <= args.tail <= 1000:
        raise ValueError('Use --tail between 1 and 1000.')
    load(args.task)
    path = paths(args.task)[3]
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - 256_000))
        lines = stream.read().decode('utf-8', errors='replace').splitlines()
    print('\n'.join(lines[-args.tail:]))
    return 0


def cancel(argv):
    parser = argparse.ArgumentParser(prog='linkget read cancel', description='Stop the verified task and its child tools; retain source text and recognition checkpoints.')
    parser.add_argument('task', help='Submitted task ID')
    args = parser.parse_args(argv)
    value = refresh(args.task)
    if value['status'] in TERMINAL:
        show(value)
        return 0
    value = update(args.task, cancel_requested=True, phase='cancellation requested')
    # Verify the exact detached worker command before signalling its process group.
    if value['status'] == 'running' and active(value):
        try:
            os.killpg(value['pid'], signal.SIGINT)
        except ProcessLookupError:
            pass
    print('Cancellation requested: ' + args.task)
    return 0


def submit(argv, kind):
    directory().mkdir(parents=True, exist_ok=True, mode=0o700)
    identifier = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:12]
    record, _, _, log = paths(identifier)
    read_store.write_json(record, {'schema': 1, 'id': identifier, 'kind': kind, 'argv': argv,
        'created': time.time(), 'status': 'queued', 'phase': 'queued', 'outputs': [], 'pid': None})
    try:
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            child = subprocess.Popen([sys.executable, str(worker_file()), 'read', '_worker', identifier],
                cwd=os.getcwd(), env={**os.environ, 'PYTHONUNBUFFERED': '1', 'LINKGET_BACKGROUND_JOB': '1', 'LINKGET_HOME': str(read_store.root().resolve())},
                stdin=subprocess.DEVNULL, stdout=stream, stderr=stream, start_new_session=True, close_fds=True)
        threading.Thread(target=child.wait, name='linkget-worker-reaper', daemon=True).start()
        # The worker may already be running or complete: never overwrite its state.
        update(identifier, pid=child.pid)
    except (OSError, RuntimeError):
        update(identifier, status='failed', phase='worker could not start', finished=time.time())
        raise
    print('Task: ' + identifier)
    print('Status: linkget read status ' + identifier)
    print('Log: linkget read logs ' + identifier)
    return 0


def worker(identifier):
    global _current
    _, _, claim, _ = paths(identifier)
    with read_store.lock(claim):
        record, lock, _, _ = paths(identifier)
        with read_store.lock(lock, wait=True):
            value = load(identifier)
            if value['status'] != 'queued':
                raise ValueError('This task has already started; submit a new task instead.')
            if not value['argv'] or value['argv'][0] in FAST:
                raise ValueError('This receipt is not a processing task.')
            if value.get('cancel_requested'):
                value.update(status='cancelled', finished=time.time())
                read_store.write_json(record, value)
                return 130
            value.update(status='running', phase=value['kind'], pid=os.getpid(), started=time.time())
            read_store.write_json(record, value)
        _current = identifier
        def interrupt(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, interrupt)
        code = 1
        try:
            if __package__:
                from . import content
            else:
                import content
            code = content.main(value['argv'])
        except (KeyboardInterrupt, EOFError):
            code = 130
        except SystemExit as error:
            code = error.code if isinstance(error.code, int) else 1
        except Exception:
            print('Background processing failed; source/checkpoints were preserved.', file=sys.stderr)
            code = 1
        finally:
            final = 'completed' if code == 0 else 'cancelled' if code == 130 else 'failed'
            update(identifier, status=final, phase=final, exit_code=code, finished=time.time())
            _current = None
        return code


def main(argv):
    try:
        if argv and argv[0] == '_worker':
            if len(argv) != 2:
                raise ValueError('Invalid worker invocation.')
            return worker(argv[1])
        if argv and argv[0] in {'status', 'logs', 'cancel'}:
            return {'status': status, 'logs': logs, 'cancel': cancel}[argv[0]](argv[1:])
        if __package__:
            from . import content, read_ai
        else:
            import content, read_ai
        if not argv or argv[0] in FAST or '-h' in argv or '--help' in argv or '--foreground' in argv:
            return content.main([arg for arg in argv if arg != '--foreground'])
        kind = argv[0] if argv[0] in {'translate', 'summarize', 'retry', 'batch'} else 'read'
        if kind in {'translate', 'summarize'}:
            args = read_ai.arguments(argv)
            source = read_store.read_json(args.folder.expanduser().resolve() / 'content.json')
            if source.get('content_complete') is not True or not source.get('text'):
                raise ValueError('AI processing requires complete source text.')
        elif kind == 'retry':
            if len(argv) > 2:
                raise ValueError('Use linkget read retry [FOLDER].')
            pending = read_store.unfinished() if len(argv) == 1 else []
            folder = Path(argv[1]).expanduser().resolve() if len(argv) == 2 else Path(pending[0]['folder']) if pending else None
            if folder is None:
                raise ValueError('No unfinished read was found.')
            read_store.read_json(folder / '.read-job.json')
            argv = ['retry', str(folder)]
        else:
            batch = kind == 'batch'
            parser = content.read_parser(batch)
            args = parser.parse_args(argv[1:] if batch else argv)
            if args.profile and args.browser in {'auto', 'none'}:
                parser.error('--profile requires an explicit browser')
            if args.language and not re.fullmatch(r'auto|[a-z]{2,3}', args.language):
                parser.error('Use auto or a Whisper language code')
            if batch and not args.url:
                if sys.stdin.isatty():
                    print('Paste one link or local file per line; blank line submits the task.')
                    links = []
                    while line := input('> ').strip():
                        links.append(line)
                else:
                    links = [line.strip() for line in sys.stdin if line.strip()]
                if not links:
                    parser.error('The read batch is empty')
                argv = ['batch', *links, *argv[1:]]
        return submit(argv, kind)
    except (OSError, ValueError, RuntimeError) as error:
        print('Read task stopped: ' + str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 130
