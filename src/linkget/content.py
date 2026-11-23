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
    from . import agent_output, toutiao, rendered_page, read_store, read_formats, read_asr, read_frames, read_ocr, read_tasks, runtime, web_article
    from .engines import route
    from .links import normalize_link
    from .media import reference, AUDIO, DownloadResult
    from .preflight import writable_directory
else:
    import agent_output, toutiao, rendered_page, read_store, read_formats, read_asr, read_frames, read_ocr, read_tasks, runtime, web_article
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
    frame_ocr = bool(getattr(args, 'frame_ocr', False))
    extract_frames = bool(getattr(args, 'frames', False) or frame_ocr)
    max_frames = int(getattr(args, 'max_frames', read_frames.DEFAULT_MAX_FRAMES))
    if not read_frames.MIN_FRAMES <= max_frames <= read_frames.MAX_FRAMES:
        raise ValueError(f'--max-frames must be between {read_frames.MIN_FRAMES} and {read_frames.MAX_FRAMES}.')
    chunk_seconds = getattr(args, 'chunk_seconds', 600)
    vad_model = getattr(args, 'vad_model', None)
    if vad_model:
        vad_model = vad_model.expanduser().resolve()
    if not 30 <= chunk_seconds <= 1800:
        raise ValueError('Chunk length must be between 30 and 1800 seconds.')
    local, public_article, wechat_article, web_article_target = None, False, False, False
    wechat_browser_eligible = False
    if not re.search(r'https?://', args.url):
        local = Path(args.url).expanduser().resolve()
        retained = getattr(args, 'resume', False) and getattr(args, 'folder', None) and (args.folder / '.read-inputs.json').is_file()
        if (not local.is_file() and not retained) or local.is_symlink():
            raise ValueError('Enter one supported post URL or an existing local media file.')
        url, site = str(local), 'Local file'
    else:
        supplied = re.findall(r'https?://[^\s<>"，。]+', args.url.strip())
        candidate = supplied[0].rstrip('.,;!）)') if len(supplied) == 1 else args.url.strip()
        cand_host = (urlsplit(candidate).hostname or '').lower().rstrip('.')
        if cand_host in toutiao.HOSTS:
            url, site = toutiao.canonical(candidate), 'Toutiao'
        elif cand_host in {'www.raspberrypi.com', 'zhuanlan.zhihu.com'}:
            url, _ = rendered_page.public_article_url(candidate)
            site = 'Raspberry Pi' if url.startswith('https://www.raspberrypi.com/') else 'Zhihu'
            public_article = True
        elif cand_host == web_article.WECHAT_HOST:
            url, wechat_browser_eligible = web_article.canonical_wechat_url(candidate)
            site, wechat_article = 'WeChat', True
        elif web_article.is_known_media_host(cand_host):
            url = normalize_link(args.url)
            site, _, domain = route(url)
            if domain == 'pixiv.net':
                raise ValueError('Pixiv is static artwork only; use a supported post or local image for OCR.')
            if domain == 'pinterest.com' and not re.fullmatch(r'/pin/\d+/?', urlsplit(url).path):
                raise ValueError('Read requires one Pinterest pin, not a board or profile.')
            if domain == 'lofter.com' and not re.fullmatch(r'/post/[A-Za-z0-9_]+/?', urlsplit(url).path):
                raise ValueError('Read requires one LOFTER post, not a profile.')
        else:
            url = web_article.validate_public_web_url(candidate, resolve_dns=False)
            site = cand_host or 'Web'
            web_article_target = True
    if getattr(args, 'page_data', None) and site != 'Toutiao':
        raise ValueError('--page-data is supported only for Toutiao.')
    rendered_backend = getattr(args, 'rendered_backend', None)
    scoped_rendered = (site == 'Toutiao' or public_article) and not getattr(args, 'page_data', None)
    wechat_rendered = wechat_article and wechat_browser_eligible
    reader_choice = (
        rendered_page.selection(
            rendered_backend,
            browser=args.browser,
            backend_source='retained checkpoint' if getattr(args, 'resume', False) and not getattr(args, '_rendered_backend_override', False) else 'command option',
            browser_source='retained checkpoint' if getattr(args, 'resume', False) else 'command browser option',
        )
        if (scoped_rendered or wechat_rendered)
        else None
    )
    if rendered_backend is not None and not (site == 'Toutiao' or public_article or wechat_rendered):
        raise ValueError('--rendered-backend applies only to Toutiao, scoped public articles, and WeChat /s/<id> articles.')
    if scoped_rendered:
        rendered_backend = rendered_page.selected_backend(rendered_backend, browser=args.browser)
    elif wechat_rendered and rendered_backend is not None:
        rendered_backend = rendered_page.selected_backend(rendered_backend, browser=args.browser)
    if public_article and (args.browser not in {'auto', 'chrome'} or args.profile):
        raise ValueError('This scoped article uses the selected rendered reader; --browser none disables '
                         'browser access, and another credential profile is not supported.')
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
                   'frames': extract_frames, 'max_frames': max_frames, 'frame_ocr': frame_ocr,
                   'chunk_seconds': chunk_seconds, 'vad_model': str(vad_model) if vad_model else None,
                   'browser': args.browser, 'profile': args.profile,
                   'rendered_backend': rendered_backend,
                   'keep_media': args.keep_media, 'page_data': str(args.page_data) if getattr(args, 'page_data', None) else None}
        if resume:
            job = read_store.read_json(folder / '.read-job.json')
            if job.get('schema') != read_store.SCHEMA or job.get('folder') != str(folder):
                raise ValueError('The read checkpoint does not match this folder.')
            if job.get('status') == 'completed':
                if (runtime.json_mode() or getattr(args, 'json', False)) and getattr(args, '_batch_collect', None) is None and not getattr(args, 'stdout', False):
                    content_file = folder / 'content.json'
                    data = read_store.read_json(content_file) if content_file.is_file() else None
                    agent_output.emit({
                        'schema_version': agent_output.SCHEMA_VERSION,
                        'ok': True,
                        'command': 'read retry',
                        'already_completed': True,
                        'folder': str(folder),
                        'content_path': str(content_file) if content_file.is_file() else None,
                        'result': data,
                    })
                else:
                    cli.status('Done', 'This read is already complete')
                return 0
            job['status'] = 'running'
            if getattr(args, '_rendered_backend_override', False):
                job['options']['rendered_backend'] = rendered_backend
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
        if rendered_backend == 'neo':
            source_key += ':rendered-reader:neo'
        elif wechat_article and rendered_backend == 'chrome':
            source_key += ':rendered-reader:chrome'
        key = read_store.cache_key(source_key, language, model, subtitle_mode, ocr, chunk_seconds, vad_model)
        completed = False
        prefetched_subtitles = None

        def finish(result):
            nonlocal completed
            if not result.get('text'):
                raise ValueError('No readable source text was available.')
            result.update(schema_version=read_store.SCHEMA, send_enabled=False)
            read_formats.export(folder, result, formats)
            read_store.write_json(output, result)
            if not extract_frames and not result.get('frames'):
                read_store.cache(key, result)
            job.update(status='completed', source=None)
            read_store.save_job(folder, job)
            completed = True
            if getattr(args, '_batch_collect', None) is not None:
                args._batch_collect.append({'folder': str(folder), 'content_path': str(output), 'result': result})
            elif getattr(args, 'stdout', False):
                fmt = getattr(args, 'stdout_format', 'md')
                if fmt == 'json':
                    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
                elif fmt == 'txt':
                    sys.stdout.write((result.get('text') or '') + '\n')
                    sys.stdout.flush()
                else:
                    md_path = folder / 'content.md'
                    text = md_path.read_text() if md_path.is_file() else read_formats.markdown(result)
                    sys.stdout.write(text)
                    sys.stdout.flush()
            elif getattr(args, 'json', False) or runtime.json_mode():
                agent_output.emit({
                    'schema_version': agent_output.SCHEMA_VERSION,
                    'ok': True,
                    'command': 'read',
                    'mode': 'foreground',
                    'folder': str(folder),
                    'content_path': str(output),
                    'result': result,
                })
            else:
                print(json.dumps({'status': 'content_ready', 'content': str(output), 'send_enabled': False}, ensure_ascii=False))

        def content_metadata(metadata):
            fields = read_formats.source_metadata(metadata or {})
            if site == 'Toutiao':
                for name in ('duration', 'duration_basis', 'media_binding', 'media_selection',
                             'transfer_evidence', 'paired_media_evidence', 'reader_backend', 'browser_controller', 'article_dom_proof'):
                    if name in (metadata or {}): fields[name] = metadata[name]
            return fields

        def video_result(items, metadata=None, evidence=None, image_text=None, frames=None):
            text_parts = [i['text'] for i in items if i.get('text')]
            has_speech_or_subs = any(i.get('segments') for i in items)
            if not text_parts and frames:
                frame_ocr_lines = [f.get('ocr_text', '') for f in frames if f.get('ocr_text')]
                if frame_ocr_lines:
                    text_parts = frame_ocr_lines
                    default_basis = 'video_keyframes_ocr'
                else:
                    text_parts = [
                        f"[{f['timestamp_label']}] Frame {f['index']} ({f['relative_path']})"
                        for f in frames
                    ]
                    default_basis = 'video_keyframes'
            else:
                default_basis = items[0]['content_basis'] if len(items) == 1 else 'multiple_video_sources' if items else 'post_text_and_ocr'
            limitations = ['Source text and speech recognition may contain errors; visual-only events are not described.']
            if frames:
                limitations = ['Extracted representative keyframes; speech/text recognition may contain errors and unselected frames are not included.']
            result = {'reference': local.name if local else source_reference(url), 'site': site,
                      'kind': 'video' if items else 'post', 'acquisition': 'local_file' if local else 'existing_media_downloader',
                      'videos': items, 'text': '\n\n'.join(text_parts), 'content_complete': True,
                      'content_basis': default_basis if (not items or not has_speech_or_subs and frames) else (items[0]['content_basis'] if len(items) == 1 else 'multiple_video_sources'),
                      'recognition_verified': False, 'video_count': len(items),
                      'source_count_verified': evidence is not None and evidence.expected_count is not None,
                      'limitations': limitations}
            if frames:
                result['frames'] = frames
            if site == 'Toutiao':
                result['url'] = url
            result.update(content_metadata(metadata))
            if image_text:
                result['images'] = image_text
                image_body = '\n\n'.join(i['text'] for i in image_text if i.get('text'))
                result['text'] = '\n\n'.join(t for t in (result['text'], image_body) if t)
            if not items and result.get('description'):
                result['text'] = '\n\n'.join(t for t in (result['description'], result['text']) if t)
            return result

        def metadata_consumer(metadata, resolved_url, resolved_site):
            nonlocal prefetched_subtitles
            if metadata.get('_type') in {'playlist', 'multi_video'} or 'entries' in metadata:
                raise ValueError('Read requires one selected video, not a playlist.')
            if metadata.get('is_live') or metadata.get('live_status') in {'is_live', 'is_upcoming', 'post_live'}:
                raise ValueError('Read requires finished media, not a live or upcoming stream.')
            if subtitle_mode == 'never':
                return False
            found = read_formats.subtitles(metadata, language)
            if found:
                cli.status('Text source', found['content_basis'])
                if extract_frames:
                    prefetched_subtitles = found
                    return False
                finish(video_result([{'index': 1, **found}], metadata))
                return True
            if subtitle_mode == 'only':
                raise ValueError('Complete subtitles in the selected language are unavailable.')
            return False

        def consume(files, records, evidence, resolved_url, resolved_site, metadata):
            motion = {name for _, name in evidence.live_pairs}
            frame_video_exts = cli.VIDEO | {'.webm'}
            audio_files = [(p, record) for p, record in zip(files, records)
                           if p.suffix.lower() in cli.VIDEO | AUDIO and p.name not in motion]
            image_files = [p for p in files if p.suffix.lower() not in cli.VIDEO | AUDIO]
            if audio_files and subtitle_mode == 'only' and prefetched_subtitles is None:
                raise ValueError('This channel did not expose complete subtitles for the selected language.')
            need_asr = (
                prefetched_subtitles is None
                and any(
                    record.get('audio') is True or not (extract_frames and p.suffix.lower() in frame_video_exts)
                    for p, record in audio_files
                )
            )
            if audio_files and need_asr and (model is None or not all(cli.find_tool(t) for t in ('ffmpeg', 'ffprobe', 'whisper-cli'))):
                raise RuntimeError('No complete subtitles: configure the existing local model with linkget read config --model PATH.')
            if audio_files and extract_frames and not all(cli.find_tool(t) for t in ('ffmpeg', 'ffprobe')):
                raise RuntimeError('Video keyframe extraction requires existing local ffmpeg and ffprobe tools.')
            # Validate all downloaded media before processing any item.
            for p, record in audio_files:
                duration = float(record.get('duration') or 0)
                if extract_frames and p.suffix.lower() in frame_video_exts and record.get('probe_ok') is True and math.isfinite(duration) and duration > 0:
                    continue
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
                                                    'metadata': content_metadata(metadata),
                                                    'duration': metadata.get('duration'), 'motion_count': len(motion),
                                                    'image_count': len(image_files), 'expected_count': evidence.expected_count,
                                                    'source_transport': evidence.source_transport})
            items = []
            all_frames = []
            for index, (path, record) in enumerate(audio_files, 1):
                working = media_root / f'video-{index:03d}'
                video = working / ('source' + path.suffix.lower())
                stored = read_store.read_json(inputs_path)
                retained = stored['audio'][index - 1]
                original_name = retained['original_name']
                if prefetched_subtitles is not None and index == 1:
                    recognized = dict(prefetched_subtitles)
                elif extract_frames and path.suffix.lower() in frame_video_exts and record.get('audio') is not True:
                    recognized = {
                        'text': '',
                        'segments': [],
                        'content_complete': True,
                        'content_basis': 'video_keyframes_ocr' if frame_ocr else 'video_keyframes',
                        'recognition_verified': False,
                    }
                else:
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
                    recognized = {**toutiao.transcribe(video, working, model, expected, **asr_options)}
                transport = stored.get('source_transport') or {}
                if transport and recognized.get('segments') is not None:
                    transfer = transport.get('files', {}).get(original_name)
                    actual_sha = read_store.digest(video)
                    actual_size = video.stat().st_size
                    if (transport.get('schema') != 'linkget.xiaohongshu.transport.v1' or
                            not isinstance(transfer, dict) or transfer.get('file') != original_name or
                            transfer.get('batch_id') != transport.get('batch_id') or
                            transfer.get('note_id') != transport.get('note_id') or
                            transfer.get('sha256') != actual_sha or
                            transfer.get('actual_bytes') != actual_size or retained['sha256'] != actual_sha or
                            record.get('sha256') != actual_sha or record.get('bytes') != actual_size):
                        raise ValueError('Downloaded source transport does not match the recognized file.')
                    duration = float(record['duration'])
                    source_bound = (transport.get('note_id') == transport.get('requested_note_id') and
                                    bool(transfer.get('final_host')) and bool(transfer.get('final_url_sha256')))
                    complete = (type(transfer.get('expected_bytes')) is int and
                                transfer['expected_bytes'] == actual_size and actual_size > 0 and
                                transfer.get('http_status') == 200 and transfer.get('range_present') is False)
                    recognized['media_evidence'] = {**recognized.get('media_evidence', {}),
                        'transfer': transfer, 'full_video_download': complete,
                        'source_binding_verified': source_bound,
                        'probe_evidence': {'duration': duration, 'audio': record['audio'],
                                           'probe_ok': record['probe_ok'], 'sha256': actual_sha,
                                           'bytes': actual_size}}
                    recognized.update(source_file_sha256=actual_sha, source_file_bytes=actual_size,
                                      download_batch_id=transport['batch_id'])
                item_frames = []
                if extract_frames and path.suffix.lower() in frame_video_exts:
                    cli.status('Extracting frames', f'Media {index} / {len(audio_files)}')
                    item_frames = read_frames.extract_keyframes(
                        video,
                        folder / 'frames',
                        ffmpeg=cli.find_tool('ffmpeg'),
                        ffprobe=cli.find_tool('ffprobe'),
                        max_frames=max_frames,
                        prefix=f'v{index}',
                    )
                    if frame_ocr and item_frames:
                        cli.status('Reading frames', f'{len(item_frames)} keyframe(s) · local OCR')
                        read_frames.annotate_frames_ocr(item_frames)
                    all_frames.extend(item_frames)
                item_entry = {'index': index, 'source_file': original_name, **recognized}
                if item_frames:
                    item_entry['frames'] = item_frames
                items.append(item_entry)
            image_text = []
            if image_files and (ocr or not audio_files):
                cli.status('Reading images', f'{len(image_files)} image(s) · local OCR')
                copied = sorted((media_root / 'images').iterdir())
                image_text = read_ocr.images(copied)
            result = video_result(items, metadata, evidence, image_text, frames=all_frames)
            stored = read_store.read_json(inputs_path)
            result.update(ignored_image_count=stored['image_count'] if audio_files and not ocr else 0,
                          ignored_motion_count=stored['motion_count'])
            if stored.get('source_transport'):
                result['source_transport'] = stored['source_transport']
            finish(result)

        try:
            cached = None if (getattr(args, 'refresh', False) or resume or args.keep_media or extract_frames) else read_store.cached(key)
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
                evidence = DownloadResult(expected_count=inputs.get('expected_count'),
                                          source_transport=inputs.get('source_transport') or {})
                consume(files, records, evidence, url, site, meta)
                return 0
            if local:
                if local.suffix.lower() in cli.VIDEO | AUDIO:
                    if extract_frames and local.suffix.lower() in cli.VIDEO | {'.webm'}:
                        info = read_frames.probe_video(local, ffprobe=cli.find_tool('ffprobe'))
                        duration = info['duration']
                        has_audio = None
                        try:
                            asr_dur, has_audio = read_asr.probe(local)
                            if duration is None:
                                duration = asr_dur
                        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                            has_audio = False
                        record = {
                            'duration': duration,
                            'audio': bool(has_audio),
                            'probe_ok': bool((info['has_video'] or has_audio) and duration),
                        }
                    else:
                        duration, audio = read_asr.probe(local)
                        record = {'duration': duration, 'audio': audio, 'probe_ok': audio}
                    consume([local], [record], DownloadResult(expected_count=1), url, site, {})
                else:
                    consume([local], [{}], DownloadResult(expected_count=1), url, site, {})
                return 0
            if web_article_target:
                cli.status('Fetching article', site, stream=sys.stderr)
                item = web_article.read_article(url)
                finish(item)
                return 0
            if wechat_article:
                if wechat_browser_eligible and rendered_backend is not None:
                    cli.status('Page reader', rendered_backend + ' · command option', stream=sys.stderr)
                    item = rendered_page.read_wechat(url, backend=rendered_backend)
                    finish({**item, 'site': 'WeChat', 'reference': url,
                            'acquisition': 'browseros_neo_rendered_page' if rendered_backend == 'neo' else 'chrome_rendered_page',
                            'content_basis': 'complete_browseros_neo_wechat_dom' if rendered_backend == 'neo' else 'complete_daily_chrome_wechat_dom',
                            'limitations': ['Only this bounded WeChat article DOM was read; HTTP transfer completeness and comments were not verified.']})
                    return 0
                try:
                    cli.status('Fetching WeChat article', 'direct HTTP', stream=sys.stderr)
                    item = web_article.read_wechat_http(url)
                    finish(item)
                    return 0
                except web_article.WeChatGateError:
                    if wechat_browser_eligible and args.browser != 'none' and reader_choice and reader_choice['supported']:
                        cli.status('Page reader', reader_choice['browser'] + ' · ' + reader_choice['source'], stream=sys.stderr)
                        item = rendered_page.read_wechat(url, backend=reader_choice['browser'])
                        finish({**item, 'site': 'WeChat', 'reference': url,
                                'acquisition': 'browseros_neo_rendered_page' if reader_choice['browser'] == 'neo' else 'chrome_rendered_page',
                                'content_basis': 'complete_browseros_neo_wechat_dom' if reader_choice['browser'] == 'neo' else 'complete_daily_chrome_wechat_dom',
                                'limitations': ['Only this bounded WeChat article DOM was read; HTTP transfer completeness and comments were not verified.']})
                        return 0
                    raise
            if public_article:
                cli.status('Page reader', reader_choice['browser']+' · '+reader_choice['source'], stream=sys.stderr)
                item = rendered_page.read_public(url, backend=rendered_backend)
                item = rendered_page.validate_public_page(item, url)
                finish({**item, 'site': site, 'reference': source_reference(url),
                        'acquisition': 'browseros_neo_rendered_page' if rendered_backend == 'neo' else 'chrome_rendered_page',
                        'content_basis': 'complete_browseros_neo_scoped_article_dom' if rendered_backend == 'neo' else 'complete_daily_chrome_scoped_article_dom',
                        'read_scope': 'scoped_public_article_rendered_text_only',
                        'limitations': ['Only this scoped article DOM was read; HTTP transfer completeness, '
                                        'linked documents and embedded media were not verified.']})
                return 0
            if site == 'Toutiao':
                if args.browser not in {'auto', 'chrome'} and not args.page_data or args.profile:
                    raise ValueError('Toutiao uses the selected rendered reader; --page-data accepts connector content without browser access.')
                if not args.page_data:
                    cli.status('Page reader', reader_choice['browser']+' · '+reader_choice['source'], stream=sys.stderr)
                page = read_store.read_json(args.page_data) if args.page_data else toutiao.read_browser(url, backend=rendered_backend)
                item = toutiao.validate_page(url, page)
                if item['kind'] == 'article':
                    finish({**item, 'site': site, 'reference': source_reference(url)})
                else:
                    if subtitle_mode == 'only':
                        raise ValueError('The Toutiao player did not expose complete source subtitles.')
                    if model is None:
                        raise RuntimeError('Toutiao video speech requires an existing local model.')
                    if not all(cli.find_tool(t) for t in ('ffmpeg', 'ffprobe', 'whisper-cli')):
                        raise RuntimeError('Toutiao video speech requires the existing local ffmpeg, ffprobe and whisper-cli tools.')
                    temporary = folder / '.toutiao-download'
                    temporary.mkdir(mode=0o700)
                    try:
                        video = temporary / 'source.mp4'
                        if 'media_streams' in item:
                            evidence = toutiao.merge_tracks(item['media_streams'], temporary, item['duration'])
                            item['paired_media_evidence'] = evidence
                            if item['duration'] is None:
                                item.update(duration=evidence['duration'], duration_basis='complete_paired_download_ffprobe')
                        else:
                            evidence = toutiao.download_video(item['media_url'], video)
                            if isinstance(evidence, dict): item['transfer_evidence'] = evidence
                        actual_duration, audio_present = read_asr.probe(video)
                        if not audio_present or (item['duration'] is not None and
                                abs(actual_duration - item['duration']) > max(1, item['duration'] * .02)):
                            raise RuntimeError('The complete Toutiao media duration/audio does not match its bound page evidence.')
                        if item['duration'] is None:
                            item.update(duration=actual_duration, duration_basis='complete_download_ffprobe')
                        records = cli.inspect_files([video])
                        consume([video], records, DownloadResult(expected_count=1), url, site,
                                content_metadata(item))
                    finally:
                        shutil.rmtree(temporary)
                return 0
            download_args = argparse.Namespace(link=url, folder=folder, browser=args.browser, profile=args.profile,
                                               date_now=False, live_photos=False, redownload=False,
                                               read_audio=not (args.keep_media or extract_frames), read_metadata_consumer=metadata_consumer)
            code = cli.download(download_args, {}, media_consumer=consume)
            if code != 0:
                job['status'] = 'cancelled' if code == 130 else 'failed'
                read_store.save_job(folder, job)
                cli.status('Retry', f'linkget read retry "{folder}"', stream=sys.stderr)
                last_err, last_phase = getattr(download_args, '_last_failure', None) or (None, None)
                err_obj = (
                    last_err
                    if last_err is not None
                    else (KeyboardInterrupt('Content read cancelled.') if code == 130 else RuntimeError('Content download failed.'))
                )
                read_tasks.failure(err_obj)
                if getattr(args, '_batch_collect', None) is not None:
                    args._batch_collect.append({
                        'folder': str(folder),
                        'error': agent_output.classify_error(err_obj, phase=last_phase),
                    })
                elif runtime.json_mode() or getattr(args, 'json', False):
                    agent_output.emit(
                        agent_output.error_payload(
                            'read',
                            err_obj,
                            phase=last_phase,
                            exit_code=code,
                            extra={'folder': str(folder)},
                        )
                    )
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
  wait [TASK]               Wait for a background read task to complete
  show [TASK_OR_FOLDER]     Print completed read content to stdout
  logs TASK [--tail N]      Show a task's private log (default: last 50 lines)
  cancel TASK               Stop a task and preserve source/checkpoints

