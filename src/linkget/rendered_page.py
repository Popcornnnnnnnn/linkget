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
PUBLIC_ORIGINS = ('https://www.raspberrypi.com/*', 'https://zhuanlan.zhihu.com/*')
MAX_RESULT_BYTES = 256000
MAX_READ_SECONDS = 45


class BrowserUnavailable(RuntimeError):
    """The existing browser path cannot deliver a complete bounded read."""


class ReaderBusy(BrowserUnavailable):
    """Another local read owns the serialized rendered-page reader."""


def selected_backend(backend=None, *, browser="auto"):
    """Resolve page automation independently of credential-source preferences."""
    if __package__:
        from . import accounts
    else:
        import accounts
    if backend is not None:
        value = backend
    elif browser != 'auto':
        value = browser
    elif 'LINKGET_RENDERED_BACKEND' in os.environ:
        value = os.environ['LINKGET_RENDERED_BACKEND']
    else:
        settings = accounts.preferences()
        value = settings.get('reader_browser', 'auto')
        if value == 'auto':
            value = settings.get('browser') or accounts.default_browser()
    if value not in ('chrome', 'neo'):
        if backend is not None or 'LINKGET_RENDERED_BACKEND' in os.environ:
            raise ValueError('The rendered content backend must be chrome or neo.')
        raise BrowserUnavailable(
            'Page reading has no supported browser controller for ' + str(value or 'the unknown system default') +
            '. Chrome and BrowserOS Neo are supported. Choose linkget config --reader-browser chrome '
            'or --reader-browser neo; no other browser was opened.')
    return value


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
    backend = selected_backend(backend)
    if __package__:
        from . import toutiao
    else:
        import toutiao
    try:
        expected = toutiao.canonical(url)
        script_name, public = 'toutiao_browser.js', False
    except ValueError:
        expected, _ = public_article_url(url)
        script_name, public = 'public_browser.js', True
    javascript = Path(__file__).with_name(script_name).read_text()
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
        result = validate_public_page(page, expected) if public else toutiao.validate_page(expected, page)
        result.update(final_url=expected, reader_revision=PUBLIC_READER_REVISION if public else TOUTIAO_READER_REVISION,
                      content_binding='exact_browseros_neo_article_dom' if public else 'exact_browseros_neo_toutiao_post')
        if result.get('kind') == 'article':
            result['text_sha256'] = hashlib.sha256(result['text'].encode()).hexdigest()
        return result
    expression = ('(()=>{const value=(' + javascript + ')(' + json.dumps(expected) + ');'
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
                    if reason in ('post_mismatch', 'public_article_mismatch', 'article_access_gate', 'article_scope_size_out_of_bounds'):
                        raise ValueError('The requested page cannot be read completely: ' + reason)
                elif page is not None:
                    try:
                        result = validate_public_page(page, expected) if public else toutiao.validate_page(expected, page)
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
        if not public:
            result.update(final_url=expected, reader_revision=TOUTIAO_READER_REVISION,
                          content_binding='exact_daily_chrome_toutiao_post')
            if result.get('kind') == 'article':
                result['text_sha256'] = hashlib.sha256(result['text'].encode()).hexdigest()
        return result


def read_public(url, timeout=MAX_READ_SECONDS, *, backend=None):
    public_article_url(url)
    return read_page(url, timeout=timeout, backend=backend)
