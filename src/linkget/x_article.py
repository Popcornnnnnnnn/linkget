"""X / Twitter Article extraction via gallery-dl GraphQL and lossless Draft.js rendering."""
from datetime import datetime, timezone
import html
import json
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

if __package__:
    from .runtime import run_process
else:
    from runtime import run_process

X_HOSTS = frozenset({
    'x.com',
    'www.x.com',
    'mobile.x.com',
    'twitter.com',
    'www.twitter.com',
    'mobile.twitter.com',
})

_ARTICLE_PATH_RE = re.compile(r'^/([A-Za-z0-9_]{1,32})/article/(\d+)/?$')
_I_ARTICLE_PATH_RE = re.compile(r'^/i/article/(\d+)/?$')
_STATUS_PATH_RE = re.compile(r'^/([A-Za-z0-9_]{1,32}|i/web)/status/(\d+)(?:/(?:photo|video)/\d+)?/?$')

_LOGIN_HINT_RE = re.compile(
    r'\b(?:401|403|unauthorized|login|logged in|log in|sign in|cookies?|authentication|'
    r'requires an account|could not log in|nsfw|age[- ]restricted)\b',
    re.I,
)
_UNAVAILABLE_HINT_RE = re.compile(
    r'\b(?:404|not found|no such tweet|unavailable|deleted|suspended|does not exist)\b',
    re.I,
)

_HEADER_LEVELS = {
    'header-one': 1,
    'header-two': 2,
    'header-three': 3,
    'header-four': 4,
    'header-five': 5,
    'header-six': 6,
}


class XArticleError(RuntimeError):
    """Raised when an X Article cannot be retrieved or parsed."""


class XArticleLoginRequired(XArticleError):
    """Raised when full X Article content requires login cookies or a logged-in browser."""


def is_x_host(hostname):
    return (hostname or '').lower().rstrip('.') in X_HOSTS


def _split_valid_x_url(url):
    if not isinstance(url, str) or not url or len(url) > 2048:
        return None
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        return None
    if not is_x_host(parsed.hostname):
        return None
    return parsed


def is_x_article_url(url):
    parsed = _split_valid_x_url(url)
    if parsed is None:
        return False
    return bool(_ARTICLE_PATH_RE.fullmatch(parsed.path) or _I_ARTICLE_PATH_RE.fullmatch(parsed.path))


def is_x_status_url(url):
    parsed = _split_valid_x_url(url)
    if parsed is None:
        return False
    return bool(_STATUS_PATH_RE.fullmatch(parsed.path))


def canonical_x_url(url):
    parsed = _split_valid_x_url(url)
    if parsed is None:
        raise ValueError('Enter a single X / Twitter post or article URL.')
    m_iart = _I_ARTICLE_PATH_RE.fullmatch(parsed.path)
    if m_iart:
        return f'https://x.com/i/article/{m_iart.group(1)}'
    m_art = _ARTICLE_PATH_RE.fullmatch(parsed.path)
    if m_art:
        return f'https://x.com/{m_art.group(1)}/article/{m_art.group(2)}'
    m_status = _STATUS_PATH_RE.fullmatch(parsed.path)
    if m_status:
        return f'https://x.com/{m_status.group(1)}/status/{m_status.group(2)}'
    raise ValueError('Enter a single X / Twitter post or article URL.')


def status_url_for_gallery_dl(url):
    canonical = canonical_x_url(url)
    parsed = urlsplit(canonical)
    if _I_ARTICLE_PATH_RE.fullmatch(parsed.path):
        return None
    m_art = _ARTICLE_PATH_RE.fullmatch(parsed.path)
    if m_art:
        return f'https://x.com/{m_art.group(1)}/status/{m_art.group(2)}'
    return canonical


def upgrade_x_media_url(url):
    if not isinstance(url, str) or not url:
        return url
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return url
    host = (parsed.hostname or '').lower()
    if parsed.scheme not in {'http', 'https'} or host != 'pbs.twimg.com':
        return url
    if not parsed.path.startswith('/media/'):
        return url
    ext_match = re.fullmatch(r'(/media/[A-Za-z0-9_-]+)\.(jpg|jpeg|png|webp|gif)', parsed.path, re.I)
    if ext_match:
        fmt = ext_match.group(2).lower()
        if fmt == 'jpeg':
            fmt = 'jpg'
        return urlunsplit(('https', 'pbs.twimg.com', ext_match.group(1), urlencode({'format': fmt, 'name': 'orig'}), ''))
    query = parse_qs(parsed.query, keep_blank_values=False)
    if 'format' in query and query['format']:
        fmt = query['format'][0].lower()
        return urlunsplit(('https', 'pbs.twimg.com', parsed.path, urlencode({'format': fmt, 'name': 'orig'}), ''))
    return url


def _format_unix_iso(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        ts = int(value)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    try:
        return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (OverflowError, OSError, ValueError):
        return None


def _format_gallery_dl_date(value):
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}', raw):
        return raw.replace(' ', 'T') + 'Z'
    return raw


