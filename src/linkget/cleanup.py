"""Safe temporary stage cleanup respecting FileProof, task locks, Photos receipts, and user folders."""
import os
from pathlib import Path
import stat
import time

if __package__:
    from . import jobs, media_limits, retention, staging, tasks
else:
    import jobs, media_limits, retention, staging, tasks

DEFAULT_OLDER_THAN = '7d'
DIR_FLAGS = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_DIRECTORY', 0)


def _open_dir_fd(target, *, dir_fd=None, expected_identity=None):
    fd = os.open(target, DIR_FLAGS) if dir_fd is None else os.open(target, DIR_FLAGS, dir_fd=dir_fd)
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError('Stage path is not a directory.')
        if expected_identity is not None and [info.st_dev, info.st_ino] != expected_identity:
            raise ValueError('Stage directory identity changed.')
    except BaseException:
        os.close(fd)
        raise
    return fd


def _stats_fd(dir_fd, root_stat):
    file_count = 0
    total_bytes = 0
    max_mtime = root_stat.st_mtime
    with os.scandir(dir_fd) as it:
        entries = list(it)
    for entry in entries:
        info = os.stat(entry.name, dir_fd=dir_fd, follow_symlinks=False)
        max_mtime = max(max_mtime, info.st_mtime)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('Stage contains a symbolic link.')
        if stat.S_ISDIR(info.st_mode):
            sub_fd = _open_dir_fd(entry.name, dir_fd=dir_fd, expected_identity=[info.st_dev, info.st_ino])
            try:
                sub_info = os.fstat(sub_fd)
                c, b, m = _stats_fd(sub_fd, sub_info)
                file_count += c
                total_bytes += b
                max_mtime = max(max_mtime, m)
            finally:
                os.close(sub_fd)
        elif stat.S_ISREG(info.st_mode):
            file_count += 1
            total_bytes += info.st_size
        else:
            raise ValueError('Stage contains a non-regular file.')
    return file_count, total_bytes, max_mtime


def _stage_stats(root, *, expected_identity=None):
    root_fd = _open_dir_fd(root, expected_identity=expected_identity)
    try:
        return _stats_fd(root_fd, os.fstat(root_fd))
    finally:
        os.close(root_fd)


def _expected_identity(row, root_str):
    snapshots = row.get('stage_snapshots')
    if snapshots is not None:
        if not isinstance(snapshots, dict):
            return None
        if root_str in snapshots:
            entry = snapshots.get(root_str)
            if not isinstance(entry, dict) or entry.get('created') is not True:
                return None
            return entry.get('identity')
    if row.get('stage') == root_str and row.get('stage_identity') is not None:
        return row['stage_identity']
    return None


def _evaluate_stage(journal, index, row, raw_path, *, cutoff):
    try:
        active = tasks.claimed(journal)
    except OSError:
        return 'kept', 'unsafe_lock', 'Task lock file is unavailable or unsafe.', None
    if active:
        return 'kept', 'active_task', 'Task is currently running.', None
    if row.get('status') != 'completed':
        return 'kept', 'incomplete_task', 'Task is incomplete; retained for retry/recovery.', None
    if row.get('receipt'):
        return 'kept', 'unverified_photos_receipt', 'Photos managed-copy receipt exists; run linkget recover --verify.', None
    path = Path(raw_path)
    if path.is_symlink():
        return 'kept', 'symlink', 'Stage path is a symbolic link.', None
    if not path.exists():
        return 'missing', 'missing', 'Stage path no longer exists.', None
    try:
        root = staging._root(path)
    except (OSError, ValueError, RuntimeError):
        return 'kept', 'unsafe_path', 'Path is outside safe linkget staging scope.', None
    root_str = str(root)
    protected = row.get('protected_stages') or []
    if not isinstance(protected, list) or any(not isinstance(value, str) for value in protected) or root_str in protected:
        return 'kept', 'protected_stage', 'Stage is protected after replacement or mismatch.', None
    try:
        overlaps = retention._protected(journal, index, root)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        return 'kept', 'overlaps_protected_path', 'Could not verify whether stage overlaps protected folders.', None
    if overlaps:
        return 'kept', 'overlaps_protected_path', 'Stage overlaps a destination folder, profile, or another task.', None
    expected_id = _expected_identity(row, root_str)
    if expected_id is None:
        return 'kept', 'missing_identity', 'Stage has no recorded creation-time identity.', None
    try:
        actual_id = staging.identity(root)
    except OSError:
        return 'kept', 'unsafe_path', 'Could not read stage directory identity.', None
    if actual_id != expected_id:
        return 'kept', 'identity_mismatch', 'Stage directory identity changed since creation.', None
    try:
        file_count, total_bytes, max_mtime = _stage_stats(root, expected_identity=expected_id)
    except (OSError, ValueError) as error:
        return 'kept', 'unsafe_contents', str(error), None
    if max_mtime > cutoff:
        return 'kept', 'too_new', 'Stage is newer than the --older-than cutoff.', {
            'task_id': journal.identifier, 'item': index, 'path': root_str,
            'file_count': file_count, 'bytes': total_bytes, 'mtime': max_mtime,
        }
    return 'eligible', 'eligible', 'Completed old temporary stage safe to remove.', {
        'task_id': journal.identifier, 'item': index, 'path': root_str,
        'file_count': file_count, 'bytes': total_bytes, 'mtime': max_mtime,
        'expected_identity': expected_id,
    }


