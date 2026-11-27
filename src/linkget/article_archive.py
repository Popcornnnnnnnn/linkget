"""Unified article classification, SSRF-safe asset download, and offline archive writer."""
from datetime import datetime, timezone
import hashlib
import html
import http.client
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener

if __package__:
    from . import read_store, rendered_page, toutiao, web_article, x_article
    from .media import reference
else:
    import read_store, rendered_page, toutiao, web_article, x_article
    from media import reference

MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_TOTAL_MEDIA_BYTES = 300 * 1024 * 1024
IMAGE_TIMEOUT_SECONDS = 30
VIDEO_TIMEOUT_SECONDS = 120

_CONTENT_TYPE_EXT = {
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/avif': '.avif',
    'image/heic': '.heic',
    'image/heif': '.heic',
    'image/svg+xml': '.svg',
    'video/mp4': '.mp4',
    'video/webm': '.webm',
    'video/quicktime': '.mov',
}

_SAFE_URL_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.heic', '.svg', '.mp4', '.webm', '.mov'}
_TRANSIENT_KEYS = ('raw_html', 'raw_source', 'body_html', 'media_items', 'content_state')


def classify_article_target(raw_input, *, force_article=False):
    """Classify a single input URL into a known article target, or return None for normal media."""
    text = (raw_input or '').strip()
    if not text or not re.search(r'https?://', text):
        return None
    urls = re.findall(r'https?://[^\s<>"，。]+', text)
    if len(urls) != 1:
        return None
    candidate = urls[0].rstrip('.,;!）)')
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password:
        return None
    host = (parsed.hostname or '').lower().rstrip('.')
    if not host:
        return None

    if host in toutiao.HOSTS:
        try:
            canon = toutiao.canonical(candidate)
        except ValueError:
            return None
        if urlsplit(canon).path.startswith('/article/'):
            return {
                'kind': 'toutiao_article',
                'url': canon,
                'site': 'Toutiao',
                'engine': 'rendered_page',
                'domain': 'toutiao.com',
                'supports_auth': False,
            }
        return None

    if host in {'www.raspberrypi.com', 'zhuanlan.zhihu.com'}:
        try:
            canon, _ = rendered_page.public_article_url(candidate)
        except ValueError:
            return None
        return {
            'kind': 'public_article',
            'url': canon,
            'site': 'Raspberry Pi' if host == 'www.raspberrypi.com' else 'Zhihu',
            'engine': 'article',
            'domain': host,
            'supports_auth': False,
        }

    if host == web_article.WECHAT_HOST:
        canon, browser_eligible = web_article.canonical_wechat_url(candidate)
        return {
            'kind': 'wechat_article',
            'url': canon,
            'site': 'WeChat',
            'engine': 'web_article',
            'domain': web_article.WECHAT_HOST,
            'supports_auth': False,
            'wechat_browser_eligible': browser_eligible,
        }

    if x_article.is_x_host(host):
        if x_article.is_x_article_url(candidate):
            canon = x_article.canonical_x_url(candidate)
            return {
                'kind': 'x_article',
                'url': canon,
                'site': 'X Article',
                'engine': 'x_article',
                'domain': 'x.com',
                'supports_auth': True,
            }
        if force_article and x_article.is_x_status_url(candidate):
            canon = x_article.canonical_x_url(candidate)
            return {
                'kind': 'x_article',
                'url': canon,
                'site': 'X Article',
                'engine': 'x_article',
                'domain': 'x.com',
                'supports_auth': True,
            }
        return None

    if web_article.is_known_media_host(host):
        return None

    canon = web_article.validate_public_web_url(candidate, resolve_dns=False)
    return {
        'kind': 'web_article',
        'url': canon,
        'site': host or 'Web',
        'engine': 'web_article',
        'domain': host or None,
        'supports_auth': False,
    }


def _sniff_media_ext(head, content_type, url, media_type):
    if head.startswith(b'\xff\xd8\xff'):
        return '.jpg', 'image/jpeg'
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png', 'image/png'
    if head.startswith((b'GIF87a', b'GIF89a')):
        return '.gif', 'image/gif'
    if len(head) >= 12 and head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return '.webp', 'image/webp'
    if len(head) >= 12 and head[4:8] == b'ftyp':
        brand = head[8:12].lower()
        if brand in {b'avif', b'avis'}:
            return '.avif', 'image/avif'
        if brand in {b'heic', b'heix', b'mif1', b'msf1'}:
            return '.heic', 'image/heic'
        if brand == b'qt  ':
            return '.mov', 'video/quicktime'
        if brand in {b'mp41', b'mp42', b'isom', b'avc1', b'iso2', b'm4v '}:
            return '.mp4', 'video/mp4'
    if head.startswith(b'\x1a\x45\xdf\xa3'):
        return '.webm', 'video/webm'

    mime = (content_type or '').split(';', 1)[0].strip().lower()
    if mime in {'text/html', 'application/xhtml+xml'}:
        raise ValueError('Asset endpoint returned HTML instead of media.')

    head_text = head[:512].lstrip().lower()
    if head_text.startswith((b'<!doctype', b'<html', b'<head', b'<body')):
        raise ValueError('Asset endpoint returned an HTML page instead of media.')
    if (
        (head_text.startswith((b'<svg', b'<?xml')) and b'<svg' in head_text)
        or (mime == 'image/svg+xml' and b'<svg' in head_text and b'<html' not in head_text)
    ):
        return '.svg', 'image/svg+xml'
    if head_text.startswith(b'<'):
        raise ValueError('Asset endpoint returned an HTML/markup page instead of media.')

    if mime in _CONTENT_TYPE_EXT:
        return _CONTENT_TYPE_EXT[mime], mime

    suffix = Path(urlsplit(url).path).suffix.lower()
    if suffix == '.jpeg':
        suffix = '.jpg'
    if suffix in _SAFE_URL_EXTS:
        return suffix, mime or None
    return ('.mp4', mime or 'video/mp4') if media_type == 'video' else ('.jpg', mime or 'image/jpeg')