Examples:
  linkget read 'https://www.bilibili.com/video/BV1bK411W797?p=1'
  linkget read ~/Downloads/recording.m4a --language zh
  linkget read ~/Downloads/screenshot.png
  linkget read URL --stdout --stdout-format md
  linkget read wait TASK --json
  linkget read show TASK --format txt
  linkget read translate '/path/to/read-folder' --language en
  linkget read status TASK
  linkget read logs TASK
  linkget read cancel TASK

Processing returns a task ID immediately; closing the terminal does not stop it.
Submission success means accepted; inspect status for completion or failure.
Add --foreground (or --stdout) to read synchronously and return the content or exit code.
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
    parser.add_argument('--frames', action='store_true', help='Extract representative video keyframes with timestamps into frames/')
    parser.add_argument('--max-frames', type=int, default=read_frames.DEFAULT_MAX_FRAMES, help='Maximum keyframes to extract per video (1-60; default: 12)')
    parser.add_argument('--frame-ocr', action='store_true', help='Run local macOS Vision OCR on extracted keyframes (implies --frames)')
    parser.add_argument('--refresh', action='store_true', help='Bypass the 24-hour complete-content cache')
    parser.add_argument('--keep-media', action='store_true')
    parser.add_argument('--page-data', type=Path, help='Toutiao only: content-only page JSON')
    parser.add_argument('--rendered-backend', choices=['chrome', 'neo'],
                        help='Scoped page reader override; otherwise use the environment or saved reader-browser preference (auto follows saved/system browser)')
    parser.add_argument('--browser', choices=['auto', 'none', 'chrome', 'firefox', 'safari', 'edge', 'brave'], default='auto', help='Existing login source; scoped pages: chrome selects Chrome, none disables browser access; other explicit browsers are not substituted')
    parser.add_argument('--profile')
    parser.add_argument('--foreground', action='store_true', help='Wait for completion instead of running in the background')
    parser.add_argument('--stdout', action='store_true', help='Run synchronously and print extracted content to stdout')
    parser.add_argument('--stdout-format', choices=['md', 'json', 'txt'], default='md', help='Format for --stdout (default: md)')
    parser.add_argument('--json', action='store_true', help='Emit structured JSON output on stdout')
    parser.add_argument('--non-interactive', action='store_true', help='Never prompt or animate spinners')
    return parser


