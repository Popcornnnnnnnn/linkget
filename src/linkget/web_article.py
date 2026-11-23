"""Stdlib-only general web article & WeChat Official Account HTTP extractor with SSRF protection."""
from codecs import lookup as lookup_codec
from datetime import datetime, timezone
import functools
import html
from html.parser import HTMLParser
import http.client
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPHandler, HTTPRedirectHandler, HTTPSHandler, Request, build_opener, getproxies
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


_FAKE_IP_DNS_NET = ipaddress.ip_network('198.18.0.0/15')


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


def _is_fake_ip_dns_address(addr_text):
    """Identify RFC 2544 198.18.0.0/15 returned by local Fake-IP TUN DNS proxies."""
    try:
        ip = ipaddress.ip_address(addr_text.split('%', 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return isinstance(ip, ipaddress.IPv4Address) and ip in _FAKE_IP_DNS_NET


@functools.lru_cache(maxsize=1)
def _fake_ip_tun_active():
    if sys.platform != 'darwin':
        return False
    try:
        proc = subprocess.run(
            ['/sbin/route', '-n', 'get', '198.18.0.1'],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return proc.returncode == 0 and 'interface: utun' in (proc.stdout or '').lower()
    except (OSError, subprocess.SubprocessError):
        return False


def _fake_ip_proxy_active():
    if any(k != 'no' and v for k, v in getproxies().items()):
        return True
    return _fake_ip_tun_active()


def _validate_and_resolve_public_url(url, *, resolve_dns=True):
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
        return url, None
    try:
        packed_ip = socket.inet_aton(host)
        canonical_ip = socket.inet_ntoa(packed_ip)
    except OSError:
        canonical_ip = None
    if canonical_ip is not None:
        if not _is_public_ip(canonical_ip):
            raise ValueError('Refusing private, loopback, or link-local IP address.')
        return url, None
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
        allow_fake_ip = _fake_ip_proxy_active()
        for info in infos:
            sockaddr = info[4]
            if not sockaddr or not (_is_public_ip(sockaddr[0]) or (allow_fake_ip and _is_fake_ip_dns_address(sockaddr[0]))):
                raise ValueError('Refusing host resolving to private, loopback, or non-global address.')
        return url, (host, target_port, infos)
    return url, None


def validate_public_web_url(url, *, resolve_dns=True):
    checked, _ = _validate_and_resolve_public_url(url, resolve_dns=resolve_dns)
    return checked


def _pinned_create_connection(pinned_map, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    host, port = address
    key = (str(host).lower().rstrip('.'), int(port))
    infos = pinned_map.get(key)
    if not infos:
        return socket.create_connection(address, timeout, source_address)
    last_err = None
    for family, socktype, proto, _, sockaddr in infos:
        sock = None
        try:
            sock = socket.socket(family, socktype, proto)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(sockaddr)
            return sock
        except OSError as err:
            last_err = err
            if sock is not None:
                sock.close()
    if last_err is not None:
        raise last_err
    raise OSError(f'No pinned addresses available for {host}:{port}')


class _PinnedHTTPHandler(HTTPHandler):
    def __init__(self, pinned_map):
        super().__init__()
        self._pinned_map = pinned_map

    def http_open(self, req):
        pinned_map = self._pinned_map

        class _Conn(http.client.HTTPConnection):
            def _create_connection(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
                if getattr(self, '_tunnel_host', None):
                    return socket.create_connection(address, timeout, source_address)
                return _pinned_create_connection(pinned_map, address, timeout, source_address)

        return self.do_open(_Conn, req)


class _PinnedHTTPSHandler(HTTPSHandler):
    def __init__(self, pinned_map):
        super().__init__()
        self._pinned_map = pinned_map

    def https_open(self, req):
        pinned_map = self._pinned_map

        class _Conn(http.client.HTTPSConnection):
            def _create_connection(self, address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
                if getattr(self, '_tunnel_host', None):
                    return socket.create_connection(address, timeout, source_address)
                return _pinned_create_connection(pinned_map, address, timeout, source_address)

        return self.do_open(_Conn, req, context=self._context)


class _SafeRedirectHandler(HTTPRedirectHandler):
    def __init__(self, *, resolve_dns=True, pinned_map=None):
        self.resolve_dns = resolve_dns
        self.pinned_map = pinned_map if pinned_map is not None else {}

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        count = getattr(req, '_linkget_redirect_count', 0) + 1
        if count > MAX_REDIRECTS:
            if fp is not None:
                fp.close()
            raise ValueError('Too many HTTP redirects while fetching web article.')
        try:
            _, resolved = _validate_and_resolve_public_url(newurl, resolve_dns=self.resolve_dns)
            if resolved is not None:
                rhost, rport, infos = resolved
                self.pinned_map[(rhost, rport)] = infos
        except Exception:
            if fp is not None:
                fp.close()
            raise
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is not None:
            new_req._linkget_redirect_count = count
        return new_req


def build_safe_opener(*, resolve_dns=True, initial_resolved=None):
    pinned_map = {}
    if initial_resolved is not None:
        rhost, rport, infos = initial_resolved
        pinned_map[(rhost, rport)] = infos
    return build_opener(
        _SafeRedirectHandler(resolve_dns=resolve_dns, pinned_map=pinned_map),
        _PinnedHTTPHandler(pinned_map),
        _PinnedHTTPSHandler(pinned_map),
    )


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
    _, resolved = _validate_and_resolve_public_url(url, resolve_dns=should_resolve)
    req = Request(
        url,
        headers={
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,text/plain,text/markdown;q=0.9,*/*;q=0.1',
            'Accept-Encoding': 'gzip, deflate',
            'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7',
        },
    )
    active_opener = opener if opener is not None else build_safe_opener(resolve_dns=should_resolve, initial_resolved=resolved)
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
        self.author_url = None
        self.lang = None
        self.json_ld = []
        self._in_json_ld = False
        self._json_ld_parts = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        attr_map = {k.lower(): (v if v is not None else '') for k, v in attrs if k}
        if tag == 'html':
            lang = attr_map.get('lang', '').strip()
            if lang and not self.lang:
                self.lang = lang
        elif tag == 'title':
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
            if 'author' in rels and attr_map.get('href') and not self.author_url:
                self.author_url = attr_map['href'].strip()
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


def _safe_resolved_http_url(raw, base_url):
    if not isinstance(raw, str):
        return None
    candidate = raw.strip()
    if not candidate or candidate.startswith(('data:', 'javascript:', 'vbscript:', '#')):
        return None
    resolved = urljoin(base_url, candidate)
    try:
        parsed = urlsplit(resolved)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {'http', 'https'} or not parsed.hostname:
        return None
    return resolved


def _parse_srcset_best(srcset_value, base_url):
    if not isinstance(srcset_value, str) or not srcset_value.strip():
        return None
    best_url = None
    best_score = -1.0
    for part in srcset_value.split(','):
        tokens = part.strip().split()
        if not tokens:
            continue
        url = _safe_resolved_http_url(tokens[0], base_url)
        if not url:
            continue
        score = 1.0
        if len(tokens) > 1:
            desc = tokens[1].lower()
            if desc.endswith('w') and desc[:-1].isdigit():
                score = float(desc[:-1])
            elif desc.endswith('x'):
                try:
                    score = float(desc[:-1]) * 1000.0
                except ValueError:
                    score = 1.0
        if score > best_score:
            best_score = score
            best_url = url
    return best_url


def _is_tracking_pixel(attrs):
    try:
        w = int(str(attrs.get('width', '')).strip().rstrip('px'))
        h = int(str(attrs.get('height', '')).strip().rstrip('px'))
        if 0 <= w <= 2 and 0 <= h <= 2:
            return True
    except (TypeError, ValueError):
        pass
    return False


def _best_image_url(node, base_url):
    if _is_tracking_pixel(node.attrs):
        return None
    for attr in ('data-srcset', 'srcset'):
        found = _parse_srcset_best(node.attrs.get(attr), base_url)
        if found:
            return found
    for attr in ('data-src', 'data-original', 'data-actualsrc', 'data-lazy-src', 'data-url'):
        found = _safe_resolved_http_url(node.attrs.get(attr), base_url)
        if found:
            return found
    return _safe_resolved_http_url(node.attrs.get('src'), base_url)


def _picture_url(picture_node, base_url):
    img = _find_first(picture_node, {'img'})
    alt = (img.attrs.get('alt') or '').strip() if img is not None else ''
    for child in picture_node.children:
        if child.tag == 'source':
            found = _parse_srcset_best(child.attrs.get('srcset') or child.attrs.get('data-srcset'), base_url)
            if found:
                return found, alt
    if img is not None:
        return _best_image_url(img, base_url), alt
    return None, ''


def _code_language(pre_node):
    candidates = [pre_node.attrs.get('data-lang', ''), pre_node.attrs.get('class', '')]
    for child in pre_node.children:
        if child.tag == 'code':
            candidates.append(child.attrs.get('data-lang', ''))
            candidates.append(child.attrs.get('class', ''))
    for raw in candidates:
        m = re.search(r'\b(?:language|lang)-([A-Za-z0-9_+.#-]+)\b', str(raw), re.I)
        if m:
            return m.group(1)
        if raw and re.fullmatch(r'[A-Za-z0-9_+.#-]{1,32}', str(raw).strip()) and ' ' not in str(raw).strip():
            # Only treat bare token if it came from data-lang
            pass
    explicit = (pre_node.attrs.get('data-lang') or '').strip()
    if explicit and re.fullmatch(r'[A-Za-z0-9_+.#-]{1,32}', explicit):
        return explicit
    return ''


def _wrap_md_inline(text, marker):
    if not text or not text.strip():
        return text
    lead_len = len(text) - len(text.lstrip())
    trail_len = len(text) - len(text.rstrip())
    lead = text[:lead_len]
    trail = text[len(text) - trail_len:] if trail_len else ''
    core = text[lead_len:len(text) - trail_len if trail_len else len(text)]
    return f'{lead}{marker}{core}{marker}{trail}'


def _render_container_rich(container, base_url, *, asset_map=None):
    """Render clean Markdown, plain text, sanitized HTML body, and media manifest."""
    md_lines = []
    txt_lines = []
    html_parts = []
    media_items = []
    seen_media = set()

    def resolve_asset(url):
        if asset_map and url in asset_map:
            return asset_map[url]
        return url

    def record_media(item):
        url = item.get('original_url')
        if not url or url in seen_media:
            return
        seen_media.add(url)
        media_items.append(item)

    def clean_inline(text):
        return re.sub(r'[ \t\r\f\v\u00a0\u202f]+', ' ', text).strip()

    def inline(cur, *, mode='md'):
        # mode in {'md', 'txt', 'html'}
        if cur.tag == '#text':
            raw = html.unescape(cur.text)
            return html.escape(raw, quote=False) if mode == 'html' else raw
        if cur.tag in _NOISE_TAGS or _is_hidden(cur):
            return ''
        if cur.tag == 'br':
            return '<br>' if mode == 'html' else '\n'
        if cur.tag == 'img':
            orig = _best_image_url(cur, base_url)
            if not orig:
                return ''
            alt = re.sub(r'\s+', ' ', (cur.attrs.get('alt') or '').strip())
            record_media({'role': 'inline', 'media_type': 'image', 'original_url': orig, 'alt': alt or None})
            src = resolve_asset(orig)
            if mode == 'md':
                safe_alt = alt.replace('[', '\\[').replace(']', '\\]')
                return f'![{safe_alt}]({src})'
            if mode == 'html':
                return f'<img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}" loading="lazy">'
            return ''
        if cur.tag == 'picture':
            res = _picture_url(cur, base_url)
            if isinstance(res, tuple):
                orig, alt = res
            else:
                orig, alt = res, ''
            if not orig:
                return ''
            record_media({'role': 'inline', 'media_type': 'image', 'original_url': orig, 'alt': alt or None})
            src = resolve_asset(orig)
            if mode == 'md':
                return f'![{alt}]({src})'
            if mode == 'html':
                return f'<img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}" loading="lazy">'
            return ''

        inner = ''.join(inline(child, mode=mode) for child in cur.children)
        if mode == 'txt':
            return inner
        if cur.tag in {'strong', 'b'}:
            if mode == 'html':
                return f'<strong>{inner}</strong>' if inner.strip() else ''
            return _wrap_md_inline(inner, '**')
        if cur.tag in {'em', 'i'}:
            if mode == 'html':
                return f'<em>{inner}</em>' if inner.strip() else ''
            return _wrap_md_inline(inner, '*')
        if cur.tag in {'del', 's', 'strike'}:
            if mode == 'html':
                return f'<del>{inner}</del>' if inner.strip() else ''
            return _wrap_md_inline(inner, '~~')
        if cur.tag == 'mark':
            if mode == 'html':
                return f'<mark>{inner}</mark>' if inner.strip() else ''
            return _wrap_md_inline(inner, '==')
        if cur.tag in {'sup', 'sub'}:
            if mode == 'html':
                return f'<{cur.tag}>{inner}</{cur.tag}>' if inner.strip() else ''
            return f'<{cur.tag}>{inner.strip()}</{cur.tag}>' if inner.strip() else ''
        if cur.tag == 'code':
            if mode == 'html':
                return f'<code>{inner}</code>' if inner.strip() else ''
            text = inner.strip()
            if not text:
                return ''
            fence = '``' if '`' in text else '`'
            pad = ' ' if (text.startswith('`') or text.endswith('`')) else ''
            return f'{fence}{pad}{text}{pad}{fence}'
        if cur.tag == 'a':
            href = (cur.attrs.get('href') or '').strip()
            resolved = _safe_resolved_http_url(href, base_url)
            if mode == 'html':
                if not inner.strip():
                    return ''
                if resolved:
                    return f'<a href="{html.escape(resolved, quote=True)}" target="_blank" rel="noopener noreferrer">{inner}</a>'
                return inner
            label = re.sub(r'\s+', ' ', inner).strip()
            if not label:
                return ''
            if resolved:
                return f'[{label}]({resolved})'
            return label
        return inner

    def extract_video(node, caption=None):
        vurl = _safe_resolved_http_url(node.attrs.get('src'), base_url)
        if not vurl:
            for child in node.children:
                if child.tag == 'source':
                    vurl = _safe_resolved_http_url(child.attrs.get('src'), base_url)
                    if vurl:
                        break
        if not vurl:
            return False
        poster_orig = _safe_resolved_http_url(node.attrs.get('poster'), base_url)
        record_media({
            'role': 'video',
            'media_type': 'video',
            'original_url': vurl,
            'poster_url': poster_orig,
            'caption': caption,
        })
        src = resolve_asset(vurl)
        poster_src = resolve_asset(poster_orig) if poster_orig else None
        label = caption or 'Video'
        md_lines.extend(['', f'[Video: {label}]({src})'])
        if poster_src:
            md_lines.append(f'![{label} poster]({poster_src})')
        md_lines.append('')
        poster_attr = f' poster="{html.escape(poster_src, quote=True)}"' if poster_src else ''
        fig_cap = f'<figcaption>{html.escape(caption)}</figcaption>' if caption else ''
        html_parts.append(
            f'<figure class="media-video"><video controls preload="metadata"{poster_attr}>'
            f'<source src="{html.escape(src, quote=True)}"></video>{fig_cap}</figure>'
        )
        if caption:
            txt_lines.extend(['', caption, ''])
        return True

    def render_table(table_node):
        rows = []

        def collect_rows(cur, in_head=False):
            if cur.tag in _NOISE_TAGS or _is_hidden(cur):
                return
            if cur.tag == 'tr':
                cells = []
                row_is_header = in_head
                for cell in cur.children:
                    if cell.tag in {'th', 'td'}:
                        if cell.tag == 'th':
                            row_is_header = True
                        cells.append((
                            cell.tag,
                            clean_inline(inline(cell, mode='md')),
                            clean_inline(inline(cell, mode='txt')),
                            inline(cell, mode='html').strip(),
                        ))
                if cells:
                    rows.append((row_is_header, cells))
                return
            next_head = in_head or (cur.tag == 'thead')
            for child in cur.children:
                collect_rows(child, in_head=next_head)

        collect_rows(table_node)
        if not rows:
            return
        col_count = max(len(cells) for _, cells in rows)
        if col_count == 0:
            return

        md_table = []
        txt_table = []
        html_rows_head = []
        html_rows_body = []

        def fmt_md_cell(val):
            return val.replace('|', '\\|').replace('\n', ' ')

        for idx, (is_hdr, cells) in enumerate(rows):
            padded = list(cells) + [('td', '', '', '')] * (col_count - len(cells))
            md_row = '| ' + ' | '.join(fmt_md_cell(c[1]) for c in padded) + ' |'
            md_table.append(md_row)
            if idx == 0:
                md_table.append('| ' + ' | '.join('---' for _ in range(col_count)) + ' |')
            txt_cells = [c[2] for c in padded if c[2]]
            if txt_cells:
                txt_table.append(' | '.join(txt_cells))
            row_html = '<tr>' + ''.join(
                f'<{"th" if (is_hdr or c[0] == "th") else "td"}>{c[3]}</{"th" if (is_hdr or c[0] == "th") else "td"}>'
                for c in padded
            ) + '</tr>'
            if is_hdr and idx == 0:
                html_rows_head.append(row_html)
            else:
                html_rows_body.append(row_html)

        md_lines.extend(['', *md_table, ''])
        txt_lines.extend(['', *txt_table, ''])
        thead_html = f'<thead>{"".join(html_rows_head)}</thead>' if html_rows_head else ''
        tbody_html = f'<tbody>{"".join(html_rows_body)}</tbody>' if html_rows_body else ''
        html_parts.append(f'<div class="table-wrap"><table>{thead_html}{tbody_html}</table></div>')

    def walk_blocks(node, *, quote=False, list_depth=0):
        if node.tag == '#text':
            raw = html.unescape(node.text)
            val = clean_inline(raw)
            if val:
                prefix = '> ' if quote else ''
                md_lines.append(prefix + val)
                txt_lines.append(val)
                html_parts.append(f'<p>{html.escape(val, quote=False)}</p>')
            return
        if node.tag in _NOISE_TAGS or _is_hidden(node):
            return
        if node.tag == 'img':
            orig = _best_image_url(node, base_url)
            if not orig:
                return
            alt = re.sub(r'\s+', ' ', (node.attrs.get('alt') or '').strip())
            record_media({'role': 'inline', 'media_type': 'image', 'original_url': orig, 'alt': alt or None})
            src = resolve_asset(orig)
            prefix = '> ' if quote else ''
            safe_alt = alt.replace('[', '\\[').replace(']', '\\]')
            md_lines.extend(['', f'{prefix}![{safe_alt}]({src})', ''])
            html_parts.append(f'<figure class="media-image"><img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}" loading="lazy"></figure>')
            return
        if node.tag == 'picture':
            res = _picture_url(node, base_url)
            orig, alt = res if isinstance(res, tuple) else (res, '')
            if orig:
                record_media({'role': 'inline', 'media_type': 'image', 'original_url': orig, 'alt': alt or None})
                src = resolve_asset(orig)
                prefix = '> ' if quote else ''
                md_lines.extend(['', f'{prefix}![{alt}]({src})', ''])
                html_parts.append(f'<figure class="media-image"><img src="{html.escape(src, quote=True)}" alt="{html.escape(alt, quote=True)}" loading="lazy"></figure>')
            return
        if node.tag == 'video':
            extract_video(node)
            return
        if node.tag == 'figure':
            cap_node = _find_first(node, {'figcaption'})
            cap_md = clean_inline(inline(cap_node, mode='md')) if cap_node else ''
            cap_txt = clean_inline(inline(cap_node, mode='txt')) if cap_node else ''
            cap_html = inline(cap_node, mode='html').strip() if cap_node else ''
            vid_node = _find_first(node, {'video'})
            if vid_node is not None and extract_video(vid_node, caption=cap_txt or None):
                return
            img_node = _find_first(node, {'img'})
            pic_node = _find_first(node, {'picture'})
            orig, alt = (None, '')
            if pic_node is not None:
                orig, alt = _picture_url(pic_node, base_url)
            elif img_node is not None:
                orig = _best_image_url(img_node, base_url)
                alt = re.sub(r'\s+', ' ', (img_node.attrs.get('alt') or '').strip())
            if orig:
                record_media({'role': 'inline', 'media_type': 'image', 'original_url': orig, 'alt': (alt or cap_txt) or None, 'caption': cap_txt or None})
                src = resolve_asset(orig)
                label = (alt or cap_txt).replace('[', '\\[').replace(']', '\\]')
                prefix = '> ' if quote else ''
                md_lines.extend(['', f'{prefix}![{label}]({src})'])
                if cap_md:
                    md_lines.append(f'{prefix}*{cap_md}*')
                md_lines.append('')
                if cap_txt:
                    txt_lines.extend(['', cap_txt, ''])
                fig_cap = f'<figcaption>{cap_html}</figcaption>' if cap_html else ''
                html_parts.append(
                    f'<figure class="media-image"><img src="{html.escape(src, quote=True)}" '
                    f'alt="{html.escape(alt or cap_txt, quote=True)}" loading="lazy">{fig_cap}</figure>'
                )
                return
            for child in node.children:
                walk_blocks(child, quote=quote, list_depth=list_depth)
            return
        if node.tag == 'table':
            render_table(node)
            return
        if node.tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            val_md = clean_inline(inline(node, mode='md'))
            val_txt = clean_inline(inline(node, mode='txt'))
            val_html = inline(node, mode='html').strip()
            if val_txt:
                level = int(node.tag[1])
                md_lines.extend(['', ('> ' if quote else '') + '#' * level + ' ' + val_md, ''])
                txt_lines.extend(['', val_txt, ''])
                html_parts.append(f'<h{level}>{val_html}</h{level}>')
            return
        if node.tag == 'pre':
            code_text = _extract_pre_text(node)
            if code_text:
                lang = _code_language(node)
                md_lines.extend(['', f'```{lang}', code_text, '```', ''])
                txt_lines.extend(['', code_text, ''])
                cls = f' class="language-{html.escape(lang, quote=True)}"' if lang else ''
                html_parts.append(f'<pre><code{cls}>{html.escape(code_text, quote=False)}</code></pre>')
            return
        if node.tag == 'blockquote':
            md_lines.append('')
            txt_lines.append('')
            html_parts.append('<blockquote>')
            for child in node.children:
                walk_blocks(child, quote=True, list_depth=list_depth)
            html_parts.append('</blockquote>')
            md_lines.append('')
            txt_lines.append('')
            return
        if node.tag in {'ul', 'ol'}:
            md_lines.append('')
            txt_lines.append('')
            ordered = node.tag == 'ol'
            tag_name = 'ol' if ordered else 'ul'
            html_parts.append(f'<{tag_name}>')
            item_no = 0
            indent = '  ' * list_depth
            for child in node.children:
                if child.tag == 'li':
                    item_no += 1
                    bullet = f'{item_no}. ' if ordered else '- '
                    nested_lists = [c for c in child.children if c.tag in {'ul', 'ol'}]
                    # Temporarily render non-nested inline children of <li>
                    inline_copy = _Node('li', child.attrs)
                    inline_copy.children = [c for c in child.children if c.tag not in {'ul', 'ol'}]
                    val_md = clean_inline(inline(inline_copy, mode='md'))
                    val_txt = clean_inline(inline(inline_copy, mode='txt'))
                    val_html = inline(inline_copy, mode='html').strip()
                    if val_md or val_txt:
                        md_lines.append(('> ' if quote else '') + indent + bullet + val_md)
                    if val_txt:
                        txt_lines.append(indent + bullet + val_txt)
                    html_parts.append(f'<li>{val_html}')
                    for sub in nested_lists:
                        walk_blocks(sub, quote=quote, list_depth=list_depth + 1)
                    html_parts.append('</li>')
            html_parts.append(f'</{tag_name}>')
            md_lines.append('')
            txt_lines.append('')
            return
        if node.tag == 'dl':
            md_lines.append('')
            txt_lines.append('')
            html_parts.append('<dl>')
            for child in node.children:
                if child.tag in {'dt', 'dd'}:
                    val_md = clean_inline(inline(child, mode='md'))
                    val_txt = clean_inline(inline(child, mode='txt'))
                    val_html = inline(child, mode='html').strip()
                    if val_md:
                        prefix = '**' if child.tag == 'dt' else ': '
                        suffix = '**' if child.tag == 'dt' else ''
                        md_lines.append(('> ' if quote else '') + f'{prefix}{val_md}{suffix}')
                    if val_txt:
                        txt_lines.append(val_txt)
                    if val_html:
                        html_parts.append(f'<{child.tag}>{val_html}</{child.tag}>')
            html_parts.append('</dl>')
            md_lines.append('')
            txt_lines.append('')
            return
        if node.tag == 'hr':
            md_lines.extend(['', '---', ''])
            html_parts.append('<hr>')
            return
        if node.tag in {'p', 'figcaption'}:
            has_nested_block = any(c.tag in {'p', 'div', 'ul', 'ol', 'pre', 'blockquote', 'table', 'figure'} for c in node.children)
            if not has_nested_block:
                val_md = clean_inline(inline(node, mode='md'))
                val_txt = clean_inline(inline(node, mode='txt'))
                val_html = inline(node, mode='html').strip()
                if val_md or val_txt or val_html:
                    prefix = '> ' if quote else ''
                    if val_md:
                        md_lines.extend(['', prefix + val_md, ''])
                    if val_txt:
                        txt_lines.extend(['', val_txt, ''])
                    if val_html:
                        html_parts.append(f'<p>{val_html}</p>')
                return
        for child in node.children:
            walk_blocks(child, quote=quote, list_depth=list_depth)

    walk_blocks(container)
    md_text = re.sub(r'\n{3,}', '\n\n', '\n'.join(md_lines)).strip()
    plain_text = re.sub(r'\n{3,}', '\n\n', '\n'.join(txt_lines)).strip()
    body_html = '\n'.join(html_parts).strip()
    if not plain_text:
        plain_text = _plain_node_text(container)
        md_text = md_text or plain_text
        if not body_html and plain_text:
            body_html = f'<p>{html.escape(plain_text, quote=False)}</p>'
    return md_text, plain_text, body_html, media_items


def _render_container(container, base_url):
    """Render both clean Markdown and plain text from a DOM subtree."""
    md_text, plain_text, _, _ = _render_container_rich(container, base_url)
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
            if not found.get('dateModified') and isinstance(entry.get('dateModified'), str):
                found['dateModified'] = entry['dateModified'].strip()
            if not found.get('inLanguage') and isinstance(entry.get('inLanguage'), str):
                found['inLanguage'] = entry['inLanguage'].strip()
            if not found.get('image'):
                img = entry.get('image')
                if isinstance(img, list) and img:
                    img = img[0]
                if isinstance(img, dict) and isinstance(img.get('url'), str):
                    found['image'] = img['url'].strip()
                elif isinstance(img, str):
                    found['image'] = img.strip()
            if not found.get('keywords'):
                kw = entry.get('keywords')
                if isinstance(kw, str) and kw.strip():
                    found['keywords'] = [part.strip() for part in kw.split(',') if part.strip()]
                elif isinstance(kw, list):
                    cleaned = [str(item).strip() for item in kw if isinstance(item, str) and item.strip()]
                    if cleaned:
                        found['keywords'] = cleaned
            if not found.get('publisher'):
                pub = entry.get('publisher')
                if isinstance(pub, dict) and isinstance(pub.get('name'), str):
                    found['publisher'] = pub['name'].strip()
            if not found.get('author'):
                author = entry.get('author')
                if isinstance(author, list) and author:
                    author = author[0]
                if isinstance(author, dict):
                    if isinstance(author.get('name'), str):
                        found['author'] = author['name'].strip()
                    if isinstance(author.get('url'), str) and not found.get('author_url'):
                        found['author_url'] = author['url'].strip()
                elif isinstance(author, str):
                    found['author'] = author.strip()
    return found


def _js_string_var(raw_html, name):
    match = re.search(rf'\bvar\s+{re.escape(name)}\s*=\s*"([^"]*)"', raw_html or '')
    return html.unescape(match.group(1)).strip() if match else ''


def _merge_cover_into_media(cover_image, media_items, title):
    items = list(media_items)
    if cover_image and not any(item.get('original_url') == cover_image for item in items):
        items.insert(0, {'role': 'cover', 'media_type': 'image', 'original_url': cover_image, 'alt': title})
    return items


def extract_html_article(raw_html, url, *, wechat=False, asset_map=None):
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

        cover_image = _safe_resolved_http_url(
            parser.meta.get('og:image') or parser.meta.get('twitter:image') or _js_string_var(raw_html, 'msg_cdn_url'),
            url,
        )
        markdown_body, plain_body, body_html, media_items = _render_container_rich(body_node, url, asset_map=asset_map)
        text = plain_body.strip()
        if len(text) < 20:
            raise ValueError('The WeChat article contained no readable body text.')
        canonical = parser.canonical_url or url
        description = parser.meta.get('description') or parser.meta.get('og:description') or None
        return {
            'title': title,
            'author': author,
            'author_url': None,
            'published_at': published_at,
            'modified_at': None,
            'description': description if description and description != text else None,
            'canonical_url': canonical,
            'site_name': parser.meta.get('og:site_name') or 'WeChat Official Account',
            'language': parser.lang or None,
            'keywords': None,
            'cover_image': cover_image,
            'media_items': _merge_cover_into_media(cover_image, media_items, title),
            'text': text,
            'markdown': markdown_body,
            'body_html': body_html,
            'raw_html': raw_html,
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
    author_url = _safe_resolved_http_url(parser.author_url or ld.get('author_url'), url)
    time_node = _find_first(parser.root, {'time'})
    published_at = (
        parser.meta.get('article:published_time')
        or ld.get('datePublished')
        or (time_node.attrs.get('datetime') or _plain_node_text(time_node) if time_node else None)
    ) or None
    modified_at = parser.meta.get('article:modified_time') or ld.get('dateModified') or None
    description = parser.meta.get('description') or parser.meta.get('og:description') or ld.get('description') or None
    site_name = parser.meta.get('og:site_name') or ld.get('publisher') or None
    language = parser.lang or ld.get('inLanguage') or None
    raw_kw = parser.meta.get('keywords') or parser.meta.get('article:tag')
    keywords = ld.get('keywords') or (
        [part.strip() for part in raw_kw.split(',') if part.strip()] if isinstance(raw_kw, str) and raw_kw.strip() else None
    )
    cover_image = _safe_resolved_http_url(
        parser.meta.get('og:image') or parser.meta.get('twitter:image') or ld.get('image'),
        url,
    )

    container = _select_main_container(parser.root)
    markdown_body, plain_body, body_html, media_items = _render_container_rich(container, url, asset_map=asset_map)
    text = plain_body.strip()
    if len(text) < 40 or (len(text) < 120 and _BOT_SHELL_RE.search(text)):
        raise ValueError('Could not extract readable article text; the page may require interactive browser rendering.')
    canonical = urljoin(url, parser.canonical_url) if parser.canonical_url else url
    return {
        'title': title,
        'author': author,
        'author_url': author_url,
        'published_at': published_at,
        'modified_at': modified_at,
        'description': description,
        'canonical_url': canonical,
        'site_name': site_name,
        'language': language,
        'keywords': keywords,
        'cover_image': cover_image,
        'media_items': _merge_cover_into_media(cover_image, media_items, title),
        'text': text,
        'markdown': markdown_body,
        'body_html': body_html,
        'raw_html': raw_html,
    }


def render_fragment(fragment_html, base_url, *, asset_map=None):
    """Parse and render a scoped HTML fragment into Markdown, plain text, body HTML, and media items."""
    wrapped = f'<!doctype html><html><body><article>{fragment_html or ""}</article></body></html>'
    parser = _DOMParser()
    parser.feed(wrapped)
    parser.close()
    container = _find_first(parser.root, {'article'}) or parser.root
    md_text, plain_text, body_html, media_items = _render_container_rich(container, base_url, asset_map=asset_map)
    return {
        'markdown': md_text,
        'text': plain_text,
        'body_html': body_html,
        'media_items': media_items,
    }


def rerender_html_article(raw_html, base_url, *, wechat=False, asset_map=None):
    """Re-render a stored HTML snapshot with local downloaded asset paths."""
    return extract_html_article(raw_html, base_url, wechat=wechat, asset_map=asset_map)


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
    fetched_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return {
        'kind': 'article',
        'site': urlsplit(final_url).hostname or urlsplit(url).hostname or 'Web',
        'url': url,
        'final_url': final_url,
        'canonical_url': parsed['canonical_url'],
        'reference': reference(final_url),
        'title': parsed['title'],
        'author': parsed['author'],
        'author_url': parsed.get('author_url'),
        'published_at': parsed['published_at'],
        'modified_at': parsed.get('modified_at'),
        'fetched_at': fetched_at,
        'description': parsed['description'],
        'site_name': parsed.get('site_name'),
        'language': parsed.get('language'),
        'keywords': parsed.get('keywords'),
        'cover_image': parsed.get('cover_image'),
        'media_items': parsed.get('media_items') or [],
        'text': text,
        'markdown': parsed['markdown'],
        'body_html': parsed.get('body_html'),
        'raw_html': parsed.get('raw_html'),
        'is_wechat': False,
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
    fetched_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    return {
        'kind': 'article',
        'site': 'WeChat',
        'url': canonical,
        'final_url': fetched['final_url'],
        'canonical_url': parsed['canonical_url'],
        'reference': canonical,
        'title': parsed['title'],
        'author': parsed['author'],
        'author_url': parsed.get('author_url'),
        'published_at': parsed['published_at'],
        'modified_at': parsed.get('modified_at'),
        'fetched_at': fetched_at,
        'description': parsed['description'],
        'site_name': parsed.get('site_name'),
        'language': parsed.get('language'),
        'keywords': parsed.get('keywords'),
        'cover_image': parsed.get('cover_image'),
        'media_items': parsed.get('media_items') or [],
        'text': parsed['text'],
        'markdown': parsed['markdown'],
        'body_html': parsed.get('body_html'),
        'raw_html': parsed.get('raw_html'),
        'is_wechat': True,
        'content_complete': True,
        'content_basis': 'direct_http_wechat_article_dom',
        'acquisition': 'direct_http_fetch',
        'limitations': ['Direct HTTP WeChat fetch; dynamically lazy-loaded comments were not captured.'],
    }