def _safe_http_url(url):
    if not isinstance(url, str):
        return None
    candidate = url.strip()
    if not candidate:
        return None
    if not re.match(r'^https?://', candidate, re.I):
        if re.match(r'^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/?#:]|$)', candidate):
            candidate = 'https://' + candidate
        else:
            return None
    try:
        parsed = urlsplit(candidate)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname:
        return None
    return candidate


def _extract_media_entry(media_obj, role):
    if not isinstance(media_obj, dict):
        return None
    info = media_obj.get('media_info') if isinstance(media_obj.get('media_info'), dict) else media_obj
    media_id = str(media_obj.get('media_id') or info.get('media_id') or '').strip()
    media_key = media_obj.get('media_key')
    if 'duration_millis' in info or isinstance(info.get('variants'), list):
        variants = [v for v in (info.get('variants') or []) if isinstance(v, dict) and isinstance(v.get('url'), str)]
        mp4_variants = [v for v in variants if 'mp4' in str(v.get('content_type', '')).lower() or '.mp4' in v['url'].lower()]
        pool = mp4_variants or variants
        if not pool:
            return None
        best = max(pool, key=lambda v: int(v.get('bit_rate') or 0))
        preview = info.get('preview_image') if isinstance(info.get('preview_image'), dict) else {}
        poster_raw = preview.get('original_img_url') or preview.get('url')
        poster_url = upgrade_x_media_url(poster_raw) if isinstance(poster_raw, str) and poster_raw else None
        return {
            'role': role if role == 'cover' else 'video',
            'media_type': 'video',
            'media_id': media_id,
            'media_key': media_key,
            'original_url': best['url'],
            'poster_url': poster_url,
            'duration_ms': info.get('duration_millis'),
            'bitrate': best.get('bit_rate'),
            'width': preview.get('original_img_width'),
            'height': preview.get('original_img_height'),
        }
    orig_raw = info.get('original_img_url') or info.get('url')
    if not isinstance(orig_raw, str) or not orig_raw.strip():
        return None
    return {
        'role': role,
        'media_type': 'image',
        'media_id': media_id,
        'media_key': media_key,
        'original_url': upgrade_x_media_url(orig_raw),
        'width': info.get('original_img_width'),
        'height': info.get('original_img_height'),
    }


def _utf16_index_map(text):
    """Map UTF-16 code-unit offsets (Draft.js / JS strings) to Python str indices."""
    mapping = []
    for py_idx, ch in enumerate(text):
        units = 2 if ord(ch) > 0xFFFF else 1
        for _ in range(units):
            mapping.append(py_idx)
    mapping.append(len(text))
    return mapping


def _slice_u16(mapping, offset, length):
    max_u16 = len(mapping) - 1
    start_u16 = max(0, min(int(offset), max_u16))
    end_u16 = max(start_u16, min(start_u16 + max(0, int(length)), max_u16))
    return mapping[start_u16], mapping[end_u16]


def _span_u16_bounds(mapping, from_idx, to_idx):
    max_u16 = len(mapping) - 1
    start_u16 = max(0, min(int(from_idx), max_u16))
    end_u16 = max(start_u16, min(int(to_idx), max_u16))
    return mapping[start_u16], mapping[end_u16]


def _normalize_entity_map(raw_map):
    entities = {}
    if isinstance(raw_map, dict):
        for key, item in raw_map.items():
            if isinstance(item, dict) and 'value' in item and 'type' not in item:
                entities[str(key)] = item.get('value')
            else:
                entities[str(key)] = item
    elif isinstance(raw_map, list):
        for item in raw_map:
            if not isinstance(item, dict) or 'key' not in item:
                continue
            entities[str(item['key'])] = item.get('value', item)
    return entities


def _normalize_style(raw_style):
    key = str(raw_style or '').strip().lower()
    if key in {'bold', 'b'}:
        return 'bold'
    if key in {'italic', 'i'}:
        return 'italic'
    if key in {'strikethrough', 'strike', 'del'}:
        return 'strike'
    if key in {'code', 'monospace'}:
        return 'code'
    if key in {'underline', 'u'}:
        return 'underline'
    return None


def _wrap_md_styles(text, styles):
    if not text or not styles:
        return text
    if not text.strip():
        return text
    lead_len = len(text) - len(text.lstrip())
    trail_len = len(text) - len(text.rstrip())
    lead = text[:lead_len]
    trail = text[len(text) - trail_len:] if trail_len else ''
    core = text[lead_len:len(text) - trail_len if trail_len else len(text)]
    if 'code' in styles:
        fence = '``' if '`' in core else '`'
        pad = ' ' if (core.startswith('`') or core.endswith('`')) else ''
        core = f'{fence}{pad}{core}{pad}{fence}'
    if 'bold' in styles:
        core = f'**{core}**'
    if 'italic' in styles:
        core = f'*{core}*'
    if 'strike' in styles:
        core = f'~~{core}~~'
    if 'underline' in styles:
        core = f'<u>{core}</u>'
    return lead + core + trail


