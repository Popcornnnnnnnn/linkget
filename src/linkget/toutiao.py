"""Toutiao rendered-page intake and optional local video transcription.

Page evidence can come from the configured browser connector (--page-data),
or LinkGet's existing daily-Chrome page controller. No credential export.
"""
import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

HOSTS = {'toutiao.com', 'www.toutiao.com', 'm.toutiao.com'}
INTERMEDIATES = ('source.mp4', 'audio.wav', 'transcript.txt', 'transcript.json')


def cleanup_media(folder):
    """Remove only this reader's intermediates, without following symlinks."""
    if folder.is_symlink() or not folder.is_dir():
        raise ValueError('Cleanup requires a real content folder.')
    paths = [folder / name for name in INTERMEDIATES]
    if any(p.is_symlink() or (p.exists() and not p.is_file()) for p in paths):
        raise ValueError('Refusing cleanup of substituted intermediate paths.')
    removed = []
    for path in paths:
        if path.exists():
            path.unlink()
            removed.append(path.name)
    return removed


def canonical(url):
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname not in HOSTS or p.username or p.password or p.port not in (None, 443):
        raise ValueError('Expected an HTTPS Toutiao article or video link.')
    m = re.fullmatch(r'/(article|video)/(\d+)/?', p.path)
    if not m:
        raise ValueError('Use a single Toutiao /article/ID/ or /video/ID/ link.')
    return f'https://www.toutiao.com/{m[1]}/{m[2]}/'


def media_url(url):
    p = urlsplit(url)
    host = p.hostname or ''
    if (p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or
            not host.endswith('.toutiaovod.com')):
        raise ValueError('The rendered player did not expose a supported Toutiao video URL.')
    return url


def validate_page(url, page):
    expected = canonical(url)
    if not isinstance(page, dict) or canonical(page.get('url', '')) != expected:
        raise ValueError('Rendered page must match the requested Toutiao post.')
    kind = expected.split('/')[3]
    title = page.get('title')
    if not isinstance(title, str) or not title.strip() or len(title) > 4000 or page.get('kind') != kind:
        raise ValueError('The matching page needs a title and correct content kind.')
    # Select only known content fields; never persist the browser's full state.
    result = {'url': expected, 'title': title.strip(), 'kind': kind,
              'acquisition': 'chrome_rendered_page'}
    if kind == 'article':
        body = page.get('text')
        if not isinstance(body, str) or len(body.strip()) < 100 or len(body) > 200000 or page.get('content_complete') is not True:
            raise ValueError('Complete article text is unavailable; a title is not sufficient.')
        result.update(text=body.strip(), content_complete=True, content_basis='rendered_article')
    else:
        duration = page.get('duration')
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            raise ValueError('A finite positive video duration is required.')
        result.update(media_url=media_url(page.get('media_url', '')), duration=duration,
                      description=title.strip(), captions=[], content_complete=False, content_basis='description_only')
        for cue in page.get('captions', []):
            if (not isinstance(cue, dict) or not isinstance(cue.get('text'), str) or
                    len(cue['text']) > 4000 or type(cue.get('start')) not in (int, float) or
                    type(cue.get('end')) not in (int, float) or not 0 <= cue['start'] < cue['end'] <= duration + 1):
                raise ValueError('Invalid player caption data.')
            result['captions'].append({k: cue[k] for k in ('start', 'end', 'text')})
    return result


