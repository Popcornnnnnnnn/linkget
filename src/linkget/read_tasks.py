"""Detached read/AI workers with private receipts, results and safe cancellation."""
import argparse
from datetime import datetime, timezone
import json
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
    from .failures import redact
    from .output import pad_cell, print_field
    from . import agent_output, read_formats, read_store, runtime
else:
    from failures import redact
    from output import pad_cell, print_field
    import agent_output, read_formats, read_store, runtime

TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
FAST = {'config', 'doctor', 'recover', 'status', 'logs', 'cancel', 'show', 'wait', '_worker'}
_TASK_ID_RE = re.compile(r'\d{8}T\d{6}-[a-f0-9]{12}')
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


def failure(error):
    progress(failure=redact(error))


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
    print(value['id'] + '  ' + pad_cell(value['status'], 11) + '  ' + value.get('phase', value['kind']))
    if value.get('model'):
        print_field('Model:', value['model'], label_width=7)
    for output in value.get('outputs', []):
        print_field('Result:', output, label_width=7)
    if value['status'] in {'failed', 'interrupted', 'cancelled'}:
        print_field('Log:', paths(value['id'])[3], label_width=7)


def _task_payload(value):
    content_path = None
    if value['status'] == 'completed' and value.get('outputs'):
        candidate = Path(value['outputs'][0]) / 'content.json'
        if candidate.is_file() and not candidate.is_symlink():
            content_path = str(candidate)
    return {
        'id': value['id'],
        'kind': value['kind'],
        'status': value['status'],
        'phase': value.get('phase', value['kind']),
        'model': value.get('model'),
        'outputs': list(value.get('outputs', [])),
        'content_path': content_path,
        'failure': value.get('failure'),
    }


def status(argv):
    parser = argparse.ArgumentParser(prog='linkget read status', description='Show phase, model and result paths. Failed, cancelled or interrupted tasks return a nonzero exit code.')
    parser.add_argument('task', nargs='?', help='Submitted task ID; omit to list the latest 20 tasks')
    parser.add_argument('--json', action='store_true', help='Print machine-readable task status')
    parser.add_argument('--non-interactive', action='store_true', help='Disable interactive prompts')
    args = parser.parse_args(argv)
    if args.task:
        value = refresh(args.task)
        code = int(value['status'] in {'failed', 'cancelled', 'interrupted'})
        if args.json:
            agent_output.emit({
                'schema_version': agent_output.SCHEMA_VERSION,
                'ok': code == 0,
                'command': 'read status',
                'task': _task_payload(value),
            })
        else:
            show(value)
        return code
    records = sorted(directory().glob('*.json'), reverse=True)[:20]
    values = [refresh(record.stem) for record in records]
    if args.json:
        agent_output.emit({
            'schema_version': agent_output.SCHEMA_VERSION,
            'ok': True,
            'command': 'read status',
            'tasks': [_task_payload(value) for value in values],
        })
        return 0
    if not values:
        print('No background read tasks.')
    for value in values:
        show(value)
    return 0


def _resolve_read_folder(target):
    if target is None:
        for record in sorted(directory().glob('*.json'), reverse=True):
            value = refresh(record.stem)
            if value['status'] == 'completed' and value.get('kind') not in {'translate', 'summarize'} and value.get('outputs'):
                raw = Path(value['outputs'][0]).expanduser()
                if not raw.is_symlink() and raw.is_dir():
                    return raw.resolve()
        raise ValueError('No completed read task was found.')
    if _TASK_ID_RE.fullmatch(target):
        value = refresh(target)
        if value['status'] in {'queued', 'running'}:
            raise ValueError(f"Read task {target} is still {value['status']}. Wait for it with linkget read wait {target}.")
        if value['status'] != 'completed' or not value.get('outputs'):
            raise ValueError(value.get('failure') or f"Read task {target} did not complete.")
        raw = Path(value['outputs'][0]).expanduser()
        if raw.is_symlink():
            raise ValueError('Read output folder must not be a symbolic link.')
        return raw.resolve()
    raw = Path(target).expanduser()
    if raw.is_symlink():
        raise ValueError('Read output folder must not be a symbolic link.')
    return raw.resolve()


