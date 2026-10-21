"""Offline fake extractors: no third-party imports, HTTP, media or Photos."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import unittest
from unittest.mock import Mock

from linkget.extractor_fallback import Adapter, ExtractedPost, FailureContext, Fallback, Policy, Request


class ExtractorFallbackTests(unittest.TestCase):
    def setUp(self):
        self.request = Request('xiaohongshu', 'fixture123', 'https://www.xiaohongshu.com/explore/fixture123?xsec_token=secret')
        self.failure = FailureContext('metadata_unavailable', verified=True)
        self.extract = Mock(return_value=ExtractedPost('xiaohongshu', 'fixture123', {'images': ['synthetic']}))
        self.adapter = Adapter('offline-fixture', frozenset({'xiaohongshu'}),
                               frozenset({'metadata_unavailable', 'unsupported'}), self.extract,
                               'MIT', True, ('synthetic:test_extractor_fallback',))
        self.policy = Policy(True, 'offline-fixture')

    def fallback(self, **kwargs):
        return Fallback(kwargs.get('policy', self.policy), kwargs.get('adapters', (self.adapter,)))

    def test_default_and_unregistered_opt_in_make_no_requests(self):
        for fallback, expected in ((Fallback(adapters=[self.adapter]), 'disabled'),
                                   (Fallback(Policy(True), [self.adapter]), 'adapter_not_selected'),
                                   (Fallback(self.policy), 'adapter_not_registered')):
            with self.subTest(expected=expected):
                result = fallback.try_extract(self.request, self.failure)
                self.assertFalse(result.attempted)
                self.assertEqual(result.status, expected)
        self.extract.assert_not_called()

    def test_only_confirmed_extraction_gap_is_eligible(self):
        contexts = [replace(self.failure, verified=False),
                    replace(self.failure, phase='transfer'), replace(self.failure, phase='save'),
                    replace(self.failure, artifacts_started=True)]
        contexts += [replace(self.failure, reason=reason) for reason in
                     ('login_required', 'login_expired', 'access_refused', 'rate_limit', 'website_check',
                      'network', 'unknown', 'postprocessing', 'unavailable')]
        for context in contexts:
            with self.subTest(context=context):
                result = self.fallback().try_extract(self.request, context)
                self.assertFalse(result.attempted)
        self.extract.assert_not_called()

    def test_review_and_scope_are_required(self):
        adapters = [replace(self.adapter, compatibility_reviewed=False),
                    replace(self.adapter, license_spdx='NOASSERTION'),
                    replace(self.adapter, evidence=()), replace(self.adapter, sites=frozenset({'douyin'})),
                    replace(self.adapter, reasons=frozenset({'unsupported'}))]
        for adapter in adapters:
            with self.subTest(adapter=adapter):
                result = self.fallback(adapters=[adapter]).try_extract(self.request, self.failure)
                self.assertFalse(result.attempted)
        duplicate = self.fallback(adapters=[self.adapter, self.adapter]).try_extract(self.request, self.failure)
        self.assertEqual(duplicate.status, 'ambiguous_adapter')
        self.extract.assert_not_called()

    def test_one_selected_adapter_produces_metadata_not_a_saved_download(self):
        other = Mock()
        fallback = self.fallback(adapters=[replace(self.adapter, name='other', extract=other), self.adapter])
        first = fallback.try_extract(self.request, self.failure)
        self.assertTrue(first.attempted)
        self.assertEqual(first.status, 'extracted')
        self.assertEqual(first.post.metadata, {'images': ['synthetic']})
        self.assertEqual(fallback.try_extract(self.request, self.failure).status, 'already_attempted')
        self.extract.assert_called_once_with(self.request)
        other.assert_not_called()

    def test_failure_is_bounded_and_never_reveals_exception_credentials(self):
        self.extract.side_effect = RuntimeError('Cookie: private; https://example.test/?token=secret')
        fallback = self.fallback()
        result = fallback.try_extract(self.request, self.failure)
        self.assertEqual(result.status, 'adapter_failed')
        self.assertIsNone(result.post)
        self.assertNotIn('private', repr(result))
        self.assertNotIn('secret', repr(result))
        self.assertEqual(fallback.try_extract(self.request, self.failure).status, 'already_attempted')
        self.extract.assert_called_once()

    def test_wrong_post_or_invalid_payload_never_becomes_usable_metadata(self):
        for result in (None, {}, ExtractedPost('douyin', 'fixture123', {'images': []}),
                       ExtractedPost('xiaohongshu', 'different', {'images': []}),
                       ExtractedPost('xiaohongshu', 'fixture123', {})):
            with self.subTest(result=result):
                self.extract.return_value = result
                outcome = self.fallback().try_extract(self.request, self.failure)
                self.assertEqual(outcome.status, 'invalid_result')
                self.assertIsNone(outcome.post)

    def test_cancelled_adapter_is_not_swallowed_or_repeated(self):
        self.extract.side_effect = KeyboardInterrupt
        fallback = self.fallback()
        with self.assertRaises(KeyboardInterrupt):
            fallback.try_extract(self.request, self.failure)
        self.assertEqual(fallback.try_extract(self.request, self.failure).status, 'already_attempted')
        self.extract.assert_called_once()

    def test_private_request_and_metadata_are_not_in_repr(self):
        result = self.fallback().try_extract(self.request, self.failure)
        self.assertNotIn('secret', repr(self.request))
        self.assertNotIn('synthetic', repr(result))
        self.assertNotIn('synthetic', repr(result.post))

    def test_unsafe_urls_do_not_reach_adapter(self):
        for url in ('file:///tmp/input', 'http://name:secret@example.test/post',
                    'https://example.test:8443/post', 'https://example.test:bad/post'):
            with self.subTest(url=url):
                result = self.fallback().try_extract(replace(self.request, url=url), self.failure)
                self.assertEqual(result.status, 'invalid_request')
        self.extract.assert_not_called()

    def test_concurrent_calls_still_make_only_one_attempt(self):
        entered = threading.Event()
        release = threading.Event()
        def extract(request):
            entered.set()
            self.assertTrue(release.wait(5))
            return ExtractedPost(request.site, request.post_id, {'images': ['fixture']})
        callback = Mock(side_effect=extract)
        fallback = self.fallback(adapters=[replace(self.adapter, extract=callback)])
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(fallback.try_extract, self.request, self.failure)
            self.assertTrue(entered.wait(5))
            second = pool.submit(fallback.try_extract, self.request, self.failure)
            self.assertEqual(second.result(5).status, 'already_attempted')
            release.set()
            self.assertEqual(first.result(5).status, 'extracted')
        callback.assert_called_once()


if __name__ == '__main__':
    unittest.main()
