"""Duration, size, and clipping helpers with pre-download and post-download caps."""
import math
from pathlib import Path
import re
import subprocess

if __package__:
    from . import ownership, transfers
    from .media import AUDIO, VIDEO
    from .runtime import run_process
else:
    import ownership, transfers
    from media import AUDIO, VIDEO
    from runtime import run_process

_SIZE_RE = re.compile(r'^\s*(\d+(?:\.\d+)?)\s*([a-zA-Z]{0,4})\s*$')
_SIZE_UNITS = {
    '': 1,
    'b': 1,
    'k': 1024,
    'kb': 1024,
    'kib': 1024,
    'm': 1024 ** 2,
    'mb': 1024 ** 2,
    'mib': 1024 ** 2,
    'g': 1024 ** 3,
    'gb': 1024 ** 3,
    'gib': 1024 ** 3,
}
_CLOCK_RE = re.compile(r'^\s*(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)\s*$')
_UNIT_RE = re.compile(r'(\d+(?:\.\d+)?)\s*([dhmsDHMS])')


def _parse_time_value(raw, *, allow_zero=False, allow_days=False):
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        val = float(raw)
        if not math.isfinite(val) or (val < 0 if allow_zero else val <= 0):
            raise ValueError('Duration must be non-negative.' if allow_zero else 'Duration must be greater than zero.')
        return val
    text = str(raw or '').strip()
    if not text:
        raise ValueError('A valid duration is required.')
    if re.fullmatch(r'\d+(?:\.\d+)?', text):
        val = float(text)
        if not math.isfinite(val) or (val < 0 if allow_zero else val <= 0):
            raise ValueError('Duration must be non-negative.' if allow_zero else 'Duration must be greater than zero.')
        return val
    clock = _CLOCK_RE.fullmatch(text)
    if clock and not allow_days:
        hrs_s, mins_s, secs_s = clock.groups()
        hrs = int(hrs_s) if hrs_s is not None else 0
        mins = int(mins_s)
        secs = float(secs_s)
        if (hrs_s is not None and mins >= 60) or secs >= 60:
            raise ValueError(f'Invalid timestamp: {text}')
        val = hrs * 3600.0 + mins * 60.0 + secs
        if not math.isfinite(val) or (val < 0 if allow_zero else val <= 0):
            raise ValueError(f'Invalid duration: {text}')
        return val
    pos = 0
    total = 0.0
    multipliers = {'d': 86400.0, 'h': 3600.0, 'm': 60.0, 's': 1.0} if allow_days else {'h': 3600.0, 'm': 60.0, 's': 1.0}
    compact = re.sub(r'\s+', '', text)
    while pos < len(compact):
        match = _UNIT_RE.match(compact, pos)
        if not match or match.group(2).lower() not in multipliers:
            raise ValueError(f'Invalid duration format: {text}')
        total += float(match.group(1)) * multipliers[match.group(2).lower()]
        pos = match.end()
    if not math.isfinite(total) or (total < 0 if allow_zero else total <= 0):
        raise ValueError(f'Invalid duration format: {text}')
    return total


def parse_duration(value):
    return _parse_time_value(value, allow_zero=False, allow_days=False)


def parse_age(value):
    return _parse_time_value(value, allow_zero=True, allow_days=True)


def parse_size(value):
    if isinstance(value, int) and not isinstance(value, bool):
        if value <= 0:
            raise ValueError('Size limit must be positive.')
        return value
    text = str(value or '').strip()
    match = _SIZE_RE.fullmatch(text)
    if not match:
        raise ValueError(f'Use a valid size cap like 50MB, 1.5GB, or 500KB (got {value!r}).')
    amount = float(match.group(1))
    unit = match.group(2).lower()
    if unit not in _SIZE_UNITS or not math.isfinite(amount):
        raise ValueError(f'Unknown size unit in {value!r}; use B, KB, MB, or GB.')
    scaled = amount * _SIZE_UNITS[unit]
    if not math.isfinite(scaled):
        raise ValueError(f'Size limit is out of bounds: {value!r}.')
    byte_count = int(scaled)
    if byte_count <= 0:
        raise ValueError('Size limit must be greater than zero.')
    return byte_count


def parse_clip(value):
    text = str(value or '').strip()
    parts = [part.strip() for part in text.split('-')]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError('Use --clip START-END, such as 0:30-2:00 or 10-45.')
    try:
        start = _parse_time_value(parts[0], allow_zero=True, allow_days=False)
        end = _parse_time_value(parts[1], allow_zero=False, allow_days=False)
    except ValueError as error:
        raise ValueError('Use --clip START-END, such as 0:30-2:00 or 10-45.') from error
    if start >= end:
        raise ValueError('--clip end time must be greater than start time.')
    return start, end


def ytdlp_section(clip_range):
    start, end = clip_range
    return f'*{start:.3f}-{end:.3f}'


def _declared_size(info):
    if not isinstance(info, dict):
        return None
    direct = info.get('filesize') or info.get('filesize_approx')
    if isinstance(direct, (int, float)) and not isinstance(direct, bool) and direct > 0:
        return int(direct)
    requested = info.get('requested_formats')
    if isinstance(requested, list) and requested:
        sizes = []
        for item in requested:
            if not isinstance(item, dict):
                continue
            val = item.get('filesize') or item.get('filesize_approx')
            if isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
                sizes.append(int(val))
        if sizes:
            return sum(sizes)
    return None


