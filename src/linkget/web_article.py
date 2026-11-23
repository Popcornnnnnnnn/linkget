"""Stdlib-only general web article & WeChat Official Account HTTP extractor with SSRF protection."""
from codecs import lookup as lookup_codec
from datetime import datetime, timezone
import html
from html.parser import HTMLParser
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zlib

if __package__:
    from . import links, toutiao
    from .media import reference
else:
    import links, toutiao
    from media import reference

WECHAT_HOST = 'mp.weixin.qq.com'
MAX_URL_LENGTH = 2048
MAX_HTML_BYTES = 5_000_000
MAX_REDIRECTS = 5
USER_AGENT = (
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
)

KNOWN_MEDIA_EXACT_HOSTS = (
    set(toutiao.HOSTS)
    | {'www.raspberrypi.com', 'zhuanlan.zhihu.com'}
    | links.XHS_SHORT_HOSTS
    | links.SOUNDCLOUD_SHORT_HOSTS
)

KNOWN_MEDIA_BASE_DOMAINS = (
    'youtube.com',
    'youtu.be',
    'instagram.com',
    'x.com',
    'twitter.com',
    'bilibili.com',
    'b23.tv',
    'tiktok.com',
    'tiktokv.com',
    'douyin.com',
    'iesdouyin.com',
    'xiaohongshu.com',
    'xhslink.com',
    'xhslink.cn',
    'weibo.com',
    'weibo.cn',
    't.cn',
    'soundcloud.com',
    'bandcamp.com',
    'reddit.com',
    'redd.it',
    'kuaishou.com',
    'lofter.com',
    'pixiv.net',
    'pinterest.com',
    'pin.it',
    'pinterest.co.uk',
    'toutiao.com',
)

_NOISE_TAGS = frozenset({
    'script', 'style', 'noscript', 'template', 'svg', 'canvas', 'iframe',
    'form', 'button', 'input', 'select', 'textarea', 'nav', 'footer',
    'aside', 'dialog', 'menu',
})
_VOID_TAGS = frozenset({
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
    'link', 'meta', 'param', 'source', 'track', 'wbr',
})
_NEGATIVE_HINT_RE = re.compile(
    r'\b(?:nav|navbar|sidebar|footer|header|comment|comments|cookie|popup|modal|'
    r'banner|promo|share|social|related|breadcrumb|menu|toc|widget|advert|ad-)\b',
    re.I,
)
_POSITIVE_HINT_RE = re.compile(
    r'\b(?:article|post|entry|content|markdown|story|main|body|prose|rich_text|richtext)\b',
    re.I,
)
_WECHAT_GATE_RE = re.compile(r'环境异常|secitptpage|请在微信客户端打开|完成验证')
_WECHAT_DELETED_RE = re.compile(r'此内容因违规无法查看|该内容已被发布者删除|该公众号已迁移|已被删除')
_BOT_SHELL_RE = re.compile(
    r'enable javascript|just a moment|checking your browser|access denied|please turn javascript on',
    re.I,
)
_ALLOWED_TEXT_MIMES = frozenset({
    'text/html',
    'application/xhtml+xml',
    'text/plain',
    'text/markdown',
    'text/x-markdown',
})


class WeChatGateError(RuntimeError):
    """Direct HTTP fetch encountered a WeChat verification/client gate."""


def is_known_media_host(hostname):
    host = (hostname or '').lower().rstrip('.')
    if not host:
        return False
    if host in KNOWN_MEDIA_EXACT_HOSTS:
        return True
    return any(host == base or host.endswith('.' + base) for base in KNOWN_MEDIA_BASE_DOMAINS)


