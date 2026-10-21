"""Evidence and redaction boundaries; no network, credentials or Photos access."""
import contextlib
import errno
import io
import json
import socket
import subprocess
import unittest
from urllib.error import HTTPError, URLError

from linkget.failures import classify, redact, retry_delay


class FailureEvidenceTests(unittest.TestCase):
    def test_http_refusal_does_not_infer_login_expiry_or_attempt_retry(self):
        for code in (401, 403):
            http_error = HTTPError('https://example.test/post?token=secret', code, 'refused', {}, None)
            self.addCleanup(http_error.close)
            for value in (f'HTTP Error {code}: Forbidden', http_error):
                with self.subTest(code=code, value=type(value).__name__):
                    failure = classify(value)
                    self.assertEqual(failure.category, 'access_refused')
                    self.assertFalse(failure.retryable)
                    self.assertNotIn('secret', failure.message)
                    self.assertIsNone(retry_delay(failure, 1, operation='read'))

    def test_explicit_login_and_expiry_are_separate_from_generic_refusal(self):
        for text, expected in (('Login required', 'login_required'), ('Please sign in', 'login_required'),
                               ('Cookies have expired', 'login_expired'), ('session is no longer valid', 'login_expired'),
                               ('登录已失效', 'login_expired')):
            self.assertEqual(classify(text).category, expected)
        self.assertEqual(classify('HTTP 403: CAPTCHA verification required').category, 'website_check')

    def test_explicit_engine_login_requirements_remain_recognized(self):
        for message in ('Cookies required', 'cookies are needed', 'authentication is required',
                        'authorization needed', 'Redirect to login page', 'Sign in if needed',
                        'ERROR: Sign in if you are not already logged in', 'fresh cookies are needed'):
            with self.subTest(message=message):
                self.assertEqual(classify(message).category, 'login_required')
        for message in ("Sign in to confirm you're not a bot", 'Sign in to confirm you are not a robot'):
            self.assertEqual(classify(message).category, 'website_check')

    def test_negated_login_diagnostics_do_not_request_reconnection(self):
        for text in ('not login expired', 'no login required', 'There is no evidence of expired cookies',
                     'This does not prove login expired', 'cookies are not expired',
                     'login_required=false', '{"login_required": false}', 'login required: 0',
                     'expired cookies were not observed', "You don't need to sign in", 'Do not sign in if asked', '不是会话过期', '并非登录已失效'):
            with self.subTest(text=text):
                result = classify(text)
                self.assertEqual(result.category, 'unknown')
                self.assertEqual(result.label, 'Unknown')
                self.assertNotIn('auth SITE', result.next_step)
        self.assertEqual(classify('not login expired; HTTP 403').category, 'access_refused')

    def test_http_error_reason_keeps_explicit_evidence_but_not_negations(self):
        for reason, expected in (('login required', 'login_required'), ('cookies expired', 'login_expired'),
                                 ('not login expired', 'access_refused'), ('Not Found', 'access_refused')):
            error = HTTPError('https://example.test/rate_limit?token=secret', 403, reason, {}, None)
            self.addCleanup(error.close)
            self.assertEqual(classify(error).category, expected)

    def test_other_known_signals_keep_categories_without_claiming_a_root_cause(self):
        for message, expected in (('HTTP Error 429: Too Many Requests', 'rate_limit'),
                                  ('HTTP Error 404: Not Found', 'unavailable'), ('HTTP 410', 'unavailable'),
                                  ('Unsupported URL', 'unsupported'), ('Unable to extract player JSON', 'extractor_failure'),
                                  ('Connection reset by peer', 'network'), ('unknown result: 123', 'unknown')):
            with self.subTest(message=message):
                result = classify(message)
                self.assertEqual(result.category, expected)
                self.assertTrue(result.next_step)
        failure = classify('new server shape')
        self.assertIn('could not be determined', failure.next_step)

    def test_extraction_failure_is_not_proof_of_an_unsupported_url(self):
        for text in ('Unable to extract player JSON', 'Extractor failed', 'extractor error: shape changed'):
            with self.subTest(text=text):
                result = classify(text)
                self.assertEqual(result.category, 'extractor_failure')
                self.assertIn('compatibility fixtures', result.next_step)
                self.assertIn('does not establish', result.next_step)
                self.assertIsNone(retry_delay(result, 1, operation='read'))
        for text in ('Unsupported URL', 'No suitable extractor', 'Media type not supported'):
            self.assertEqual(classify(text).category, 'unsupported')
        self.assertEqual(classify('Unable to extract: HTTP Error 403').category, 'access_refused')
        self.assertEqual(classify('Extractor failed: connection timed out').category, 'network')

    def test_url_and_credential_contents_cannot_select_a_failure_category(self):
        for message in (
                'Transfer failed for https://example.test/rate_limit?reason=login_required',
                'Transfer failed for https://example.test/unsupported#captcha',
                'Transfer failed; Cookie: note=login required; detail=HTTP 429',
                'Transfer failed; token="HTTP 404"'):
            with self.subTest(message=message):
                self.assertEqual(classify(message).category, 'unknown')

    def test_typed_network_failure_is_classified_without_english_exception_text(self):
        for error in (TimeoutError(), ConnectionResetError(), URLError(socket.gaierror(-2, 'lookup failed')),
                      subprocess.TimeoutExpired(['engine'], 10), OSError(errno.ENETUNREACH, 'localized message')):
            self.assertEqual(classify(error).category, 'network')

    def wrapped(self, cause):
        error = RuntimeError('adapter could not finish; retained files are safe')
        error.__cause__ = cause
        return error

    def test_wrapped_typed_disk_and_transport_failures_keep_their_evidence(self):
        for number in (errno.ENOSPC, errno.EDQUOT):
            error = self.wrapped(OSError(number, 'localized disk failure'))
            result = classify(error, phase='download')
            self.assertEqual(result.category, 'postprocessing')
            self.assertIn('Free space', result.next_step)
            self.assertFalse(result.retryable)
        for cause in (TimeoutError(), URLError(TimeoutError()),
                      URLError(socket.gaierror(-2, 'localized lookup failure')),
                      URLError(OSError(errno.ENETUNREACH, 'localized network failure')),
                      URLError('connection reset by peer')):
            with self.subTest(cause=type(cause).__name__):
                self.assertEqual(classify(self.wrapped(cause)).category, 'network')
        http = HTTPError('https://example.test/post?token=secret', 429, 'reply', {}, None)
        self.addCleanup(http.close)
        self.assertEqual(classify(self.wrapped(http)).category, 'rate_limit')

    def test_cause_traversal_is_bounded_and_ignores_fake_fields_and_implicit_context(self):
        fake = RuntimeError('adapter failed')
        fake.errno, fake.reason, fake.code = errno.ENOSPC, TimeoutError(), 429
        self.assertEqual(classify(fake).category, 'unknown')
        fake.__context__ = OSError(errno.ENOSPC, 'implicit context')
        self.assertEqual(classify(fake).category, 'unknown')
        for description in ('ENOSPC', 'disk failure', 'postprocessing'):
            self.assertEqual(classify(self.wrapped(RuntimeError(description))).category, 'unknown')
        shallow = OSError(errno.ENOSPC, 'localized')
        for _ in range(3):
            shallow = self.wrapped(shallow)
        self.assertEqual(classify(shallow).category, 'postprocessing')
        self.assertEqual(classify(self.wrapped(shallow)).category, 'unknown')
        cycle = RuntimeError('cyclic cause')
        cycle.__cause__ = cycle
        self.assertEqual(classify(cycle).category, 'unknown')

    def test_postprocessing_is_a_caller_owned_boundary_and_never_auto_retried(self):
        for phase in ('save', 'import', 'pair', 'postprocess'):
            result = classify(TimeoutError('timed out'), phase=phase)
            self.assertEqual(result.category, 'postprocessing')
            self.assertIsNone(retry_delay(result, 1, operation='read'))
        for number in (errno.ENOSPC, errno.EDQUOT):
            result = classify(OSError(number, 'localized message'), phase='download')
            self.assertEqual(result.category, 'postprocessing')
            self.assertIn('Free space', result.next_step)
        self.assertEqual(classify('postprocess might have failed').category, 'unknown')

    def test_redaction_removes_url_userinfo_queries_and_fragments(self):
        message = 'HTTP 403 https://user:password@example.test/post/123?xsec_token=private&signature=secret#hidden'
        safe = redact(message)
        self.assertIn('example.test/post/123', safe)
        for value in ('user:', 'password', 'private', 'signature', 'secret', 'hidden'):
            self.assertNotIn(value, safe)
        self.assertEqual(classify(message).category, 'access_refused')

    def test_redaction_handles_headers_json_credentials_bearer_and_terminal_controls(self):
        message = ('Cookie: SESSDATA=private-cookie; sessionid=also-private\n'
                   'Authorization: Bearer private-bearer\n'
                   'Set-Cookie: session=renewed; HttpOnly\n'
                   '{"api_key": "private-api", "access_token": "private-token"}\n'
                   'token=unquoted-secret; password=private-password\n'
                   'Basic cHJpdmF0ZQ==\n\x1b[31mError\x00')
        safe = redact(message)
        for value in ('private-cookie', 'also-private', 'private-bearer', 'renewed', 'private-api',
                      'private-token', 'unquoted-secret', 'private-password', 'cHJpdmF0ZQ==', '\x1b', '\x00'):
            self.assertNotIn(value, safe)
        self.assertIn('omitted', safe)

    def test_escaped_json_and_folded_cookie_headers_do_not_leak_values(self):
        cases = [
            ('{"api_key": "value\\\"still-secret"}', 'still-secret'),
            ('Cookie: first=hidden\n second=folded-secret', 'folded-secret'),
            ('cookies: {"custom_name": "cookie-secret"}', 'cookie-secret'),
            ('https:\\/\\/example.test/media?opaque=unrecognized-secret', 'unrecognized-secret'),
        ]
        for message, secret in cases:
            with self.subTest(message=message):
                self.assertNotIn(secret, redact(message))

    def test_json_secret_keys_support_explicit_snake_camel_and_case_aliases(self):
        keys = ('refresh_token', 'refreshToken', 'REFRESH_TOKEN', 'id_token', 'idToken', 'ID_TOKEN',
                'access_token', 'accessToken', 'ACCESS_TOKEN', 'client_secret', 'clientSecret',
                'CLIENT_SECRET', 'xsecToken', 'xsec_token', 'XSECTOKEN', 'csrfToken', 'csrf_token',
                'authToken', 'authorization_token', 'apiKey', 'api_secret', 'consumerSecret',
                'session_id', 'sessionId', 'sessionToken', 'bearerToken', 'oauth_token_secret',
                'private_key', 'secretKey', 'password', 'SESSDATA', 'csrf')
        for key in keys:
            secret = 'SYNTHETIC_VALUE_' + key
            with self.subTest(key=key):
                safe = redact(json.dumps({key: secret}))
                self.assertNotIn(secret, safe)
                self.assertIn('[redacted]', safe)
                self.assertNotIn(secret, classify(json.dumps({key: secret})).message)

    def test_named_secret_matching_preserves_unrelated_business_fields(self):
        message = json.dumps({'token_count': 27, 'model': 'fixture-model', 'request_id': 'fixture-id',
                              'client_id': 'public-client', 'id': '123', 'access_level': 'public',
                              'caption': 'fixture caption', 'not_secret': 'ordinary text',
                              'metadataToken': 'business token label'})
        self.assertEqual(redact(message), message)

    def test_structured_credentials_redact_whole_nested_values_in_native_wrappers(self):
        structures = (
            {'session': {'value': 'SYNTHETIC_SECRET', 'other': ['OTHER_SECRET']}},
            {'access_token': ['SYNTHETIC_SECRET', 'OTHER_SECRET']},
            {'credentials': [{'name': 'account', 'value': 'SYNTHETIC_SECRET'}, ['OTHER_SECRET']]},
            {'error': {'message': 'request failed', 'context': {'session': {'value': 'SYNTHETIC_SECRET'}}}},
            {'headers': {'Cookie': {'value': 'SYNTHETIC_SECRET'}, 'X-API-Key': ['OTHER_SECRET']}},
        )
        for data in structures:
            for value in (data, json.dumps(data), str(data), URLError(json.dumps(data)),
                          RuntimeError('native transfer failed: ' + str(URLError(json.dumps(data))))):
                with self.subTest(data=data, kind=type(value).__name__):
                    safe = redact(value)
                    self.assertNotIn('SYNTHETIC_SECRET', safe)
                    self.assertNotIn('OTHER_SECRET', safe)
                    self.assertNotIn('SYNTHETIC_SECRET', classify(value).message)

    def test_real_newlines_escaped_strings_and_later_array_items_do_not_leak(self):
        diagnostics = (
            '{"token": "LINE1\nLINE2_SECRET"}',
            '{"token": ["SYNTHETIC_SECRET", "OTHER_SECRET", {"last": "LAST_SECRET"}]}',
            '{"message": "{\\"session\\": {\\"value\\": \\"SYNTHETIC_SECRET\\"}}"}',
            'token=LINE1\nLINE2_SECRET',
            'Authorization: Bearer LINE1\nLINE2_SECRET',
        )
        for message in diagnostics:
            with self.subTest(message=message):
                safe = redact(URLError(message))
                for secret in ('LINE2_SECRET', 'SYNTHETIC_SECRET', 'OTHER_SECRET', 'LAST_SECRET'):
                    self.assertNotIn(secret, safe)

    def test_unparseable_sensitive_diagnostics_fail_closed_instead_of_retaining_tail(self):
        for message in ('{"session": {"value": "SYNTHETIC_SECRET"',
                        '{"token": "LINE1\nLINE2_SECRET',
                        'cookies: prefix {"value": "SYNTHETIC_SECRET"}',
                        'access_token=["first", "OTHER_SECRET"',
                        '{"\\u0074oken": "SYNTHETIC_SECRET"'):
            with self.subTest(message=message):
                safe = redact(message)
                self.assertIn('omitted', safe)
                for secret in ('SYNTHETIC_SECRET', 'OTHER_SECRET', 'LINE2_SECRET'):
                    self.assertNotIn(secret, safe)

    def test_classification_retains_only_clear_evidence_before_opaque_credentials(self):
        for diagnostic in ('HTTP 403 https://example.test/post?token=hidden\nCookie: sensitive',
                           'HTTP 403\nAuthorization: Bearer sensitive\nHTTP 429',
                           'Connection reset by peer\nCookie: HTTP 404; login required'):
            with self.subTest(diagnostic=diagnostic):
                expected = 'network' if diagnostic.startswith('Connection') else 'access_refused'
                result = classify(diagnostic)
                self.assertEqual(result.category, expected)
                self.assertNotIn('sensitive', result.message)
        self.assertEqual(classify('Cookie: opaque\nHTTP 403').category, 'unknown')

    def test_native_error_reporting_and_engine_auth_use_the_safe_shared_policy(self):
        from linkget import cli, engines
        reason = URLError('{"session": {"value": "SYNTHETIC_SECRET"}, "token": ["first", "OTHER_SECRET"]}')
        outer = RuntimeError('Native transfer failed: ' + str(reason))
        outer.__cause__ = reason
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            cli.report_failure(outer)
        self.assertNotIn('SYNTHETIC_SECRET', output.getvalue())
        self.assertNotIn('OTHER_SECRET', output.getvalue())
        self.assertIn('Next', output.getvalue())
        self.assertTrue(engines.needs_login('{"login_required": true, "rate_limit": false}'))
        self.assertFalse(engines.needs_login('Client configured with ratelimit=10; Unable to extract JSON'))

    def test_structured_redaction_preserves_safe_context_and_handles_cyclic_values(self):
        data = {'post_id': '123', 'message': 'HTTP 403', 'resources': [{'kind': 'image', 'width': 16}],
                'session': {'value': 'SYNTHETIC_SECRET'}}
        safe = json.loads(redact(data))
        self.assertEqual(safe['post_id'], '123')
        self.assertEqual(safe['resources'], [{'kind': 'image', 'width': 16}])
        self.assertEqual(safe['session'], '[redacted]')
        cyclic = {}
        cyclic['self'] = cyclic
        self.assertIn('omitted', redact(cyclic))

    def test_response_flags_keep_affirmative_auth_separate_from_false_rate_evidence(self):
        for data in ({'login_required': True, 'rate_limit': False},
                     {'rate_limit': False, 'login_required': True},
                     {'error': {'login_required': True, 'rate_limited': False}},
                     {'login_required': True, 'message': 'not rate limited'}):
            for value in (data, json.dumps(data), str(data)):
                with self.subTest(value=value):
                    result = classify(value)
                    self.assertEqual(result.category, 'login_required')
                    self.assertIsNone(retry_delay(result, 1, operation='read'))
        self.assertEqual(classify('rate_limit=false; login_required=true').category, 'login_required')
        self.assertEqual(classify({'rate_limit': True, 'login_required': False}).category, 'rate_limit')

    def test_false_structured_flags_never_override_a_separate_positive_failure(self):
        for key in ('rate_limit', 'captcha', 'verification_required', 'unsupported', 'unavailable',
                    'extractor_failed', 'network_error', 'access_refused'):
            with self.subTest(key=key):
                self.assertEqual(classify(json.dumps({key: False})).category, 'unknown')
                self.assertEqual(classify(json.dumps({key: False, 'login_required': True})).category, 'login_required')
        self.assertEqual(classify({'description': 'login required'}).category, 'unknown')

    def test_configuration_values_are_not_observed_failures(self):
        for text in ('ratelimit=10', 'rate_limit="10"', 'rate limit set to 10',
                     'timeout=10', 'timeout setting 30', 'TLS=true', 'SSL=false',
                     '{"config": {"rate_limit": true, "timeout": 30}}'):
            with self.subTest(text=text):
                self.assertEqual(classify(text).category, 'unknown')
        result = classify('Client configured with ratelimit=10; Unable to extract JSON')
        self.assertEqual(result.category, 'extractor_failure')
        self.assertIsNone(retry_delay(result, 1, operation='read'))
        self.assertEqual(classify('TLS handshake failed').category, 'network')

    def test_negative_evidence_does_not_select_any_failure_category(self):
        for text in ('not rate limited', 'no rate limit', 'rate_limit=false', 'captcha=false',
                     'no CAPTCHA challenge', 'CAPTCHA disabled', 'verification_required=false',
                     'not unsupported', 'unsupported=false', 'unavailable=false', 'not HTTP 404',
                     'no network timeout', 'network_error=false', 'extractor_failed=false',
                     'not unable to extract', 'not forbidden', 'access_refused=false'):
            with self.subTest(text=text):
                self.assertEqual(classify(text).category, 'unknown')
        self.assertEqual(classify('not rate limited, login required').category, 'login_required')
        self.assertEqual(classify('login not required but HTTP 429').category, 'rate_limit')
        # A typed transport status is stronger evidence than a contradictory body flag.
        error = HTTPError('https://example.test/post', 429, 'rate_limit=false', {}, None)
        self.addCleanup(error.close)
        self.assertEqual(classify(error).category, 'rate_limit')

    def test_redaction_bounds_large_unknown_diagnostics(self):
        result = classify('x'*5000 + ' token=private')
        self.assertLessEqual(len(result.message), 2001)
        self.assertNotIn('private', result.message)
        self.assertEqual(result.category, 'unknown')