def _wrap_html_styles(text, styles):
    escaped = html.escape(text, quote=False)
    if not styles or not text:
        return escaped
    out = escaped
    if 'code' in styles:
        out = f'<code>{out}</code>'
    if 'underline' in styles:
        out = f'<u>{out}</u>'
    if 'strike' in styles:
        out = f'<del>{out}</del>'
    if 'italic' in styles:
        out = f'<em>{out}</em>'
    if 'bold' in styles:
        out = f'<strong>{out}</strong>'
    return out


def _render_styled_slice(text, start, end, styles, *, markdown=True):
    if start >= end:
        return ''
    points = {start, end}
    clipped = []
    for s_start, s_end, s_name in styles:
        a = max(start, s_start)
        b = min(end, s_end)
        if a < b:
            clipped.append((a, b, s_name))
            points.add(a)
            points.add(b)
    sorted_pts = sorted(points)
    parts = []
    for p, q in zip(sorted_pts, sorted_pts[1:]):
        if p >= q:
            continue
        active = frozenset(s_name for a, b, s_name in clipped if a <= p and q <= b)
        seg = text[p:q]
        if markdown:
            parts.append(_wrap_md_styles(seg, active))
        else:
            parts.append(_wrap_html_styles(seg, active))
    return ''.join(parts)


def _render_block_inline(block, entities):
    text = block.get('text') or ''
    if not text:
        return '', ''
    mapping = _utf16_index_map(text)

    styles = []
    for item in block.get('inlineStyleRanges') or []:
        if not isinstance(item, dict):
            continue
        sname = _normalize_style(item.get('style'))
        if not sname:
            continue
        try:
            a, b = _slice_u16(mapping, item.get('offset', 0), item.get('length', 0))
        except (TypeError, ValueError):
            continue
        if a < b:
            styles.append((a, b, sname))

    links = []
    for item in block.get('entityRanges') or []:
        if not isinstance(item, dict):
            continue
        ent = entities.get(str(item.get('key')))
        if not isinstance(ent, dict):
            continue
        etype = str(ent.get('type') or '').upper()
        edata = ent.get('data') if isinstance(ent.get('data'), dict) else {}
        href = None
        if etype == 'LINK':
            href = _safe_http_url(edata.get('url') or edata.get('href'))
        elif etype == 'MENTION':
            handle = str(edata.get('screenName') or edata.get('username') or edata.get('text') or '').lstrip('@').strip()
            if handle:
                href = f'https://x.com/{handle}'
        elif etype == 'HASHTAG':
            tag = str(edata.get('hashtag') or edata.get('text') or '').lstrip('#').strip()
            if tag:
                href = f'https://x.com/hashtag/{tag}'
        if href:
            try:
                a, b = _slice_u16(mapping, item.get('offset', 0), item.get('length', 0))
            except (TypeError, ValueError):
                continue
            if a < b:
                links.append((a, b, href))

    bdata = block.get('data') if isinstance(block.get('data'), dict) else {}
    for item in bdata.get('urls') or []:
        if not isinstance(item, dict):
            continue
        href = _safe_http_url(item.get('url') or item.get('expanded_url') or item.get('text'))
        if href and 'fromIndex' in item and 'toIndex' in item:
            try:
                a, b = _span_u16_bounds(mapping, item['fromIndex'], item['toIndex'])
            except (TypeError, ValueError):
                continue
            if a < b:
                links.append((a, b, href))
    for item in bdata.get('mentions') or []:
        if not isinstance(item, dict):
            continue
        handle = str(item.get('text') or item.get('screenName') or item.get('username') or '').lstrip('@').strip()
        if handle and 'fromIndex' in item and 'toIndex' in item:
            try:
                a, b = _span_u16_bounds(mapping, item['fromIndex'], item['toIndex'])
            except (TypeError, ValueError):
                continue
            if a < b:
                links.append((a, b, f'https://x.com/{handle}'))
    for item in bdata.get('hashtags') or []:
        if not isinstance(item, dict):
            continue
        tag = str(item.get('text') or '').lstrip('#').strip()
        if tag and 'fromIndex' in item and 'toIndex' in item:
            try:
                a, b = _span_u16_bounds(mapping, item['fromIndex'], item['toIndex'])
            except (TypeError, ValueError):
                continue
            if a < b:
                links.append((a, b, f'https://x.com/hashtag/{tag}'))

    # Keep non-overlapping links ordered from left to right.
    links.sort(key=lambda entry: (entry[0], -(entry[1] - entry[0])))
    selected_links = []
    cursor = 0
    for a, b, href in links:
        if a >= cursor:
            selected_links.append((a, b, href))
            cursor = b

    md_parts = []
    html_parts = []
    pos = 0
    for a, b, href in selected_links:
        if pos < a:
            md_parts.append(_render_styled_slice(text, pos, a, styles, markdown=True))
            html_parts.append(_render_styled_slice(text, pos, a, styles, markdown=False))
        inner_md = _render_styled_slice(text, a, b, styles, markdown=True)
        inner_html = _render_styled_slice(text, a, b, styles, markdown=False)
        safe_label = inner_md.replace('[', '\\[').replace(']', '\\]')
        md_parts.append(f'[{safe_label}]({href})')
        html_parts.append(f'<a href="{html.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer">{inner_html}</a>')
        pos = b
    if pos < len(text):
        md_parts.append(_render_styled_slice(text, pos, len(text), styles, markdown=True))
        html_parts.append(_render_styled_slice(text, pos, len(text), styles, markdown=False))
    return ''.join(md_parts), ''.join(html_parts)


