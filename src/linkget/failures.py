"""Evidence-based failure descriptions and an opt-in read-only retry policy.

This module performs no requests, retries, logging, credential reads or file writes.
Callers must keep side-effecting save/import operations outside automatic retries.
"""
from dataclasses import dataclass
import errno
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
# Deliberately named credential fields, including common API JSON spellings.
# Full-key boundaries preserve business fields such as token_count/client_id.
_SECRET_KEY = (
    r'(?:access|refresh|id|auth(?:orization)?|csrf|xsec|session|bearer)[_-]?token'
    r'|oauth[_-]?token(?:[_-]?secret)?|token|signature|password|passwd'
    r'|(?:api|client|consumer)[_-]?(?:key|secret)|(?:private|secret)[_-]?key'
    r'|secret|session(?:[_-]?id)?|SESSDATA|csrf|csrftoken'
)
_SECRET = re.compile(
    r'''(?<![\w])(["']?)(''' + _SECRET_KEY
    + r''')\1\s*[:=]\s*(?:"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\s,;]+)''', re.I)

_ANSI = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')


def redact(value):
    """Remove request credentials and URL query/fragment data before display."""
    text = str(value).replace("\\/", "/")
    text = _ANSI.sub('', text)
    text = ''.join(c for c in text if c in '\n\t' or ord(c) >= 32 and ord(c) != 127)

    def url(match):
        try:
            parsed = urlsplit(match[0])
            host = parsed.hostname
            if not host:
                return '[redacted URL]'
            # Keep IPv6 notation valid; accessing .port also rejects malformed URLs.
            host = '[' + host + ']' if ':' in host else host
            if parsed.port:
                host += ':' + str(parsed.port)
            return urlunsplit((parsed.scheme, host, parsed.path,
                               '[redacted]' if parsed.query else '',
                               '[redacted]' if parsed.fragment else ''))
        except ValueError:
            return '[redacted URL]'

    text = _URL.sub(url, text)
    text = _HEADER.sub(lambda m: m[1] + ': [redacted]', text)
    text = _SECRET.sub(lambda m: m[2] + '=[redacted]', text)
    text = re.sub(r'\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+', r'\1 [redacted]', text, flags=re.I)
    return text[:2000] + ('…' if len(text) > 2000 else '')


def _affirmative_auth_signals(signals):
    # Authentication diagnostics are often explanatory or structured negatives.
    # Discard an ambiguous clause rather than interpreting a negated requirement
    # (or a JSON login_required=false field) as authorization to reconnect.
    signals = re.sub(r'\bno longer valid\b', 'expired', signals, flags=re.I)
    clauses = re.split(r'[.;!?\n]+', signals)
    negation = r"\b(?:not|no|never|without|false|cannot)\b|\b(?:isn.t|aren.t|wasn.t|weren.t|hasn.t|haven.t|doesn.t|didn.t|don.t|can.t)\b|[:=]\s*(?:0|false|no)\b|不是|并非|没有|无需|不需要|未过期|未失效"
    affirmative = []
    for clause in clauses:
        negative = re.search(negation, clause, re.I)
        conditional = re.search(r'\bsign in if\b', clause, re.I)
        # 'Sign in if not already logged in' is an explicit engine instruction;
        # 'do not sign in if ...' remains negated.
        if not negative or conditional and conditional.start() < negative.start():
            affirmative.append(clause)
    return '\n'.join(affirmative)


def _typed_chain(error):
    """Read at most four explicit causes/reasons; never chase implicit context."""
    seen = set()
    current = error
    for _ in range(4):
        if not isinstance(current, BaseException) or id(current) in seen:
            break
        seen.add(id(current))
        yield current
        reason = current.reason if isinstance(current, URLError) else None
        current = reason if isinstance(reason, BaseException) else current.__cause__