def canonical_wechat_url(url):
    if not isinstance(url, str) or not url or len(url) > MAX_URL_LENGTH:
        raise ValueError('A bounded WeChat Official Account article URL is required.')
    parsed = urlsplit(url)
    host = (parsed.hostname or '').lower().rstrip('.')
    if parsed.scheme != 'https' or host != WECHAT_HOST or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Unsupported WeChat Official Account URL.')
    if re.fullmatch(r'/s/[A-Za-z0-9_-]{8,128}/?', parsed.path):
        return f'https://{WECHAT_HOST}{parsed.path.rstrip("/")}', True
    if parsed.path in ('/s', '/s/'):
        qs = parse_qs(parsed.query, keep_blank_values=False)
        required = {}
        for key, pattern in (
            ('__biz', r'[A-Za-z0-9_=+/-]{4,96}'),
            ('mid', r'[0-9]{1,30}'),
            ('idx', r'[0-9]{1,5}'),
            ('sn', r'[A-Za-z0-9_-]{8,128}'),
        ):
            vals = qs.get(key)
            if not vals or len(vals) != 1 or not re.fullmatch(pattern, vals[0]):
                raise ValueError('WeChat article query URL must include valid __biz, mid, idx, and sn parameters.')
            required[key] = vals[0]
        ordered = urlencode([(k, required[k]) for k in ('__biz', 'mid', 'idx', 'sn')])
        return f'https://{WECHAT_HOST}/s?{ordered}', False
    raise ValueError('Use one WeChat Official Account article link (https://mp.weixin.qq.com/s/...).')


def is_wechat_article(url):
    try:
        canonical_wechat_url(url)
        return True
    except ValueError:
        return False


def _is_public_ip(addr_text):
    try:
        ip = ipaddress.ip_address(addr_text.split('%', 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_global
        and not ip.is_private
        and not ip.is_loopback
        and not ip.is_link_local
        and not ip.is_multicast
        and not ip.is_reserved
        and not ip.is_unspecified
    )


def validate_public_web_url(url, *, resolve_dns=True):
    if not isinstance(url, str) or not url.strip() or len(url) > MAX_URL_LENGTH:
        raise ValueError('A bounded http/https web article URL is required.')
    if any(ch.isspace() for ch in url):
        raise ValueError('Web URL must not contain whitespace.')
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as error:
        raise ValueError('Invalid web URL.') from error
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Web article URLs must use public http or https without credentials.')
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Invalid URL port.')
    host = parsed.hostname.lower().rstrip('.')
    if (
        not host
        or host in {'localhost', 'localhost.localdomain', 'ip6-localhost', 'ip6-loopback'}
        or host.endswith(('.local', '.internal', '.localhost', '.arpa', '.home.arpa'))
    ):
        raise ValueError('Refusing non-public or internal host.')
    try:
        ipaddress.ip_address(host)
        is_literal_ip = True
    except ValueError:
        is_literal_ip = False
    if is_literal_ip:
        if not _is_public_ip(host):
            raise ValueError('Refusing private, loopback, or link-local IP address.')
        return url
    try:
        packed_ip = socket.inet_aton(host)
        canonical_ip = socket.inet_ntoa(packed_ip)
    except OSError:
        canonical_ip = None
    if canonical_ip is not None:
        if not _is_public_ip(canonical_ip):
            raise ValueError('Refusing private, loopback, or link-local IP address.')
        return url
    if '.' not in host:
        raise ValueError('Refusing unqualified or internal hostname.')
    if resolve_dns:
        target_port = port or (443 if parsed.scheme == 'https' else 80)
        try:
            infos = socket.getaddrinfo(host, target_port, type=socket.SOCK_STREAM)
        except OSError as error:
            raise ValueError(f'Could not resolve host {host}: {error}') from error
        if not infos:
            raise ValueError(f'Host {host} resolved to no network addresses.')
        for info in infos:
            sockaddr = info[4]
            if not sockaddr or not _is_public_ip(sockaddr[0]):
                raise ValueError('Refusing host resolving to private, loopback, or non-global address.')
    return url


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, *, resolve_dns=True):
        self.resolve_dns = resolve_dns

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        count = getattr(req, '_linkget_redirect_count', 0) + 1
        if count > MAX_REDIRECTS:
            if fp is not None:
                fp.close()
            raise ValueError('Too many HTTP redirects while fetching web article.')
        try:
            validate_public_web_url(newurl, resolve_dns=self.resolve_dns)
        except Exception:
            if fp is not None:
                fp.close()
            raise
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            new_req._linkget_redirect_count = count
        return new_req


def _bounded_read(response, max_bytes):
    chunks = []
    total = 0
    while True:
        chunk = response.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise ValueError(f'Web document exceeded {max_bytes} byte limit.')
        chunks.append(chunk)
    return b''.join(chunks)