def read_browser(url):
    url = canonical(url)
    script = Path(__file__).with_name('douyin_browser.applescript')
    javascript = Path(__file__).with_name('toutiao_browser.js').read_text()
    # The existing controller reuses a matching tab, restores a temporary tab,
    # and never enables Chrome's scripting permission itself.
    expression = '(' + javascript + ')()'
    expression = 'JSON.stringify(' + expression + ')'
    try:
        process = subprocess.run(['/usr/bin/osascript', str(script), url, expression],
                                 capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        raise RuntimeError('Toutiao browser read timed out; check this page in daily Chrome.') from None
    if process.returncode:
        raise RuntimeError('Chrome could not read this Toutiao page. Use an authorized browser connector '
                           'with --page-data, or check existing Chrome scripting permission; no permissions were changed.')
    try:
        return validate_page(url, json.loads(process.stdout))
    except (ValueError, TypeError):
        raise RuntimeError('Chrome did not expose complete content for the requested Toutiao post.') from None


def download_video(url, path):
    url = media_url(url)
    request = Request(url, headers={'Referer': 'https://www.toutiao.com/', 'User-Agent': 'Mozilla/5.0'})
    limit, count, start = 200 * 1024 * 1024, 0, time.monotonic()
    with urlopen(request, timeout=8) as response:
        media_url(response.geturl())
        with path.open('xb') as output:
            path.chmod(0o600)
            while chunk := response.read(256 * 1024):
                count += len(chunk)
                if count > limit or time.monotonic() - start > 60:
                    raise RuntimeError('Toutiao sample exceeds the transfer size or time bound.')
                output.write(chunk)
    if count == 0:
        raise RuntimeError('The player returned an empty video.')



def transcribe(video, folder, model, duration=None, language='zh', **options):
    if __package__:
        from .read_asr import transcribe as recognize
    else:
        from read_asr import transcribe as recognize
    return recognize(video, folder, model, duration, language=language, **options)


def read(url, folder, page=None, model=None, keep_media=False, language='zh'):
    url = canonical(url)
    if folder.exists():
        raise ValueError('The content folder already exists; choose a new folder to preserve prior evidence.')
    if page is not None:
        item = validate_page(url, page)
    else:
        item = read_browser(url)
    if item['kind'] == 'video' and model is None:
        raise ValueError('Videos need --transcribe --whisper-model; title-only content is not summary-ready.')
    if item['kind'] == 'video' and (not model.is_file() or not all(shutil.which(t) for t in ('ffmpeg', 'ffprobe', 'whisper-cli'))):
        raise RuntimeError('Check the existing whisper model and transcription tools before fetching media.')
    folder.mkdir(parents=True, mode=0o700)
    item['send_enabled'] = False
    output = folder / 'content.json'
    try:
        if item['kind'] == 'video':
            download_video(item.pop('media_url'), folder / 'source.mp4')
            options = {} if language == 'zh' else {'language': language}
            item.update(transcribe(folder / 'source.mp4', folder, model, item['duration'], **options))
        # Complete the durable content record before removing its source media.
        with output.open('x') as stream:
            output.chmod(0o600)
            json.dump(item, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return output
    finally:
        if not keep_media:
            cleanup_media(folder)


def main(argv):
    parser = argparse.ArgumentParser(prog='linkget read', description='Read one Toutiao post for summarization, without Photos import or sending.')
    parser.add_argument('url')
    parser.add_argument('--folder', required=True, type=Path, help='New content folder; never overwrite existing evidence')
    parser.add_argument('--page-data', type=Path, help='Content-only JSON from the authorized Chrome connector')
    parser.add_argument('--transcribe', action='store_true', help='Transcribe the complete video locally')
    parser.add_argument('--whisper-model', type=Path, help='Existing whisper.cpp model; nothing is downloaded automatically')
    parser.add_argument('--keep-media', action='store_true', help='Temporarily retain media for manual recognition review; clean after review')
    parser.add_argument('--language', default='zh', help='Whisper language code or auto; default: zh')
    args = parser.parse_args(argv)
    if args.transcribe != (args.whisper_model is not None):
        parser.error('--transcribe and --whisper-model must be supplied together')
    try:
        page = json.loads(args.page_data.read_text()) if args.page_data else None
        output = read(args.url, args.folder.expanduser().resolve(), page,
                      args.whisper_model.expanduser().resolve() if args.whisper_model else None,
                      keep_media=args.keep_media, language=args.language)
        print(json.dumps({'status': 'content_ready', 'content': str(output), 'send_enabled': False}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Toutiao read stopped: ' + str(error), file=sys.stderr)
        return 1
