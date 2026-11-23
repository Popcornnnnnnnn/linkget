"""Bounded content reads using an explicitly selected, authorized browser.

This MIT adapter implements the existing article/player DOM contract without
loading an extension, exporting credentials, or changing browser permissions.
It is an acquisition helper, not a source of HTTP transfer-completeness proof.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time
from urllib.parse import urlsplit

PUBLIC_READER_REVISION = 2
TOUTIAO_READER_REVISION = 4
WECHAT_READER_REVISION = 1
X_ARTICLE_READER_REVISION = 1
PUBLIC_ORIGINS = ('https://www.raspberrypi.com/*', 'https://zhuanlan.zhihu.com/*')
WECHAT_ORIGIN = 'https://mp.weixin.qq.com/*'
X_ORIGIN = 'https://x.com/*'
MAX_RESULT_BYTES = 256000
MAX_READ_SECONDS = 45


class BrowserUnavailable(RuntimeError):
    """The existing browser path cannot deliver a complete bounded read."""


class ReaderBusy(BrowserUnavailable):
    """Another local read owns the serialized rendered-page reader."""


def selection(backend=None, *, browser="auto", backend_source='command option', browser_source='command browser option'):
    """Explain selection without reading sessions, probing browsers or saving."""
    if __package__:
        from . import accounts
    else:
        import accounts
    if browser == 'none':
        value, source = 'none', 'browser disabled by command option'
    elif backend is not None:
        value, source = backend, backend_source
    elif browser != 'auto':
        value, source = browser, browser_source
    elif 'LINKGET_RENDERED_BACKEND' in os.environ:
        value, source = os.environ['LINKGET_RENDERED_BACKEND'], 'LINKGET_RENDERED_BACKEND'
    else:
        settings = accounts.preferences()
        value, source = settings.get('reader_browser', 'auto'), 'personal reader preference'
        if value == 'auto':
            value, source = settings.get('browser'), 'saved browser choice'
            if not value:
                value, source = accounts.default_browser(), 'system default'
    value = value if isinstance(value, str) else 'unknown'
    return {'browser': value, 'source': source, 'supported': value in ('chrome','neo'),
            'enabled': value != 'none'}


def selected_backend(backend=None, *, browser="auto"):
    choice = selection(backend, browser=browser)
    if not choice['supported']:
        if backend is not None or 'LINKGET_RENDERED_BACKEND' in os.environ:
            raise ValueError('The rendered content backend must be chrome or neo.')
        raise BrowserUnavailable(
            'Page reading has no supported browser controller for ' + choice['browser'] +
            '. Chrome and BrowserOS Neo are supported. Choose linkget config --reader-browser chrome '
            'or --reader-browser neo; no other browser was opened.')
    return choice['browser']


def public_article_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('A bounded public article URL is required.')
    p = urlsplit(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or p.query or p.fragment:
        raise ValueError('Unsupported public article URL.')
    if p.hostname == 'www.raspberrypi.com' and p.path in ('/products/raspberry-pi-5', '/products/raspberry-pi-5/'):
        return 'https://www.raspberrypi.com/products/raspberry-pi-5/', PUBLIC_ORIGINS[0]
    if p.hostname == 'zhuanlan.zhihu.com' and re.fullmatch(r'/p/[0-9]{1,20}/?', p.path):
        return 'https://zhuanlan.zhihu.com' + p.path.rstrip('/'), PUBLIC_ORIGINS[1]
    raise ValueError('The public article is outside the supported reader scope.')


def validate_public_page(page, expected):
    expected, _ = public_article_url(expected)
    if not isinstance(page, dict) or page.get('url') != expected or page.get('final_url') != expected:
        raise ValueError('Exact article URL and final URL binding are required.')
    title, text, proof = page.get('title'), page.get('text'), page.get('dom_proof')
    if (page.get('kind') != 'article' or page.get('content_complete') is not True or
            not isinstance(title, str) or not 1 <= len(title) <= 300 or
            title != title.strip() or not title.strip() or
            not isinstance(text, str) or not 200 <= len(text.encode()) <= 120000 or
            not isinstance(proof, dict) or proof.get('reader_revision') != PUBLIC_READER_REVISION or
            proof.get('basis') != 'bounded_article_dom' or proof.get('http_transfer_verified') is not False):
        raise ValueError('Bounded article text and DOM scope evidence are required.')
    tail = proof.get('end_excerpt')
    if not isinstance(tail, str) or not 1 <= len(tail) <= 512 or not text.endswith(tail):
        raise ValueError('The captured article tail does not match its text.')
    if expected.startswith('https://www.raspberrypi.com/'):
        sections = proof.get('sections')
        if title != 'Raspberry Pi 5' or proof.get('selector') != 'main' or not isinstance(sections, dict):
            raise ValueError('The exact Raspberry Pi 5 product scope is required.')
        clean_sections = {}
        for name, pattern in (('specifications', r'\b(?:specifications?|tech specs)\b'), ('faq', r'\bFAQs?\b|frequently asked questions|your questions answered')):
            section = sections.get(name)
            if not isinstance(section, dict) or type(section.get('offset')) is not int:
                raise ValueError('Product section offsets are required.')
            excerpt, offset = section.get('excerpt'), section['offset']
            if (not isinstance(excerpt, str) or not 1 <= len(excerpt) <= 1000 or offset < 0 or
                    text[offset:offset + len(excerpt)] != excerpt or not re.search(pattern, excerpt, re.I)):
                raise ValueError('The product section evidence does not match the text.')
            clean_sections[name] = {'offset': offset, 'excerpt': excerpt}
        if not re.search(r'BCM2712', text, re.I):
            raise ValueError('The product processor specifications are unavailable.')
        scope = {'selector': 'main', 'sections': clean_sections}
    else:
        publication = proof.get('publication_text')
        selectors = ('.Post-RichTextContainer .RichText', '.Post-RichText', 'article .RichText')
        if (proof.get('selector') not in selectors or not isinstance(publication, str) or
                not 1 <= len(publication) <= 300 or publication not in text or
                not text.startswith(title + '\n\n') or
                not re.search(r'(发布于|编辑于).*?(?:19|20)[0-9]{2}[-年/]', publication)):
            raise ValueError('The scoped Zhihu article publication date is required.')
        scope = {'selector': proof['selector'], 'publication_text': publication}
    # Only approved content/proof fields leave the browser. Arbitrary page
    # payload keys cannot become credentials, instructions, or persisted state.
    if __package__:
        from .rendered_proof import browser_metadata
    else:
        from rendered_proof import browser_metadata
    metadata = browser_metadata(page)
    neo = metadata.get('reader_backend') == 'browseros_neo'
    return {'url': expected, 'final_url': expected, 'title': title, 'kind': 'article',
            'text': text, 'content_complete': True,
            'dom_proof': {**scope, 'reader_revision': PUBLIC_READER_REVISION,
                          'basis': 'bounded_article_dom', 'http_transfer_verified': False, 'end_excerpt': tail},
            'reader_revision': PUBLIC_READER_REVISION,
            'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'content_binding': 'exact_browseros_neo_article_dom' if neo else 'exact_daily_chrome_article_dom',
            'transport_proof': {'kind': 'browseros_neo_dom' if neo else 'daily_chrome_dom',
                                'http_transfer_verified': False, 'cookies_exported': False}, **metadata}


def wechat_article_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('A bounded WeChat article URL is required.')
    p = urlsplit(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or p.query or p.fragment:
        raise ValueError('Unsupported WeChat browser article URL.')
    if p.hostname == 'mp.weixin.qq.com' and re.fullmatch(r'/s/[A-Za-z0-9_-]{8,128}/?', p.path):
        return 'https://mp.weixin.qq.com' + p.path.rstrip('/'), WECHAT_ORIGIN
    raise ValueError('The WeChat article URL is outside browser reader scope.')


def validate_wechat_page(page, expected):
    expected, _ = wechat_article_url(expected)
    if not isinstance(page, dict) or page.get('url') != expected or page.get('final_url') != expected:
        raise ValueError('Exact WeChat article URL and final URL binding are required.')
    title, text, proof = page.get('title'), page.get('text'), page.get('dom_proof')
    if (page.get('kind') != 'article' or page.get('content_complete') is not True or
            not isinstance(title, str) or not 1 <= len(title) <= 300 or
            title != title.strip() or not title.strip() or
            not isinstance(text, str) or not 40 <= len(text.encode()) <= 120000 or
            not isinstance(proof, dict) or proof.get('reader_revision') != WECHAT_READER_REVISION or
            proof.get('selector') != '#js_content' or
            proof.get('basis') != 'bounded_wechat_article_dom' or
            proof.get('http_transfer_verified') is not False):
        raise ValueError('Bounded WeChat article text and DOM scope evidence are required.')
    tail = proof.get('end_excerpt')
    if not isinstance(tail, str) or not 1 <= len(tail) <= 512 or not text.endswith(tail):
        raise ValueError('The captured WeChat article tail does not match its text.')
    author = page.get('author')
    published_at = page.get('published_at')
    if author is not None and (not isinstance(author, str) or len(author) > 200):
        author = None
    if published_at is not None and (not isinstance(published_at, str) or len(published_at) > 100):
        published_at = None
    if __package__:
        from .rendered_proof import browser_metadata
        from . import web_article
    else:
        from rendered_proof import browser_metadata
        import web_article
    metadata = browser_metadata(page)
    neo = metadata.get('reader_backend') == 'browseros_neo'
    cover_image = page.get('cover_image')
    if not isinstance(cover_image, str) or not cover_image.startswith(('http://', 'https://')):
        cover_image = None
    body_html_raw = page.get('body_html')
    markdown_body = None
    body_html = None
    fragment_media = []
    if isinstance(body_html_raw, str) and body_html_raw.strip() and hasattr(web_article, 'render_fragment'):
        frag = web_article.render_fragment(body_html_raw, expected)
        markdown_body = frag.get('markdown') or None
        body_html = frag.get('body_html') or None
        fragment_media = frag.get('media_items') or []
    media_items = []
    seen = set()
    if cover_image:
        media_items.append({'role': 'cover', 'media_type': 'image', 'original_url': cover_image, 'alt': title})
        seen.add(cover_image)
    for item in fragment_media:
        orig = item.get('original_url', '')
        if orig and orig not in seen:
            media_items.append(item)
            seen.add(orig)
    for raw_img in page.get('images') or []:
        if isinstance(raw_img, str) and raw_img.startswith(('http://', 'https://')) and raw_img not in seen:
            media_items.append({'role': 'inline', 'media_type': 'image', 'original_url': raw_img})
            seen.add(raw_img)
    out = {'url': expected, 'final_url': expected, 'title': title,
           'author': author or None, 'published_at': published_at or None,
           'kind': 'article', 'text': text, 'content_complete': True,
           'dom_proof': {'selector': '#js_content', 'reader_revision': WECHAT_READER_REVISION,
                         'basis': 'bounded_wechat_article_dom', 'http_transfer_verified': False, 'end_excerpt': tail},
           'reader_revision': WECHAT_READER_REVISION,
           'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
           'content_binding': 'exact_browseros_neo_wechat_dom' if neo else 'exact_daily_chrome_wechat_dom',
           'transport_proof': {'kind': 'browseros_neo_dom' if neo else 'daily_chrome_dom',
                               'http_transfer_verified': False, 'cookies_exported': False}, **metadata}
    if cover_image:
        out['cover_image'] = cover_image
    if media_items:
        out['media_items'] = media_items
    if markdown_body:
        out['markdown'] = markdown_body
    if body_html:
        out['body_html'] = body_html
    if isinstance(body_html_raw, str) and body_html_raw.strip():
        out['raw_html'] = body_html_raw
    return out


def x_article_url(url):
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('A bounded X article URL is required.')
    p = urlsplit(url)
    host = (p.hostname or '').lower().rstrip('.')
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or p.query or p.fragment:
        raise ValueError('Unsupported X browser article URL.')
    if host in {'x.com', 'www.x.com', 'mobile.x.com', 'twitter.com', 'www.twitter.com', 'mobile.twitter.com'}:
        m = re.fullmatch(r'/([A-Za-z0-9_]{1,15})/(status|article)/([0-9]{1,25})/?', p.path)
        if m:
            return f'https://x.com/{m.group(1)}/{m.group(2)}/{m.group(3)}', X_ORIGIN
        m_web = re.fullmatch(r'/i/web/status/([0-9]{1,25})/?', p.path)
        if m_web:
            return f'https://x.com/i/web/status/{m_web.group(1)}', X_ORIGIN
    raise ValueError('The X article URL is outside browser reader scope; use x.com/USER/status/ID or x.com/USER/article/ID.')


def validate_x_article_page(page, expected):
    expected, _ = x_article_url(expected)
    if not isinstance(page, dict) or page.get('url') != expected or page.get('final_url') != expected:
        raise ValueError('Exact X article URL and final URL binding are required.')
    title, text, proof = page.get('title'), page.get('text'), page.get('dom_proof')
    if (page.get('kind') != 'article' or page.get('content_complete') is not True or
            not isinstance(title, str) or not 1 <= len(title) <= 300 or
            title != title.strip() or not title.strip() or
            not isinstance(text, str) or not 40 <= len(text.encode()) <= 240000 or
            not isinstance(proof, dict) or proof.get('reader_revision') != X_ARTICLE_READER_REVISION or
            proof.get('selector') != '[data-testid="twitterArticleReadView"]' or
            proof.get('basis') != 'bounded_x_article_dom' or
            proof.get('http_transfer_verified') is not False):
        raise ValueError('Bounded X article text and DOM scope evidence are required.')
    tail = proof.get('end_excerpt')
    if not isinstance(tail, str) or not 1 <= len(tail) <= 512 or not text.endswith(tail):
        raise ValueError('The captured X article tail does not match its text.')
    author = page.get('author')
    author_handle = page.get('author_handle')
    published_at = page.get('published_at')
    if author is not None and (not isinstance(author, str) or len(author) > 200):
        author = None
    if author_handle is not None and (not isinstance(author_handle, str) or not re.fullmatch(r'[A-Za-z0-9_]{1,32}', author_handle)):
        author_handle = None
    if published_at is not None and (not isinstance(published_at, str) or len(published_at) > 100):
        published_at = None
    if __package__:
        from .rendered_proof import browser_metadata
        from . import web_article, x_article
    else:
        from rendered_proof import browser_metadata
        import web_article, x_article
    metadata = browser_metadata(page)
    neo = metadata.get('reader_backend') == 'browseros_neo'

    cover_image = page.get('cover_image')
    if not isinstance(cover_image, str) or not cover_image.startswith(('http://', 'https://')):
        cover_image = None
    else:
        cover_image = x_article.upgrade_x_media_url(cover_image)

    body_html_raw = page.get('body_html')
    markdown_body = None
    body_html = None
    fragment_media = []
    if isinstance(body_html_raw, str) and body_html_raw.strip() and hasattr(web_article, 'render_fragment'):
        frag = web_article.render_fragment(body_html_raw, expected)
        markdown_body = frag.get('markdown') or None
        body_html = frag.get('body_html') or None
        fragment_media = frag.get('media_items') or []

    media_items = []
    seen = set()
    if cover_image:
        media_items.append({'role': 'cover', 'media_type': 'image', 'original_url': cover_image, 'alt': title})
        seen.add(cover_image)
    for item in fragment_media:
        orig = x_article.upgrade_x_media_url(item.get('original_url', ''))
        if orig and orig not in seen:
            media_items.append({**item, 'original_url': orig})
            seen.add(orig)
    for raw_img in page.get('images') or []:
        if isinstance(raw_img, str) and raw_img.startswith(('http://', 'https://')):
            orig = x_article.upgrade_x_media_url(raw_img)
            if orig not in seen:
                media_items.append({'role': 'inline', 'media_type': 'image', 'original_url': orig})
                seen.add(orig)
    for raw_vid in page.get('videos') or []:
        if isinstance(raw_vid, dict) and isinstance(raw_vid.get('url'), str) and raw_vid['url'].startswith(('http://', 'https://')):
            vurl = raw_vid['url']
            if vurl not in seen:
                poster = raw_vid.get('poster')
                media_items.append({
                    'role': 'video',
                    'media_type': 'video',
                    'original_url': vurl,
                    'poster_url': x_article.upgrade_x_media_url(poster) if isinstance(poster, str) and poster.startswith(('http://', 'https://')) else None,
                })
                seen.add(vurl)

    return {
        'url': expected,
        'final_url': expected,
        'canonical_url': expected,
        'title': title,
        'author': author or None,
        'author_handle': author_handle or None,
        'author_url': f'https://x.com/{author_handle}' if author_handle else None,
        'published_at': published_at or None,
        'cover_image': cover_image,
        'media_items': media_items,
        'kind': 'article',
        'text': text,
        'markdown': markdown_body or text,
        'body_html': body_html,
        'raw_html': body_html_raw if isinstance(body_html_raw, str) else None,
        'content_complete': True,
        'dom_proof': {
            'selector': '[data-testid="twitterArticleReadView"]',
            'reader_revision': X_ARTICLE_READER_REVISION,
            'basis': 'bounded_x_article_dom',
            'http_transfer_verified': False,
            'end_excerpt': tail,
        },
        'reader_revision': X_ARTICLE_READER_REVISION,
        'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
        'content_binding': 'exact_browseros_neo_x_article_dom' if neo else 'exact_daily_chrome_x_article_dom',
        'transport_proof': {
            'kind': 'browseros_neo_dom' if neo else 'daily_chrome_dom',
            'http_transfer_verified': False,
            'cookies_exported': False,
        },
        **metadata,
    }


def lock_path():
    return Path.home() / 'Library/Application Support/linkget/browser/reader.lock'


@contextmanager
def reader_lock(deadline):
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise BrowserUnavailable('The content reader lock directory is invalid.')
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        details = os.fstat(fd)
        if not stat.S_ISREG(details.st_mode) or details.st_uid != os.getuid():
            raise BrowserUnavailable('The content reader lock file is invalid.')
        os.fchmod(fd, 0o600)
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ReaderBusy('The content reader is busy; no new tab was opened.') from None
                time.sleep(0.1)
        yield
    finally:
        os.close(fd)


def controller(action, *args, timeout=8):
    script = Path(__file__).with_name('rendered_page.applescript')
    result = subprocess.run(['/usr/bin/osascript', str(script), action, *map(str, args)],
                            capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise BrowserUnavailable('Daily Chrome could not run the content reader. Check its existing Automation '
                                 'and JavaScript from Apple Events permission; no permission was changed.')
    if len(result.stdout.encode()) > MAX_RESULT_BYTES:
        raise BrowserUnavailable('The browser content result exceeded its byte limit.')
    return result.stdout.strip()


def read_page(url, timeout=MAX_READ_SECONDS, *, backend=None):
    """Read one supported normal HTTPS page; keep unrelated tabs untouched.

    ``timeout`` bounds lock wait and page polling (at most 45 seconds), followed
    by an independently bounded cleanup. User-navigated temporary tabs are
    preserved and explicitly reported, rather than closing unrelated work.
    """
    if type(timeout) not in (int, float) or not 1 <= timeout <= MAX_READ_SECONDS:
        raise ValueError('The rendered reader timeout must be between 1 and 45 seconds.')
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError('A bounded normal HTTPS content URL is required.')
    if __package__:
        from . import toutiao
    else:
        import toutiao
    try:
        expected = toutiao.canonical(url)
        script_name, mode = 'toutiao_browser.js', 'toutiao'
    except ValueError:
        try:
            expected, _ = public_article_url(url)
            script_name, mode = 'public_browser.js', 'public'
        except ValueError:
            try:
                expected, _ = wechat_article_url(url)
                script_name, mode = 'wechat_browser.js', 'wechat'
            except ValueError:
                expected, _ = x_article_url(url)
                script_name, mode = 'x_article_browser.js', 'x_article'
    backend = selected_backend(backend)
    javascript = Path(__file__).with_name(script_name).read_text()
    validator = (
        (lambda p, exp: validate_public_page(p, exp)) if mode == 'public'
        else (lambda p, exp: validate_wechat_page(p, exp)) if mode == 'wechat'
        else (lambda p, exp: validate_x_article_page(p, exp)) if mode == 'x_article'
        else (lambda p, exp: toutiao.validate_page(exp, p))
    )
    if backend == 'neo':
        if __package__:
            from .neo_browser import NeoUnavailable, read_dom
        else:
            from neo_browser import NeoUnavailable, read_dom
        deadline = time.monotonic() + timeout
        with reader_lock(deadline):
            remaining = deadline - time.monotonic()
            if remaining < 30:
                raise ReaderBusy('Another content read occupied this bounded window; no Neo tab was opened.')
            try:
                page = read_dom(expected, javascript, timeout=remaining)
            except NeoUnavailable as error:
                raise BrowserUnavailable(str(error)) from error
        result = validator(page, expected)
        rev = (
            PUBLIC_READER_REVISION if mode == 'public'
            else WECHAT_READER_REVISION if mode == 'wechat'
            else X_ARTICLE_READER_REVISION if mode == 'x_article'
            else TOUTIAO_READER_REVISION
        )
        binding = (
            'exact_browseros_neo_article_dom' if mode == 'public'
            else 'exact_browseros_neo_wechat_dom' if mode == 'wechat'
            else 'exact_browseros_neo_x_article_dom' if mode == 'x_article'
            else 'exact_browseros_neo_toutiao_post'
        )
        result.update(final_url=expected, reader_revision=rev, content_binding=binding)
        if result.get('kind') == 'article':
            result['text_sha256'] = hashlib.sha256(result['text'].encode()).hexdigest()
        return result
    expression = ('(()=>{if(location.href==="about:blank"||location.href==="")return"null";const value=(' + javascript + ')(' + json.dumps(expected) + ');'
                  'const text=JSON.stringify(value);return new TextEncoder().encode(text).length>'
                  + str(MAX_RESULT_BYTES) + '?JSON.stringify({diagnostic:{reason:"article_scope_size_out_of_bounds"}}):text;})()')
    deadline, owner = time.monotonic() + timeout, None
    with reader_lock(deadline):
        try:
            identifiers = controller('open', expected, timeout=min(8, max(0.1, deadline-time.monotonic())))
            if not re.fullmatch(r'[0-9]+:[0-9]+:[0-9]+', identifiers):
                raise BrowserUnavailable('Chrome did not identify the owned reading tab.')
            owner = identifiers.split(':')
            reason = None
            while time.monotonic() < deadline:
                raw = controller('read', owner[0], owner[1], expression,
                                 timeout=min(8, max(0.1, deadline-time.monotonic())))
                try:
                    page = json.loads(raw or 'null')
                except ValueError:
                    raise ValueError('Chrome returned invalid content evidence.') from None
                if isinstance(page, dict) and 'diagnostic' in page:
                    reason = page['diagnostic'].get('reason') if isinstance(page['diagnostic'], dict) else None
                    if reason in ('post_mismatch', 'public_article_mismatch', 'article_access_gate', 'article_scope_size_out_of_bounds', 'x_article_login_required'):
                        raise ValueError('The requested page cannot be read completely: ' + reason)
                elif page is not None:
                    try:
                        result = validator(page, expected)
                    except (TypeError, ValueError):
                        raise ValueError('Chrome did not expose complete bound content for this exact page.') from None
                    break
                time.sleep(min(0.3, max(0, deadline-time.monotonic())))
            else:
                raise ValueError('The rendered content is incomplete within the bounded page read' + (': ' + reason if reason else '.'))
        except subprocess.TimeoutExpired:
            raise BrowserUnavailable('The existing Chrome content interface timed out; no permission was changed.') from None
        finally:
            if owner:
                # A failed cleanup must never be mistaken for a successful read.
                cleanup = controller('close', owner[0], owner[1], owner[2], expected)
                if cleanup not in ('closed', 'preserved_navigated_tab'):
                    raise BrowserUnavailable('The reading tab cleanup could not be verified.')
        result['browser_controller'] = {'kind': 'daily_chrome_apple_events', 'reader_revision': 1,
                                        'temporary_tab_cleanup': cleanup, 'credentials_exported': False}
        if mode == 'toutiao':
            result.update(final_url=expected, reader_revision=TOUTIAO_READER_REVISION,
                          content_binding='exact_daily_chrome_toutiao_post')
            if result.get('kind') == 'article':
                result['text_sha256'] = hashlib.sha256(result['text'].encode()).hexdigest()
        return result


def read_public(url, timeout=MAX_READ_SECONDS, *, backend=None):
    public_article_url(url)
    return read_page(url, timeout=timeout, backend=backend)


def read_wechat(url, timeout=MAX_READ_SECONDS, *, backend=None):
    wechat_article_url(url)
    return read_page(url, timeout=timeout, backend=backend)


def read_x_article(url, timeout=MAX_READ_SECONDS, *, backend=None):
    x_article_url(url)
    return read_page(url, timeout=timeout, backend=backend)
