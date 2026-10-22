"""One read-only view of download receipts and background reading tasks."""
import argparse
import fcntl
import os
import re
from pathlib import Path
import shlex
import time

if __package__:
    from . import jobs, read_tasks, staging
    from .failures import redact
    from .output import format_size
else:
    import jobs, read_tasks, staging
    from failures import redact
    from output import format_size


def display(value):
    return re.sub(r'https?://[^\s<>]+', '[URL omitted]', redact(value))


def retained_size(paths):
    """Observe files without following links or granting cleanup authority."""
    size, seen, missing = 0, set(), False
    for name in paths:
        path = Path(name)
        if path.is_symlink() or not path.exists():
            missing = True
            continue
        candidates = [path] if path.is_file() else (Path(root) / file
            for root, dirs, files in os.walk(path, followlinks=False)
            for file in files)
        try:
            for candidate in candidates:
                if candidate.is_symlink():
                    continue
                stat = candidate.stat()
                key = (stat.st_dev, stat.st_ino)
                if key not in seen:
                    seen.add(key)
                    size += stat.st_size
        except OSError:
            missing = True
    return format_size(size) + (' (some paths unavailable)' if missing else '')


def claimed(journal):
    try:
        fd = os.open(journal.path.with_suffix('.lock'), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False
    finally:
        os.close(fd)


def show_download(journal):
    active = claimed(journal)
    rows = journal.data['items']
    complete = sum(row['status'] == 'completed' for row in rows)
    state = 'running' if active else 'needs attention' if complete < len(rows) else 'completed'
    print(f'Download {journal.identifier}  {state}  {complete}/{len(rows)} completed')
    destination = journal.data['options'].get('folder') or 'Photos'
    print('Destination: ' + display(destination))
    for index, row in enumerate(rows, 1):
        state = row['status']
        if not active and state == 'running':
            state = 'interrupted (recorded running; no active task lock)'
        print(f'  Item {index}: {state}')
        failure = row.get('failure')
        if state.startswith(('failed', 'cancelled', 'interrupted')) and not isinstance(failure, dict):
            print('  Failure: No structured cause recorded; inspect the retained diagnostics.')
        if isinstance(failure, dict) and row['status'] != 'completed':
            print('  Failure: ' + display(failure.get('message', 'Unknown')))
            print('  Next: ' + display(failure.get('next_step', 'Inspect retained files.')))
        paths = list(staging.paths(row))
        receipt = row.get('receipt') or {}
        paths.extend(resource['path'] for resource in receipt.get('staged', []) if isinstance(resource, dict) and isinstance(resource.get('path'), str))
        if paths:
            print('  Retained size (observed): ' + retained_size(paths))
            for path in dict.fromkeys(paths):
                print('  Files: ' + display(path))
        if row.get('receipt'):
            print(f'  Next: linkget recover {journal.identifier} --item {index} --verify')
        elif row.get('ready'):
            print(f'  Next: linkget recover {journal.identifier} --item {index}')
    if complete < len(rows):
        print('Next: linkget retry ' + journal.identifier)
    print('')


def show_read(value):
    identifier = value['id']
    state = value['status']
    if state not in read_tasks.TERMINAL and not read_tasks.active(value) and (state == 'running' or time.time() - value.get('created', 0) > 10):
        state = 'worker unconfirmed (recorded ' + state + ')'
    print(f'Read {identifier}  {state}  ' + display(value.get('phase', value['kind'])))
    outputs = value.get('outputs', [])
    for path in outputs:
        print('Result: ' + display(path))
    retained = list(outputs)
    source = value.get('source_folder')
    if isinstance(source, str) and Path(source).is_absolute():
        retained.append(source)
        print('Source/checkpoints: ' + display(source))
    if retained:
        print('Retained size (observed): ' + retained_size(retained))
    if value['status'] in {'failed', 'interrupted', 'cancelled'}:
        print('Failure: ' + display(value.get('failure') or 'No structured failure recorded; inspect the private log.'))
        print('Next: linkget read logs ' + identifier)
        if value['kind'] in {'read', 'retry', 'batch'}:
            for path in outputs:
                if (Path(path) / '.read-job.json').is_file():
                    print('Next: linkget read retry ' + shlex.quote(path))
        elif value['kind'] in {'translate', 'summarize'}:
            print('Next: inspect the log, then rerun the original read ' + value['kind'] + ' command.')
    elif value['status'] in {'queued', 'running'}:
        print('Next: linkget read status ' + identifier)
        print('Cancel: linkget read cancel ' + identifier)
    print('')


def main(argv):
    parser = argparse.ArgumentParser(prog='linkget tasks', description='View download and read tasks without retrying, saving or removing files. No source links or share tokens are displayed.')
    parser.add_argument('task', nargs='?')
    parser.add_argument('--kind', choices=['download', 'read'])
    parser.add_argument('--limit', type=int, default=20, help='Latest tasks across both kinds; default: 20')
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000:
        parser.error('--limit must be between 1 and 1000.')
    entries = []
    for kind, root in [('download', jobs.directory()), ('read', read_tasks.directory())]:
        if args.kind and kind != args.kind:
            continue
        for path in root.glob('*.json'):
            if args.task and path.stem != args.task:
                continue
            try:
                value = jobs.Journal.load(path.stem) if kind == 'download' else read_tasks.load(path.stem)
                created = value.data.get('created_ns', 0) / 1e9 if kind == 'download' else value.get('created', 0)
                entries.append((created, kind, value))
            except (OSError, ValueError, RuntimeError, KeyError, TypeError):
                print(f'Unreadable {kind} receipt; retained files were not touched.')
    for _, kind, value in sorted(entries, key=lambda row: row[0], reverse=True)[:args.limit]:
        (show_download if kind == 'download' else show_read)(value)
    if not entries:
        print('No matching tasks.' if args.task else 'No tasks.')
    return int(bool(args.task) and not entries)