def _build_media_lookup(article_data):
    media_map = {}
    for entry in article_data.get('media_entities') or []:
        parsed = _extract_media_entry(entry, 'inline')
        if parsed and parsed.get('media_id'):
            media_map[str(parsed['media_id'])] = parsed
    for entry in article_data.get('media_items') or []:
        if isinstance(entry, dict) and entry.get('media_id'):
            media_map.setdefault(str(entry['media_id']), entry)
    return media_map


def _resolve_asset(url, media_id, asset_map):
    if not asset_map:
        return url
    if url and url in asset_map:
        return asset_map[url]
    if media_id and f'id:{media_id}' in asset_map:
        return asset_map[f'id:{media_id}']
    return url


def _render_atomic_entity(block, entity, media_map, asset_map):
    etype = str(entity.get('type') or '').upper()
    edata = entity.get('data') if isinstance(entity.get('data'), dict) else {}

    if etype in {'MEDIA', 'IMAGE', 'VIDEO'}:
        md_items = []
        html_items = []
        txt_items = []
        caption = (edata.get('caption') or '').strip()
        items = edata.get('mediaItems') or []
        if not items and edata.get('mediaId'):
            items = [{'mediaId': edata['mediaId']}]
        for item in items:
            if not isinstance(item, dict):
                continue
            mid = str(item.get('mediaId') or item.get('media_id') or '').strip()
            media = media_map.get(mid) or {}
            orig_url = media.get('original_url') or _safe_http_url(edata.get('url'))
            if not orig_url:
                continue
            src = _resolve_asset(orig_url, mid, asset_map)
            mtype = media.get('media_type') or ('video' if etype == 'VIDEO' else 'image')
            if mtype == 'video':
                poster_orig = media.get('poster_url')
                poster_src = _resolve_asset(poster_orig, f'{mid}-poster', asset_map) if poster_orig else None
                label = caption or f'Video {mid}'.strip()
                md_items.append(f'[Video: {label}]({src})')
                if poster_src:
                    md_items.append(f'![{label} poster]({poster_src})')
                poster_attr = f' poster="{html.escape(poster_src, quote=True)}"' if poster_src else ''
                fig_caption = f'<figcaption>{html.escape(caption)}</figcaption>' if caption else ''
                html_items.append(
                    f'<figure class="media-video"><video controls preload="metadata"{poster_attr}>'
                    f'<source src="{html.escape(src, quote=True)}">'
                    f'</video>{fig_caption}</figure>'
                )
                txt_items.append(f'[Video: {caption or src}]')
            else:
                alt = caption or f'Image {mid}'.strip()
                md_items.append(f'![{alt}]({src})')
                if caption:
                    md_items.append(f'*{caption}*')
                    txt_items.append(caption)
                fig_caption = f'<figcaption>{html.escape(caption)}</figcaption>' if caption else ''
                html_items.append(
                    f'<figure class="media-image"><img src="{html.escape(src, quote=True)}" '
                    f'alt="{html.escape(alt, quote=True)}" loading="lazy">{fig_caption}</figure>'
                )
        return md_items, html_items, txt_items

    if etype == 'MARKDOWN':
        md_raw = str(edata.get('markdown') or block.get('text') or '').strip('\n')
        if not md_raw:
            return [], [], []
        fence_match = re.fullmatch(r'```([A-Za-z0-9_+.#-]*)\n([\s\S]*?)\n```', md_raw.strip())
        if fence_match:
            lang = fence_match.group(1)
            code_body = fence_match.group(2)
            cls = f' class="language-{html.escape(lang, quote=True)}"' if lang else ''
            html_out = f'<pre><code{cls}>{html.escape(code_body)}</code></pre>'
            return [md_raw], [html_out], [code_body]
        return [md_raw], [f'<pre class="markdown-block">{html.escape(md_raw)}</pre>'], [md_raw]

    if etype == 'DIVIDER':
        return ['---'], ['<hr>'], []

    if etype == 'LATEX':
        tex = str(edata.get('tex') or edata.get('latex') or block.get('text') or '').strip()
        if not tex:
            return [], [], []
        return [f'$$\n{tex}\n$$'], [f'<div class="math-block"><pre><code>{html.escape(tex)}</code></pre></div>'], [tex]

    if etype == 'TWEET':
        tid = str(edata.get('tweetId') or edata.get('id') or '').strip()
        tweet_url = _safe_http_url(edata.get('url')) or (f'https://x.com/i/web/status/{tid}' if tid else None)
        if not tweet_url:
            return [], [], []
        return (
            [f'> Embedded X post: {tweet_url}'],
            [f'<blockquote class="embedded-tweet"><a href="{html.escape(tweet_url, quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(tweet_url)}</a></blockquote>'],
            [f'Embedded X post: {tweet_url}'],
        )

    # Generic fallback: preserve any exposed URL/caption/text rather than dropping content.
    fallback_bits = [
        str(edata.get(k)).strip()
        for k in ('title', 'caption', 'text', 'markdown', 'url')
        if isinstance(edata.get(k), str) and edata.get(k).strip()
    ]
    if not fallback_bits and (block.get('text') or '').strip():
        fallback_bits.append(block['text'].strip())
    if not fallback_bits:
        return [], [], []
    joined = ' — '.join(fallback_bits)
    return [joined], [f'<p>{html.escape(joined)}</p>'], [joined]