def _image_dimensions(data, ext):
    try:
        if ext == '.png' and len(data) >= 24 and data.startswith(b'\x89PNG\r\n\x1a\n'):
            w, h = struct.unpack('>II', data[16:24])
            return int(w), int(h)
        if ext == '.gif' and len(data) >= 10 and data[:6] in (b'GIF87a', b'GIF89a'):
            w, h = struct.unpack('<HH', data[6:10])
            return int(w), int(h)
        if ext == '.jpg' and len(data) >= 4 and data.startswith(b'\xff\xd8'):
            idx = 2
            n = len(data)
            while idx + 4 <= n:
                if data[idx] != 0xFF:
                    idx += 1
                    continue
                while idx < n and data[idx] == 0xFF:
                    idx += 1
                if idx >= n:
                    break
                marker = data[idx]
                idx += 1
                if marker in (0xD8, 0xD9) or (0xD0 <= marker <= 0xD7) or marker == 0x01:
                    continue
                if idx + 2 > n:
                    break
                seg_len = struct.unpack('>H', data[idx:idx + 2])[0]
                if seg_len < 2 or idx + seg_len > n:
                    break
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    if idx + 7 <= n:
                        h, w = struct.unpack('>HH', data[idx + 3:idx + 7])
                        return int(w), int(h)
                idx += seg_len
        if ext == '.webp' and len(data) >= 30 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            chunk = data[12:16]
            if chunk == b'VP8 ' and len(data) >= 30:
                w, h = struct.unpack('<HH', data[26:30])
                return int(w & 0x3FFF), int(h & 0x3FFF)
            if chunk == b'VP8L' and len(data) >= 25:
                bits = struct.unpack('<I', data[21:25])[0]
                return int((bits & 0x3FFF) + 1), int(((bits >> 14) & 0x3FFF) + 1)
            if chunk == b'VP8X' and len(data) >= 30:
                w = 1 + int.from_bytes(data[24:27], 'little')
                h = 1 + int.from_bytes(data[27:30], 'little')
                return w, h
    except (struct.error, ValueError):
        pass
    return None, None