def _decompress_bounded(raw, encoding, max_bytes):
    enc = (encoding or '').strip().lower()
    if not enc or enc == 'identity':
        return raw
    if enc in {'gzip', 'x-gzip'}:
        decomp = zlib.decompressobj(16 + zlib.MAX_WBITS)
    elif enc == 'deflate':
        decomp = zlib.decompressobj(zlib.MAX_WBITS)
    else:
        return raw
    try:
        try:
            out = decomp.decompress(raw, max_bytes + 1)
        except zlib.error:
            if enc == 'deflate':
                decomp = zlib.decompressobj(-zlib.MAX_WBITS)
                out = decomp.decompress(raw, max_bytes + 1)
            else:
                raise
        if len(out) > max_bytes or decomp.unconsumed_tail:
            raise ValueError(f'Decompressed web document exceeded {max_bytes} byte limit.')
        tail = decomp.flush(max_bytes - len(out) + 1)
        out += tail
        if len(out) > max_bytes:
            raise ValueError(f'Decompressed web document exceeded {max_bytes} byte limit.')
        return out
    except zlib.error as error:
        raise ValueError('Web document contained invalid compressed data.') from error


def _is_text_codec(name):
    try:
        info = lookup_codec(name)
        if getattr(info, '_is_text_encoding', True) is False:
            return False
        b''.decode(name)
        ''.encode(name)
        return True
    except (LookupError, TypeError, ValueError):
        return False


def _detect_charset(content_type, raw_bytes):
    match = re.search(r'charset\s*=\s*["\']?([A-Za-z0-9._-]+)', content_type or '', re.I)
    if match and _is_text_codec(match.group(1)):
        return match.group(1)
    head = raw_bytes[:8192].decode('ascii', errors='ignore')
    for pattern in (
        r'<meta\b[^>]*\bcharset\s*=\s*["\']?\s*([A-Za-z0-9._-]+)',
        r'<meta\b[^>]*\bcontent\s*=\s*["\'][^"\']*charset\s*=\s*([A-Za-z0-9._-]+)',
    ):
        meta = re.search(pattern, head, re.I)
        if meta and _is_text_codec(meta.group(1)):
            return meta.group(1)
    return 'utf-8'