def check_metadata_limits(info, *, max_duration=None, max_size=None, clip=None):
    if not isinstance(info, dict):
        return
    raw_duration = info.get('duration')
    duration = float(raw_duration) if isinstance(raw_duration, (int, float)) and not isinstance(raw_duration, bool) else None
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        duration = None
    if clip is not None and duration is not None and clip[0] >= duration:
        raise ValueError(f'Clip start ({clip[0]:.1f}s) exceeds media duration ({duration:.1f}s).')
    if max_duration is not None and duration is not None:
        effective = max(0.0, min(duration, clip[1]) - clip[0]) if clip is not None else duration
        if effective > max_duration + 0.5:
            raise ValueError(f'Media duration ({effective:.1f}s) exceeds --max-duration ({max_duration:.1f}s).')
    if max_size is not None and clip is None:
        declared = _declared_size(info)
        if declared is not None and declared > max_size:
            raise ValueError(f'Media size ({declared} bytes) exceeds --max-size ({max_size} bytes).')


def check_file_limits(files, media_records, *, max_duration=None, max_size=None):
    if max_size is not None:
        total_bytes = sum(Path(path).stat().st_size for path in files if Path(path).is_file())
        if total_bytes > max_size:
            raise ValueError(f'Downloaded media ({total_bytes} bytes) exceeds --max-size ({max_size} bytes).')
    if max_duration is not None:
        for record in media_records:
            raw_dur = record.get('duration')
            if raw_dur is None:
                continue
            try:
                dur = float(raw_dur)
            except (TypeError, ValueError):
                continue
            if math.isfinite(dur) and dur > 0 and dur > max_duration + 0.5:
                raise ValueError(f'Media duration ({dur:.1f}s) exceeds --max-duration ({max_duration:.1f}s).')


def _probe_duration(ffprobe, path):
    if not ffprobe:
        return None
    res = run_process(
        [ffprobe, '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
        capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL,
    )
    if res.returncode != 0:
        return None
    text = (res.stdout or '').strip().splitlines()
    if not text:
        return None
    try:
        dur = float(text[0].strip())
    except ValueError:
        return None
    return dur if math.isfinite(dur) and dur > 0 else None


def clip_media_files(files, live_pairs, output_dir, *, clip, ffmpeg, ffprobe=None):
    if not ffmpeg:
        raise RuntimeError('Clipping requires ffmpeg.')
    start, end = clip
    transfers.verify(files)
    motion = {name for _, name in (live_pairs or [])}
    av_extensions = VIDEO | AUDIO
    has_av = any(Path(path).suffix.lower() in av_extensions and Path(path).name not in motion for path in files)
    if not has_av:
        raise ValueError('--clip applies to audio and video media, not image-only posts.')

    dest_dir = Path(output_dir)
    if dest_dir.is_symlink():
        raise ValueError('Clip output directory must not be a symbolic link.')
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    out_paths = []
    out_proofs = []
    used_names = set()
    clip_len = end - start
    for path in files:
        src = Path(path)
        if src.name in motion or src.suffix.lower() not in av_extensions:
            if src.name not in motion:
                out_paths.append(src)
                existing_proof = files.proof(src) if isinstance(files, ownership.FileList) else None
                out_proofs.append(existing_proof if existing_proof is not None else ownership.FileProof.observe(src))
            continue

        src_dur = _probe_duration(ffprobe, src)
        if src_dur is not None and start >= src_dur:
            raise ValueError(f'Clip start ({start:.1f}s) exceeds media duration ({src_dur:.1f}s).')

        candidate_name = src.name
        suffix_idx = 1
        while candidate_name in used_names or (dest_dir / candidate_name).exists() or (dest_dir / candidate_name).is_symlink():
            candidate_name = f'{src.stem}-{suffix_idx}{src.suffix}'
            suffix_idx += 1
        used_names.add(candidate_name)
        dest = dest_dir / candidate_name
        if dest.is_symlink():
            raise ValueError('Clip destination must not be a symbolic link.')

        copy_cmd = [
            ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
            '-ss', f'{start:.3f}', '-to', f'{end:.3f}',
            '-i', str(src), '-c', 'copy', '-avoid_negative_ts', 'make_zero',
            str(dest),
        ]
        res = run_process(copy_cmd, capture_output=True, text=True, timeout=180, stdin=subprocess.DEVNULL)
        copy_ok = res.returncode == 0 and dest.is_file() and dest.stat().st_size > 0
        if copy_ok and ffprobe:
            out_dur = _probe_duration(ffprobe, dest)
            expected = max(0.0, (min(src_dur, end) if src_dur is not None else end) - start)
            if out_dur is None or abs(out_dur - expected) > 1.0:
                copy_ok = False
        if not copy_ok:
            dest.unlink(missing_ok=True)
            reencode_cmd = [
                ffmpeg, '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                '-ss', f'{start:.3f}',
                '-i', str(src),
                '-t', f'{clip_len:.3f}',
                str(dest),
            ]
            res2 = run_process(reencode_cmd, capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
            if res2.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
                detail = (res2.stderr or res.stderr or '').strip().splitlines()
                tail = detail[-1] if detail else f'Could not clip {src.name}.'
                raise RuntimeError(tail)
        dest.chmod(0o600)
        out_paths.append(dest)
        out_proofs.append(ownership.FileProof.observe(dest))
    transfers.verify(files)
    return ownership.FileList(out_paths, out_proofs)
