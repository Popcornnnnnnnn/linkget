"""Evidence-based failures and a pure, opt-in read-only retry policy.

No requests, retries, logging, credential reads or file writes are performed here.
"""
import ast
from dataclasses import dataclass
import errno
import json
import math
import re
import socket
import ssl
import subprocess
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit


@dataclass(frozen=True)
class Failure:
    category: str
    label: str
    message: str
    next_step: str
    retryable: bool = False


_URL = re.compile(r'https?://[^\s<>"\']+', re.I)
_HEADER = re.compile(r'\b(authorization|proxy-authorization|cookies?|set-cookie)["\']?\s*[:=]\s*[^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*', re.I)
_SECRET_KEY = (
    r'(?:access|refresh|id|auth(?:orization)?|csrf|xsec|session|bearer)[_-]?token'
    r'|oauth[_-]?token(?:[_-]?secret)?|token|signature|password|passwd'
    r'|(?:api|client|consumer)[_-]?(?:key|secret)|(?:private|secret)[_-]?key'
    r'|secret|session(?:[_-]?id)?|SESSDATA|csrf|csrftoken'
    r'|authorization|proxy[_-]?authorization|cookies?|set[_-]?cookie'
    r'|x[_-]?api[_-]?key|credentials?'
)
_SECRET_NAME = re.compile(r'(?:' + _SECRET_KEY + r')\Z', re.I)
_SECRET_FIELD = re.compile(r'''(?<![\w])(["']?)(?:''' + _SECRET_KEY + r''')\1\s*[:=]''', re.I)
_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')
_OMITTED = '[Sensitive diagnostic omitted: credentials could not be safely separated]'
_NEGATIVE = re.compile(
    r"\b(?:not|no|never|without|false|cannot|disabled|absent)\b|\b(?:isn.t|aren.t|wasn.t|weren.t|hasn.t|haven.t|doesn.t|didn.t|don.t|can.t)\b|不是|并非|没有|无需|不需要|未过期|未失效", re.I)


class _UnsafeDiagnostic(ValueError):
    pass


def _clean(value):
    text = _ANSI.sub('', str(value).replace('\\/', '/'))
    return ''.join(c for c in text if c in '\n\t' or ord(c) >= 32 and ord(c) != 127)


def _container_end(text, start):
    """Find a complete JSON/Python literal without executing it."""
    stack, quote, escaped = [], None, False
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
        elif char in ('"', "'"):
            quote = char
        elif char in '[{':
            stack.append(char)
            if len(stack) > 32:
                raise _UnsafeDiagnostic('Too deeply nested')
        elif char in ']}':
            if not stack or (stack.pop(), char) not in {('[', ']'), ('{', '}')}:
                return None
            if not stack:
                return index + 1
    return None


def _rewrite_structures(text, replace, plain=lambda text: text):
    """Transform full structures, including those inside a URLError wrapper."""
    if len(text) > 65536:
        raise _UnsafeDiagnostic('Diagnostic exceeds safe parsing bound')
    output, index, count = [], 0, 0
    while index < len(text):
        match = re.search(r'[\[{]', text[index:])
        if not match:
            output.append(plain(text[index:]))
            break
        start = index + match.start()
        if _SECRET_FIELD.search(text[index:start]):
            raise _UnsafeDiagnostic('Structured value follows an unstructured credential field')
        output.append(plain(text[index:start]))
        count += 1
        if count > 64:
            raise _UnsafeDiagnostic('Too many structures')
        end = _container_end(text, start)
        segment = text[start:end] if end else text[start:]
        try:
            if end is None:
                raise ValueError('Incomplete structure')
            try:
                # Diagnostic strings can contain literal newlines in token values.
                value = json.loads(segment, strict=False)
            except (ValueError, RecursionError):
                value = ast.literal_eval(segment)
            if not isinstance(value, (dict, list, tuple)):
                raise ValueError('Not a supported structure')
        except (ValueError, SyntaxError, RecursionError):
            # A broken object with quoted keys or any sensitive field cannot be
            # reliably separated. Never print a partially redacted remainder.
            if re.match(r'''[\[{]\s*["']''', segment) or _SECRET_FIELD.search(segment):
                raise _UnsafeDiagnostic('Unparseable sensitive structure') from None
            output.append(plain(text[start]))
            index = start + 1
            continue
        output.append(replace(value))
        index = end
    return ''.join(output)