def show_content(argv):
    parser = argparse.ArgumentParser(prog='linkget read show', description='Print the completed content of a read task or folder to stdout.')
    parser.add_argument('target', nargs='?', help='Task ID or read folder; omit for the latest completed read task')
    parser.add_argument('--format', choices=['md', 'json', 'txt'], default=None, help='Output format (default: md, or json when --json is set)')
    parser.add_argument('--json', action='store_true', help='Print raw content.json output')
    parser.add_argument('--non-interactive', action='store_true', help='Disable interactive prompts')
    args = parser.parse_args(argv)
    folder = _resolve_read_folder(args.target)
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Read output folder is unavailable or a symbolic link.')
    content_path = folder / 'content.json'
    if content_path.is_symlink() or not content_path.is_file():
        raise ValueError('No content.json found in ' + str(folder))
    data = read_store.read_json(content_path)
    fmt = args.format or ('json' if args.json else 'md')
    if fmt == 'json':
        print(json.dumps(data, ensure_ascii=False, indent=2), flush=True)
    elif fmt == 'txt':
        txt_path = folder / 'content.txt'
        text = txt_path.read_text() if txt_path.is_file() and not txt_path.is_symlink() else ((data.get('text') or '') + '\n')
        sys.stdout.write(text)
        sys.stdout.flush()
    else:
        md_path = folder / 'content.md'
        text = md_path.read_text() if md_path.is_file() and not md_path.is_symlink() else read_formats.markdown(data)
        sys.stdout.write(text)
        sys.stdout.flush()
    return 0


def wait_task(argv):
    parser = argparse.ArgumentParser(prog='linkget read wait', description='Wait for a background read task to finish and report its terminal status.')
    parser.add_argument('task', nargs='?', help='Submitted task ID; omit for the latest read task')
    parser.add_argument('--timeout', type=float, default=0.0, help='Maximum seconds to wait (0 waits until done; default: 0)')
    parser.add_argument('--interval', type=float, default=0.5, help='Polling interval in seconds (0.1-10.0; default: 0.5)')
    parser.add_argument('--json', action='store_true', help='Print machine-readable completion JSON')
    parser.add_argument('--non-interactive', action='store_true', help='Disable interactive prompts')
    args = parser.parse_args(argv)
    if args.timeout < 0:
        parser.error('--timeout must be >= 0.')
    if not 0.1 <= args.interval <= 10.0:
        parser.error('--interval must be between 0.1 and 10 seconds.')
    task_id = args.task
    if not task_id:
        records = sorted(directory().glob('*.json'), reverse=True)
        if not records:
            raise ValueError('No background read tasks found.')
        task_id = records[0].stem
    deadline = time.monotonic() + args.timeout if args.timeout > 0 else None
    while True:
        value = refresh(task_id)
        if value['status'] in TERMINAL:
            break
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if args.json:
                    agent_output.emit({
                        'schema_version': agent_output.SCHEMA_VERSION,
                        'ok': False,
                        'command': 'read wait',
                        'task_id': task_id,
                        'status': value['status'],
                        'phase': value.get('phase', value['kind']),
                        'timed_out': True,
                        'outputs': list(value.get('outputs', [])),
                    })
                else:
                    print(f"Timed out waiting for {task_id} ({value['status']} · {value.get('phase', value['kind'])})", file=sys.stderr)
                return 124
            time.sleep(min(args.interval, remaining))
        else:
            time.sleep(args.interval)
    code = 0 if value['status'] == 'completed' else 130 if value['status'] == 'cancelled' else 1
    if args.json:
        payload = _task_payload(value)
        agent_output.emit({
            'schema_version': agent_output.SCHEMA_VERSION,
            'ok': code == 0,
            'command': 'read wait',
            'task_id': task_id,
            'timed_out': False,
            **payload,
        })
    else:
        show(value)
    return code


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
    worker_argv = [arg for arg in argv if arg != '--json']
    read_store.write_json(record, {'schema': 1, 'id': identifier, 'kind': kind, 'argv': worker_argv,
        'created': time.time(), 'status': 'queued', 'phase': 'queued', 'outputs': [], 'pid': None,
        'source_folder': str(Path(worker_argv[1]).expanduser().resolve()) if kind in {'translate', 'summarize', 'retry'} and len(worker_argv) > 1 else None})
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
    if runtime.json_mode():
        agent_output.emit({
            'schema_version': agent_output.SCHEMA_VERSION,
            'ok': True,
            'command': 'read',
            'mode': 'background',
            'task_id': identifier,
            'kind': kind,
            'status': 'queued',
            'status_command': 'linkget read status ' + identifier,
            'wait_command': 'linkget read wait ' + identifier,
            'show_command': 'linkget read show ' + identifier,
            'log_command': 'linkget read logs ' + identifier,
        })
    else:
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
        except Exception as error:
            failure(error)
            print('Background processing failed; source/checkpoints were preserved.', file=sys.stderr)
            code = 1
        finally:
            final = 'completed' if code == 0 else 'cancelled' if code == 130 else 'failed'
            update(identifier, status=final, phase=final, exit_code=code, finished=time.time(),
                   failure=None if code == 0 else 'Cancelled by request.' if code == 130 else load(identifier).get('failure') or 'Processing failed (exit ' + str(code) + '); inspect the private log for the cause.')
            _current = None
        return code


