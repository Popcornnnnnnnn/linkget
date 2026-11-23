"""Bounded local video keyframe extraction via FFmpeg scene detection and interval sampling."""
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

if __package__:
    from . import read_formats, read_ocr, read_store
else:
    import read_formats, read_ocr, read_store

DEFAULT_MAX_FRAMES = 12
MIN_FRAMES = 1
MAX_FRAMES = 60
DEFAULT_SCENE_THRESHOLD = 0.35
DEFAULT_JPEG_QUALITY = 3


def probe_video(path, *, ffprobe=None):
    target = Path(path)
    if target.is_symlink() or not target.is_file():
        raise ValueError('Keyframe extraction requires a real local media file.')
    tool = ffprobe or shutil.which('ffprobe')
    if not tool:
        raise RuntimeError('Video keyframe extraction requires ffprobe.')
    result = subprocess.run(
        [tool, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(target)],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    try:
        data = json.loads(result.stdout)
        streams = data.get('streams') or []
        video_stream = next(
            (
                stream for stream in streams
                if stream.get('codec_type') == 'video'
                and (stream.get('disposition') or {}).get('attached_pic', 0) != 1
            ),
            None,
        )
        raw_duration = (data.get('format') or {}).get('duration')
        if raw_duration is None and video_stream is not None:
            raw_duration = video_stream.get('duration')
        duration = float(raw_duration) if raw_duration is not None else None
        if duration is not None and (not math.isfinite(duration) or duration <= 0):
            duration = None
    except (ValueError, TypeError, KeyError):
        raise RuntimeError('Video returned unreadable stream information.') from None
    return {
        'has_video': bool(video_stream),
        'duration': duration,
        'width': video_stream.get('width') if video_stream else None,
        'height': video_stream.get('height') if video_stream else None,
    }


def _parse_showinfo_pts(stderr_text):
    return [float(match.group(1)) for match in re.finditer(r'\bpts_time:\s*([0-9]+(?:\.[0-9]+)?)', stderr_text or '')]


def extract_keyframes(
    video_path,
    output_dir,
    *,
    ffmpeg,
    ffprobe=None,
    max_frames=DEFAULT_MAX_FRAMES,
    scene_threshold=DEFAULT_SCENE_THRESHOLD,
    prefix='v1',
):
    video = Path(video_path)
    if video.is_symlink() or not video.is_file():
        raise ValueError('Keyframe extraction requires a real local video file.')
    if type(max_frames) is not int or not MIN_FRAMES <= max_frames <= MAX_FRAMES:
        raise ValueError(f'--max-frames must be an integer between {MIN_FRAMES} and {MAX_FRAMES}.')
    threshold = float(scene_threshold)
    if not math.isfinite(threshold) or not 0.05 <= threshold <= 0.95:
        raise ValueError('Scene threshold must be between 0.05 and 0.95.')
    ffmpeg_tool = ffmpeg or shutil.which('ffmpeg')
    if not ffmpeg_tool:
        raise RuntimeError('Video keyframe extraction requires ffmpeg.')

    info = probe_video(video, ffprobe=ffprobe)
    if not info['has_video']:
        return []

    dest_dir = Path(output_dir)
    if dest_dir.is_symlink():
        raise ValueError('Keyframe output folder must not be a symbolic link.')
    dest_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if dest_dir.is_symlink() or not dest_dir.is_dir():
        raise ValueError('Keyframe output folder is invalid.')

    duration = info['duration']
    timeout_seconds = min(300, max(60, int((duration or 60) * 2)))
    safe_prefix = re.sub(r'[^A-Za-z0-9_-]+', '-', str(prefix or 'v1')).strip('-') or 'v1'

    with tempfile.TemporaryDirectory(prefix='.frames-tmp-', dir=dest_dir) as tmp:
        tmp_dir = Path(tmp)
        scene_cmd = [
            ffmpeg_tool, '-hide_banner', '-loglevel', 'info',
            '-i', str(video),
            '-vf', f"select='gt(scene,{threshold:.2f})',showinfo",
            '-fps_mode', 'vfr',
            '-q:v', str(DEFAULT_JPEG_QUALITY),
            '-frames:v', str(max_frames * 3),
            str(tmp_dir / 'scene-%04d.jpg'),
        ]
        scene_res = subprocess.run(scene_cmd, check=False, capture_output=True, text=True, timeout=timeout_seconds)
        if scene_res.returncode != 0:
            err_text = scene_res.stderr or ''
            if 'No filtered frames' not in err_text and 'Nothing was written' not in err_text and 'Output file is empty' not in err_text:
                raise subprocess.CalledProcessError(
                    scene_res.returncode, scene_cmd, output=scene_res.stdout, stderr=scene_res.stderr
                )
        scene_files = sorted(path for path in tmp_dir.glob('scene-*.jpg') if path.is_file() and path.stat().st_size > 0)
        pts_list = _parse_showinfo_pts(scene_res.stderr)
        selected_pairs = [(file_path, pts_list[i] if i < len(pts_list) else 0.0) for i, file_path in enumerate(scene_files)]
        method = 'scene'

        if len(selected_pairs) < min(3, max_frames):
            if duration and duration > 0:
                min_target = min(max_frames, 3) if duration >= 3.0 else 1
                count = min(max_frames, max(min_target, int(math.ceil(duration / 15.0))))
                timestamps = [round((i + 0.5) * duration / count, 3) for i in range(count)]
            else:
                timestamps = [0.0]
            uniform_pairs = []
            for idx, ts in enumerate(timestamps, 1):
                out_jpg = tmp_dir / f'uniform-{idx:04d}.jpg'
                subprocess.run(
                    [
                        ffmpeg_tool, '-hide_banner', '-loglevel', 'error',
                        '-ss', f'{ts:.3f}', '-i', str(video),
                        '-frames:v', '1', '-q:v', str(DEFAULT_JPEG_QUALITY),
                        str(out_jpg),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if out_jpg.is_file() and out_jpg.stat().st_size > 0:
                    uniform_pairs.append((out_jpg, ts))
            if not uniform_pairs and not selected_pairs:
                first_jpg = tmp_dir / 'uniform-0001.jpg'
                subprocess.run(
                    [
                        ffmpeg_tool, '-hide_banner', '-loglevel', 'error',
                        '-ss', '0.000', '-i', str(video),
                        '-frames:v', '1', '-q:v', str(DEFAULT_JPEG_QUALITY),
                        str(first_jpg),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                if first_jpg.is_file() and first_jpg.stat().st_size > 0:
                    uniform_pairs.append((first_jpg, 0.0))
            if uniform_pairs:
                selected_pairs = uniform_pairs
                method = 'uniform'

        if len(selected_pairs) > max_frames:
            if max_frames == 1:
                indices = [0]
            else:
                indices = list(dict.fromkeys(
                    round(i * (len(selected_pairs) - 1) / (max_frames - 1))
                    for i in range(max_frames)
                ))
            selected_pairs = [selected_pairs[i] for i in indices]

        frames = []
        for idx, (src_jpg, ts) in enumerate(selected_pairs, 1):
            dest = dest_dir / f'{safe_prefix}-frame-{idx:03d}.jpg'
            if dest.is_symlink():
                raise ValueError('Keyframe destination file must not be a symbolic link.')
            os.replace(src_jpg, dest)
            dest.chmod(0o600)
            frames.append({
                'index': idx,
                'timestamp': round(float(ts), 3),
                'timestamp_label': read_formats.timestamp(max(0.0, float(ts))).split('.')[0],
                'path': str(dest),
                'relative_path': f'frames/{dest.name}',
                'method': method,
                'sha256': read_store.digest(dest),
                'bytes': dest.stat().st_size,
            })
        return frames


def annotate_frames_ocr(frames):
    if not frames:
        return []
    ocr_items = read_ocr.images([Path(frame['path']) for frame in frames])
    for frame, item in zip(frames, ocr_items):
        frame['ocr_text'] = (item.get('text') or '').strip()
    return frames