def _plain_redact(text):
    def url(match):
        try:
            parsed = urlsplit(match[0])
            host = parsed.hostname
            if not host:
                return '[redacted URL]'
            host = '[' + host + ']' if ':' in host else host
            if parsed.port:
                host += ':' + str(parsed.port)
            return urlunsplit((parsed.scheme, host, parsed.path,
                               '[redacted]' if parsed.query else '',
                               '[redacted]' if parsed.fragment else ''))
        except ValueError:
            return '[redacted URL]'

    text = _URL.sub(url, text)
    # Unstructured multiline header/token values have no reliable end boundary.
    # Suppressing that diagnostic is safer than retaining its later lines.
    if '\n' in text and (_HEADER.search(text) or re.search(r'\b(?:Bearer|Basic)\s+', text, re.I)):
        return _OMITTED
    text = _HEADER.sub('[credentials redacted]', text)
    if _SECRET_FIELD.search(text):
        return _OMITTED
    return re.sub(r'\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', r'\1 [redacted]', text, flags=re.I)


def _redact_value(value, depth=0):
    if depth > 16:
        return _OMITTED
    if isinstance(value, dict):
        return {str(key): '[redacted]' if _SECRET_NAME.fullmatch(str(key)) else _redact_value(item, depth + 1)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item, depth + 1) for item in value]
    if isinstance(value, str):
        return _redact_text(value, depth + 1)
    if value is None or type(value) in (bool, int, float):
        return value
    return _OMITTED


def _redact_text(text, depth=0):
    if depth > 16:
        return _OMITTED
    try:
        def plain(segment):
            safe = _plain_redact(segment)
            if safe == _OMITTED:
                raise _UnsafeDiagnostic('Unstructured sensitive value')
            return safe
        return _rewrite_structures(_clean(text),
                                   lambda value: json.dumps(_redact_value(value, depth + 1), ensure_ascii=False), plain)
    except _UnsafeDiagnostic:
        return _OMITTED


def redact(value):
    """Recursively remove credential values; suppress unsafe malformed details."""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(_redact_value(value), ensure_ascii=False)
    else:
        text = _redact_text(str(value))
    return text[:2000] + ('…' if len(text) > 2000 else '')


_FLAG_KEYS = {
    'loginrequired': 'login_required', 'loginexpired': 'login_expired',
    'sessionexpired': 'login_expired', 'cookieexpired': 'login_expired', 'cookiesexpired': 'login_expired',
    'authenticationexpired': 'login_expired', 'ratelimit': 'rate_limit', 'ratelimited': 'rate_limit',
    'captcha': 'website_check', 'verificationrequired': 'website_check', 'websitecheck': 'website_check',
    'challengerequired': 'website_check', 'accessrefused': 'access_refused', 'forbidden': 'access_refused',
    'unavailable': 'unavailable', 'postunavailable': 'unavailable', 'deleted': 'unavailable',
    'unsupported': 'unsupported', 'unsupportedurl': 'unsupported', 'notsupported': 'unsupported',
    'networkerror': 'network', 'timeout': 'network', 'timedout': 'network',
    'extractorerror': 'extractor_failure', 'extractorfailed': 'extractor_failure',
}
_FLAG_ASSIGN = re.compile(
    r'''(?<![\w])(["']?)(login[_ -]?required|(?:login|session|cookies?|authentication)[_ -]?expired|rate[_ -]?limit(?:ed)?|captcha|verification[_ -]?required|website[_ -]?check|challenge[_ -]?required|access[_ -]?refused|forbidden|(?:post[_ -]?)?unavailable|deleted|unsupported(?:[_ -]?url)?|not[_ -]?supported|network[_ -]?error|timeout|timed[_ -]?out|extractor[_ -]?(?:error|failed))\1\s*[:=]\s*(["']?)(true|false|yes|no|null|[+-]?\d+(?:\.\d+)?)\3(?![\w.])''', re.I)
_CONFIG_KEYS = {'config', 'configuration', 'settings', 'options', 'preferences', 'parameters', 'params'}
_DIAGNOSTIC_KEYS = {'message', 'error', 'detail', 'details', 'reason', 'errordescription'}


def _key(value):
    return re.sub(r'[^a-z0-9]', '', str(value).lower())


def _affirmative_clauses(text):
    # These phrases describe an observed error despite containing a negative word.
    substitutions = (
        (r'\bno longer valid\b', 'expired'), (r'\bnot supported\b|\bnot implemented\b', 'unsupported'),
        (r'\bno suitable extractor\b', 'unsupported extractor'), (r'\bcould not resolve\b', 'dns resolution failed'),
        (r'\bnot found\b', 'missing resource'),
        (r'confirm you(?:.re| are) not a (?:bot|robot)', 'website verification required'),
        (r'\bif (?:you are |you.re )?not already logged in\b', 'if needed'),
    )
    for pattern, replacement in substitutions:
        text = re.sub(pattern, replacement, text, flags=re.I)
    return '\n'.join(clause for clause in re.split(r'[.;,!?\n]+|\bbut\b|\bhowever\b', text, flags=re.I)
                     if not _NEGATIVE.search(clause))