def _purge_fd(dir_fd):
    with os.scandir(dir_fd) as it:
        entries = list(it)
    for entry in entries:
        info = os.stat(entry.name, dir_fd=dir_fd, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('Refusing symbolic link during stage cleanup.')
        if stat.S_ISDIR(info.st_mode):
            sub_fd = _open_dir_fd(entry.name, dir_fd=dir_fd, expected_identity=[info.st_dev, info.st_ino])
            try:
                _purge_fd(sub_fd)
            finally:
                os.close(sub_fd)
            os.rmdir(entry.name, dir_fd=dir_fd)
        elif stat.S_ISREG(info.st_mode):
            os.unlink(entry.name, dir_fd=dir_fd)
        else:
            raise ValueError('Refusing special file during stage cleanup.')


def _remove_stage_tree(root, expected_identity):
    if expected_identity is None:
        raise ValueError('Missing creation-time stage identity.')
    verified_root = staging._root(root)
    _stage_stats(verified_root, expected_identity=expected_identity)
    parent_fd = _open_dir_fd(verified_root.parent)
    root_fd = None
    try:
        root_fd = _open_dir_fd(verified_root.name, dir_fd=parent_fd, expected_identity=expected_identity)
        _purge_fd(root_fd)
        info = os.fstat(root_fd)
        if [info.st_dev, info.st_ino] != expected_identity:
            raise ValueError('Stage directory identity changed during cleanup.')
        parent_entry = os.stat(verified_root.name, dir_fd=parent_fd, follow_symlinks=False)
        if [parent_entry.st_dev, parent_entry.st_ino] != expected_identity:
            raise ValueError('Stage directory identity changed before removal.')
        os.rmdir(verified_root.name, dir_fd=parent_fd)
    finally:
        if root_fd is not None:
            os.close(root_fd)
        os.close(parent_fd)


def _safe_mtime(path):
    try:
        return path.lstat().st_mtime
    except OSError:
        return 0.0


def inspect_and_clean(*, older_than=None, apply=False, now=None):
    age_spec = DEFAULT_OLDER_THAN if older_than is None else older_than
    older_than_seconds = media_limits.parse_age(age_spec)
    now_ts = time.time() if now is None else float(now)
    cutoff = now_ts - older_than_seconds

    eligible = []
    kept = []
    deleted = []
    errors = []
    freed_bytes = 0
    seen_roots = set()

    journal_paths = sorted(jobs.directory().glob('*.json'), key=_safe_mtime, reverse=True)
    for journal_path in journal_paths:
        task_id = journal_path.stem
        try:
            journal = jobs.Journal.load(task_id)
        except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
            errors.append({'task_id': task_id, 'message': f'Could not read task receipt: {error}'})
            continue
        rows = journal.data.get('items', [])
        if not isinstance(rows, list):
            errors.append({'task_id': journal.identifier, 'message': 'Invalid task receipt items list.'})
            continue
        journal_candidates = []
        for index, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                kept.append({'task_id': journal.identifier, 'item': index, 'path': None, 'reason': 'invalid_metadata', 'message': 'Invalid task item metadata.'})
                continue
            try:
                stage_paths = staging.paths(row)
            except (ValueError, TypeError):
                kept.append({'task_id': journal.identifier, 'item': index, 'path': None, 'reason': 'invalid_metadata', 'message': 'Invalid stage metadata.'})
                continue
            for raw_path in stage_paths:
                raw_path_obj = Path(raw_path)
                resolved_key = (
                    str(raw_path_obj.resolve())
                    if raw_path_obj.exists() and not raw_path_obj.is_symlink()
                    else os.path.normpath(raw_path)
                )
                if resolved_key in seen_roots:
                    continue
                outcome, reason, message, info = _evaluate_stage(journal, index, row, raw_path, cutoff=cutoff)
                if outcome == 'missing':
                    continue
                seen_roots.add(resolved_key)
                if outcome == 'eligible':
                    entry = {k: v for k, v in info.items() if k != 'expected_identity'}
                    eligible.append(entry)
                    journal_candidates.append((index, raw_path, info))
                else:
                    kept.append({
                        'task_id': journal.identifier,
                        'item': index,
                        'path': raw_path,
                        'reason': reason,
                        'message': message,
                        **({'bytes': info['bytes'], 'file_count': info['file_count']} if info else {}),
                    })

        if apply and journal_candidates:
            try:
                with journal.claim():
                    fresh = jobs.Journal.load(journal.identifier)
                    for index, raw_path, _ in journal_candidates:
                        fresh_row = fresh.data['items'][index - 1]
                        if fresh_row.get('status') != 'completed' or fresh_row.get('receipt'):
                            continue
                        if raw_path not in staging.paths(fresh_row):
                            continue
                        if not isinstance(fresh_row.get('previous_stages', []), list):
                            continue
                        if not isinstance(fresh_row.get('stage_snapshots', {}), dict):
                            continue
                        path = Path(raw_path)
                        if path.is_symlink() or not path.exists():
                            continue
                        root = staging._root(path)
                        root_str = str(root)
                        protected_stages = fresh_row.get('protected_stages') or []
                        if (not isinstance(protected_stages, list)
                                or any(not isinstance(value, str) for value in protected_stages)
                                or root_str in protected_stages):
                            continue
                        if retention._protected(fresh, index, root):
                            continue
                        exp_id = _expected_identity(fresh_row, root_str)
                        if exp_id is None or staging.identity(root) != exp_id:
                            continue
                        file_count, total_bytes, max_mtime = _stage_stats(root, expected_identity=exp_id)
                        if max_mtime > cutoff:
                            continue
                        _remove_stage_tree(root, exp_id)
                        freed_bytes += total_bytes
                        deleted.append({
                            'task_id': fresh.identifier,
                            'item': index,
                            'path': root_str,
                            'file_count': file_count,
                            'bytes': total_bytes,
                        })
                        prev = [p for p in fresh_row.get('previous_stages', []) if p != root_str and p != raw_path]
                        snaps = dict(fresh_row.get('stage_snapshots') or {})
                        snaps.pop(root_str, None)
                        changes = {'previous_stages': prev, 'stage_snapshots': snaps}
                        if fresh_row.get('stage') in {root_str, raw_path}:
                            changes['stage'] = None
                            changes['stage_identity'] = None
                        fresh.update(index, **changes)
            except (OSError, ValueError, RuntimeError, KeyError, TypeError) as error:
                errors.append({'task_id': journal.identifier, 'message': str(error)})

    reclaimable_bytes = sum(item['bytes'] for item in eligible)
    return {
        'ok': not errors,
        'mode': 'apply' if apply else 'dry_run',
        'older_than': age_spec,
        'older_than_seconds': older_than_seconds,
        'eligible_count': len(eligible),
        'reclaimable_bytes': reclaimable_bytes,
        'deleted_count': len(deleted),
        'freed_bytes': freed_bytes,
        'kept_count': len(kept),
        'eligible': eligible,
        'deleted': deleted,
        'kept': kept,
        'errors': errors,
    }