def retry_parser():
    parser = argparse.ArgumentParser(prog='linkget read retry', description='Resume retained checkpoints. Preserve the recorded browser/backend unless explicitly overridden.')
    parser.add_argument('folder', nargs='?', type=Path, help='Unfinished read folder; omit for the latest unfinished read')
    parser.add_argument('--foreground', action='store_true', help='Wait for completion and return its exit code')
    parser.add_argument('--rendered-backend', choices=['chrome', 'neo'], help='Explicitly override the retained scoped reader choice; does not enable a disabled browser')
    parser.add_argument('--json', action='store_true', help='Emit structured JSON output on stdout')
    parser.add_argument('--non-interactive', action='store_true', help='Never prompt or animate spinners')
    return parser


def main(argv):
    saved_modes = runtime.snapshot_modes()
    wants_json = '--json' in argv
    wants_stdout = '--stdout' in argv
    try:
        if '--non-interactive' in argv or wants_json:
            runtime.set_non_interactive(True)
        if wants_json:
            runtime.set_json_mode(True)
        if wants_stdout:
            runtime.set_quiet_status(True)
        if argv and argv[0] == 'config':
            return config_command(argv[1:])
        if argv and argv[0] == 'doctor':
            return doctor()
        if argv and argv[0] == 'recover':
            recover_parser = argparse.ArgumentParser(prog='linkget read recover', description='List unfinished reads and retained checkpoint folders; use read retry FOLDER to resume.')
            recover_parser.add_argument('--json', action='store_true', help='Emit structured JSON output')
            recover_parser.add_argument('--non-interactive', action='store_true', help='Never prompt')
            r_args = recover_parser.parse_args(argv[1:])
            unfinished = read_store.unfinished()
            if r_args.json:
                agent_output.emit({'schema_version': agent_output.SCHEMA_VERSION, 'ok': True, 'command': 'read recover', 'unfinished': unfinished})
            else:
                for job in unfinished:
                    print(job['status'] + '  ' + job['folder'])
            return 0
        if argv and argv[0] in {'summarize', 'translate'}:
            if __package__:
                from .read_ai import main as ai_main
            else:
                from read_ai import main as ai_main
            return ai_main(argv)
        if argv and argv[0] == 'retry':
            retry = retry_parser().parse_args(argv[1:])
            pending = read_store.unfinished()
            folder = retry.folder.expanduser().resolve() if retry.folder else Path(pending[0]['folder']) if pending else None
            if folder is None:
                raise ValueError('No unfinished read was found.')
            record = read_store.read_json(folder / '.read-job.json')
            args = argparse.Namespace(**record['options'], url=record.get('source') or '', folder=folder,
                                      resume=True, refresh=False, transcribe=True, json=retry.json)
            if retry.rendered_backend is not None:
                args.rendered_backend = retry.rendered_backend
                args._rendered_backend_override = True
            for field in ('whisper_model', 'vad_model', 'page_data'):
                value = getattr(args, field, None)
                setattr(args, field, Path(value) if value else None)
            if record['status'] == 'completed':
                if retry.json:
                    content_file = folder / 'content.json'
                    data = read_store.read_json(content_file) if content_file.is_file() else None
                    agent_output.emit({
                        'schema_version': agent_output.SCHEMA_VERSION,
                        'ok': True,
                        'command': 'read retry',
                        'already_completed': True,
                        'folder': str(folder),
                        'content_path': str(content_file) if content_file.is_file() else None,
                        'result': data,
                    })
                else:
                    print('This read is already complete.')
                return 0
            return read_videos(args)
        batch = bool(argv and argv[0] == 'batch')
        parser = read_parser(batch)
        args = parser.parse_args(argv[1:] if batch else argv)
        if batch and args.stdout:
            parser.error('--stdout applies to a single read, not batch.')
        args.resume = False
        if args.profile and args.browser in {'auto', 'none'}:
            parser.error('--profile requires an explicit browser')
        if args.language and not re.fullmatch(r'auto|[a-z]{2,3}', args.language):
            parser.error('Use auto or a Whisper language code')
        if not read_frames.MIN_FRAMES <= args.max_frames <= read_frames.MAX_FRAMES:
            parser.error(f'--max-frames must be between {read_frames.MIN_FRAMES} and {read_frames.MAX_FRAMES}.')
        if not batch:
            return read_videos(args)
        links = args.url
        if not links:
            if runtime.is_interactive():
                print('Paste one link or local file per line; blank line starts reads.')
                while line := input('> ').strip():
                    links.append(line)
            elif not sys.stdin.isatty():
                links = [line.strip() for line in sys.stdin if line.strip()]
        if not links:
            if wants_json:
                raise ValueError('The read batch is empty')
            parser.error('The read batch is empty')
        failed = []
        batch_results = []
        for index, link in enumerate(links, 1):
            item = argparse.Namespace(**vars(args))
            item.url = link
            item.folder = args.folder / f'item-{index:03d}' if args.folder else None
            collected = []
            if args.json:
                item._batch_collect = collected
            if not args.json:
                print(f'Reading {index} / {len(links)}')
            try:
                code = read_videos(item)
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                read_tasks.failure(error)
                if not args.json:
                    print(str(error), file=sys.stderr)
                collected.append({'error': agent_output.classify_error(error)})
                code = 1
            if code == 130:
                if args.json:
                    agent_output.emit({'schema_version': agent_output.SCHEMA_VERSION, 'ok': False, 'command': 'read batch', 'cancelled': True, 'items': batch_results})
                return 130
            if code:
                failed.append(index)
            entry = {'index': index, 'ok': code == 0}
            if collected:
                entry.update(collected[0])
            batch_results.append(entry)
        if args.json:
            agent_output.emit({
                'schema_version': agent_output.SCHEMA_VERSION,
                'ok': not failed,
                'command': 'read batch',
                'completed_count': len(links) - len(failed),
                'failed_items': failed,
                'items': batch_results,
            })
        else:
            print(f'{len(links) - len(failed)} completed; failed items: {failed}')
        return 1 if failed else 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        read_tasks.failure(error)
        if wants_json:
            agent_output.emit(agent_output.error_payload('read', error))
        else:
            print('Content read stopped: ' + str(error), file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        if wants_json:
            agent_output.emit(agent_output.error_payload('read', KeyboardInterrupt('Content read stopped'), exit_code=130))
        return 130
    finally:
        runtime.restore_modes(saved_modes)