def fetch_web_document(url, *, timeout=15, max_bytes=MAX_HTML_BYTES, opener=None, resolve_dns=None):
    should_resolve = (opener is None) if resolve_dns is None else resolve_dns
    validate_public_web_url(url, resolve_dns=should_resolve)
    req = Request(
        url,
        headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,text/plain,text/markdown;q=0.9,*/*;q=0.1',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
        },
    )
    active_opener = opener if opener is not None else build_opener(_SafeRedirectHandler(resolve_dns=should_resolve))
    try:
        with active_opener.open(req, timeout=timeout) as response:
            final_url = response.geturl() or url
            validate_public_web_url(final_url, resolve_dns=False)
            headers = getattr(response, 'headers', {}) or {}
            content_type = headers.get('Content-Type') or headers.get('content-type') or 'text/html; charset=utf-8'
            mime = content_type.split(';', 1)[0].strip().lower() or 'text/html'
            if mime not in _ALLOWED_TEXT_MIMES:
                raise ValueError(f'Unsupported content type for article reading: {mime}.')
            raw = _bounded_read(response, max_bytes)
            content_encoding = headers.get('Content-Encoding') or headers.get('content-encoding') or ''
            payload = _decompress_bounded(raw, content_encoding, max_bytes)
            charset = _detect_charset(content_type, payload)
            try:
                text = payload.decode(charset, errors='replace')
            except LookupError:
                text = payload.decode('utf-8', errors='replace')
    except http.client.HTTPException as error:
        raise RuntimeError(f'HTTP transfer failed while fetching web article: {error}') from error
    return {'url': url, 'final_url': final_url, 'content_type': mime, 'text': text}


class _Node:
    __slots__ = ('tag', 'attrs', 'children', 'text')

    def __init__(self, tag, attrs=None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children = []
        self.text = ''


class _DOMParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node('document')
        self.stack = [self.root]
        self.title_parts = []
        self._in_title = False
        self.meta = {}
        self.canonical_url = None
        self.json_ld = []
        self._in_json_ld = False
        self._json_ld_parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr_map = {k.lower(): (v if v is not None else '') for k, v in attrs if k}
        if tag == 'title':
            self._in_title = True
        elif tag == 'meta':
            key = (attr_map.get('property') or attr_map.get('name') or '').strip().lower()
            val = attr_map.get('content', '').strip()
            if key and val and key not in self.meta:
                self.meta[key] = val
        elif tag == 'link':
            rels = {r.lower() for r in attr_map.get('rel', '').split()}
            if 'canonical' in rels and attr_map.get('href') and not self.canonical_url:
                self.canonical_url = attr_map['href'].strip()
        elif tag == 'script' and 'ld+json' in attr_map.get('type', '').lower():
            self._in_json_ld = True
            self._json_ld_parts = []

        node = _Node(tag, attr_map)
        self.stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        tag = tag.lower()
        if tag not in _VOID_TAGS and len(self.stack) > 1 and self.stack[-1].tag == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == 'title':
            self._in_title = False
        if tag == 'script' and self._in_json_ld:
            self._in_json_ld = False
            raw_json = ''.join(self._json_ld_parts).strip()
            if raw_json:
                self.json_ld.append(raw_json)
            self._json_ld_parts = []
        if tag in _VOID_TAGS:
            return
        for idx in range(len(self.stack) - 1, 0, -1):
            if self.stack[idx].tag == tag:
                del self.stack[idx:]
                break

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        if self._in_json_ld:
            self._json_ld_parts.append(data)
        if data:
            node = _Node('#text')
            node.text = data
            self.stack[-1].children.append(node)


def _is_hidden(node):
    if 'hidden' in node.attrs or node.attrs.get('aria-hidden', '').lower() == 'true':
        return True
    style = node.attrs.get('style', '').replace(' ', '').lower()
    return 'display:none' in style or 'visibility:hidden' in style


def _find_by_id(node, target_id):
    if node.attrs.get('id') == target_id:
        return node
    for child in node.children:
        if child.tag != '#text':
            found = _find_by_id(child, target_id)
            if found is not None:
                return found
    return None


def _find_first(node, tags):
    if node.tag in tags:
        return node
    for child in node.children:
        if child.tag != '#text':
            found = _find_first(child, tags)
            if found is not None:
                return found
    return None


def _plain_node_text(node, *, skip_noise=True):
    parts = []

    def walk(cur):
        if cur.tag == '#text':
            parts.append(cur.text)
            return
        if skip_noise and (cur.tag in _NOISE_TAGS or _is_hidden(cur)):
            return
        for child in cur.children:
            walk(child)
        if cur.tag in {'p', 'div', 'section', 'article', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'tr'}:
            parts.append(' ')

    walk(node)
    return re.sub(r'\s+', ' ', html.unescape(''.join(parts))).strip()


def _inline_stats(node):
    """Return (text_len, link_text_len, p_count, punct_count) ignoring noise."""
    text_len = 0
    link_len = 0
    p_count = 0
    punct_count = 0

    def walk(cur, in_link=False):
        nonlocal text_len, link_len, p_count, punct_count
        if cur.tag == '#text':
            cleaned = re.sub(r'\s+', ' ', cur.text).strip()
            if cleaned:
                length = len(cleaned)
                text_len += length
                if in_link:
                    link_len += length
                punct_count += sum(cleaned.count(ch) for ch in '.。,，;；!！?？')
            return
        if cur.tag in _NOISE_TAGS or _is_hidden(cur):
            return
        if cur.tag == 'p':
            p_count += 1
        next_in_link = in_link or (cur.tag == 'a')
        for child in cur.children:
            walk(child, next_in_link)

    walk(node)
    return text_len, link_len, p_count, punct_count


def _select_main_container(root):
    body = _find_first(root, {'body'}) or root
    candidates = []

    def collect(node):
        if node.tag == '#text' or node.tag in _NOISE_TAGS or _is_hidden(node):
            return
        if node.tag in {'article', 'main', 'section', 'div'} or node.attrs.get('role') == 'main':
            text_len, link_len, p_count, punct_count = _inline_stats(node)
            if text_len >= 40:
                score = float(text_len + p_count * 60 + punct_count * 12)
                if node.tag == 'article':
                    score += 400.0
                elif node.tag == 'main' or node.attrs.get('role') == 'main':
                    score += 300.0
                hint = f"{node.attrs.get('id', '')} {node.attrs.get('class', '')}"
                if _POSITIVE_HINT_RE.search(hint):
                    score += 200.0
                if _NEGATIVE_HINT_RE.search(hint):
                    score -= 500.0
                ratio = link_len / max(text_len, 1)
                if ratio > 0.55:
                    score *= max(0.15, 1.0 - ratio)
                candidates.append((score, text_len, node))
        for child in node.children:
            collect(child)

    collect(body)
    if not candidates:
        return body
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    best = candidates[0][2]
    best_len = candidates[0][1]
    body_len, _, _, _ = _inline_stats(body)
    if best_len < 40 or (candidates[0][0] < 0 and body_len > best_len):
        return body
    return best


def _extract_pre_text(node):
    parts = []

    def walk(cur):
        if cur.tag == '#text':
            parts.append(cur.text)
            return
        if cur.tag == 'br':
            parts.append('\n')
            return
        for child in cur.children:
            walk(child)
        if cur.tag in {'p', 'div', 'li'}:
            parts.append('\n')

    walk(node)
    return html.unescape(''.join(parts)).strip('\n')


def _render_container(container, base_url):
    """Render both clean Markdown and plain text from a DOM subtree."""
    md_lines = []
    txt_lines = []

    def inline(cur, *, markdown_mode=True):
        if cur.tag == '#text':
            return html.unescape(cur.text)
        if cur.tag in _NOISE_TAGS or _is_hidden(cur):
            return ''
        if cur.tag == 'br':
            return '\n'
        inner = ''.join(inline(child, markdown_mode=markdown_mode) for child in cur.children)
        if not markdown_mode:
            return inner
        if cur.tag in {'strong', 'b'}:
            text = inner.strip()
            return f'**{text}**' if text else ''
        if cur.tag in {'em', 'i'}:
            text = inner.strip()
            return f'*{text}*' if text else ''
        if cur.tag == 'code':
            text = inner.strip()
            return f'`{text}`' if text else ''
        if cur.tag == 'a':
            label = re.sub(r'\s+', ' ', inner).strip()
            href = (cur.attrs.get('href') or '').strip()
            if not label:
                return ''
            resolved = urljoin(base_url, href) if href else ''
            try:
                scheme = urlsplit(resolved).scheme
            except ValueError:
                scheme = ''
            if scheme in {'http', 'https'}:
                return f'[{label}]({resolved})'
            return label
        return inner

    def clean_inline(text):
        return re.sub(r'[ \t\r\f\v\u00a0\u202f]+', ' ', text).strip()

    def walk_blocks(node, *, quote=False):
        if node.tag == '#text':
            val = clean_inline(html.unescape(node.text))
            if val:
                prefix = '> ' if quote else ''
                md_lines.append(prefix + val)
                txt_lines.append(val)
            return
        if node.tag in _NOISE_TAGS or _is_hidden(node):
            return
        if node.tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            val_md = clean_inline(inline(node, markdown_mode=True))
            val_txt = clean_inline(inline(node, markdown_mode=False))
            if val_txt:
                level = int(node.tag[1])
                md_lines.append('')
                md_lines.append(('> ' if quote else '') + '#' * level + ' ' + val_md)
                md_lines.append('')
                txt_lines.append('')
                txt_lines.append(val_txt)
                txt_lines.append('')
            return
        if node.tag == 'pre':
            code_text = _extract_pre_text(node)
            if code_text:
                md_lines.extend(['', '```', code_text, '```', ''])
                txt_lines.extend(['', code_text, ''])
            return
        if node.tag == 'blockquote':
            md_lines.append('')
            txt_lines.append('')
            for child in node.children:
                walk_blocks(child, quote=True)
            md_lines.append('')
            txt_lines.append('')
            return
        if node.tag in {'ul', 'ol'}:
            md_lines.append('')
            txt_lines.append('')
            ordered = node.tag == 'ol'
            item_no = 0
            for child in node.children:
                if child.tag == 'li':
                    item_no += 1
                    bullet = f'{item_no}. ' if ordered else '- '
                    val_md = clean_inline(inline(child, markdown_mode=True))
                    val_txt = clean_inline(inline(child, markdown_mode=False))
                    if val_txt:
                        md_lines.append(('> ' if quote else '') + bullet + val_md)
                        txt_lines.append(bullet + val_txt)
            md_lines.append('')
            txt_lines.append('')
            return
        if node.tag == 'hr':
            md_lines.extend(['', '---', ''])
            return
        if node.tag == 'p' or node.tag == 'figcaption':
            has_nested_block = any(c.tag in {'p', 'div', 'ul', 'ol', 'pre', 'blockquote'} for c in node.children)
            if not has_nested_block:
                val_md = clean_inline(inline(node, markdown_mode=True))
                val_txt = clean_inline(inline(node, markdown_mode=False))
                if val_txt:
                    prefix = '> ' if quote else ''
                    md_lines.extend(['', prefix + val_md, ''])
                    txt_lines.extend(['', val_txt, ''])
                return
        for child in node.children:
            walk_blocks(child, quote=quote)

    walk_blocks(container)
    md_text = re.sub(r'\n{3,}', '\n\n', '\n'.join(md_lines)).strip()
    plain_text = re.sub(r'\n{3,}', '\n\n', '\n'.join(txt_lines)).strip()
    if not plain_text:
        plain_text = _plain_node_text(container)
        md_text = md_text or plain_text
    return md_text, plain_text


def _parse_json_ld(blocks):
    found = {}
    for raw in blocks:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get('@graph', [data]) if isinstance(data, dict) else []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            if not found.get('headline') and isinstance(entry.get('headline'), str):
                found['headline'] = entry['headline'].strip()
            if not found.get('description') and isinstance(entry.get('description'), str):
                found['description'] = entry['description'].strip()
            if not found.get('datePublished') and isinstance(entry.get('datePublished'), str):
                found['datePublished'] = entry['datePublished'].strip()
            if not found.get('author'):
                author = entry.get('author')
                if isinstance(author, list) and author:
                    author = author[0]
                if isinstance(author, dict) and isinstance(author.get('name'), str):
                    found['author'] = author['name'].strip()
                elif isinstance(author, str):
                    found['author'] = author.strip()
    return found


def _js_string_var(raw_html, name):
    match = re.search(rf'\bvar\s+{re.escape(name)}\s*=\s*"([^"]*)"', raw_html or '')
    return html.unescape(match.group(1)).strip() if match else ''


def extract_html_article(raw_html, url, *, wechat=False):
    parser = _DOMParser()
    parser.feed(raw_html or '')
    parser.close()

    ld = _parse_json_ld(parser.json_ld)
    doc_title = re.sub(r'\s+', ' ', html.unescape(''.join(parser.title_parts))).strip()

    if wechat:
        body_node = _find_by_id(parser.root, 'js_content')
        full_text = _plain_node_text(parser.root, skip_noise=True)
        if body_node is None or not _plain_node_text(body_node):
            if _WECHAT_DELETED_RE.search(full_text) or _WECHAT_DELETED_RE.search(raw_html or ''):
                raise RuntimeError('This WeChat article has been deleted, reported, or migrated.')
            if _WECHAT_GATE_RE.search(full_text) or _WECHAT_GATE_RE.search(raw_html or ''):
                raise WeChatGateError('WeChat requested browser verification or client opening.')
            raise ValueError('Could not find #js_content in WeChat article.')

        title_node = _find_by_id(parser.root, 'activity-name')
        author_node = _find_by_id(parser.root, 'js_name')
        pub_node = _find_by_id(parser.root, 'publish_time')

        title = (
            (_plain_node_text(title_node) if title_node else '')
            or parser.meta.get('og:title')
            or parser.meta.get('twitter:title')
            or _js_string_var(raw_html, 'msg_title')
            or doc_title
            or 'WeChat Article'
        )
        author = (
            (_plain_node_text(author_node) if author_node else '')
            or _js_string_var(raw_html, 'nickname')
            or parser.meta.get('author')
            or None
        )
        published_at = (_plain_node_text(pub_node) if pub_node else '') or None
        if not published_at:
            ct_match = re.search(r'\bvar\s+ct\s*=\s*"?([0-9]{9,12})"?', raw_html or '')
            if ct_match:
                try:
                    published_at = datetime.fromtimestamp(int(ct_match.group(1)), timezone.utc).strftime('%Y-%m-%d')
                except (ValueError, OverflowError, OSError):
                    published_at = None

        markdown_body, plain_body = _render_container(body_node, url)
        text = plain_body.strip()
        if len(text) < 20:
            raise ValueError('The WeChat article contained no readable body text.')
        canonical = parser.canonical_url or url
        description = parser.meta.get('description') or parser.meta.get('og:description') or None
        return {
            'title': title,
            'author': author,
            'published_at': published_at,
            'description': description if description and description != text else None,
            'canonical_url': canonical,
            'text': text,
            'markdown': markdown_body,
        }

    h1_node = _find_first(parser.root, {'h1'})
    h1_text = _plain_node_text(h1_node) if h1_node else ''
    title = (
        parser.meta.get('og:title')
        or ld.get('headline')
        or parser.meta.get('twitter:title')
        or doc_title
        or h1_text
        or urlsplit(url).hostname
        or 'Web Article'
    )
    author = parser.meta.get('author') or parser.meta.get('article:author') or ld.get('author') or None
    time_node = _find_first(parser.root, {'time'})
    published_at = (
        parser.meta.get('article:published_time')
        or ld.get('datePublished')
        or (time_node.attrs.get('datetime') or _plain_node_text(time_node) if time_node else None)
    ) or None
    description = parser.meta.get('description') or parser.meta.get('og:description') or ld.get('description') or None

    container = _select_main_container(parser.root)
    markdown_body, plain_body = _render_container(container, url)
    text = plain_body.strip()
    if len(text) < 40 or (len(text) < 120 and _BOT_SHELL_RE.search(text)):
        raise ValueError('Could not extract readable article text; the page may require interactive browser rendering.')
    canonical = urljoin(url, parser.canonical_url) if parser.canonical_url else url
    return {
        'title': title,
        'author': author,
        'published_at': published_at,
        'description': description,
        'canonical_url': canonical,
        'text': text,
        'markdown': markdown_body,
    }


def _plain_text_article(body, url, mime):
    text = (body or '').replace('\r\n', '\n').replace('\r', '\n').strip()
    if len(text.strip()) < 10:
        raise ValueError('The document contained no readable text.')
    title = None
    if mime in {'text/markdown', 'text/x-markdown'}:
        for line in text.splitlines():
            match = re.match(r'^#\s+(.+)$', line.strip())
            if match:
                title = match.group(1).strip()
                break
    if not title:
        name = Path(urlsplit(url).path).name
        title = name or urlsplit(url).hostname or 'Document'
    return {
        'title': title,
        'author': None,
        'published_at': None,
        'description': None,
        'canonical_url': url,
        'text': text,
        'markdown': text if mime in {'text/markdown', 'text/x-markdown'} else None,
    }


def read_article(url, *, opener=None, resolve_dns=None):
    fetched = fetch_web_document(url, opener=opener, resolve_dns=resolve_dns)
    final_url = fetched['final_url']
    if fetched['content_type'] in {'text/plain', 'text/markdown', 'text/x-markdown'}:
        parsed = _plain_text_article(fetched['text'], final_url, fetched['content_type'])
        basis = 'direct_http_plain_text'
    else:
        parsed = extract_html_article(fetched['text'], final_url, wechat=False)
        basis = 'direct_http_article_dom'
    text = parsed['text']
    return {
        'kind': 'article',
        'site': urlsplit(final_url).hostname or urlsplit(url).hostname or 'Web',
        'url': url,
        'final_url': final_url,
        'canonical_url': parsed['canonical_url'],
        'reference': reference(final_url),
        'title': parsed['title'],
        'author': parsed['author'],
        'published_at': parsed['published_at'],
        'description': parsed['description'],
        'text': text,
        'markdown': parsed['markdown'],
        'content_complete': True,
        'content_basis': basis,
        'acquisition': 'direct_http_fetch',
        'limitations': ['Direct HTTP fetch only; dynamically loaded script content or linked documents were not executed.'],
    }


def read_wechat_http(url, *, opener=None, resolve_dns=None):
    canonical, _ = canonical_wechat_url(url)
    fetched = fetch_web_document(canonical, opener=opener, resolve_dns=resolve_dns)
    if fetched['content_type'] not in {'text/html', 'application/xhtml+xml'}:
        raise WeChatGateError('WeChat returned a non-HTML response; browser verification may be required.')
    parsed = extract_html_article(fetched['text'], fetched['final_url'], wechat=True)
    return {
        'kind': 'article',
        'site': 'WeChat',
        'url': canonical,
        'final_url': fetched['final_url'],
        'canonical_url': parsed['canonical_url'],
        'reference': canonical,
        'title': parsed['title'],
        'author': parsed['author'],
        'published_at': parsed['published_at'],
        'description': parsed['description'],
        'text': parsed['text'],
        'markdown': parsed['markdown'],
        'content_complete': True,
        'content_basis': 'direct_http_wechat_article_dom',
        'acquisition': 'direct_http_fetch',
        'limitations': ['Direct HTTP WeChat fetch; dynamically lazy-loaded images or comments were not captured.'],
    }
