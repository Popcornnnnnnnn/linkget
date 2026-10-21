"""Opt-in metadata extraction policy. No bundled adapters, network or save code.

Create one Fallback per post attempt. A registered adapter may extract metadata
only; callers must run normal identity/source/completeness validation before any
media transfer or save. Registration never installs or discovers software.
"""
from dataclasses import dataclass, field
from threading import Lock
from typing import Callable, Mapping
from urllib.parse import urlsplit

ELIGIBLE_REASONS = frozenset({'unsupported', 'metadata_unavailable'})


@dataclass(frozen=True)
class Policy:
    enabled: bool = False
    adapter_name: str = ''


@dataclass(frozen=True)
class Request:
    site: str
    post_id: str
    url: str = field(repr=False)


@dataclass(frozen=True)
class FailureContext:
    reason: str
    verified: bool = False
    phase: str = 'extract'
    artifacts_started: bool = False


@dataclass(frozen=True)
class ExtractedPost:
    site: str
    post_id: str
    metadata: Mapping = field(repr=False)


@dataclass(frozen=True)
class Adapter:
    name: str
    sites: frozenset
    reasons: frozenset
    extract: Callable[[Request], ExtractedPost] = field(repr=False)
    license_spdx: str = ''
    compatibility_reviewed: bool = False
    evidence: tuple = ()


@dataclass(frozen=True)
class Outcome:
    attempted: bool
    status: str
    adapter: str = ''
    post: ExtractedPost | None = field(default=None, repr=False)


class Fallback:
    """Use only the explicitly selected adapter, at most once per instance."""
    def __init__(self, policy=Policy(), adapters=()):
        self.policy = policy
        self.adapters = tuple(adapters)
        self._attempted = False
        self._lock = Lock()

    def try_extract(self, request, failure):
        with self._lock:
            status = self._eligibility(request, failure)
            if status:
                return Outcome(False, status)
            selected = [adapter for adapter in self.adapters if adapter.name == self.policy.adapter_name]
            if len(selected) != 1:
                return Outcome(False, 'adapter_not_registered' if not selected else 'ambiguous_adapter')
            adapter = selected[0]
            if request.site not in adapter.sites or failure.reason not in adapter.reasons:
                return Outcome(False, 'adapter_not_applicable')
            if (not adapter.compatibility_reviewed or not adapter.evidence
                    or adapter.license_spdx in {'', 'NONE', 'NOASSERTION'}):
                return Outcome(False, 'adapter_review_required')
            self._attempted = True
        # No retry, alternate adapter, auth refresh, download or save is added.
        try:
            result = adapter.extract(request)
            if (not isinstance(result, ExtractedPost) or result.site != request.site
                    or result.post_id != request.post_id or not isinstance(result.metadata, Mapping)
                    or not result.metadata):
                return Outcome(True, 'invalid_result', adapter.name)
            post = ExtractedPost(result.site, result.post_id, dict(result.metadata))
            return Outcome(True, 'extracted', adapter.name, post)
        except Exception:
            # Adapter errors can contain signed media URLs or credentials. The
            # original primary failure remains the user's diagnostic evidence.
            return Outcome(True, 'adapter_failed', adapter.name)

    def _eligibility(self, request, failure):
        if not self.policy.enabled:
            return 'disabled'
        if not self.policy.adapter_name:
            return 'adapter_not_selected'
        if self._attempted:
            return 'already_attempted'
        if not failure.verified:
            return 'unverified_failure'
        if failure.reason not in ELIGIBLE_REASONS:
            return 'reason_not_eligible'
        if failure.phase != 'extract':
            return 'phase_not_eligible'
        if failure.artifacts_started:
            return 'artifacts_already_started'
        try:
            parsed = urlsplit(request.url)
            valid = (bool(request.site and request.post_id) and parsed.scheme in {'http', 'https'}
                     and bool(parsed.hostname) and not parsed.username and not parsed.password
                     and parsed.port in {None, 80, 443})
        except (TypeError, ValueError):
            valid = False
        if not valid:
            return 'invalid_request'
        return ''
