"""Extract, tag, and optionally convert single-item audio downloads."""

from http.client import HTTPException
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

if __package__:
    from . import ownership, transfers
    from .media import AUDIO, VIDEO, reference
    from .read_formats import plain
    from .runtime import check_batch_cancelled, run_process
else:
    import ownership, transfers
    from media import AUDIO, VIDEO, reference
    from read_formats import plain
    from runtime import check_batch_cancelled, run_process

AUDIO_FORMATS = ("mp3", "m4a", "flac", "wav", "opus")
CODEC_EXT = {
    "aac": ".m4a",
    "alac": ".m4a",
    "mp3": ".mp3",
    "opus": ".opus",
    "vorbis": ".ogg",
    "flac": ".flac",
    "ac3": ".m4a",
    "eac3": ".m4a",
}
FORMAT_CODECS = {
    "mp3": {"mp3"},
    "m4a": {"aac", "alac"},
    "opus": {"opus"},
    "flac": {"flac"},
}
TRANSCODE_ARGS = {
    "mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
    "m4a": ["-c:a", "aac", "-b:a", "256k"],
    "opus": ["-c:a", "libopus", "-b:a", "160k"],
    "flac": ["-c:a", "flac"],
    "wav": ["-c:a", "pcm_s16le"],
}
LOSSY_CODECS = {"aac", "mp3", "opus", "vorbis", "wmav2", "ac3", "eac3", "amr_nb", "amr_wb"}
COVER_ART_EXTS = {".m4a", ".mp3", ".flac"}
COVER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
MAX_COVER_BYTES = 10 * 1024 * 1024


def extension_for_codec(codec, current_suffix=""):
    codec = (codec or "").lower()
    if codec.startswith("pcm_"):
        return ".wav"
    if codec in CODEC_EXT:
        return CODEC_EXT[codec]
    suffix = (current_suffix or "").lower()
    if suffix in AUDIO and suffix != ".webm":
        return suffix
    return ".mka"


def codec_matches_format(codec, target_format):
    codec = (codec or "").lower()
    if target_format == "wav":
        return codec.startswith("pcm_")
    return codec in FORMAT_CODECS.get(target_format, set())


def probe_audio(path, ffprobe):
    result = run_process(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, timeout=20,
    )
    if result.returncode:
        return None
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        return None
    streams = data.get("streams") or []
    audio_stream = next((item for item in streams if item.get("codec_type") == "audio"), None)
    if not audio_stream:
        return None
    has_real_video = any(
        item.get("codec_type") == "video" and (item.get("disposition") or {}).get("attached_pic") != 1
        for item in streams
    )
    return {
        "codec": (audio_stream.get("codec_name") or "").lower(),
        "sample_rate": audio_stream.get("sample_rate"),
        "channels": audio_stream.get("channels"),
        "bit_rate": audio_stream.get("bit_rate") or (data.get("format") or {}).get("bit_rate"),
        "duration": (data.get("format") or {}).get("duration") or audio_stream.get("duration"),
        "has_real_video": has_real_video,
    }


def _clean_text(value):
    if not isinstance(value, str):
        return ""
    cleaned = html.unescape(value).replace("\x00", "")
    cleaned = "".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _url_scheme(url):
    try:
        return urlsplit(url).scheme
    except ValueError:
        return ""


def _first_text(*values):
    for value in values:
        text = _clean_text(value)
        if text:
            return text
    return ""


def _actor_name(value):
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, dict):
        return _first_text(
            value.get("nickname"),
            value.get("nick"),
            value.get("screen_name"),
            value.get("name"),
            value.get("full_name"),
            value.get("uniqueId"),
            value.get("username"),
        )
    return ""


def _format_date(raw):
    for key in ("release_date", "upload_date"):
        value = raw.get(key)
        if isinstance(value, str) and re.fullmatch(r"\d{8}", value.strip()):
            text = value.strip()
            return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    year = raw.get("release_year")
    if isinstance(year, int) and 1000 <= year <= 9999:
        return str(year)
    if isinstance(year, str) and re.fullmatch(r"\d{4}", year.strip()):
        return year.strip()
    date_value = raw.get("date")
    if isinstance(date_value, str):
        match = re.match(r"(\d{4}-\d{2}-\d{2})", date_value.strip())
        if match:
            return match[1]
    return ""


def _thumbnail_url(raw):
    thumb = raw.get("thumbnail")
    if isinstance(thumb, str) and _url_scheme(thumb) in {"http", "https"}:
        return thumb
    thumbnails = raw.get("thumbnails")
    if isinstance(thumbnails, list):
        for entry in reversed(thumbnails):
            if isinstance(entry, dict):
                candidate = entry.get("url")
                if isinstance(candidate, str) and _url_scheme(candidate) in {"http", "https"}:
                    return candidate
    return ""