def _evidence(error):
    """Separate response flags/messages from config, negatives and secret data."""
    positive, negative, messages = set(), set(), []

    def collect(value, depth=0):
        if depth > 16:
            return
        if isinstance(value, dict):
            for name, item in value.items():
                normalized = _key(name)
                if _SECRET_NAME.fullmatch(str(name)) or normalized in _CONFIG_KEYS:
                    continue
                category = _FLAG_KEYS.get(normalized)
                if category:
                    if type(item) is bool:
                        (positive if item else negative).add(category)
                elif normalized in _DIAGNOSTIC_KEYS and isinstance(item, str):
                    messages.append(item)
                elif isinstance(item, (dict, list, tuple)):
                    collect(item, depth + 1)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect(item, depth + 1)
        return ''

    if isinstance(error, (dict, list, tuple)):
        collect(error)
        text = ''
    else:
        text = _clean(error)
        text = _URL.sub('[URL]', text)
        try:
            text = _rewrite_structures(text, collect)
        except _UnsafeDiagnostic:
            return '', set(), set()
    text += '\n' + '\n'.join(messages)
    # Message strings can themselves contain structured diagnostics.
    if messages:
        try:
            text = _rewrite_structures(text, lambda value: '')
        except _UnsafeDiagnostic:
            return '', set(), set()
    # Preserve affirmative diagnostics before a credential field, while treating
    # its entire remaining tail as opaque. Later lines may still be credentials.
    credential = _SECRET_FIELD.search(text)
    bearer = re.search(r'\b(?:Bearer|Basic)\s+', text, re.I)
    boundaries = [match.start() for match in (credential, bearer) if match]
    if boundaries:
        text = text[:min(boundaries)]
    text = _plain_redact(_URL.sub('[URL]', text))
    if text == _OMITTED:
        return '', positive - negative, negative

    def assignment(match):
        category = _FLAG_KEYS[_key(match[2])]
        prefix = re.split(r'[.;,!?\n]+', text[:match.start()])[-1]
        if re.search(r'\bconfig(?:ured|uration)?\b|\bsettings?\b|\boptions?\b', prefix, re.I):
            return ''
        if match[4].lower() == 'true':
            (negative if _NEGATIVE.search(prefix) else positive).add(category)
        elif match[4].lower() in {'false', 'no', '0'}:
            negative.add(category)
        return ''

    text = _FLAG_ASSIGN.sub(assignment, text)
    text = re.sub(r'\b(?:rate[ _-]?limit|timeout)(?:[ _-]?(?:value|setting|option))?\s*(?:(?:is )?(?:set|configured)(?: (?:to|as))?|of|=|:)?\s*\d+(?:\.\d+)?\b', '', text, flags=re.I)
    return _affirmative_clauses(text), positive - negative, negative


def _typed_chain(error):
    """Read at most four explicit causes/reasons; never chase implicit context."""
    seen, current = set(), error
    for _ in range(4):
        if not isinstance(current, BaseException) or id(current) in seen:
            break
        seen.add(id(current))
        yield current
        reason = current.reason if isinstance(current, URLError) else None
        current = reason if isinstance(reason, BaseException) else current.__cause__