def _probe_video_dimensions(path, ffprobe_bin):
    if not ffprobe_bin:
        return None, None
    try:
        proc = subprocess.run(
            [ffprobe_bin, '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height', '-of', 'json', str(path)],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if proc.returncode != 0:
            return None, None
        info = json.loads(proc.stdout or '{}')
        streams = info.get('streams') or []
        if streams and isinstance(streams[0], dict):
            w, h = streams[0].get('width'), streams[0].get('height')
            if isinstance(w, int) and isinstance(h, int) and w > 0 and h > 0:
                return w, h
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None, None


def _referer_for(asset_url, article_url):
    host = (urlsplit(asset_url).hostname or '').lower()
    if host.endswith('twimg.com'):
        return 'https://x.com/'
    if host.endswith(('qpic.cn', 'qlogo.cn')):
        return 'https://mp.weixin.qq.com/'
    if article_url:
        parts = urlsplit(article_url)
        if parts.scheme in {'http', 'https'} and parts.netloc:
            return f'{parts.scheme}://{parts.netloc}/'
    return None


def _unique_asset_path(assets_dir, stem, ext):
    candidate = assets_dir / f'{stem}{ext}'
    idx = 1
    while candidate.exists() or candidate.is_symlink():
        candidate = assets_dir / f'{stem}-{idx}{ext}'
        idx += 1
    return candidate


def _sha256_file(path):
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        while True:
            chunk = stream.read(65536)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _download_one_asset(
    url,
    role,
    media_type,
    stem,
    assets_dir,
    *,
    article_url=None,
    opener=None,
    should_resolve=True,
    remaining_budget=MAX_TOTAL_MEDIA_BYTES,
    ffprobe_bin=None,
    reuse_matching=False,
):
    _, resolved = web_article._validate_and_resolve_public_url(url, resolve_dns=should_resolve)
    max_bytes = min(MAX_VIDEO_BYTES if media_type == 'video' else MAX_IMAGE_BYTES, remaining_budget)
    if max_bytes <= 0:
        raise ValueError('Article media archive size limit reached.')
    timeout = VIDEO_TIMEOUT_SECONDS if media_type == 'video' else IMAGE_TIMEOUT_SECONDS
    headers = {
        'User-Agent': web_article.USER_AGENT,
        'Accept': 'video/*,image/*,*/*;q=0.5' if media_type == 'video' else 'image/avif,image/webp,image/png,image/jpeg,image/*,*/*;q=0.5',
    }
    referer = _referer_for(url, article_url)
    if referer:
        headers['Referer'] = referer
    pinned_map = {}
    if resolved is not None:
        rhost, rport, infos = resolved
        pinned_map[(rhost, rport)] = infos
    active_opener = opener if opener is not None else build_opener(
        web_article._SafeRedirectHandler(resolve_dns=should_resolve, pinned_map=pinned_map),
        web_article._PinnedHTTPHandler(pinned_map),
        web_article._PinnedHTTPSHandler(pinned_map),
    )
    req = Request(url, headers=headers)

    if assets_dir.is_symlink():
        raise ValueError('Refusing a substituted assets directory.')
    assets_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix='.asset-', dir=str(assets_dir))
    tmp_path = Path(tmp_name)
    hasher = hashlib.sha256()
    total = 0
    head = bytearray()
    try:
        with os.fdopen(fd, 'wb') as out_stream:
            with active_opener.open(req, timeout=timeout) as response:
                final_url = response.geturl() or url
                web_article.validate_public_web_url(final_url, resolve_dns=False)
                resp_headers = getattr(response, 'headers', {}) or {}
                content_type = resp_headers.get('Content-Type') or resp_headers.get('content-type') or ''
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError(f'Asset exceeded {max_bytes} byte limit.')
                    if len(head) < 1024:
                        need = 1024 - len(head)
                        head.extend(chunk[:need])
                    hasher.update(chunk)
                    out_stream.write(chunk)
        if total == 0:
            raise ValueError('Asset response was empty.')
        digest_hex = hasher.hexdigest()
        ext, mime = _sniff_media_ext(bytes(head), content_type, final_url, media_type)
        base_candidate = assets_dir / f'{stem}{ext}'
        if reuse_matching and base_candidate.exists() and not base_candidate.is_symlink() and base_candidate.is_file():
            if _sha256_file(base_candidate) == digest_hex:
                tmp_path.unlink(missing_ok=True)
                dest = base_candidate
            else:
                dest = _unique_asset_path(assets_dir, stem, ext)
                os.chmod(tmp_path, 0o644)
                os.replace(tmp_path, dest)
        else:
            dest = _unique_asset_path(assets_dir, stem, ext)
            os.chmod(tmp_path, 0o644)
            os.replace(tmp_path, dest)
        width, height = (None, None)
        if media_type == 'video':
            width, height = _probe_video_dimensions(dest, ffprobe_bin)
        elif total <= MAX_IMAGE_BYTES and ext in {'.png', '.jpg', '.gif', '.webp'}:
            with dest.open('rb') as img_fp:
                width, height = _image_dimensions(img_fp.read(262144), ext)
        return {
            'role': role,
            'media_type': media_type,
            'original_url': url,
            'local_path': f'assets/{dest.name}',
            'abs_path': dest,
            'mime_type': mime,
            'size_bytes': total,
            'width': width,
            'height': height,
            'sha256': digest_hex,
            'status': 'downloaded',
        }
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def download_article_assets(
    bundle_dir,
    media_items,
    *,
    article_url=None,
    opener=None,
    resolve_dns=None,
    ffprobe_bin=None,
    reuse_matching=False,
):
    """Download declared images/videos safely into <bundle_dir>/assets/.

    Returns ``(asset_map, manifest, limitations)``.
    """
    assets_dir = Path(bundle_dir) / 'assets'
    if assets_dir.is_symlink():
        raise ValueError('Refusing a substituted assets directory.')
    should_resolve = (opener is None) if resolve_dns is None else resolve_dns

    asset_map = {}
    manifest = []
    limitations = []
    seen_urls = {}
    remaining_budget = MAX_TOTAL_MEDIA_BYTES
    img_seq = 0
    vid_seq = 0

    for item in media_items or []:
        if not isinstance(item, dict):
            continue
        orig_url = item.get('original_url')
        if not isinstance(orig_url, str) or not orig_url.startswith(('http://', 'https://')):
            continue
        role = item.get('role') or 'inline'
        media_type = item.get('media_type') or ('video' if role == 'video' else 'image')
        media_id = item.get('media_id')

        if orig_url in seen_urls:
            local_rel = seen_urls[orig_url]
            if local_rel and media_id:
                asset_map[f'id:{media_id}'] = local_rel
            continue

        if role == 'cover':
            stem = 'cover'
        elif role == 'avatar':
            stem = 'avatar'
        elif media_type == 'video':
            vid_seq += 1
            stem = f'video-{vid_seq:03d}'
        else:
            img_seq += 1
            stem = f'img-{img_seq:03d}'

        try:
            record = _download_one_asset(
                orig_url,
                role,
                media_type,
                stem,
                assets_dir,
                article_url=article_url,
                opener=opener,
                should_resolve=should_resolve,
                remaining_budget=remaining_budget,
                ffprobe_bin=ffprobe_bin,
                reuse_matching=reuse_matching,
            )
            remaining_budget -= record['size_bytes']
            if media_id:
                record['media_id'] = media_id
                asset_map[f'id:{media_id}'] = record['local_path']
            if item.get('alt'):
                record['alt'] = item['alt']
            if item.get('caption'):
                record['caption'] = item['caption']
            asset_map[orig_url] = record['local_path']
            seen_urls[orig_url] = record['local_path']
            manifest.append(record)
        except (OSError, ValueError, RuntimeError, http.client.HTTPException) as error:
            seen_urls[orig_url] = None
            manifest.append({
                'role': role,
                'media_type': media_type,
                'original_url': orig_url,
                'media_id': media_id,
                'status': 'failed',
                'error': str(error),
            })
            limitations.append(f'Asset not downloaded ({role}: {orig_url}): {error}')

        # Also download poster image for videos if exposed
        poster_url = item.get('poster_url')
        if isinstance(poster_url, str) and poster_url.startswith(('http://', 'https://')) and poster_url not in seen_urls:
            poster_stem = f'{stem}-poster'
            try:
                p_rec = _download_one_asset(
                    poster_url,
                    'poster',
                    'image',
                    poster_stem,
                    assets_dir,
                    article_url=article_url,
                    opener=opener,
                    should_resolve=should_resolve,
                    remaining_budget=remaining_budget,
                    ffprobe_bin=ffprobe_bin,
                    reuse_matching=reuse_matching,
                )
                remaining_budget -= p_rec['size_bytes']
                if media_id:
                    asset_map[f'id:{media_id}-poster'] = p_rec['local_path']
                asset_map[poster_url] = p_rec['local_path']
                seen_urls[poster_url] = p_rec['local_path']
                manifest.append(p_rec)
            except (OSError, ValueError, RuntimeError, http.client.HTTPException) as error:
                seen_urls[poster_url] = None
                manifest.append({
                    'role': 'poster',
                    'media_type': 'image',
                    'original_url': poster_url,
                    'status': 'failed',
                    'error': str(error),
                })
                limitations.append(f'Asset not downloaded (poster: {poster_url}): {error}')

    return asset_map, manifest, limitations


