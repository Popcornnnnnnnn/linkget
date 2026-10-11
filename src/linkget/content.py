"""Unified content reading: subtitles, local ASR, OCR, exports and recovery."""
import argparse
from datetime import datetime
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
from urllib.parse import parse_qs, urlsplit

if __package__:
    from . import toutiao, read_store, read_formats, read_asr, read_ocr, read_tasks
    from .engines import route
    from .links import normalize_link
    from .media import reference, AUDIO, DownloadResult
    from .preflight import writable_directory
else:
    import toutiao, read_store, read_formats, read_asr, read_ocr, read_tasks
    from engines import route
    from links import normalize_link
    from media import reference, AUDIO, DownloadResult
    from preflight import writable_directory


def cli_module():
    if __package__:
        from . import cli
    else:
        import cli
    return cli


def source_reference(url):
    value = reference(url)
    part = parse_qs(urlsplit(url).query).get('p', [])
    if urlsplit(url).hostname in {'www.bilibili.com', 'bilibili.com'} and len(part) == 1 and re.fullmatch(r'[1-9]\d*', part[0]):
        value += '?p=' + part[0]
    return value


def read_videos(args):
    cli = cli_module()
    config = read_store.settings()
    model = getattr(args, 'whisper_model', None) or read_store.model_default(config)
    model = model.expanduser().resolve() if model else None
    if model is not None and not model.is_file():
        if getattr(args, 'whisper_model', None):
            raise ValueError('The selected Whisper model is missing; use linkget read config --model PATH.')
        model = None
    language = getattr(args, 'language', None) or config.get('language', 'auto')
    formats = getattr(args, 'formats', None) or config.get('formats', 'md,txt,srt')
    formats = [value.strip() for value in formats.split(',') if value.strip()]
    if any(f not in {'md', 'txt', 'srt', 'vtt'} for f in formats):
        raise ValueError('Formats must be md, txt, srt or vtt.')
    subtitle_mode = getattr(args, 'subtitles', 'auto')
    ocr = getattr(args, 'ocr', False)
    chunk_seconds = getattr(args, 'chunk_seconds', 600)
    vad_model = getattr(args, 'vad_model', None)
    if vad_model:
        vad_model = vad_model.expanduser().resolve()
    if not 30 <= chunk_seconds <= 1800:
        raise ValueError('Chunk length must be between 30 and 1800 seconds.')
    local = None
    if not re.search(r'https?://', args.url):
        local = Path(args.url).expanduser().resolve()
        retained = getattr(args, 'resume', False) and getattr(args, 'folder', None) and (args.folder / '.read-inputs.json').is_file()
        if (not local.is_file() and not retained) or local.is_symlink():
            raise ValueError('Enter one supported post URL or an existing local media file.')
        url, site = str(local), 'Local file'
    else:
        supplied = re.findall(r'https?://[^\s<>"，。]+', args.url.strip())
        candidate = supplied[0].rstrip('.,;!）)') if len(supplied) == 1 else args.url
        if urlsplit(candidate).hostname in toutiao.HOSTS:
            url, site = toutiao.canonical(candidate), 'Toutiao'
        else:
            url = normalize_link(args.url)
            site, _, domain = route(url)
            if domain == 'pixiv.net':
                raise ValueError('Pixiv is static artwork only; use a supported post or local image for OCR.')
            if domain == 'pinterest.com' and not re.fullmatch(r'/pin/\d+/?', urlsplit(url).path):
                raise ValueError('Read requires one Pinterest pin, not a board or profile.')
            if domain == 'lofter.com' and not re.fullmatch(r'/post/[A-Za-z0-9_]+/?', urlsplit(url).path):
                raise ValueError('Read requires one LOFTER post, not a profile.')
    if getattr(args, 'page_data', None) and site != 'Toutiao':
        raise ValueError('--page-data is supported only for Toutiao.')
    if site == 'Toutiao' and not getattr(args, 'language', None) and not config.get('language'):
        language = 'zh'
    folder_option = getattr(args, 'folder', None)
    if folder_option:
        folder = folder_option.expanduser().resolve()
    else:
        base = Path(config.get('output', str(Path.home() / 'Downloads/linkget-read'))).expanduser().resolve()
        folder = base / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
    resume = getattr(args, 'resume', False)
    if folder.exists() and not resume:
        raise ValueError('The content folder already exists; choose a new folder or use read retry FOLDER.')
    writable_directory(folder)
    folder.mkdir(parents=True, exist_ok=resume, mode=0o700)
    read_tasks.record_output(folder)
    read_tasks.progress(phase='reading content')
    with read_store.lock(folder / '.read.lock'):
        options = {'whisper_model': str(model) if model else None, 'language': language,
                   'formats': ','.join(formats), 'subtitles': subtitle_mode, 'ocr': ocr,
                   'chunk_seconds': chunk_seconds, 'vad_model': str(vad_model) if vad_model else None,
                   'browser': args.browser, 'profile': args.profile,
                   'keep_media': args.keep_media, 'page_data': str(args.page_data) if getattr(args, 'page_data', None) else None}
        if resume:
            job = read_store.read_json(folder / '.read-job.json')
            if job.get('schema') != read_store.SCHEMA or job.get('folder') != str(folder):
                raise ValueError('The read checkpoint does not match this folder.')
            if job.get('status') == 'completed':
                cli.status('Done', 'This read is already complete')
                return 0
            job['status'] = 'running'
            read_store.save_job(folder, job)
        else:
            job = read_store.checkpoint(folder, args.url, options)
        media_root = folder / 'media'
        output = folder / 'content.json'
        if local:
            inputs = read_store.read_json(folder / '.read-inputs.json') if resume and not local.exists() else {}
            entries = inputs.get('audio', []) + inputs.get('images', [])
            identity = read_store.digest(local) if local.exists() else entries[0].get('sha256', 'missing') if entries else 'missing'
            source_key = str(local) + ':' + identity
        else:
            source_key = source_reference(url)
        key = read_store.cache_key(source_key, language, model, subtitle_mode, ocr, chunk_seconds, vad_model)
        completed = False

        def finish(result):
            nonlocal completed
            if not result.get('text'):
                raise ValueError('No readable source text was available.')
            result.update(schema_version=read_store.SCHEMA, send_enabled=False)
            read_formats.export(folder, result, formats)
            read_store.write_json(output, result)
            read_store.cache(key, result)
            job.update(status='completed', source=None)
            read_store.save_job(folder, job)
            completed = True
            print(json.dumps({'status': 'content_ready', 'content': str(output), 'send_enabled': False}, ensure_ascii=False))

        def video_result(items, metadata=None, evidence=None, image_text=None):
            result = {'reference': local.name if local else source_reference(url), 'site': site,
                      'kind': 'video' if items else 'post', 'acquisition': 'local_file' if local else 'existing_media_downloader',
                      'videos': items, 'text': '\n\n'.join(i['text'] for i in items), 'content_complete': True,
                      'content_basis': items[0]['content_basis'] if len(items) == 1 else 'multiple_video_sources' if items else 'post_text_and_ocr',
                      'recognition_verified': False, 'video_count': len(items),
                      'source_count_verified': evidence is not None and evidence.expected_count is not None,
                      'limitations': ['Source text and speech recognition may contain errors; visual-only events are not described.']}
            if site == 'Toutiao':
                result['url'] = url
            result.update(read_formats.source_metadata(metadata or {}))
            if image_text:
                result['images'] = image_text
                image_body = '\n\n'.join(i['text'] for i in image_text if i.get('text'))
                result['text'] = '\n\n'.join(t for t in (result['text'], image_body) if t)
            if not items and result.get('description'):
                result['text'] = '\n\n'.join(t for t in (result['description'], result['text']) if t)
            return result

        def metadata_consumer(metadata, resolved_url, resolved_site):
            if metadata.get('_type') in {'playlist', 'multi_video'} or 'entries' in metadata:
                raise ValueError('Read requires one selected video, not a playlist.')
            if metadata.get('is_live') or metadata.get('live_status') in {'is_live', 'is_upcoming', 'post_live'}:
                raise ValueError('Read requires finished media, not a live or upcoming stream.')
            if subtitle_mode == 'never':
                return False
            found = read_formats.subtitles(metadata, language)
            if found:
                cli.status('Text source', found['content_basis'])
                finish(video_result([{'index': 1, **found}], metadata))
                return True
            if subtitle_mode == 'only':
                raise ValueError('Complete subtitles in the selected language are unavailable.')
            return False

        def consume(files, records, evidence, resolved_url, resolved_site, metadata):
            motion = {name for _, name in evidence.live_pairs}
            audio_files = [(p, record) for p, record in zip(files, records)
                           if p.suffix.lower() in cli.VIDEO | AUDIO and p.name not in motion]
            image_files = [p for p in files if p.suffix.lower() not in cli.VIDEO | AUDIO]
            if audio_files and subtitle_mode == 'only':
                raise ValueError('This channel did not expose complete subtitles for the selected language.')
            if audio_files and (model is None or not all(cli.find_tool(t) for t in ('ffmpeg', 'ffprobe', 'whisper-cli'))):
                raise RuntimeError('No complete subtitles: configure the existing local model with linkget read config --model PATH.')
            # Validate all downloaded audio before recognizing any item.
            for _, record in audio_files:
                duration = float(record.get('duration') or 0)
                if record.get('probe_ok') is not True or record.get('audio') is not True or not math.isfinite(duration) or duration <= 0:
                    raise RuntimeError('Every selected media file must pass format validation and contain audio.')
            inputs_path = folder / '.read-inputs.json'
            if not inputs_path.exists():
                entries = []
                for index, (path, record) in enumerate(audio_files, 1):
                    working = media_root / f'video-{index:03d}'
                    working.mkdir(parents=True, exist_ok=True, mode=0o700)
                    dest = working / ('source' + path.suffix.lower())
                    if dest.is_symlink():
                        raise ValueError('Refusing a substituted source file.')
                    if path != dest:
                        shutil.copyfile(path, dest)
                    dest.chmod(0o600)
                    entries.append({'path': str(dest.relative_to(folder)), 'sha256': read_store.digest(dest),
                                    'original_name': path.name, 'record': record})
                image_entries = []
                if image_files and (ocr or not audio_files):
                    dest_root = media_root / 'images'
                    dest_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                    for path in image_files:
                        dest = dest_root / path.name
                        if dest.is_symlink():
                            raise ValueError('Refusing a substituted image file.')
                        if path != dest:
                            shutil.copyfile(path, dest)
                        image_entries.append({'path': str(dest.relative_to(folder)), 'sha256': read_store.digest(dest)})
                read_store.write_json(inputs_path, {'audio': entries, 'images': image_entries,
                                                    'metadata': read_formats.source_metadata(metadata),
                                                    'duration': metadata.get('duration'), 'motion_count': len(motion),
                                                    'image_count': len(image_files), 'expected_count': evidence.expected_count})
            items = []
            for index, (path, record) in enumerate(audio_files, 1):
                working = media_root / f'video-{index:03d}'
                video = working / ('source' + path.suffix.lower())
                cli.status('Transcribing', f'Media {index} / {len(audio_files)} · {float(record["duration"]):.0f}s')
                expected = metadata.get('duration') if len(audio_files) == 1 else None
                expected = float(expected) if expected is not None else None
                if expected is not None and (not math.isfinite(expected) or expected <= 0):
                    raise ValueError('The source returned an invalid duration.')
                asr_options = {} if language == 'zh' else {'language': language}
                if chunk_seconds != 600:
                    asr_options['chunk_seconds'] = chunk_seconds
                if vad_model:
                    asr_options['vad_model'] = vad_model
                recognized = toutiao.transcribe(video, working, model, expected, **asr_options)
                stored = read_store.read_json(inputs_path)
                original_name = stored['audio'][index - 1]['original_name']
                items.append({'index': index, 'source_file': original_name, **recognized})
            image_text = []
            if image_files and (ocr or not audio_files):
                cli.status('Reading images', f'{len(image_files)} image(s) · local OCR')
                copied = sorted((media_root / 'images').iterdir())
                image_text = read_ocr.images(copied)
            result = video_result(items, metadata, evidence, image_text)
            stored = read_store.read_json(inputs_path)
            result.update(ignored_image_count=stored['image_count'] if audio_files and not ocr else 0,
                          ignored_motion_count=stored['motion_count'])
            finish(result)

        try:
            cached = None if getattr(args, 'refresh', False) or resume or args.keep_media else read_store.cached(key)
            if cached:
                cli.status('Cache', 'Reusing checked source text (use --refresh to fetch again)')
                finish(cached)
                return 0
            inputs_path = folder / '.read-inputs.json'
            if resume and inputs_path.exists():
                inputs = read_store.read_json(inputs_path)
                files, records = [], []
                for entry in inputs['audio'] + inputs['images']:
                    path = folder / entry['path']
                    if path.is_symlink() or not path.is_file() or folder not in path.resolve().parents or read_store.digest(path) != entry['sha256']:
                        raise ValueError('Retained media is missing or changed; choose a new folder for a fresh read.')
                    files.append(path)
                    records.append(entry.get('record', {}))
                meta = inputs['metadata']
                if inputs.get('duration') is not None:
                    meta['duration'] = inputs['duration']
                evidence = DownloadResult(expected_count=inputs.get('expected_count'))
                consume(files, records, evidence, url, site, meta)
                return 0
            if local:
                if local.suffix.lower() in cli.VIDEO | AUDIO:
                    duration, audio = read_asr.probe(local)
                    record = {'duration': duration, 'audio': audio, 'probe_ok': audio}
                    consume([local], [record], DownloadResult(expected_count=1), url, site, {})
                else:
                    consume([local], [{}], DownloadResult(expected_count=1), url, site, {})
                return 0
            if site == 'Toutiao':
                if args.browser not in {'auto', 'chrome'} and not args.page_data or args.profile:
                    raise ValueError('Toutiao reads daily Chrome; --page-data accepts connector content without browser access.')
                page = read_store.read_json(args.page_data) if args.page_data else toutiao.read_browser(url)
                item = toutiao.validate_page(url, page)
                if item['kind'] == 'article':
                    finish({**item, 'site': site, 'reference': source_reference(url)})
                else:
                    if subtitle_mode == 'only':
                        raise ValueError('The Toutiao player did not expose complete source subtitles.')
                    if model is None:
                        raise RuntimeError('Toutiao video speech requires an existing local model.')
                    temporary = folder / '.toutiao-download'
                    temporary.mkdir(mode=0o700)
                    try:
                        video = temporary / 'source.mp4'
                        toutiao.download_video(item['media_url'], video)
                        records = cli.inspect_files([video])
                        consume([video], records, DownloadResult(expected_count=1), url, site,
                                {'title': item['title'], 'description': item['description'], 'duration': item['duration']})
                    finally:
                        shutil.rmtree(temporary)
                return 0
            download_args = argparse.Namespace(link=url, folder=folder, browser=args.browser, profile=args.profile,
                                               date_now=False, live_photos=False, redownload=False,
                                               read_audio=not args.keep_media, read_metadata_consumer=metadata_consumer)
            code = cli.download(download_args, {}, media_consumer=consume)
            if code != 0:
                job['status'] = 'cancelled' if code == 130 else 'failed'
                read_store.save_job(folder, job)
                cli.status('Retry', f'linkget read retry "{folder}"', stream=sys.stderr)
            return code
        except BaseException as error:
            job['status'] = 'cancelled' if isinstance(error, (KeyboardInterrupt, EOFError)) else 'failed'
            read_store.save_job(folder, job)
            raise
        finally:
            if completed and not args.keep_media and media_root.exists():
                shutil.rmtree(media_root)