def render_content_state(article_data, *, asset_map=None):
    """Render Draft.js content_state to Markdown, sanitized body HTML, and plain text."""
    content_state = article_data.get('content_state') if isinstance(article_data, dict) else None
    if not isinstance(content_state, dict):
        return {'markdown': '', 'body_html': '', 'text': ''}
    blocks = content_state.get('blocks') or []
    entities = _normalize_entity_map(content_state.get('entityMap'))
    media_map = _build_media_lookup(article_data)

    md_blocks = []
    html_blocks = []
    txt_blocks = []

    idx = 0
    total = len(blocks)
    while idx < total:
        block = blocks[idx]
        if not isinstance(block, dict):
            idx += 1
            continue
        btype = str(block.get('type') or 'unstyled')

        if btype == 'atomic':
            ranges = block.get('entityRanges') or []
            for er in ranges:
                if not isinstance(er, dict):
                    continue
                ent = entities.get(str(er.get('key')))
                if isinstance(ent, dict):
                    m_out, h_out, t_out = _render_atomic_entity(block, ent, media_map, asset_map)
                    md_blocks.extend(m_out)
                    html_blocks.extend(h_out)
                    txt_blocks.extend(t_out)
            idx += 1
            continue

        if btype == 'code-block':
            code_lines = []
            lang = ''
            while idx < total and isinstance(blocks[idx], dict) and blocks[idx].get('type') == 'code-block':
                b = blocks[idx]
                if not lang and isinstance(b.get('data'), dict):
                    lang = str(b['data'].get('language') or b['data'].get('lang') or '').strip()
                code_lines.append(b.get('text') or '')
                idx += 1
            code_body = '\n'.join(code_lines)
            md_blocks.append(f'```{lang}\n{code_body}\n```')
            cls = f' class="language-{html.escape(lang, quote=True)}"' if lang else ''
            html_blocks.append(f'<pre><code{cls}>{html.escape(code_body)}</code></pre>')
            if code_body.strip():
                txt_blocks.append(code_body)
            continue

        if btype == 'blockquote':
            quote_md = []
            quote_html = []
            quote_txt = []
            while idx < total and isinstance(blocks[idx], dict) and blocks[idx].get('type') == 'blockquote':
                b = blocks[idx]
                m_line, h_line = _render_block_inline(b, entities)
                t_line = (b.get('text') or '').strip()
                if m_line:
                    quote_md.append('> ' + m_line)
                    quote_html.append(f'<p>{h_line}</p>')
                    if t_line:
                        quote_txt.append(t_line)
                idx += 1
            if quote_md:
                md_blocks.append('\n'.join(quote_md))
                html_blocks.append('<blockquote>' + ''.join(quote_html) + '</blockquote>')
                txt_blocks.append('\n'.join(quote_txt))
            continue

        if btype in {'unordered-list-item', 'ordered-list-item'}:
            md_list = []
            txt_list = []
            counters = {}
            html_list = []
            stack = []  # list of (tag, depth)

            def _close_to(target_depth, target_tag=None):
                while stack and (stack[-1][1] > target_depth or (stack[-1][1] == target_depth and target_tag is not None and stack[-1][0] != target_tag)):
                    tag, _ = stack.pop()
                    html_list.append(f'</{tag}>')

            while idx < total and isinstance(blocks[idx], dict) and blocks[idx].get('type') in {'unordered-list-item', 'ordered-list-item'}:
                b = blocks[idx]
                cur_type = b.get('type')
                tag = 'ol' if cur_type == 'ordered-list-item' else 'ul'
                try:
                    depth = max(0, min(6, int(b.get('depth') or 0)))
                except (TypeError, ValueError):
                    depth = 0
                for d in list(counters):
                    if d > depth:
                        del counters[d]
                if cur_type == 'ordered-list-item':
                    counters[depth] = counters.get(depth, 0) + 1
                    bullet = f'{counters[depth]}. '
                else:
                    counters.pop(depth, None)
                    bullet = '- '
                m_line, h_line = _render_block_inline(b, entities)
                t_line = (b.get('text') or '').strip()
                indent = '  ' * depth
                md_list.append(indent + bullet + m_line)
                if t_line:
                    txt_list.append(indent + bullet + t_line)
                _close_to(depth, tag)
                if not stack or stack[-1][1] < depth:
                    html_list.append(f'<{tag}>')
                    stack.append((tag, depth))
                html_list.append(f'<li>{h_line}</li>')
                idx += 1

            _close_to(-1)
            if md_list:
                md_blocks.append('\n'.join(md_list))
                html_blocks.append('\n'.join(html_list))
                txt_blocks.append('\n'.join(txt_list))
            continue

        if btype in _HEADER_LEVELS:
            level = _HEADER_LEVELS[btype]
            m_line, h_line = _render_block_inline(block, entities)
            t_line = (block.get('text') or '').strip()
            if t_line:
                md_blocks.append('#' * level + ' ' + m_line)
                html_blocks.append(f'<h{level}>{h_line}</h{level}>')
                txt_blocks.append(t_line)
            idx += 1
            continue

        # Default / unstyled paragraph
        raw_text = block.get('text') or ''
        if raw_text.strip():
            m_line, h_line = _render_block_inline(block, entities)
            md_blocks.append(m_line)
            html_blocks.append(f'<p>{h_line}</p>')
            txt_blocks.append(raw_text.strip())
        idx += 1

    md_text = re.sub(r'\n{3,}', '\n\n', '\n\n'.join(md_blocks)).strip()
    body_html = '\n'.join(html_blocks).strip()
    plain_text = re.sub(r'\n{3,}', '\n\n', '\n\n'.join(txt_blocks)).strip()
    return {'markdown': md_text, 'body_html': body_html, 'text': plain_text}


