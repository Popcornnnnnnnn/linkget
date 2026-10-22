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
from urllib.parse import urlsplit,parse_qs
from urllib.request import Request, urlopen

HOSTS = {'toutiao.com', 'www.toutiao.com', 'm.toutiao.com'}
INTERMEDIATES = ('source.mp4', 'source-video.mp4', 'source-audio.m4a', 'audio.wav', 'transcript.txt', 'transcript.json')
MAX_MEDIA_BYTES = 512 * 1024 * 1024
MIN_TRANSFER_SECONDS = 60
MAX_TRANSFER_SECONDS = 600
TRANSFER_BYTES_PER_SECOND = 1024 * 1024


class MediaTransferLimit(RuntimeError):
    def __init__(self, reason, downloaded, expected, elapsed, budget):
        self.diagnostic = {'reason':reason,'downloaded_bytes':downloaded,
            'declared_bytes':expected,'elapsed_seconds':round(elapsed,3),
            'size_limit_bytes':MAX_MEDIA_BYTES,'time_budget_seconds':budget}
        super().__init__('Toutiao media transfer limit: '+json.dumps(self.diagnostic,sort_keys=True))


def transfer_budget(expected):
    if expected is None:return MAX_TRANSFER_SECONDS
    return min(MAX_TRANSFER_SECONDS,max(MIN_TRANSFER_SECONDS,
        math.ceil(expected / TRANSFER_BYTES_PER_SECOND) + 30))


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
    # Chunk checkpoints are redundant once the consolidated, durable content
    # record exists. Preserve unfinished work and unknown files for inspection.
    content = folder / 'content.json'
    chunks = folder / 'chunks'
    if chunks.exists() and content.is_file() and not content.is_symlink() and json.loads(content.read_text()).get('content_complete') is True:
        if chunks.is_symlink() or not chunks.is_dir():raise ValueError('Refusing substituted chunk checkpoints.')
        checkpoints = list(chunks.iterdir())
        if any(p.is_symlink() or not p.is_file() or not re.fullmatch(r'[0-9a-f]{64}\.json',p.name) for p in checkpoints):
            raise ValueError('Refusing unknown checkpoint files.')
        for path in checkpoints:
            path.unlink();removed.append('chunks/'+path.name)
        chunks.rmdir()
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


def media_pair(streams):
    if not isinstance(streams,dict) or set(streams)!= {'video','audio'}:
        raise ValueError('One exact video/audio resource pair is required.')
    result={role:media_url(url) for role,url in streams.items()}
    parts={role:urlsplit(url) for role,url in result.items()}
    for role,suffix in [('video','/media-video-avc1/'),('audio','/media-audio-und-mp4a/')]:
        if '/video/tos/' not in parts[role].path or not parts[role].path.endswith(suffix):
            raise ValueError('Unsupported separate Toutiao track identity.')
    queries={role:parse_qs(p.query) for role,p in parts.items()}
    if parts['video'].netloc!=parts['audio'].netloc or any(
            len(queries['video'].get(key,[]))!=1 or not queries['video'][key][0] or
            queries['video'].get(key)!=queries['audio'].get(key) for key in ('l','dy_q')):
        raise ValueError('Audio/video must belong to the same observed request batch.')
    return result


