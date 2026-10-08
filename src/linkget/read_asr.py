"""Local full-audio recognition with content-addressed chunk checkpoints."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess

if __package__:
    from . import read_store
else:
    import read_store


def probe(path):
    tool = shutil.which('ffprobe')
    if not tool:
        raise RuntimeError('Local reading requires ffprobe.')
    result = subprocess.run([tool, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                            check=True, capture_output=True, text=True, timeout=20)
    try:
        data = json.loads(result.stdout)
        duration = float(data['format']['duration'])
        audio = any(s.get('codec_type') == 'audio' for s in data['streams'])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise RuntimeError('Media has no readable duration/audio evidence.') from None
    return duration, audio


def quality(segments):
    markers = {'music', 'lyrics', 'instrumental', 'applause', 'silence', 'inaudible',
               '音乐', '音樂', '音楽', '歌詞', '歌词', '掌声', '掌聲', '无声', '無聲'}
    if not segments or all(s['text'].strip(' ()[]（）【】♪♫.。').casefold() in markers for s in segments):
        raise RuntimeError('No readable speech was recognized; music/silence labels are not content.')
    suspicious = []
    for index, segment in enumerate(segments):
        if index >= 2 and segment['text'] == segments[index - 1]['text'] == segments[index - 2]['text']:
            suspicious.append({'start': segment['start'], 'end': segment['end'], 'reason': 'repeated_phrase'})
    return suspicious


def transcribe(video, folder, model, duration=None, language='zh', chunk_seconds=600, vad_model=None):
    tools = {name: shutil.which(name) for name in ('ffmpeg', 'ffprobe', 'whisper-cli')}
    if not all(tools.values()) or not model.is_file():
        raise RuntimeError('Local transcription requires ffmpeg, ffprobe, whisper-cli and an existing model.')
    if vad_model is not None and not vad_model.is_file():
        raise ValueError('The selected VAD model is missing.')
    actual, audio_present = probe(video)
    if not audio_present or (duration is not None and abs(actual - duration) > max(1, duration * .02)):
        raise RuntimeError('Media duration/audio does not match the source evidence.')
    if not 30 <= chunk_seconds <= 1800:
        raise ValueError('Chunk length must be between 30 and 1800 seconds.')
    identity = [read_store.digest(video), read_store.digest(model), language, chunk_seconds,
                read_store.digest(vad_model) if vad_model else None, read_store.SCHEMA]
    chunks, recognized_language = [], None
    count = math.ceil(actual / chunk_seconds)
    for index in range(count):
        start, length = index * chunk_seconds, min(chunk_seconds, actual - index * chunk_seconds)
        key = hashlib.sha256(json.dumps([identity, index]).encode()).hexdigest()
        record = folder / 'chunks' / (key + '.json')
        if record.exists():
            saved = read_store.read_json(record)
            valid = isinstance(saved.get('segments'), list) and all(
                isinstance(s, dict) and isinstance(s.get('text'), str) and
                isinstance(s.get('start'), (float, int)) and isinstance(s.get('end'), (float, int)) and
                start <= s['start'] < s['end'] <= start + length for s in saved['segments'])
            if saved.get('key') == key and saved.get('length') == length and valid:
                chunks.extend(saved['segments'])
                recognized_language = saved.get('language') or recognized_language
                continue
        working = folder if count == 1 else folder / f'chunk-{index:04d}'
        working.mkdir(parents=True, exist_ok=True, mode=0o700)
        wav, base = working / 'audio.wav', working / 'transcript'
        for path in (wav, base.with_suffix('.json'), base.with_suffix('.txt')):
            if path.is_symlink():
                raise ValueError('Refusing a substituted recognition file.')
            path.unlink(missing_ok=True)
        subprocess.run([tools['ffmpeg'], '-nostdin', '-hide_banner', '-loglevel', 'error', '-ss', str(start),
                        '-i', str(video), '-t', str(length), '-vn', '-ac', '1', '-ar', '16000', str(wav)],
                       check=True, capture_output=True, timeout=max(60, length))
        command = [tools['whisper-cli'], '-m', str(model), '-f', str(wav), '-l', language,
                   '-otxt', '-oj', '-of', str(base)]
        if vad_model:
            command.extend(['--vad', '--vad-model', str(vad_model)])
        subprocess.run(command, check=True, capture_output=True, timeout=max(180, length * 3))
        try:
            data = json.loads(base.with_suffix('.json').read_text())
            segments = [{'text': s['text'].strip(), 'start': s['offsets']['from'] / 1000,
                         'end': s['offsets']['to'] / 1000} for s in data.get('transcription', []) if s.get('text', '').strip()]
            if any(not math.isfinite(s['start']) or not math.isfinite(s['end']) or
                   (not 0 <= s['start'] < min(s['end'], length) <= length or s['end'] > length + 1) for s in segments):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise RuntimeError('Recognition returned invalid text/timestamps.') from None
        recognized_language = data.get('result', {}).get('language') or recognized_language
        # Empty chunks can be silence; only the whole recording must contain readable speech.
        global_segments = [{**s, 'start': start + s['start'], 'end': min(actual, start + s['end'])} for s in segments]
        read_store.write_json(record, {'key': key, 'length': length, 'segments': global_segments,
                                      'language': recognized_language})
        chunks.extend(global_segments)
        if count > 1:
            shutil.rmtree(working)
    suspicious = quality(chunks)
    return {'text': '\n'.join(s['text'] for s in chunks), 'segments': chunks, 'language': recognized_language or language,
            'content_complete': True, 'content_basis': 'full_video_local_asr', 'recognition_verified': False,
            'suspicious_segments': suspicious, 'chunks_processed': count,
            'media_evidence': {'duration': actual, 'audio': True, 'full_audio_processed': True},
            'limitations': ['Recognition may contain errors; check names, numbers and flagged repeated phrases.']}