def parse_raw_article(result, tdata=None, *, canonical_url=None):
    """Build normalized article dict + raw source snapshot from X GraphQL article result."""
    if not isinstance(result, dict):
        raise XArticleError('X Article result payload is missing.')
    tdata = tdata if isinstance(tdata, dict) else {}
    content_state = result.get('content_state')
    if not isinstance(content_state, dict) or not isinstance(content_state.get('blocks'), list):
        raise XArticleLoginRequired('X Articles require an X login or browser reader to fetch full content; guest access exposes only a preview.')

    tweet_id = str(tdata.get('tweet_id') or '').strip() or None
    article_id = str(result.get('rest_id') or result.get('id') or '').strip() or None
    title = str(result.get('title') or '').strip() or 'X Article'

    author_obj = tdata.get('author') if isinstance(tdata.get('author'), dict) else (
        tdata.get('user') if isinstance(tdata.get('user'), dict) else {}
    )
    author_handle = str(author_obj.get('name') or '').strip() or None
    author_nick = str(author_obj.get('nick') or '').strip() or None
    if author_nick and author_handle:
        author = f'{author_nick} (@{author_handle})'
    elif author_nick:
        author = author_nick
    elif author_handle:
        author = f'@{author_handle}'
    else:
        author = None
    author_url = f'https://x.com/{author_handle}' if author_handle else None

    avatar_url = author_obj.get('profile_image') if isinstance(author_obj.get('profile_image'), str) else None
    banner_url = author_obj.get('profile_banner') if isinstance(author_obj.get('profile_banner'), str) else None
    affiliation = author_obj.get('affiliation')
    author_detail = {
        'name': author_nick,
        'handle': author_handle,
        'url': author_url,
        'id': str(author_obj['id']) if author_obj.get('id') is not None else None,
        'bio': author_obj.get('description') or None,
        'location': author_obj.get('location') or None,
        'avatar_url': avatar_url,
        'banner_url': banner_url,
        'followers_count': author_obj.get('followers_count'),
        'following_count': author_obj.get('following_count'),
        'verified': author_obj.get('verified'),
        'affiliation': affiliation if isinstance(affiliation, dict) and affiliation.get('name') else None,
        'joined_at': _format_gallery_dl_date(author_obj.get('date')),
    }
    author_detail = {k: v for k, v in author_detail.items() if v is not None}

    meta_obj = result.get('metadata') if isinstance(result.get('metadata'), dict) else {}
    life_obj = result.get('lifecycle_state') if isinstance(result.get('lifecycle_state'), dict) else {}
    published_at = _format_unix_iso(meta_obj.get('first_published_at_secs')) or _format_gallery_dl_date(tdata.get('date'))
    modified_at = _format_unix_iso(life_obj.get('modified_at_secs'))
    fetched_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    raw_lang = tdata.get('lang')
    language = raw_lang.strip() if isinstance(raw_lang, str) and raw_lang.strip() not in {'', 'zxx', 'und'} else None

    metric_map = {
        'views': 'view_count',
        'likes': 'favorite_count',
        'retweets': 'retweet_count',
        'replies': 'reply_count',
        'bookmarks': 'bookmark_count',
        'quotes': 'quote_count',
    }
    metrics = {
        out_key: tdata[src_key]
        for out_key, src_key in metric_map.items()
        if isinstance(tdata.get(src_key), int) and not isinstance(tdata.get(src_key), bool)
    }

    community_note = tdata.get('birdwatch') if isinstance(tdata.get('birdwatch'), str) and tdata['birdwatch'].strip() else None

    cover_media = _extract_media_entry(result.get('cover_media'), 'cover')
    if cover_media is not None:
        cover_media['alt'] = title

    media_items = []
    seen_urls = set()
    if cover_media is not None:
        media_items.append(cover_media)
        seen_urls.add(cover_media['original_url'])
    for ent in result.get('media_entities') or []:
        parsed = _extract_media_entry(ent, 'inline')
        if parsed and parsed.get('original_url') not in seen_urls:
            media_items.append(parsed)
            seen_urls.add(parsed['original_url'])
    if avatar_url and avatar_url not in seen_urls:
        media_items.append({
            'role': 'avatar',
            'media_type': 'image',
            'media_id': 'author-avatar',
            'original_url': avatar_url,
            'alt': f'{author_nick or author_handle or "Author"} avatar',
        })
        seen_urls.add(avatar_url)
    affiliation = author_detail.get('affiliation')
    if isinstance(affiliation, dict) and affiliation.get('badge_url') and affiliation['badge_url'] not in seen_urls:
        media_items.append({
            'role': 'affiliation',
            'media_type': 'image',
            'original_url': affiliation['badge_url'],
            'alt': affiliation.get('name'),
        })
        seen_urls.add(affiliation['badge_url'])

    status_url = canonical_url
    if not status_url:
        if author_handle and tweet_id:
            status_url = f'https://x.com/{author_handle}/status/{tweet_id}'
        elif tweet_id:
            status_url = f'https://x.com/i/web/status/{tweet_id}'
        elif article_id:
            status_url = f'https://x.com/i/article/{article_id}'

    article_url = (
        f'https://x.com/{author_handle}/article/{tweet_id}'
        if (author_handle and tweet_id)
        else (f'https://x.com/i/article/{article_id}' if article_id else status_url)
    )

    rendered = render_content_state({
        'content_state': content_state,
        'media_entities': result.get('media_entities') or [],
        'media_items': media_items,
    })
    if not rendered['text']:
        raise XArticleError('The X Article contained no readable body text.')

    safe_tweet_meta = {
        key: tdata[key]
        for key in (
            'tweet_id', 'conversation_id', 'date', 'lang',
            'view_count', 'favorite_count', 'retweet_count', 'reply_count', 'bookmark_count', 'quote_count',
            'birdwatch', 'reply_to', 'quote_id', 'quoted_id', 'sensitive',
        )
        if key in tdata and tdata[key] is not None
    }
    if author_detail:
        safe_tweet_meta['author'] = author_detail
    raw_source = {
        'schema': 'x_article_raw_v1',
        'tweet_id': tweet_id,
        'article_id': article_id,
        'url': status_url,
        'article_url': article_url,
        'tweet_metadata': safe_tweet_meta,
        'article': result,
    }

    preview_text = str(result.get('preview_text') or '').strip() or None
    summary_text = str(result.get('summary_text') or '').strip() or None

    return {
        'kind': 'article',
        'site': 'X',
        'url': status_url,
        'final_url': status_url,
        'canonical_url': status_url,
        'article_url': article_url,
        'reference': status_url.removeprefix('https://') if status_url else None,
        'tweet_id': tweet_id,
        'article_id': article_id,
        'title': title,
        'author': author,
        'author_handle': author_handle,
        'author_url': author_url,
        'author_detail': author_detail or None,
        'published_at': published_at,
        'modified_at': modified_at,
        'fetched_at': fetched_at,
        'language': language,
        'description': preview_text,
        'preview_text': preview_text,
        'summary_text': summary_text,
        'metrics': metrics or None,
        'community_note': community_note,
        'cover_image': cover_media['original_url'] if cover_media else None,
        'cover_media': cover_media,
        'media_items': media_items,
        'content_state': content_state,
        'markdown': rendered['markdown'],
        'body_html': rendered['body_html'],
        'text': rendered['text'],
        'raw_source': raw_source,
        'content_complete': True,
        'content_basis': 'x_article_graphql_content_state',
        'acquisition': 'gallery_dl_x_article_graphql',
        'limitations': [],
    }