def validate_page(url, page):
    expected = canonical(url)
    if not isinstance(page, dict) or canonical(page.get('url', '')) != expected:
        raise ValueError('Rendered page must match the requested Toutiao post.')
    if 'final_url' in page and canonical(page['final_url']) != expected:
        raise ValueError('The rendered final URL must match the requested post.')
    kind = expected.split('/')[3]
    title = page.get('title')
    if not isinstance(title, str) or not title.strip() or len(title) > 4000 or page.get('kind') != kind:
        raise ValueError('The matching page needs a title and correct content kind.')
    # Select only known content fields; never persist the browser's full state.
    result = {'url': expected, 'title': title.strip(), 'kind': kind,
              'acquisition': 'chrome_rendered_page'}
    if __package__:
        from .rendered_proof import browser_metadata
    else:
        from rendered_proof import browser_metadata
    metadata = browser_metadata(page)
    if metadata:
        result.update(**metadata, acquisition='browseros_neo_rendered_page')
    if kind == 'article':
        body = page.get('text')
        if not isinstance(body, str) or len(body.strip()) < 100 or len(body) > 200000 or page.get('content_complete') is not True:
            raise ValueError('Complete article text is unavailable; a title is not sufficient.')
        result.update(text=body.strip(), content_complete=True, content_basis='rendered_article')
        proof = page.get('article_dom_proof')
        if proof is not None:
            if (not isinstance(proof, dict) or proof.get('basis') != 'stable_rendered_article_range' or
                    proof.get('document_state') != 'interactive' or type(proof.get('stable_ms')) is not int or
                    proof['stable_ms'] != 1500 or type(proof.get('paragraph_count')) is not int or
                    not 2 <= proof['paragraph_count'] <= 10000 or type(proof.get('paragraph_chars')) is not int or
                    not 100 <= proof['paragraph_chars'] <= 200000 or
                    any(type(proof.get(key)) is not int or proof[key] != 0 for key in
                        ('pending_images', 'visible_loaders', 'visible_expand_controls')) or
                    proof.get('clipped_article') is not False or not isinstance(proof.get('end_excerpt'), str) or
                    not 1 <= len(proof['end_excerpt']) <= 512 or not body.strip().endswith(proof['end_excerpt'])):
                raise ValueError('A complete, stable article range and matching tail are required.')
            result['article_dom_proof'] = {key: proof[key] for key in
                ('basis', 'document_state', 'stable_ms', 'paragraph_count', 'paragraph_chars',
                 'pending_images', 'visible_loaders', 'visible_expand_controls', 'clipped_article', 'end_excerpt')}
    else:
        duration = page.get('duration')
        paired=page.get('media_binding')=='paired_page_vod_resources'
        pending = duration is None and page.get('duration_pending') is True and (paired or page.get('media_binding') == 'player_current_src') and not page.get('captions')
        if not pending and (type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0):
            raise ValueError('A finite positive video duration is required.')
        media={'media_streams':media_pair(page.get('media_streams'))} if paired else {'media_url':media_url(page.get('media_url', ''))}
        result.update(**media,duration=duration,
                      description=title.strip(), captions=[], content_complete=False, content_basis='description_only')
        # Keep the validated acquisition binding through read_browser -> read.
        # Pending and split sources are validated again before full downloads.
        if page.get('media_binding') in ('player_current_src','single_page_vod_resource','paired_page_vod_resources'):
            result['media_binding']=page['media_binding']
        if pending:
            result['duration_basis']='pending_download_probe'
            result['duration_pending']=True
        selection = page.get('media_selection')
        if selection is not None:
            if (not paired or not isinstance(selection, dict) or
                    selection.get('strategy') != 'latest_observed_video_variant' or
                    type(selection.get('video_variant_count')) is not int or
                    not 1 <= selection['video_variant_count'] <= 4 or
                    type(selection.get('audio_resource_count')) is not int or selection['audio_resource_count'] != 1 or
                    type(selection.get('selected_video_resource_index')) is not int or
                    not 0 <= selection['selected_video_resource_index'] < 256 or
                    selection.get('same_request_batch') is not True):
                raise ValueError('Adaptive media selection requires one exact observed request batch.')
            result['media_selection'] = {key: selection[key] for key in
                ('strategy', 'video_variant_count', 'audio_resource_count',
                 'selected_video_resource_index', 'same_request_batch')}
        for cue in page.get('captions', []):
            if (not isinstance(cue, dict) or not isinstance(cue.get('text'), str) or
                    len(cue['text']) > 4000 or type(cue.get('start')) not in (int, float) or
                    type(cue.get('end')) not in (int, float) or not 0 <= cue['start'] < cue['end'] <= duration + 1):
                raise ValueError('Invalid player caption data.')
            result['captions'].append({k: cue[k] for k in ('start', 'end', 'text')})
    return result


def read_browser(url, *, backend=None):
    # Douyin keeps its existing controller. This helper owns one temporary tab,
    # bounds page reads, and cleans it without exporting credentials.
    if __package__:
        from .rendered_page import read_page
    else:
        from rendered_page import read_page
    return read_page(canonical(url), backend=backend)


def download_video(url, path):
    url = media_url(url)
    request = Request(url, headers={'Referer': 'https://www.toutiao.com/', 'User-Agent': 'Mozilla/5.0'})
    count, start = 0, time.monotonic()
    with urlopen(request, timeout=8) as response:
        media_url(response.geturl())
        if response.status != 200 or response.headers.get('Content-Range'):
            raise RuntimeError('A complete HTTP media response is required; byte ranges are not full content.')
        expected = response.headers.get('Content-Length')
        expected = int(expected) if expected is not None else None
        if expected is not None and expected < 0:raise RuntimeError('Invalid declared media size.')
        budget=transfer_budget(expected)
        if expected is not None and expected > MAX_MEDIA_BYTES:
            raise MediaTransferLimit('declared_size',count,expected,time.monotonic()-start,budget)
        with path.open('xb') as output:
            path.chmod(0o600)
            while True:
                elapsed=time.monotonic()-start
                if elapsed > budget:raise MediaTransferLimit('time',count,expected,elapsed,budget)
                chunk=response.read(256 * 1024)
                elapsed=time.monotonic()-start
                if elapsed > budget:raise MediaTransferLimit('time',count,expected,elapsed,budget)
                if not chunk:break
                count += len(chunk)
                if count > MAX_MEDIA_BYTES:raise MediaTransferLimit('streamed_size',count,expected,elapsed,budget)
                output.write(chunk)
    if expected is not None and count != expected:
        raise RuntimeError('The media response ended before its complete declared size.')
    if count == 0:
        raise RuntimeError('The player returned an empty video.')
    return {'size_bytes':count,'declared_bytes':expected,'elapsed_seconds':round(time.monotonic()-start,3),
        'time_budget_seconds':budget,'complete_http_response':True}



def transcribe(video, folder, model, duration=None, language='zh', **options):
    if __package__:
        from .read_asr import transcribe as recognize
    else:
        from read_asr import transcribe as recognize
    return recognize(video, folder, model, duration, language=language, **options)