def extract_metadata(raw, source_url, site):
    data = raw if isinstance(raw, dict) else {}
    post = data.get("status") if isinstance(data.get("status"), dict) else data

    artists_value = post.get("artists")
    first_listed_artist = ""
    if isinstance(artists_value, list):
        for item in artists_value:
            first_listed_artist = _actor_name(item)
            if first_listed_artist:
                break

    artist = _first_text(
        post.get("artist"),
        first_listed_artist,
        post.get("creator"),
        post.get("uploader"),
        post.get("channel"),
        _actor_name(post.get("author")),
        _actor_name(post.get("user")),
        _actor_name(post.get("owner")),
        post.get("username"),
    )
    if artist.endswith(" - Topic"):
        artist = artist[: -len(" - Topic")].strip()

    title = _first_text(post.get("track"), post.get("title"), post.get("fulltitle"))
    if not title:
        for field in ("description", "desc", "caption", "content", "text"):
            raw_desc = post.get(field)
            if isinstance(raw_desc, str) and raw_desc.strip():
                cleaned_desc = plain(raw_desc).replace("\x00", "")
                first_line = next((line for line in cleaned_desc.splitlines() if line.strip()), "")
                first_line = "".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in first_line)
                first_line = re.sub(r"\s+", " ", first_line).strip()
                if first_line:
                    title = first_line[:80].rstrip()
                    break

    album = _first_text(post.get("album"))
    date = _format_date(post)
    thumbnail = _thumbnail_url(post)
    comment = reference(source_url) or site
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "date": date,
        "thumbnail": thumbnail,
        "comment": comment,
    }