def _post_id(post):
    legacy = post.get('legacy') or post
    return str(post.get('tweet_id') or post.get('rest_id') or legacy.get('id_str') or '')


def _article_post_metadata(post):
    """Keep only the fields needed by the article parser from an untransformed post."""
    if post.get('tweet_id'):
        return post
    legacy = post.get('legacy') or post
    user = post.get('author') or ((post.get('core') or {}).get('user_results') or {}).get('result') or post.get('user') or {}
    core = user.get('core') or user.get('legacy') or user
    profile = user.get('legacy') or user
    avatar = (user.get('avatar') or {}).get('image_url') or profile.get('profile_image_url_https')
    label = (user.get('affiliates_highlighted_label') or {}).get('label') or {}
    affiliation = None
    affiliation_name = label.get('description')
    if isinstance(affiliation_name, str) and 0 < len(affiliation_name.strip()) <= 100:
        affiliation = {'name': affiliation_name.strip()}
        badge_url = (label.get('badge') or {}).get('url')
        if isinstance(badge_url, str) and badge_url.startswith('https://pbs.twimg.com/'):
            affiliation['badge_url'] = badge_url
    count = (post.get('views') or {}).get('count')
    return {
        **{key: legacy.get(key) for key in (
            'lang', 'favorite_count', 'retweet_count',
            'reply_count', 'bookmark_count', 'quote_count',
        )},
        'tweet_id': _post_id(post),
        'conversation_id': legacy.get('conversation_id_str'),
        'quote_id': legacy.get('quoted_by_id_str'),
        'quoted_id': legacy.get('quoted_status_id_str'),
        'sensitive': legacy.get('possibly_sensitive'),
        'birdwatch': ((post.get('birdwatch_pivot') or {}).get('subtitle') or {}).get('text'),
        'date': legacy.get('created_at'),
        'view_count': int(count) if isinstance(count, str) and count.isascii() and count.isdecimal() and len(count) <= 18 else count,
        'author': {
            'id': user.get('rest_id') or profile.get('id_str'),
            'name': core.get('screen_name'),
            'nick': core.get('name'),
            'profile_image': avatar.replace('_normal.', '.') if isinstance(avatar, str) else None,
            'date': core.get('created_at'),
            'description': (user.get('profile_bio') or {}).get('description') or profile.get('description'),
            'location': profile.get('location'),
            'profile_banner': (user.get('banner') or {}).get('image_url') or profile.get('profile_banner_url'),
            'followers_count': (user.get('relationship_counts') or {}).get('followers', profile.get('followers_count')),
            'following_count': (user.get('relationship_counts') or {}).get('following', profile.get('friends_count')),
            'verified': any(flag is True for flag in (
                user.get('is_blue_verified'), user.get('is_verified'),
                (user.get('verification') or {}).get('verified'), profile.get('verified'),
            )),
            'affiliation': affiliation,
        },
    }