def merge_tracks(streams,folder,expected_duration):
    streams=media_pair(streams)
    paths={'video':folder/'source-video.mp4','audio':folder/'source-audio.m4a'}
    durations={};starts={};transfers={}
    for role,path in paths.items():
        transfers[role]=download_video(streams[role],path)
        data=json.loads(subprocess.run([shutil.which('ffprobe'),'-v','error','-show_streams','-show_format','-of','json',str(path)],
            check=True,capture_output=True,text=True,timeout=20).stdout)
        if [s.get('codec_type') for s in data.get('streams',[])]!=[role]:
            raise RuntimeError('Separate media resources do not match their exact track roles.')
        durations[role]=float(data['format']['duration']);starts[role]=float(data['format'].get('start_time','0'))
        if not math.isfinite(durations[role]) or durations[role]<=0 or not math.isfinite(starts[role]):
            raise RuntimeError('Separate tracks need finite timing evidence.')
    duration=max(durations.values());tolerance=max(.5,duration*.002)
    if abs(durations['video']-durations['audio'])>tolerance or abs(starts['video']-starts['audio'])>.5:
        raise RuntimeError('Audio/video timing does not match; never transcribe an incomplete pair.')
    if expected_duration is not None and abs(duration-expected_duration)>max(1,expected_duration*.02):
        raise RuntimeError('Separate tracks do not cover the player duration.')
    if sum(p.stat().st_size for p in paths.values())>MAX_MEDIA_BYTES:raise RuntimeError('Combined media exceeds transfer bound.')
    subprocess.run([shutil.which('ffmpeg'),'-nostdin','-hide_banner','-loglevel','error',
        '-i',str(paths['video']),'-i',str(paths['audio']),'-map','0:v:0','-map','1:a:0','-c','copy',
        '-movflags','+faststart',str(folder/'source.mp4')],check=True,capture_output=True,timeout=60)
    return {'video_duration':durations['video'],'audio_duration':durations['audio'],
            'same_request_batch':True,'timing_matched':True,'duration':duration,
            **({'transfers':transfers} if all(isinstance(t,dict) for t in transfers.values()) else {})}


def prepare_checkpoint_resume(folder):
    """Validate the entire generated checkpoint tree before cleaning failed ASR work."""
    if folder.is_symlink() or not folder.is_dir():raise ValueError('A real checkpoint folder is required.')
    working=[]
    for child in folder.iterdir():
        if child.is_symlink() or not child.is_dir():raise ValueError('Unknown incomplete content evidence; refusing checkpoint resume.')
        if child.name=='chunks':
            if any(p.is_symlink() or not p.is_file() or not re.fullmatch(r'[0-9a-f]{64}\.json',p.name) for p in child.iterdir()):
                raise ValueError('Unknown checkpoint evidence; refusing resume.')
        elif re.fullmatch(r'chunk-\d{4}',child.name):
            files=list(child.iterdir())
            if any(p.is_symlink() or not p.is_file() or p.name not in ('audio.wav','transcript.json','transcript.txt') for p in files):
                raise ValueError('Unknown ASR work evidence; refusing resume.')
            working.append((child,files))
        else:raise ValueError('Unknown incomplete content evidence; refusing checkpoint resume.')
    for child,files in working:
        for p in files:p.unlink()
        child.rmdir()


def read(url, folder, page=None, model=None, keep_media=False, language='zh',resume_checkpoints=False):
    url = canonical(url)
    if folder.exists() and not resume_checkpoints:
        raise ValueError('The content folder already exists; choose a new folder to preserve prior evidence.')
    if page is not None:
        item = validate_page(url, page)
    else:
        item = read_browser(url)
    if item['kind'] == 'video' and model is None:
        raise ValueError('Videos need --transcribe --whisper-model; title-only content is not summary-ready.')
    if item['kind'] == 'video' and (not model.is_file() or not all(shutil.which(t) for t in ('ffmpeg', 'ffprobe', 'whisper-cli'))):
        raise RuntimeError('Check the existing whisper model and transcription tools before fetching media.')
    if folder.exists():prepare_checkpoint_resume(folder)
    else:folder.mkdir(parents=True, mode=0o700)
    item['send_enabled'] = False
    output = folder / 'content.json'
    try:
        if item['kind'] == 'video':
            if 'media_streams' in item:
                evidence=merge_tracks(item.pop('media_streams'),folder,item['duration'])
                item['paired_media_evidence']=evidence
                if item['duration'] is None:item.update(duration=evidence['duration'],duration_basis='complete_paired_download_ffprobe')
            else:
                transfer=download_video(item.pop('media_url'), folder / 'source.mp4')
                if isinstance(transfer,dict):item['transfer_evidence']=transfer
            if item['duration'] is None:
                if __package__:from .read_asr import probe
                else:from read_asr import probe
                duration, audio_present = probe(folder / 'source.mp4')
                if not audio_present:raise RuntimeError('Downloaded video has no audio track.')
                item.update(duration=duration,duration_basis='complete_download_ffprobe')
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
