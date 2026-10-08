"""Read video speech through the existing single-post downloader."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

if __package__:
    from . import toutiao
    from .engines import route
    from .links import normalize_link
    from .media import reference
    from .preflight import writable_directory
else:
    import toutiao
    from engines import route
    from links import normalize_link
    from media import reference
    from preflight import writable_directory


def read_videos(args):
    # Import lazily: cli also dispatches here, including Homebrew direct launch.
    if __package__:
        from . import cli
    else:
        import cli
    folder = args.folder.expanduser().resolve()
    if folder.exists():
        raise ValueError('The content folder already exists; choose a new folder.')
    if args.page_data:
        raise ValueError('--page-data is supported only for Toutiao.')
    if not args.transcribe or args.whisper_model is None:
        raise ValueError('Videos need --transcribe --whisper-model to read their speech.')
    model = args.whisper_model.expanduser().resolve()
    if not model.is_file() or not all(cli.find_tool(t) for t in ('ffmpeg', 'ffprobe', 'whisper-cli')):
        raise RuntimeError('Local transcription requires ffmpeg, ffprobe, whisper-cli and an existing --whisper-model file.')
    writable_directory(folder)
    url = normalize_link(args.url)
    _, _, domain = route(url)
    if domain == 'pixiv.net':
        raise ValueError('Pixiv supports static images only; read requires video speech.')
    if domain == 'pinterest.com' and not re.fullmatch(r'/pin/\d+/?', urlsplit(url).path):
        raise ValueError('Read requires one Pinterest pin, not a board or profile.')
    if domain == 'lofter.com' and not re.fullmatch(r'/post/[A-Za-z0-9_]+/?', urlsplit(url).path):
        raise ValueError('Read requires one LOFTER post, not a profile.')
    folder.mkdir(parents=True, mode=0o700)
    media_root = folder / 'media'
    output = folder / 'content.json'

    def consume(files, records, evidence, resolved_url, site, metadata):
        motion = {name for _, name in evidence.live_pairs}
        videos = [(path, record) for path, record in zip(files, records)
                  if path.suffix.lower() in cli.VIDEO and path.name not in motion]
        if not videos:
            raise ValueError('This post has no ordinary video to transcribe; images and Live Photo motion are not speech content.')
        # Validate the entire set before recognizing any of it.
        for _, record in videos:
            duration = float(record.get('duration') or 0)
            if record.get('probe_ok') is not True or record.get('audio') is not True:
                raise RuntimeError('Every video must pass media validation and contain audio.')
            if not math.isfinite(duration) or not 0 < duration <= 1800:
                raise ValueError('Read supports videos up to 30 minutes each.')
        if metadata.get('_type') in {'playlist', 'multi_video'} or 'entries' in metadata:
            raise ValueError('Read requires a single selected video, not a playlist.')
        items = []
        for index, (path, record) in enumerate(videos, 1):
            working = media_root / f'video-{index:03d}'
            working.mkdir(parents=True, mode=0o700)
            video = working / ('source' + path.suffix.lower())
            shutil.copyfile(path, video)
            video.chmod(0o600)
            cli.status('Transcribing', f'Video {index} / {len(videos)} · local speech recognition')
            # Source duration when supplied is independent of the local probe.
            expected = metadata.get('duration') if len(videos) == 1 else None
            if expected is not None:
                expected = float(expected)
                if not math.isfinite(expected) or expected <= 0:
                    raise ValueError('The source returned an invalid video duration.')
            recognized = toutiao.transcribe(video, working, model, expected, language=args.language)
            items.append({'index': index, 'source_file': path.name, **recognized})
        source_reference = reference(resolved_url)
        part = parse_qs(urlsplit(resolved_url).query).get('p', [])
        if domain == 'bilibili.com' and len(part) == 1 and re.fullmatch(r'[1-9]\d*', part[0]):
            source_reference += '?p=' + part[0]
        result = {'reference': source_reference, 'site': site, 'kind': 'video',
                  'acquisition': 'existing_media_downloader', 'videos': items,
                  'text': '\n\n'.join(item['text'] for item in items),
                  'content_complete': True, 'content_basis': 'full_video_local_asr',
                  'recognition_verified': False, 'send_enabled': False,
                  'video_count': len(items), 'ignored_image_count': sum(p.suffix.lower() not in cli.VIDEO for p in files),
                  'ignored_motion_count': len(motion),
                  'source_count_verified': evidence.expected_count is not None,
                  'limitations': ['Speech recognition may contain errors; visual-only content is not read.',
                                  'Completeness covers downloaded video audio; source gallery counts may be unavailable.']}
        # Select text only; never persist signed URLs, cookies or raw metadata.
        for field in ('title', 'description'):
            if isinstance(metadata.get(field), str):
                result[field] = metadata[field]
        pending = folder / '.content.json.tmp'
        try:
            with pending.open('x') as stream:
                pending.chmod(0o600)
                json.dump(result, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            pending.rename(output)
        finally:
            pending.unlink(missing_ok=True)

    download_args = argparse.Namespace(link=url, folder=folder, browser=args.browser,
                                       profile=args.profile, date_now=False, live_photos=False,
                                       redownload=False)
    try:
        code = cli.download(download_args, {}, media_consumer=consume)
        if code == 0:
            print(json.dumps({'status': 'content_ready', 'content': str(output), 'send_enabled': False}, ensure_ascii=False))
        return code
    finally:
        if media_root.exists() and not args.keep_media:
            shutil.rmtree(media_root)


def main(argv):
    parser = argparse.ArgumentParser(prog='linkget read', description='Read Toutiao article text or transcribe videos from supported post channels locally.')
    parser.add_argument('url', help='Single post URL or quoted share text')
    parser.add_argument('--folder', required=True, type=Path, help='New content folder; existing folders are refused')
    parser.add_argument('--transcribe', action='store_true', help='Transcribe video speech locally')
    parser.add_argument('--whisper-model', type=Path, help='Existing whisper.cpp model; no automatic downloads')
    parser.add_argument('--language', default='auto', help='Whisper language code, e.g. zh, en, or auto (default)')
    parser.add_argument('--keep-media', action='store_true', help='Retain media temporarily for recognition review')
    parser.add_argument('--page-data', type=Path, help='Toutiao only: content JSON from an authorized browser connector')
    parser.add_argument('--browser', choices=['auto', 'none', 'chrome', 'firefox', 'safari', 'edge', 'brave'], default='auto', help='Existing download login flow; none means public only')
    parser.add_argument('--profile', help='Selected browser profile; requires an explicit browser')
    args = parser.parse_args(argv)
    if args.transcribe != (args.whisper_model is not None):
        parser.error('--transcribe and --whisper-model must be supplied together')
    if args.profile and args.browser in {'auto', 'none'}:
        parser.error('--profile requires an explicit --browser')
    if not re.fullmatch(r'auto|[a-z]{2,3}', args.language):
        parser.error('--language must be auto or a Whisper language code such as zh or en')
    supplied = re.findall(r'https?://[^\s<>"，。]+', args.url.strip())
    candidate = supplied[0].rstrip('.,;!）)') if len(supplied) == 1 else args.url
    if urlsplit(candidate).hostname in toutiao.HOSTS:
        if (args.browser not in {'auto', 'chrome'} and args.page_data is None) or args.profile:
            parser.error('Toutiao reads the daily Chrome page; use --page-data for connector evidence')
        tokens = list(argv)
        tokens[tokens.index(args.url)] = candidate
        if '--browser' in tokens:
            index = tokens.index('--browser')
            del tokens[index:index + 2]
        tokens = [token for token in tokens if not token.startswith('--browser=')]
        return toutiao.main(tokens)
    try:
        return read_videos(args)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Content read stopped: ' + str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 130