def fetch_via_gallery_dl(url, gallery_dl_bin, *, cookie_path=None, timeout=45):
    """Fetch one X status/article URL via gallery-dl -j without downloading files.

    Returns ``None`` if the URL is a normal non-article X post.
    Raises ``XArticleLoginRequired`` if the post is an X Article without full
    ``content_state`` (e.g. guest preview only) or requires authentication.
    """
    status_url = status_url_for_gallery_dl(url)
    if status_url is None:
        raise XArticleError('Direct /i/article/ID links require a browser reader rather than gallery-dl.')
    canonical = canonical_x_url(url)
    m = _STATUS_PATH_RE.fullmatch(urlsplit(status_url).path)
    target_tweet_id = m.group(2) if m else None

    command = [
        gallery_dl_bin,
        '--config-ignore',
        '-j',
        '-o', 'extractor.twitter.text-tweets=true',
        '-o', 'extractor.twitter.transform=false',
    ]
    if cookie_path is not None:
        command.extend(['-o', 'extractor.twitter.articles=cover,media', '--cookies', str(cookie_path)])
    else:
        command.extend(['-o', 'extractor.twitter.articles=false'])
    command.append(status_url)

    proc = run_process(command, capture_output=True, text=True, timeout=timeout)
    stderr_text = (proc.stderr or '').strip()
    stdout_text = (proc.stdout or '').strip()
    combined = '\n'.join(part for part in (stderr_text, stdout_text[-1000:]) if part)

    if proc.returncode != 0:
        if _LOGIN_HINT_RE.search(combined):
            raise XArticleLoginRequired('X login cookies are required or expired for this X post.')
        raise XArticleError('Could not read this X post metadata.\n' + '\n'.join(combined.splitlines()[-6:]))

    try:
        items = json.loads(stdout_text or '[]')
    except ValueError as error:
        raise XArticleError('gallery-dl returned unreadable X metadata JSON.') from error
    if not isinstance(items, list):
        raise XArticleError('gallery-dl returned unexpected X metadata format.')

    dir_entries = [entry[1] for entry in items if isinstance(entry, list) and len(entry) >= 2 and entry[0] == 2 and isinstance(entry[1], dict)]
    tdata = None
    if target_tweet_id:
        for entry in dir_entries:
            if _post_id(entry) == target_tweet_id:
                tdata = entry
                break
    if tdata is None and dir_entries:
        tdata = dir_entries[0]
    if tdata is None:
        if _LOGIN_HINT_RE.search(stderr_text):
            raise XArticleLoginRequired('X login cookies are required or expired for this X post.')
        if _UNAVAILABLE_HINT_RE.search(stderr_text):
            raise XArticleError('This X post is unavailable, deleted, or could not be loaded.')
        return None

    art_wrapper = tdata.get('article')
    if not isinstance(art_wrapper, dict) or not art_wrapper:
        return None

    if isinstance(art_wrapper.get('article_results'), dict) and isinstance(art_wrapper['article_results'].get('result'), dict):
        result = art_wrapper['article_results']['result']
    else:
        result = art_wrapper

    return parse_raw_article(result, _article_post_metadata(tdata), canonical_url=canonical)