def _clean_slug_token(value, max_len=48, fallback=''):
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f\x7f]+', ' ', str(value or ''))
    cleaned = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af-]+', '-', cleaned, flags=re.U)
    cleaned = re.sub(r'-{2,}', '-', cleaned).strip('-_. ')
    if not cleaned or cleaned in {'.', '..'}:
        return fallback
    return cleaned[:max_len].rstrip('-_. ') or fallback


def slugify_folder_name(article):
    pub = str(article.get('published_at') or article.get('fetched_at') or '')
    m_date = re.search(r'(\d{4}-\d{2}-\d{2})', pub)
    date_part = m_date.group(1) if m_date else datetime.now(timezone.utc).strftime('%Y-%m-%d')
    tag_raw = article.get('author_handle') or article.get('site') or 'article'
    tag_part = _clean_slug_token(tag_raw.lower(), max_len=24, fallback='article')
    title_part = _clean_slug_token(article.get('title') or '', max_len=52, fallback='untitled')
    combined = f'{date_part}-{tag_part}-{title_part}'
    return combined[:96].rstrip('-_. ')


def allocate_bundle_dir(parent_dir, article):
    raw_parent = Path(parent_dir).expanduser()
    if raw_parent.is_symlink():
        raise ValueError('Destination folder must not be a symbolic link.')
    parent = raw_parent.resolve()
    if parent.is_symlink():
        raise ValueError('Destination folder must not be a symbolic link.')
    parent.mkdir(parents=True, exist_ok=True)
    base_name = slugify_folder_name(article)
    suffix = 0
    while True:
        name = base_name if suffix == 0 else f'{base_name}-{suffix}'
        candidate = parent / name
        try:
            candidate.mkdir(mode=0o755)
            return candidate
        except FileExistsError:
            suffix += 1


def _yaml_scalar(value):
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value).replace('\r\n', '\n').replace('\r', '\n')
    return json.dumps(text, ensure_ascii=False)


def _format_frontmatter(fields):
    lines = ['---']
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, dict):
            if not value:
                continue
            lines.append(f'{key}:')
            for sub_k, sub_v in value.items():
                if sub_v is not None:
                    lines.append(f'  {sub_k}: {_yaml_scalar(sub_v)}')
        elif isinstance(value, list):
            if not value:
                continue
            lines.append(f'{key}:')
            for item in value:
                lines.append(f'  - {_yaml_scalar(item)}')
        else:
            lines.append(f'{key}: {_yaml_scalar(value)}')
    lines.append('---')
    return '\n'.join(lines)


