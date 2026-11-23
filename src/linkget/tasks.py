"""One read-only view of download receipts and background reading tasks."""
import argparse
import fcntl
import os
import re
from pathlib import Path
import shlex
import time

if __package__:
    from . import agent_output, jobs, read_tasks, staging, retention
    from .failures import redact
    from .output import format_size
else:
    import agent_output, jobs, read_tasks, staging, retention
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


def show_download(journal, *, preview=False):
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
        if row.get('destination_folder') and row.get('destination_folder') != destination:
            print('  Destination: ' + display(row['destination_folder']))
        failure = row.get('failure')
        if state.startswith(('failed', 'cancelled', 'interrupted')) and not isinstance(failure, dict):
            print('  Failure: No structured cause recorded; inspect the retained diagnostics.')
        if isinstance(failure, dict) and row['status'] != 'completed':
            print('  Failure: ' + display(failure.get('message', 'Unknown')))
            print('  Next: ' + display(failure.get('next_step', 'Inspect retained files.')))
        try:
            paths = list(staging.paths(row))
        except (ValueError,TypeError):
            paths=[]
            print('  Retained because: Invalid path metadata; no file ownership can be inferred.')
        receipt = row.get('receipt') or {}
        if not isinstance(receipt,dict):
            receipt={}
            print('  Retained because: Invalid import receipt; resources remain protected.')
        resources=receipt.get('staged',[])
        if not isinstance(resources,list):
            resources=[]
            print('  Retained because: Invalid staged-resource metadata; no resource was inspected.')
        paths.extend(resource['path'] for resource in resources if isinstance(resource, dict) and isinstance(resource.get('path'), str))
        if paths:
            for reason in retention.reasons(row,active):
                print('  Retained because: '+display(reason))
            print('  Retained size (observed): ' + retained_size(paths))
            for path in dict.fromkeys(paths):
                print('  Files: ' + display(path))
        if preview:
            for entry in retention.preview(journal,index,active):
                print('  Preview '+entry['classification']+': '+display(entry['path'] or 'recorded files')+' · '+(retained_size([entry['path']]) if entry['path'] else 'size unknown'))
                print('  Reason: '+display(entry['reason']))
        if row.get('receipt'):
            print(f'  Next: linkget recover {journal.identifier} --item {index} --verify')
        elif row.get('ready'):
            print(f'  Next: linkget recover {journal.identifier} --item {index}')
    if complete < len(rows):
        print('Next: linkget retry ' + journal.identifier)
    print('')


def show_read(value, *, preview=False):
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
        print('Retained because: User results and retry/checkpoints are preserved; reading files are not cleanup candidates.')
        if preview:
            print('Preview must keep: All read outputs, source media and checkpoints; no deletion or ownership inference.')
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


def download_summary(journal, *, preview=False):
    active = claimed(journal)
    rows = journal.data['items']
    complete = sum(row['status'] == 'completed' for row in rows)
    state = 'running' if active else 'needs_attention' if complete < len(rows) else 'completed'
    destination = journal.data['options'].get('folder') or 'Photos'
    items = []
    for index, row in enumerate(rows, 1):
        item_state = row['status']
        if not active and item_state == 'running':
            item_state = 'interrupted'
        failure = row.get('failure')
        failure_info = None
        if isinstance(failure, dict) and row['status'] != 'completed':
            failure_info = {
                'category': failure.get('category'),
                'message': display(failure.get('message', 'Unknown')),
                'next_step': display(failure.get('next_step', 'Inspect retained files.')),
                'phase': failure.get('phase'),
            }
        entry = {
            'item': index,
            'status': item_state,
            'destination': display(row.get('destination_folder') or destination),
            'failure': failure_info,
            'retained_paths': [],
            'saved_files': [item.get('path') for item in row.get('destination_files', []) if isinstance(item, dict) and item.get('path')],
        }
        try:
            paths = list(staging.paths(row))
        except (ValueError, TypeError):
            paths = []
        receipt = row.get('receipt') or {}
        if isinstance(receipt, dict) and isinstance(receipt.get('staged'), list):
            paths.extend(r['path'] for r in receipt['staged'] if isinstance(r, dict) and isinstance(r.get('path'), str))
        entry['retained_paths'] = [str(p) for p in dict.fromkeys(paths)]
        if preview:
            entry['cleanup_preview'] = retention.preview(journal, index, active)
        items.append(entry)
    return {
        'kind': 'download',
        'id': journal.identifier,
        'status': state,
        'completed_items': complete,
        'total_items': len(rows),
        'destination': display(destination),
        'audio': bool(journal.data['options'].get('audio')),
        'audio_format': journal.data['options'].get('audio_format'),
        'items': items,
    }


def read_summary(value, *, preview=False):
    identifier = value['id']
    state = value['status']
    if state not in read_tasks.TERMINAL and not read_tasks.active(value) and (state == 'running' or time.time() - value.get('created', 0) > 10):
        state = 'worker_unconfirmed'
    outputs = [str(p) for p in value.get('outputs', [])]
    source = value.get('source_folder')
    result = {
        'kind': 'read',
        'id': identifier,
        'task_kind': value['kind'],
        'status': state,
        'phase': value.get('phase', value['kind']),
        'outputs': outputs,
        'source_folder': source if isinstance(source, str) and Path(source).is_absolute() else None,
        'failure': display(value['failure']) if value.get('failure') else None,
    }
    if preview:
        result['cleanup_preview'] = 'must_keep_read_outputs'
    return result


def main(argv):
    parser = argparse.ArgumentParser(prog='linkget tasks', description='View download and read tasks without retrying, saving or removing files. No source links or share tokens are displayed.')
    parser.add_argument('task', nargs='?')
    parser.add_argument('--cleanup-preview',action='store_true',help='Read-only preview for one task; media/results/profile/installers are excluded. No deletion.')
    parser.add_argument('--kind', choices=['download', 'read'])
    parser.add_argument('--limit', type=int, default=20, help='Latest tasks across both kinds; default: 20')
    parser.add_argument('--json', action='store_true', help='Print machine-readable task summaries on stdout')
    parser.add_argument('--non-interactive', action='store_true', help='Never prompt or animate terminal spinners')
    args = parser.parse_args(argv)
    if args.cleanup_preview and not args.task:
        parser.error('Select one task: linkget tasks TASK --cleanup-preview.')
    if not 1 <= args.limit <= 1000:
        parser.error('--limit must be between 1 and 1000.')
    entries = []
    warnings = []
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
                warnings.append(f'Unreadable {kind} receipt; retained files were not touched.')
                if not args.json:
                    print(warnings[-1])
    selected = sorted(entries, key=lambda row: row[0], reverse=True)[:args.limit]
    if args.json:
        tasks_payload = [
            download_summary(value, preview=args.cleanup_preview) if kind == 'download'
            else read_summary(value, preview=args.cleanup_preview)
            for _, kind, value in selected
        ]
        ok = not (args.task and not selected)
        agent_output.emit({
            'schema_version': agent_output.SCHEMA_VERSION,
            'ok': ok,
            'command': 'tasks',
            'tasks': tasks_payload,
            'warnings': warnings,
        })
        return int(not ok)
    if args.cleanup_preview:
        print('Read-only cleanup preview: no files or receipts are changed. Candidates are observations, not new ownership or deletion authority.')
    for _, kind, value in selected:
        (show_download if kind == 'download' else show_read)(value,preview=args.cleanup_preview)
    if not entries:
        print('No matching tasks.' if args.task else 'No tasks.')
    return int(bool(args.task) and not entries)