def classify(error, *, phase=None):
    """Use affirmative observations and typed failures; otherwise report Unknown."""
    message = redact(error) or 'The operation failed without diagnostic details.'
    signals, flags, denied = _evidence(error)
    chain = list(_typed_chain(error))
    disk_full = any(isinstance(cause, OSError) and cause.errno in {errno.ENOSPC, errno.EDQUOT} for cause in chain)
    if disk_full or phase in {'save', 'import', 'pair', 'postprocess'}:
        next_step = 'Keep retained media. Fix the named tool or destination error, then use the printed retry or recovery command.'
        if disk_full:
            next_step = 'Free space in the selected destination, then use the printed recovery command; retained originals are kept.'
        return Failure('postprocessing', 'Save/processing', message, next_step)

    def observed(category, pattern):
        return category in flags or category not in denied and re.search(pattern, signals, re.I)

    code = next((cause.code for cause in chain if isinstance(cause, HTTPError)), None)
    if code == 429 or observed('rate_limit', r'HTTP(?: Error)?[ :]+429|too many requests|\brate[ _-]limit(?:ed|ing)?\b'):
        return Failure('rate_limit', 'Rate limit', message,
                       'Wait before retrying this item; honor any server retry time.', True)
    if observed('website_check', r'captcha|verification page|verification required|(?:javascript|js|security|browser) challenge'):
        return Failure('website_check', 'Website check', message,
                       'Open the post yourself, complete the website check if shown, then retry this item.')
    if observed('login_expired', r'(?:cookies|session|login|authentication)(?: are| is| have| has)? expired|expired (?:cookies|session|login)|会话过期|登录(?:已)?失效'):
        return Failure('login_expired', 'Login expired', message,
                       'Reconnect only the named website with linkget auth SITE, then retry this item.')
    if observed('login_required', r"login[_ -]required|log[ -]?in (?:is )?required|(?:please|must|need to) (?:log[ -]?in|sign in)|(?:account|login) credentials (?:required|needed)|(?:cookies|authentication|authorization)[\"']? (?:are |is )?(?:required|needed)|invalid login credentials|fresh cookies.*needed|redirect to login page|sign in (?:to|if)|登录后|请(?:先)?登录|需要登录"):
        return Failure('login_required', 'Login required', message,
                       'Check the post in your browser; explicitly connect the named website with linkget auth SITE if needed.')
    if code in {401, 403} or observed('access_refused', r'HTTP(?: Error)?[ :]+(?:401|403)|forbidden|blocked by network security'):
        return Failure('access_refused', 'Access refused', message,
                       'Open the post yourself to check access or website verification. The response does not establish expired login.')
    if code in {404, 410} or observed('unavailable', r'HTTP(?: Error)?[ :]+(?:404|410)|unavailable, deleted, or private|video unavailable|post (?:is )?(?:deleted|unavailable)|expired (?:share )?link'):
        return Failure('unavailable', 'Unavailable', message,
                       'Check that the post still opens, or copy a fresh full share link; the server did not provide the requested resource.')
    if observed('unsupported', r'unsupported|no suitable extractor'):
        return Failure('unsupported', 'Unsupported', message,
                       'Check linkget sites and the installed engine version. Keep diagnostics for a linkget or upstream extractor compatibility check.')
    network_types = (TimeoutError, ConnectionError, socket.gaierror, ssl.SSLError, subprocess.TimeoutExpired)
    network_numbers = {errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED,
                       errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EPIPE}
    typed_network = any(isinstance(cause, network_types)
                        or isinstance(cause, OSError) and cause.errno in network_numbers for cause in chain)
    for cause in chain:
        if isinstance(cause, URLError):
            transport, positive, negative = _evidence(cause.reason)
            signals += '\n' + transport
            denied.update(negative)
            flags.update(positive)
    flags.difference_update(denied)
    if typed_network or observed('network', r'timed? out|timeout|connection (?:reset|refused|aborted)|(?:TLS|SSL)[^\n.;,]{0,40}(?:failed|failure|error|refused|timeout)|SSLError|CERTIFICATE_VERIFY_FAILED|dns resolution failed|temporary failure in name resolution|network is unreachable|remote end closed'):
        return Failure('network', 'Network', message,
                       'Check your connection, then retry this item. Existing retained files are kept.', True)
    if observed('extractor_failure', r'unable to extract|extractor (?:error|failed)'):
        return Failure('extractor_failure', 'Extraction failed', message,
                       'Keep diagnostics and check the installed engine version against the compatibility fixtures. The failure does not establish that the URL is unsupported.')
    return Failure('unknown', 'Unknown', message,
                   'The cause could not be determined. Keep retained files and inspect the saved diagnostics before choosing a retry or recovery command.')


def retry_delay(failure, attempt, *, operation, retry_after=None):
    """Return a delay for at most two explicitly read-only retries, else ``None``.

    ``attempt`` is 1 for the first retry and 2 for the last. No callback is run.
    Long/invalid Retry-After values stop rather than retrying before the server's
    instruction. Saves, imports and unknown operations are never eligible.
    """
    if (not isinstance(failure, Failure) or failure.category not in {'network', 'rate_limit'}
            or not failure.retryable or operation != 'read'
            or type(attempt) is not int or attempt not in {1, 2}):
        return None
    delay = float(2 ** (attempt - 1))
    if retry_after is not None:
        if type(retry_after) is bool:
            return None
        try:
            minimum = float(retry_after)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(minimum) or minimum < 0 or minimum > 30:
            return None
        delay = max(delay, minimum)
    return delay