def sanitize_component(text):
    if not isinstance(text, str):
        return ""
    value = text.replace("/", "-").replace(":", "-")
    value = "".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in value)
    value = re.sub(r'[\\*?"<>|]+', "", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return value


def _truncate_utf8(stem, max_bytes=180):
    chars = []
    used = 0
    for char in stem:
        size = len(char.encode("utf-8"))
        if used + size > max_bytes:
            break
        chars.append(char)
        used += size
    return "".join(chars).rstrip(" .")


def build_filename(meta, fallback_stem, ext, index=1, total=1):
    artist = sanitize_component(meta.get("artist", ""))
    title = sanitize_component(meta.get("title", ""))
    if artist and title:
        lower_title = title.lower()
        lower_artist = artist.lower()
        if lower_title.startswith((lower_artist + " - ", lower_artist + " -")):
            stem = title
        else:
            stem = f"{artist} - {title}"
    else:
        stem = title or sanitize_component(fallback_stem) or "audio"
    stem = _truncate_utf8(stem, max_bytes=170 if total > 1 else 180) or "audio"
    if total > 1:
        stem = f"{stem} ({index})"
    return f"{stem}{ext}"


def _unique_path(directory, stem, suffix):
    candidate = directory / f"{stem}{suffix}"
    index = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = directory / f"{stem}_{index}{suffix}"
        index += 1
    return candidate


def fetch_cover(url, work_dir, ffmpeg):
    try:
        parsed = urlsplit(url or "")
    except ValueError:
        return None, "Cover art unavailable; saved audio without thumbnail."
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password):
        return None, "Cover art unavailable; saved audio without thumbnail."
    work_dir.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    raw_path = _unique_path(work_dir, f"cover_{token}", ".raw")
    raw_proof = None
    writer = None
    try:
        request = Request(url, headers={"User-Agent": COVER_UA})
        with urlopen(request, timeout=15) as response:
            if _url_scheme(response.geturl() or url) not in {"http", "https"}:
                return None, "Cover art unavailable; saved audio without thumbnail."
            writer = ownership.Writer(raw_path)
            with writer:
                total = 0
                while chunk := response.read(65536):
                    check_batch_cancelled()
                    total += len(chunk)
                    if total > MAX_COVER_BYTES:
                        return None, "Cover art exceeded size limit; saved audio without thumbnail."
                    writer.write(chunk)
                raw_proof = writer.seal()
        jpg_path = _unique_path(work_dir, f"cover_{token}", ".jpg")
        result = run_process(
            [ffmpeg, "-n", "-v", "error", "-i", str(raw_path), "-frames:v", "1", "-q:v", "2", str(jpg_path)],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode or not jpg_path.is_file() or jpg_path.stat().st_size == 0:
            return None, "Cover art unavailable; saved audio without thumbnail."
        return jpg_path, None
    except (HTTPError, URLError, HTTPException, OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        return None, "Cover art unavailable; saved audio without thumbnail."
    finally:
        if raw_proof is not None:
            try:
                raw_proof.remove()
            except (OSError, ValueError, RuntimeError):
                pass
        elif writer is not None:
            writer.discard()


def _ffmpeg_command(ffmpeg, source_path, out_path, ext, copy_audio, target_format, meta, cover_path=None):
    command = [ffmpeg, "-n", "-v", "error", "-i", str(source_path)]
    if cover_path is not None:
        command.extend([
            "-i", str(cover_path),
            "-map", "0:a:0",
            "-map", "1:v:0",
            "-c:v", "mjpeg",
            "-disposition:v:0", "attached_pic",
        ])
        if ext == ".mp3":
            command.extend(["-id3v2_version", "3", "-metadata:s:v", "title=Album cover", "-metadata:s:v", "comment=Cover (front)"])
    else:
        command.extend(["-vn", "-map", "0:a:0"])
    if copy_audio:
        command.extend(["-c:a", "copy"])
    else:
        command.extend(TRANSCODE_ARGS[target_format])
    command.extend(["-map_metadata", "0", "-fflags", "+bitexact"])
    if ext == ".m4a":
        command.extend(["-movflags", "+faststart"])
    for key in ("title", "artist", "album", "date", "comment"):
        value = meta.get(key)
        if value:
            command.extend(["-metadata", f"{key}={value}"])
    command.append(str(out_path))
    return command


def prepare_audio(files, live_pairs, metadata_by_file, default_metadata, source_url, site, output_dir, tools, target_format=None):
    transfers.verify(files)
    motion_names = {video for _, video in (live_pairs or ())}
    candidates = [path for path in files if path.suffix.lower() in (AUDIO | VIDEO) and path.name not in motion_names]
    if not candidates:
        raise RuntimeError("No video or audio track found in this post.")
    audible = []
    for path in candidates:
        info = probe_audio(path, tools["ffprobe"])
        if info is not None:
            audible.append((path, info))
    if not audible:
        raise RuntimeError("Downloaded media has no audio track.")

    output_dir.mkdir(parents=True, exist_ok=True)
    cover_out_dir = output_dir.parent / "audio-cover"
    work_dir = output_dir.parent / "audio-work"
    used_names = set()
    cover_cache = {}
    outputs, proofs, notes = [], [], []
    seen_notes = set()

    def add_note(note):
        if note and note not in seen_notes:
            seen_notes.add(note)
            notes.append(note)

    total = len(audible)
    for index, (source_path, info) in enumerate(audible, 1):
        check_batch_cancelled()
        raw = (metadata_by_file or {}).get(source_path.name) or default_metadata or {}
        meta = extract_metadata(raw, source_url, site)
        if target_format is None:
            copy_audio = True
            ext = extension_for_codec(info["codec"], source_path.suffix.lower())
        else:
            ext = "." + target_format
            copy_audio = codec_matches_format(info["codec"], target_format)
            if not copy_audio and target_format in {"flac", "wav"} and info["codec"] in LOSSY_CODECS:
                add_note(f"Converted from lossy {info['codec']} source; lossless container does not restore lost audio detail.")

        filename = build_filename(meta, source_path.stem, ext, index=index, total=total)
        stem = Path(filename).stem
        candidate_name = filename
        suffix_num = 1
        while (candidate_name in used_names
               or (output_dir / candidate_name).exists() or (output_dir / candidate_name).is_symlink()
               or (cover_out_dir / candidate_name).exists() or (cover_out_dir / candidate_name).is_symlink()):
            candidate_name = f"{stem}-{suffix_num}{ext}"
            suffix_num += 1
        used_names.add(candidate_name)

        cover_path = None
        thumb_url = meta.get("thumbnail", "")
        if ext in COVER_ART_EXTS and thumb_url:
            if thumb_url not in cover_cache:
                cover_cache[thumb_url] = fetch_cover(thumb_url, work_dir, tools["ffmpeg"])
            cover_path, cover_note = cover_cache[thumb_url]
            add_note(cover_note)

        if cover_path is not None:
            cover_out_dir.mkdir(parents=True, exist_ok=True)
            cover_out_path = cover_out_dir / candidate_name
            command = _ffmpeg_command(tools["ffmpeg"], source_path, cover_out_path, ext, copy_audio, target_format, meta, cover_path)
            result = run_process(command, capture_output=True, text=True, timeout=300)
            if result.returncode == 0 and cover_out_path.is_file():
                final_path = cover_out_path
            else:
                add_note("Cover art could not be embedded; saved audio without thumbnail.")
                out_path = output_dir / candidate_name
                command = _ffmpeg_command(tools["ffmpeg"], source_path, out_path, ext, copy_audio, target_format, meta, cover_path=None)
                result = run_process(command, capture_output=True, text=True, timeout=300)
                final_path = out_path
        else:
            out_path = output_dir / candidate_name
            command = _ffmpeg_command(tools["ffmpeg"], source_path, out_path, ext, copy_audio, target_format, meta, cover_path=None)
            result = run_process(command, capture_output=True, text=True, timeout=300)
            final_path = out_path

        if result.returncode or not final_path.is_file():
            detail = "\n".join((result.stderr or "").splitlines()[-6:]).strip()
            raise RuntimeError("Audio processing failed." + ("\n" + detail if detail else ""))

        proofs.append(ownership.FileProof.observe(final_path))
        outputs.append(final_path)

    transfers.verify(files)
    return ownership.FileList(outputs, proofs), notes
