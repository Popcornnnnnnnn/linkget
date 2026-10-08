"""Content-only subtitle parsing and human-readable source exports."""
import html
from html.parser import HTMLParser
import json
import math
from pathlib import Path
import re
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, value):
        self.parts.append(value)


def plain(value):
    if not isinstance(value, str):
        return ''
    parser = PlainText()
    parser.feed(value)
    return html.unescape(''.join(parser.parts)).strip()


def source_metadata(value):
    if not isinstance(value, dict):
        return {}
    post = value.get('status') if isinstance(value.get('status'), dict) else value
    result = {}
    for field, alternatives in {'title': ('title',), 'description': ('description', 'desc', 'content', 'text')}.items():
        for name in alternatives:
            text = plain(post.get(name))
            if text:
                result[field] = text
                break
    return result


def seconds(value):
    parts = value.replace(',', '.').split(':')
    return sum(float(number) * 60 ** power for power, number in enumerate(reversed(parts)))


def parse_subtitles(text, duration):
    cues = []
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if isinstance(data, dict) and isinstance(data.get('body'), list):
        cues = [{'start': c['from'], 'end': c['to'], 'text': plain(c['content'])} for c in data['body']]
    elif isinstance(data, dict) and isinstance(data.get('events'), list):
        for event in data['events']:
            words = ''.join(s.get('utf8', '') for s in event.get('segs', []))
            if words.strip():
                cues.append({'start': event['tStartMs'] / 1000,
                             'end': (event['tStartMs'] + event.get('dDurationMs', 0)) / 1000,
                             'text': plain(words)})
    else:
        for match in re.finditer(r'(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?[.,]\d{3})[^\n]*\n(.*?)(?=\n\s*\n|\Z)', text, re.S):
            cues.append({'start': seconds(match[1]), 'end': seconds(match[2]), 'text': plain(match[3])})
    cleaned = []
    for cue in cues:
        start, end = float(cue['start']), float(cue['end'])
        if not cue['text']:
            continue
        if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end <= duration + 1:
            raise ValueError('Subtitle timestamps do not match the selected media.')
        if cleaned and start < cleaned[-1]['start']:
            raise ValueError('Subtitle timestamps are out of order.')
        if not cleaned or (start, end, cue['text']) != (cleaned[-1]['start'], cleaned[-1]['end'], cleaned[-1]['text']):
            cleaned.append({'start': start, 'end': end, 'text': cue['text']})
    if not cleaned or cleaned[0]['start'] > min(15, max(1, duration * .08)) or cleaned[-1]['end'] < duration - min(15, max(1, duration * .08)):
        raise ValueError('Subtitle coverage is incomplete or unavailable.')
    return cleaned


def subtitles(metadata, language):
    duration = float(metadata.get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0:
        return None
    desired = metadata.get('language') if language == 'auto' else language
    if desired is None:
        available = {lang.removeprefix('ai-').split('-')[0].split('_')[0]
                     for field in ('subtitles', 'automatic_captions') for lang in (metadata.get(field) or {})}
        if len(available) == 1:
            desired = available.pop()
    for field, basis in [('subtitles', 'publisher_subtitles'), ('automatic_captions', 'platform_auto_subtitles')]:
        tracks = metadata.get(field) or {}
        for lang, variants in tracks.items():
            normalized = lang.removeprefix('ai-').split('-')[0].split('_')[0]
            # Do not silently select an unrelated language when auto cannot establish the source language.
            if desired is None or normalized != desired.split('-')[0] or lang.endswith('-orig'):
                continue
            ranked = sorted(variants, key=lambda v: {'json3': 0, 'json': 1, 'vtt': 2, 'srt': 3}.get(v.get('ext'), 9))
            for variant in ranked:
                target = variant.get('url', '')
                p = urlsplit(target)
                if p.scheme != 'https' or p.username or p.password:
                    continue
                try:
                    with urlopen(Request(target, headers={'User-Agent': 'Mozilla/5.0'}), timeout=12) as response:
                        if urlsplit(response.geturl()).scheme != 'https':
                            continue
                        payload = response.read(8_000_001)
                    if len(payload) > 8_000_000:
                        continue
                    segments = parse_subtitles(payload.decode('utf-8-sig'), duration)
                    return {'text': '\n'.join(s['text'] for s in segments), 'segments': segments,
                            'content_complete': True, 'content_basis': basis, 'language': lang,
                            'recognition_verified': False,
                            'media_evidence': {'duration': duration, 'full_audio_processed': False},
                            'limitations': ['Subtitle coverage was checked; caption accuracy and visual content are not verified.']}
                except (OSError, ValueError, KeyError, TypeError):
                    continue
    return None


def timestamp(value, srt=False):
    millis = max(0, round(value * 1000))
    seconds_, ms = divmod(millis, 1000)
    minutes, second = divmod(seconds_, 60)
    hour, minute = divmod(minutes, 60)
    return f'{hour:02d}:{minute:02d}:{second:02d}{"," if srt else "."}{ms:03d}'


def markdown(result):
    title = result.get('title') or result.get('site') or 'Content'
    reference = result.get('reference') or result.get('url') or ''
    header = f'# {title}\n\nSource: {reference}\n\n'
    if result.get('description'):
        header += '<a id="description"></a>\n## Publisher description\n\n' + result['description'] + '\n\n'
    videos = result.get('videos') or ([result] if result.get('segments') else [])
    body = ''
    if videos:
        for index, video in enumerate(videos, 1):
            body += f'## Video {index}\n\n'
            body += '\n\n'.join(f'<a id="v{index}-s{n}"></a>\n[{timestamp(s["start"]).split(".")[0]}] {s["text"]}' for n, s in enumerate(video['segments'], 1)) + '\n\n'
        for index, image in enumerate(result.get('images', []), 1):
            body += f'<a id="image-{index}"></a>\n## Image {index}\n\n' + image.get('text', '') + '\n\n'
    else:
        body = result.get('text', '') + '\n'
    return header + body


def export(folder, result, formats, basename='content'):
    videos = result.get('videos') or ([result] if result.get('segments') else [])
    for format_ in formats:
        if format_ == 'md':
            write_text(folder / (basename + '.md'), markdown(result))
        elif format_ == 'txt':
            write_text(folder / (basename + '.txt'), result.get('text', '') + '\n')
        elif format_ in {'srt', 'vtt'}:
            for index, video in enumerate(videos, 1):
                name = 'content' if len(videos) == 1 else f'video-{index:03d}'
                lines = ['WEBVTT\n'] if format_ == 'vtt' else []
                for number, cue in enumerate(video['segments'], 1):
                    lines.append(f'{number}\n{timestamp(cue["start"], format_ == "srt")} --> {timestamp(cue["end"], format_ == "srt")}\n{cue["text"]}\n')
                write_text(folder / (name + '.' + format_),'\n'.join(lines))


def write_text(path, text):
    if path.is_symlink():
        raise ValueError('Refusing a substituted export file.')
    if path.exists():
        if path.read_text() == text:
            return
        raise ValueError('An export file already contains different text; preserve it and use a new folder.')
    with path.open('x') as stream:
        path.chmod(0o600)
        stream.write(text)