def classify(error, *, phase=None):
    """Classify only observable signals; generic 401/403 never imply expired login.

    ``phase`` is an explicit caller-owned boundary, not inferred from remote text.
    Use save/import/pair/postprocess only after acquisition finished. A 404 states
    that a resource is unavailable, never that its owner deleted the post.
    """
    raw = str(error)
    message = redact(raw) or 'The operation failed without diagnostic details.'
    # URL paths, query fields and credential values are data, not failure signals.
    signals = redact(_URL.sub('[URL]', raw.replace("\\/", "/")))
    auth_signals = _affirmative_auth_signals(signals)
    chain = list(_typed_chain(error))
    disk_full = any(isinstance(cause, OSError) and cause.errno in {errno.ENOSPC, errno.EDQUOT}
                    for cause in chain)
    if disk_full or phase in {'save', 'import', 'pair', 'postprocess'}:
        next_step = 'Keep retained media. Fix the named tool or destination error, then use the printed retry or recovery command.'
        if disk_full:
            next_step = 'Free space in the selected destination, then use the printed recovery command; retained originals are kept.'
        return Failure('postprocessing', 'Save/processing', message, next_step)

    code = next((cause.code for cause in chain if isinstance(cause, HTTPError)), None)
    if code == 429 or re.search(r'HTTP(?: Error)?[ :]+429|too many requests|rate.?limit', signals, re.I):
        return Failure('rate_limit', 'Rate limit', message,
                       'Wait before retrying this item; honor any server retry time.', True)
    if re.search(r'captcha|verification page|verification required|confirm you(?:.re| are) not a (?:bot|robot)|(?:javascript|js|security|browser) challenge', signals, re.I):
        return Failure('website_check', 'Website check', message,
                       'Open the post yourself, complete the website check if shown, then retry this item.')
    if re.search(r'(?:cookies|session|login|authentication)(?: are| is| have| has)? (?:expired|no longer valid)|expired (?:cookies|session|login)|会话过期|登录(?:已)?失效', auth_signals, re.I):
        return Failure('login_expired', 'Login expired', message,
                       'Reconnect only the named website with linkget auth SITE, then retry this item.')
    if re.search(r"login[_ -]required|log[ -]?in (?:is )?required|(?:please|must|need to) (?:log[ -]?in|sign in)|(?:account|login) credentials (?:required|needed)|(?:cookies|authentication|authorization)[\"']? (?:are |is )?(?:required|needed)|invalid login credentials|fresh cookies.*needed|redirect to login page|sign in (?:to|if)|登录后|请(?:先)?登录|需要登录", auth_signals, re.I):
        return Failure('login_required', 'Login required', message,
                       'Check the post in your browser; explicitly connect the named website with linkget auth SITE if needed.')
    if code in {401, 403} or re.search(r'HTTP(?: Error)?[ :]+(?:401|403)|forbidden|blocked by network security', signals, re.I):
        return Failure('access_refused', 'Access refused', message,
                       'Open the post yourself to check access or website verification. The response does not establish expired login.')
    if code in {404, 410} or re.search(r'HTTP(?: Error)?[ :]+(?:404|410)|unavailable, deleted, or private|video unavailable|post (?:is )?(?:deleted|unavailable)|expired (?:share )?link', signals, re.I):
        return Failure('unavailable', 'Unavailable', message,
                       'Check that the post still opens, or copy a fresh full share link; the server did not provide the requested resource.')
    if re.search(r'unsupported|not supported|not implemented|no suitable extractor', signals, re.I):
        return Failure('unsupported', 'Unsupported', message,
                       'Check linkget sites and the installed engine version. Keep diagnostics for a linkget or upstream extractor compatibility check.')
    network_types = (TimeoutError, ConnectionError, socket.gaierror, ssl.SSLError, subprocess.TimeoutExpired)
    network_numbers = {errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED,
                       errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EPIPE}
    typed_network = any(isinstance(cause, network_types)
                        or isinstance(cause, OSError) and cause.errno in network_numbers for cause in chain)
    # URLError is a trusted transport exception, but a text reason still needs a
    # recognized network signal. Arbitrary RuntimeError.reason fields are ignored.
    transport_signals = signals + '\n' + '\n'.join(
        redact(_URL.sub('[URL]', str(cause.reason))) for cause in chain if isinstance(cause, URLError))
    if typed_network or re.search(
            r'timed? out|timeout|connection (?:reset|refused|aborted)|TLS|SSL|could not resolve|temporary failure in name resolution|network is unreachable|remote end closed', transport_signals, re.I):
        return Failure('network', 'Network', message,
                       'Check your connection, then retry this item. Existing retained files are kept.', True)
    if re.search(r'unable to extract|extractor (?:error|failed)', signals, re.I):
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