def config_command(argv):
    parser = argparse.ArgumentParser(prog='linkget read config')
    parser.add_argument('--model', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--language')
    parser.add_argument('--formats')
    parser.add_argument('--ai-provider', choices=['openai', 'compatible', 'ollama', 'lmstudio'])
    parser.add_argument('--ai-model')
    parser.add_argument('--ai-endpoint')
    parser.add_argument('--ai-key-env')
    parser.add_argument('--ai-auth', choices=['env', 'cpa'], help='env: named environment key; cpa: reuse existing loopback proxy client authentication')
    parser.add_argument('--ai-auth-file', type=Path, help='Existing CPA config path; default /opt/homebrew/etc/cliproxyapi.conf')
    parser.add_argument('--ai-fallback-provider', choices=['openai', 'compatible', 'ollama', 'lmstudio'])
    parser.add_argument('--ai-fallback-model', help='Opt into one automatic fallback on a provider outage')
    parser.add_argument('--ai-fallback-endpoint')
    parser.add_argument('--ai-fallback-key-env')
    parser.add_argument('--ai-fallback-auth', choices=['env', 'cpa'])
    parser.add_argument('--ai-fallback-auth-file', type=Path)
    parser.add_argument('--clear', action='append', choices=['model', 'output', 'language', 'formats', 'ai_provider', 'ai_model', 'ai_endpoint', 'ai_key_env', 'ai_auth', 'ai_auth_file', 'ai_fallback_provider', 'ai_fallback_model', 'ai_fallback_endpoint', 'ai_fallback_key_env', 'ai_fallback_auth', 'ai_fallback_auth_file'])
    args = parser.parse_args(argv)
    changes = {key: value for key, value in vars(args).items() if key != 'clear' and value is not None}
    for key in ('model', 'output', 'ai_auth_file', 'ai_fallback_auth_file'):
        if key in changes:
            path = changes[key].expanduser().resolve()
            if key in {'model', 'ai_auth_file', 'ai_fallback_auth_file'} and not path.is_file():
                raise ValueError('The model or authentication configuration file does not exist.')
            changes[key] = str(path)
    if 'language' in changes and not re.fullmatch(r'auto|[a-z]{2,3}', changes['language']):
        raise ValueError('Use auto or a Whisper language code.')
    if 'formats' in changes and any(f not in {'md', 'txt', 'srt', 'vtt'} for f in changes['formats'].split(',')):
        raise ValueError('Use md,txt,srt,vtt output formats.')
    changes.update({key: None for key in args.clear or []})
    print(json.dumps(read_store.settings(changes if changes else None), ensure_ascii=False, indent=2))
    return 0


def doctor():
    cli = cli_module()
    config = read_store.settings()
    missing = False
    for name in ('ffmpeg', 'ffprobe', 'whisper-cli'):
        found = cli.find_tool(name)
        cli.status(name, found or 'Missing')
        missing |= not bool(found)
    model = read_store.model_default(config)
    cli.status('Whisper model', str(model) if model and model.is_file() else 'Missing · read config --model PATH')
    return 1 if missing or not model or not model.is_file() else 0


def read_parser(batch=False):
    parser = argparse.ArgumentParser(prog='linkget read',
        description='Read post text, subtitles, local media speech and image text. Runs in the background by default.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''Commands:
  config [OPTIONS]          View/change recognition and AI/fallback settings
  doctor                    Check local recognition tools and model
  batch [URL_OR_FILE ...]    Read multiple inputs; accepts one per stdin line
  recover                   List unfinished reads and retained checkpoints
  retry [FOLDER]            Resume a retained unfinished read
  translate FOLDER          Translate complete saved text (--language zh)
  summarize FOLDER          Summarize complete saved text with source references
  status [TASK]             List recent tasks, or inspect one task and results
  logs TASK [--tail N]      Show a task's private log (default: last 50 lines)
  cancel TASK               Stop a task and preserve source/checkpoints

Examples:
  linkget read 'https://www.bilibili.com/video/BV1bK411W797?p=1'
  linkget read ~/Downloads/recording.m4a --language zh
  linkget read ~/Downloads/screenshot.png
  linkget read translate '/path/to/read-folder' --language en
  linkget read status TASK
  linkget read logs TASK
  linkget read cancel TASK

Processing returns a task ID immediately; closing the terminal does not stop it.
Submission success means accepted; inspect status for completion or failure.
Add --foreground to read, batch, retry, translate or summarize to wait.
Recognition/OCR run locally. Only translate/summarize use the configured AI.
An enabled AI fallback switches on provider outages; config --help lists options.''')
    parser.add_argument('url', nargs='*' if batch else None)
    parser.add_argument('--folder', type=Path, help='New content folder; batch: parent folder')
    parser.add_argument('--transcribe', action='store_true', help='Accepted for compatibility; missing subtitles automatically use local ASR')
    parser.add_argument('--whisper-model', type=Path, help='Override saved/default local model')
    parser.add_argument('--language', help='Whisper language or auto; default: saved setting or auto')
    parser.add_argument('--formats', help='md,txt,srt,vtt; JSON is always saved')
    parser.add_argument('--subtitles', choices=['auto', 'never', 'only'], default='auto')
    parser.add_argument('--chunk-seconds', type=int, default=600)
    parser.add_argument('--vad-model', type=Path, help='Existing optional speech activity model')
    parser.add_argument('--ocr', action='store_true', help='Also OCR images in mixed video/image posts; image-only reads use OCR')
    parser.add_argument('--refresh', action='store_true', help='Bypass the 24-hour complete-content cache')
    parser.add_argument('--keep-media', action='store_true')
    parser.add_argument('--page-data', type=Path, help='Toutiao only: content-only page JSON')
    parser.add_argument('--browser', choices=['auto', 'none', 'chrome', 'firefox', 'safari', 'edge', 'brave'], default='auto')
    parser.add_argument('--profile')
    parser.add_argument('--foreground', action='store_true', help='Wait for completion instead of running in the background')
    return parser


def main(argv):
    try:
        if argv and argv[0] == 'config':
            return config_command(argv[1:])
        if argv and argv[0] == 'doctor':
            return doctor()
        if argv and argv[0] == 'recover':
            argparse.ArgumentParser(prog='linkget read recover', description='List unfinished reads and retained checkpoint folders; use read retry FOLDER to resume.').parse_args(argv[1:])
            for job in read_store.unfinished():
                print(job['status'] + '  ' + job['folder'])
            return 0
        if argv and argv[0] in {'summarize', 'translate'}:
            if __package__:
                from .read_ai import main as ai_main
            else:
                from read_ai import main as ai_main
            return ai_main(argv)
        if argv and argv[0] == 'retry':
            parser = argparse.ArgumentParser(prog='linkget read retry', description='Resume retained media and recognition checkpoints in the background; preserves completed chunks.')
            parser.add_argument('folder', nargs='?', type=Path, help='Unfinished read folder; omit to select the latest unfinished read')
            parser.add_argument('--foreground', action='store_true', help='Wait for completion and return its exit code')
            retry = parser.parse_args(argv[1:])
            pending = read_store.unfinished()
            folder = retry.folder.expanduser().resolve() if retry.folder else Path(pending[0]['folder']) if pending else None
            if folder is None:
                raise ValueError('No unfinished read was found.')
            record = read_store.read_json(folder / '.read-job.json')
            args = argparse.Namespace(**record['options'], url=record.get('source') or '', folder=folder,
                                      resume=True, refresh=False, transcribe=True)
            for field in ('whisper_model', 'vad_model', 'page_data'):
                value = getattr(args, field, None)
                setattr(args, field, Path(value) if value else None)
            if record['status'] == 'completed':
                print('This read is already complete.')
                return 0
            return read_videos(args)
        batch = bool(argv and argv[0] == 'batch')
        parser = read_parser(batch)
        args = parser.parse_args(argv[1:] if batch else argv)
        args.resume = False
        if args.profile and args.browser in {'auto', 'none'}:
            parser.error('--profile requires an explicit browser')
        if args.language and not re.fullmatch(r'auto|[a-z]{2,3}', args.language):
            parser.error('Use auto or a Whisper language code')
        if not batch:
            return read_videos(args)
        links = args.url
        if not links:
            if sys.stdin.isatty():
                print('Paste one link or local file per line; blank line starts reads.')
                while line := input('> ').strip():
                    links.append(line)
            else:
                links = [line.strip() for line in sys.stdin if line.strip()]
        if not links:
            parser.error('The read batch is empty')
        failed = []
        for index, link in enumerate(links, 1):
            item = argparse.Namespace(**vars(args))
            item.url = link
            item.folder = args.folder / f'item-{index:03d}' if args.folder else None
            print(f'Reading {index} / {len(links)}')
            try:
                code = read_videos(item)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                print(str(error), file=sys.stderr)
                code = 1
            if code == 130:
                return 130
            if code:
                failed.append(index)
        print(f'{len(links) - len(failed)} completed; failed items: {failed}')
        return 1 if failed else 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Content read stopped: ' + str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        return 130