def _safe_write_new(path, content, *, mode=0o644, replace_existing=False):
    if path.parent.is_symlink():
        raise ValueError('Refusing a substituted output directory: ' + str(path.parent))
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise ValueError('Refusing a substituted output directory: ' + str(path.parent))
    if path.is_symlink():
        raise ValueError('Refusing a substituted output path: ' + str(path))
    if path.exists():
        if path.read_text(encoding='utf-8') == content:
            return path
        if not replace_existing:
            raise ValueError('Output file already exists with different content: ' + str(path))
        fd, tmp_name = tempfile.mkstemp(prefix='.tmp-', dir=str(path.parent))
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(content)
            os.chmod(tmp_path, mode)
            os.replace(tmp_path, path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return path
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(content)
    return path


def _build_article_markdown(article, asset_map, assets_downloaded, assets_total):
    cover_orig = article.get('cover_image')
    cover_local = asset_map.get(cover_orig) if cover_orig else None
    frontmatter = _format_frontmatter({
        'title': article.get('title'),
        'author': article.get('author'),
        'author_handle': article.get('author_handle'),
        'author_url': article.get('author_url'),
        'site': article.get('site'),
        'site_name': article.get('site_name'),
        'url': article.get('url'),
        'article_url': article.get('article_url'),
        'canonical_url': article.get('canonical_url'),
        'tweet_id': article.get('tweet_id'),
        'article_id': article.get('article_id'),
        'published_at': article.get('published_at'),
        'modified_at': article.get('modified_at'),
        'fetched_at': article.get('fetched_at'),
        'language': article.get('language'),
        'cover': cover_local or cover_orig,
        'cover_original_url': cover_orig,
        'keywords': article.get('keywords'),
        'metrics': article.get('metrics'),
        'assets_downloaded': assets_downloaded,
        'assets_total': assets_total,
    })

    title = article.get('title') or article.get('site') or 'Article'
    parts = [frontmatter, '']
    cover_src = cover_local or cover_orig
    if cover_src:
        safe_title = title.replace('[', '\\[').replace(']', '\\]')
        parts.append(f'![{safe_title}]({cover_src})')
        parts.append('')
    parts.append(f'# {title}')
    parts.append('')

    meta_bits = [part for part in (article.get('author'), article.get('published_at')) if part]
    source_link = article.get('url') or article.get('canonical_url') or article.get('reference')
    if source_link:
        meta_bits.append(f'Source: {source_link}')
    if meta_bits:
        parts.append('_' + ' · '.join(meta_bits) + '_')
        parts.append('')

    if article.get('community_note'):
        parts.append('> **Community Note:** ' + str(article['community_note']).strip())
        parts.append('')

    body = (article.get('markdown') or article.get('text') or '').strip()
    if body:
        parts.append(body)
        parts.append('')

    return '\n'.join(parts)


def _format_metrics_html(metrics):
    if not isinstance(metrics, dict) or not metrics:
        return ''
    labels = [
        ('views', 'Views'),
        ('likes', 'Likes'),
        ('retweets', 'Reposts'),
        ('replies', 'Replies'),
        ('bookmarks', 'Bookmarks'),
        ('quotes', 'Quotes'),
    ]
    chips = []
    for key, label in labels:
        val = metrics.get(key)
        if isinstance(val, int) and not isinstance(val, bool):
            chips.append(f'<span class="metric"><strong>{val:,}</strong> {label}</span>')
    if not chips:
        return ''
    return '<div class="metrics">' + ''.join(chips) + '</div>'


def _x_byline(author, author_detail, author_url, avatar_src, asset_map):
    name = author_detail.get('name') or author
    handle = author_detail.get('handle')
    avatar = f'<img class="avatar" src="{html.escape(avatar_src, quote=True)}" alt="">' if avatar_src else ''
    affiliation = author_detail.get('affiliation')
    badge = ''
    if isinstance(affiliation, dict) and isinstance(affiliation.get('name'), str):
        badge_url = asset_map.get(affiliation.get('badge_url'))
        icon = f'<img src="{html.escape(badge_url, quote=True)}" alt="">' if badge_url else ''
        badge = f'<span class="affiliation">{icon}{html.escape(affiliation["name"])}</span>'
    verified = '<span class="verified" title="Verified on X" aria-label="Verified on X">✓</span>' if author_detail.get('verified') is True else ''
    name_html = html.escape(name)
    if author_url and author_url.startswith(('http://', 'https://')):
        name_html = f'<a href="{html.escape(author_url, quote=True)}" rel="noopener noreferrer">{name_html}</a>'
    handle_html = f'<span class="author-handle">@{html.escape(handle)}</span>' if handle else ''
    return (f'<span class="author x-byline">{avatar}<span class="author-identity">'
            f'<span class="author-name">{name_html}{verified}{badge}</span>{handle_html}</span></span>')


def _build_article_html(article, asset_map):
    title = article.get('title') or article.get('site') or 'Article'
    escaped_title = html.escape(title)
    lang = article.get('language') or 'en'
    cover_orig = article.get('cover_image')
    cover_src = asset_map.get(cover_orig) or cover_orig if cover_orig else None

    author_detail = article.get('author_detail') if isinstance(article.get('author_detail'), dict) else {}
    avatar_orig = author_detail.get('avatar_url')
    avatar_src = asset_map.get(avatar_orig) or avatar_orig if avatar_orig else None

    author_text = article.get('author') or ''
    author_url = article.get('author_url')
    if author_text and author_url and author_url.startswith(('http://', 'https://')):
        author_html = f'<a href="{html.escape(author_url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(author_text)}</a>'
    else:
        author_html = html.escape(author_text)

    meta_items = []
    is_x = article.get('site') == 'X'
    if author_html and is_x:
        meta_items.append(_x_byline(author_text, author_detail, author_url, avatar_src, asset_map))
    elif author_html:
        avatar_tag = f'<img class="avatar" src="{html.escape(avatar_src, quote=True)}" alt="">' if avatar_src else ''
        meta_items.append(f'<span class="author">{avatar_tag}{author_html}</span>')
    publication = []
    if article.get('published_at'):
        timestamp = str(article['published_at'])
        display = timestamp
        if is_x:
            try:
                date = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                display = f'{date:%b} {date.day}, {date.year}'
            except ValueError:
                pass
        publication.append(f'<time datetime="{html.escape(timestamp, quote=True)}">{html.escape(display)}</time>')
    if is_x:
        publication.insert(0, '<span class="site">X Article</span>')
        meta_items.append('<span class="publication">' + ''.join(publication) + '</span>')
    else:
        meta_items.extend(publication)
        if article.get('site'):
            meta_items.append(f'<span class="site">{html.escape(str(article["site"]))}</span>')

    source_url = article.get('url') or article.get('canonical_url') or ''
    source_html = (
        f'<a href="{html.escape(source_url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(source_url)}</a>'
        if source_url.startswith(('http://', 'https://'))
        else html.escape(source_url)
    )

    cover_html = (
        f'<figure class="cover"><img src="{html.escape(cover_src, quote=True)}" alt="{html.escape(title, quote=True)}"></figure>'
        if cover_src
        else ''
    )
    note_html = (
        f'<aside class="community-note"><strong>Community Note:</strong> {html.escape(str(article["community_note"]))}</aside>'
        if article.get('community_note')
        else ''
    )
    metrics_html = _format_metrics_html(article.get('metrics'))

    body_html = (article.get('body_html') or '').strip()
    if not body_html:
        paras = [p.strip() for p in re.split(r'\n{2,}', article.get('text') or '') if p.strip()]
        body_html = '\n'.join(f'<p>{html.escape(p).replace(chr(10), "<br>")}</p>' for p in paras)

    return f'''<!doctype html>
<html lang="{html.escape(str(lang), quote=True)}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{escaped_title}</title>
  <style>
    :root {{
      --bg: #ffffff;
      --fg: #181a1b;
      --muted: #5c6670;
      --border: #e3e7ec;
      --card: #f6f8fa;
      --accent: #0f62fe;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0f1419;
        --fg: #e7e9ea;
        --muted: #8b98a5;
        --border: #2f3336;
        --card: #16181c;
        --accent: #4d9eff;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 2rem 1.25rem 4rem;
      background: var(--bg);
      color: var(--fg);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", Roboto, sans-serif;
      line-height: 1.75;
      font-size: 17px;
    }}
    main {{ max-width: 740px; margin: 0 auto; }}
    header.article-header {{ margin-bottom: 1.75rem; border-bottom: 1px solid var(--border); padding-bottom: 1.25rem; }}
    h1 {{ font-size: 1.95rem; line-height: 1.28; margin: 0 0 0.9rem; }}
    .meta {{ display: flex; flex-wrap: wrap; align-items: center; gap: 0.75rem 1.1rem; color: var(--muted); font-size: 0.92rem; }}
    .author {{ display: inline-flex; align-items: center; gap: 0.5rem; font-weight: 600; color: var(--fg); }}
    .avatar {{ width: 28px; height: 28px; border-radius: 50%; object-fit: cover; }}
    .author-identity {{ display: grid; min-width: 0; }}
    .author-name {{ display: flex; flex-wrap: wrap; align-items: center; gap: 0.45em; }}
    .author-handle {{ color: var(--muted); font-weight: 400; }}
    .verified {{ display: inline-grid; place-items: center; border-radius: 50%; width: 1.25em; height: 1.25em; background: var(--accent); color: white; line-height: 1; }}
    .affiliation {{ display: inline-flex; align-items: center; gap: 0.35em; border: 1px solid var(--border); border-radius: 0.4em; padding: 0.1em 0.45em; font-size: 0.76em; }}
    .affiliation img {{ width: 1em; height: 1em; object-fit: contain; border-radius: 0.18em; }}
    .publication {{ display: inline-flex; flex-wrap: wrap; gap: 0.6em; align-items: center; }}
    .publication time:not(:first-child)::before {{ content: "·"; margin-right: 0.6em; }}
    .metrics {{ display: flex; flex-wrap: wrap; gap: 0.9rem; margin-top: 0.75rem; font-size: 0.88rem; color: var(--muted); }}
    .cover {{ margin: 0 0 1.5rem; }}
    .cover img {{ width: 100%; height: auto; border-radius: 10px; display: block; }}
    .community-note {{ background: var(--card); border-left: 4px solid var(--accent); padding: 0.85rem 1rem; border-radius: 6px; margin: 1.25rem 0; }}
    article img, article video {{ max-width: 100%; height: auto; border-radius: 8px; display: block; margin: 1rem auto; }}
    figure {{ margin: 1.5rem 0; }}
    figcaption {{ color: var(--muted); font-size: 0.88rem; text-align: center; margin-top: 0.4rem; }}
    blockquote {{ margin: 1.25rem 0; padding: 0.4rem 1rem; border-left: 3px solid var(--border); color: var(--muted); }}
    pre {{ background: var(--card); padding: 1rem; border-radius: 8px; overflow-x: auto; font-size: 0.9rem; line-height: 1.5; }}
    code {{ font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.9em; }}
    .table-wrap {{ overflow-x: auto; margin: 1.25rem 0; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 0.94rem; }}
    th, td {{ border: 1px solid var(--border); padding: 0.5rem 0.75rem; text-align: left; }}
    th {{ background: var(--card); font-weight: 600; }}
    a {{ color: var(--accent); text-decoration: none; word-break: break-word; }}
    a:hover {{ text-decoration: underline; }}
    hr {{ border: 0; border-top: 1px solid var(--border); margin: 2rem 0; }}
    footer.article-footer {{ margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.88rem; }}
  </style>
</head>
<body>
  <main>
    {cover_html}
    <header class="article-header">
      <h1>{escaped_title}</h1>
      <div class="meta">{''.join(meta_items)}</div>
      {metrics_html}
    </header>
    {note_html}
    <article class="article-body">
      {body_html}
    </article>
    <footer class="article-footer">
      <div>Original source: {source_html}</div>
      {f'<div>Archived at: {html.escape(str(article.get("fetched_at")))}</div>' if article.get('fetched_at') else ''}
    </footer>
  </main>
</body>
</html>
'''


def _build_article_text(article):
    title = article.get('title') or article.get('site') or 'Article'
    meta = ' · '.join(
        part for part in (article.get('author'), article.get('published_at'), article.get('url')) if part
    )
    sections = [title]
    if meta:
        sections.append(meta)
    if article.get('community_note'):
        sections.append('Community Note: ' + str(article['community_note']).strip())
    body = (article.get('text') or '').strip()
    if body:
        sections.append(body)
    return '\n\n'.join(sections) + '\n'


def strip_transient_fields(article):
    """Return a copy of article payload safe and compact for content.json and stdout JSON."""
    cleaned = dict(article)
    for key in _TRANSIENT_KEYS:
        cleaned.pop(key, None)
    return cleaned


def _prepare_archive_core(
    bundle_path,
    article,
    *,
    download_assets=True,
    opener=None,
    resolve_dns=None,
    ffprobe_bin=None,
    retry_safe=False,
):
    """Download assets, re-render links, and write source/HTML artifacts into ``bundle_path``."""
    working = dict(article)
    asset_map = {}
    assets_manifest = []
    asset_limitations = []

    media_items = list(working.get('media_items') or [])
    if working.get('cover_image') and not any(m.get('original_url') == working['cover_image'] for m in media_items):
        media_items.insert(0, {
            'role': 'cover',
            'media_type': 'image',
            'original_url': working['cover_image'],
            'alt': working.get('title'),
        })

    if download_assets and media_items:
        asset_map, assets_manifest, asset_limitations = download_article_assets(
            bundle_path,
            media_items,
            article_url=working.get('url') or working.get('canonical_url'),
            opener=opener,
            resolve_dns=resolve_dns,
            ffprobe_bin=ffprobe_bin,
            reuse_matching=retry_safe,
        )
        if working.get('content_state'):
            rerendered = x_article.render_content_state(working, asset_map=asset_map)
            working.update(rerendered)
        elif working.get('raw_html'):
            raw_str = working['raw_html']
            base = working.get('final_url') or working.get('url') or ''
            is_fragment = bool(working.get('raw_html_is_fragment') or working.get('dom_proof'))
            if not is_fragment:
                rerendered = web_article.rerender_html_article(
                    raw_str, base, wechat=bool(working.get('is_wechat')), asset_map=asset_map
                )
            else:
                rerendered = web_article.render_fragment(raw_str, base, asset_map=asset_map)
            working['markdown'] = rerendered.get('markdown') or working.get('markdown')
            working['body_html'] = rerendered.get('body_html') or working.get('body_html')
            if rerendered.get('text'):
                working['text'] = rerendered['text']
        elif working.get('body_html'):
            rerendered = web_article.render_fragment(
                working['body_html'],
                working.get('final_url') or working.get('url') or '',
                asset_map=asset_map,
            )
            working['markdown'] = rerendered.get('markdown') or working.get('markdown')
            working['body_html'] = rerendered.get('body_html') or working.get('body_html')

    written = []
    source_rel = None
    if working.get('raw_source') is not None:
        source_path = bundle_path / 'source' / 'x-article.json'
        _safe_write_new(
            source_path,
            json.dumps(working['raw_source'], ensure_ascii=False, indent=2),
            replace_existing=retry_safe,
        )
        source_rel = 'source/x-article.json'
        written.append(source_path)
    elif isinstance(working.get('raw_html'), str) and working['raw_html']:
        source_path = bundle_path / 'source' / 'original.html'
        _safe_write_new(source_path, working['raw_html'], replace_existing=retry_safe)
        source_rel = 'source/original.html'
        written.append(source_path)

    assets_downloaded = sum(1 for item in assets_manifest if item.get('status') == 'downloaded')
    assets_failed = sum(1 for item in assets_manifest if item.get('status') == 'failed')
    assets_total = len(assets_manifest)

    html_path = bundle_path / 'article.html'
    html_content = _build_article_html(working, asset_map)
    _safe_write_new(html_path, html_content, replace_existing=retry_safe)
    written.append(html_path)

    for item in assets_manifest:
        abs_p = item.pop('abs_path', None)
        if abs_p is not None and isinstance(abs_p, Path) and abs_p.is_file():
            written.append(abs_p)

    clean = strip_transient_fields(working)
    clean['markdown'] = working.get('markdown')
    clean.setdefault('reference', reference(working.get('final_url') or working.get('url') or ''))
    clean['assets'] = assets_manifest
    clean['assets_downloaded'] = assets_downloaded
    clean['assets_failed'] = assets_failed
    if clean.get('cover_image') and clean['cover_image'] in asset_map:
        clean['cover_local_path'] = asset_map[clean['cover_image']]
    clean['files'] = {
        'html': 'article.html',
        'json': 'content.json',
        'source': source_rel,
        'assets': [item['local_path'] for item in assets_manifest if item.get('status') == 'downloaded'],
    }
    if asset_limitations:
        existing_lims = list(clean.get('limitations') or [])
        for msg in asset_limitations:
            if msg not in existing_lims:
                existing_lims.append(msg)
        clean['limitations'] = existing_lims

    return working, clean, asset_map, assets_downloaded, assets_total, written


def prepare_read_archive(
    bundle_dir,
    article,
    *,
    download_assets=True,
    opener=None,
    resolve_dns=None,
    ffprobe_bin=None,
    formats=None,
):
    """Archive assets, article.html, and original source for ``linkget read``."""
    raw_bundle = Path(bundle_dir).expanduser()
    if raw_bundle.is_symlink():
        raise ValueError('Article destination folder must not be a symbolic link.')
    bundle_path = raw_bundle.resolve()
    if bundle_path.is_symlink():
        raise ValueError('Article destination folder must not be a symbolic link.')
    bundle_path.mkdir(parents=True, exist_ok=True)
    incomplete_read = not (bundle_path / 'content.json').exists()
    _, clean, _, _, _, _ = _prepare_archive_core(
        bundle_path,
        article,
        download_assets=download_assets,
        opener=opener,
        resolve_dns=resolve_dns,
        ffprobe_bin=ffprobe_bin,
        retry_safe=incomplete_read,
    )
    active_formats = {'md', 'txt'} if formats is None else set(formats)
    if 'md' in active_formats:
        clean['files']['markdown'] = 'content.md'
    if 'txt' in active_formats:
        clean['files']['text'] = 'content.txt'
    return clean


def write_article_bundle(
    bundle_dir,
    article,
    *,
    mode='download',
    download_assets=True,
    opener=None,
    resolve_dns=None,
    ffprobe_bin=None,
):
    """Write a complete offline article archive into ``bundle_dir``.

    Returns ``(clean_result, written_paths)``.
    """
    raw_bundle = Path(bundle_dir).expanduser()
    if raw_bundle.is_symlink():
        raise ValueError('Article destination folder must not be a symbolic link.')
    bundle_path = raw_bundle.resolve()
    if bundle_path.is_symlink():
        raise ValueError('Article destination folder must not be a symbolic link.')
    bundle_path.mkdir(parents=True, exist_ok=True)

    working, clean, asset_map, assets_downloaded, assets_total, written = _prepare_archive_core(
        bundle_path,
        article,
        download_assets=download_assets,
        opener=opener,
        resolve_dns=resolve_dns,
        ffprobe_bin=ffprobe_bin,
    )

    md_filename = 'content.md' if mode == 'read' else 'article.md'
    md_path = bundle_path / md_filename
    md_content = _build_article_markdown(working, asset_map, assets_downloaded, assets_total)
    _safe_write_new(md_path, md_content)
    written.append(md_path)

    txt_path = bundle_path / 'content.txt'
    txt_content = _build_article_text(working)
    _safe_write_new(txt_path, txt_content)
    written.append(txt_path)

    clean['files'].update({'markdown': md_filename, 'text': 'content.txt'})

    if mode == 'download':
        json_path = bundle_path / 'content.json'
        _safe_write_new(json_path, json.dumps(clean, ensure_ascii=False, indent=2), mode=0o600)
        written.append(json_path)

    return clean, written