class ReadRetryPolicyTests(unittest.TestCase):
    def test_explicit_read_retries_are_finite_and_back_off(self):
        for value in ('HTTP 429', TimeoutError()):
            failure = classify(value)
            self.assertEqual(retry_delay(failure, 1, operation='read'), 1)
            self.assertEqual(retry_delay(failure, 2, operation='read'), 2)
            self.assertIsNone(retry_delay(failure, 3, operation='read'))
            for invalid in (0, -1, True, 1.0, '1'):
                self.assertIsNone(retry_delay(failure, invalid, operation='read'))

    def test_import_save_or_unknown_operation_never_retries(self):
        failure = classify('HTTP 429')
        for operation in ('save', 'import', 'pair', 'download', None):
            self.assertIsNone(retry_delay(failure, 1, operation=operation))
        for text in ('HTTP 403', 'Login required', 'CAPTCHA', 'HTTP 404', 'unexpected failure'):
            self.assertIsNone(retry_delay(classify(text), 1, operation='read'))

    def test_retry_after_is_honored_or_stops_without_shortening_server_delay(self):
        failure = classify('HTTP 429')
        self.assertEqual(retry_delay(failure, 1, operation='read', retry_after=5), 5)
        self.assertEqual(retry_delay(failure, 2, operation='read', retry_after=0), 2)
        for value in (31, 3600, -1, True, 'not seconds', 'nan', float('inf')):
            self.assertIsNone(retry_delay(failure, 1, operation='read', retry_after=value))


if __name__ == '__main__':
    unittest.main()