def main(argv):
    saved_modes = runtime.snapshot_modes()
    wants_json = '--json' in argv
    try:
        if '--non-interactive' in argv or wants_json:
            runtime.set_non_interactive(True)
        if wants_json:
            runtime.set_json_mode(True)
        if argv and argv[0] == '_worker':
            if len(argv) != 2:
                raise ValueError('Invalid worker invocation.')
            return worker(argv[1])
        if argv and argv[0] in {'status', 'logs', 'cancel', 'show', 'wait'}:
            return {'status': status, 'logs': logs, 'cancel': cancel, 'show': show_content, 'wait': wait_task}[argv[0]](argv[1:])
        if __package__:
            from . import content, read_ai, read_frames
        else:
            import content, read_ai, read_frames
        if not argv or argv[0] in FAST or '-h' in argv or '--help' in argv or '--foreground' in argv or '--stdout' in argv:
            return content.main([arg for arg in argv if arg != '--foreground'])
        kind = argv[0] if argv[0] in {'translate', 'summarize', 'retry', 'batch'} else 'read'
        if kind in {'translate', 'summarize'}:
            args = read_ai.arguments(argv)
            source = read_store.read_json(args.folder.expanduser().resolve() / 'content.json')
            if source.get('content_complete') is not True or not source.get('text'):
                raise ValueError('AI processing requires complete source text.')
        elif kind == 'retry':
            retry = content.retry_parser().parse_args(argv[1:])
            pending = read_store.unfinished() if retry.folder is None else []
            folder = retry.folder.expanduser().resolve() if retry.folder else Path(pending[0]['folder']) if pending else None
            if folder is None:
                raise ValueError('No unfinished read was found.')
            read_store.read_json(folder / '.read-job.json')
            argv = ['retry', str(folder)]
            if retry.rendered_backend is not None:
                argv += ['--rendered-backend', retry.rendered_backend]
        else:
            batch = kind == 'batch'
            parser = content.read_parser(batch)
            args = parser.parse_args(argv[1:] if batch else argv)
            if args.profile and args.browser in {'auto', 'none'}:
                parser.error('--profile requires an explicit browser')
            if args.language and not re.fullmatch(r'auto|[a-z]{2,3}', args.language):
                parser.error('Use auto or a Whisper language code')
            if not read_frames.MIN_FRAMES <= args.max_frames <= read_frames.MAX_FRAMES:
                parser.error(f'--max-frames must be between {read_frames.MIN_FRAMES} and {read_frames.MAX_FRAMES}.')
            if batch and not args.url:
                if runtime.is_interactive():
                    print('Paste one link or local file per line; blank line submits the task.')
                    links = []
                    while line := input('> ').strip():
                        links.append(line)
                elif not sys.stdin.isatty():
                    links = [line.strip() for line in sys.stdin if line.strip()]
                else:
                    links = []
                if not links:
                    if wants_json:
                        raise ValueError('The read batch is empty')
                    parser.error('The read batch is empty')
                argv = ['batch', *links, *argv[1:]]
        return submit(argv, kind)
    except (OSError, ValueError, RuntimeError) as error:
        if wants_json:
            agent_output.emit(agent_output.error_payload('read', error))
        else:
            print('Read task stopped: ' + str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        if wants_json:
            agent_output.emit(agent_output.error_payload('read', KeyboardInterrupt('Read stopped'), exit_code=130))
        return 130
    finally:
        runtime.restore_modes(saved_modes)
