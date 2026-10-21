"""Error presentation integration: no credential output or inferred login expiry."""
import argparse
import contextlib
import errno
import io
import subprocess
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from media_stub import uninspected
import unittest

from linkget import accounts, cli


class FailureReportingTests(unittest.TestCase):
    def test_phase_and_typed_disk_error_survive_cli_reporting(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            category = cli.report_failure(OSError(errno.ENOSPC, 'disk full'), phase='download')
        self.assertEqual(category, 'postprocessing')
        self.assertIn('Free space', output.getvalue())
        with contextlib.redirect_stderr(output):
            self.assertEqual(cli.report_failure('codec helper failed', phase='pair'), 'postprocessing')
            self.assertEqual(cli.report_failure(subprocess.TimeoutExpired(['helper'], 1), phase='pair'), 'postprocessing')

    def test_normal_and_login_guidance_redact_remote_credentials(self):
        secret = 'HTTP 403 https://user:pass@example.test/post?xsec_token=private-token\nCookie: custom=private-cookie'
        output = io.StringIO()
        with contextlib.redirect_stderr(output):
            self.assertEqual(cli.report_failure(secret), 'access_refused')
            cli.login_guidance(argparse.Namespace(browser='none'), 'example.test', '', secret)
        for value in ('user:pass', 'private-token', 'private-cookie'):
            self.assertNotIn(value, output.getvalue())
        self.assertIn('does not establish expired login', output.getvalue())

    def test_engine_diagnostics_do_not_guess_login_deletion_or_region(self):
        cases = [('login_required=false; ffmpeg not found', 'unknown'),
                 ('blocked by network security', 'access_refused'),
                 ('cannot determine region metadata', 'unknown'),
                 ('Cookies have expired', 'login_expired')]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for number, (message, expected) in enumerate(cases):
                def engine(command, **kwargs):
                    kwargs['stdout'].write(message)
                    return subprocess.CompletedProcess(command, 1)
                report = root/f'report-{number}.json'
                with self.subTest(message=message), \
                        patch.object(accounts, 'directory', return_value=root/'settings/sessions'), \
                        patch.object(cli.tempfile, 'tempdir', str(root)), patch.object(cli, 'first_run', return_value={}), \
                        patch.object(cli, 'find_tool', return_value='trusted-fake-engine'), \
                        patch.object(cli, 'run_process', side_effect=engine), \
                        patch.object(cli, 'login_guidance') as login, contextlib.redirect_stdout(io.StringIO()), \
                        contextlib.redirect_stderr(io.StringIO()):
                    code = cli.main(['https://www.reddit.com/r/fixture/comments/123/post', '--browser', 'none',
                                     '--folder', str(root/'out'), '--report', str(report)])
                    self.assertEqual(code, 1)
                    self.assertEqual(json.loads(report.read_text())['items'][0]['failure'], expected)
                    self.assertEqual(login.call_count, int(expected == 'login_expired'))

    def test_local_media_consumer_timeout_is_processing_not_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            def download(url, folder, cookies):
                (folder/'fixture.jpg').write_bytes(b'generated fixture')
            args = argparse.Namespace(link='https://www.pixiv.net/artworks/123', folder=root/'out', browser='none',
                                      profile=None, date_now=False, report=None, live_photos=False, redownload=False, page_data=None)
            records = []
            with patch.object(cli.tempfile, 'tempdir', str(root)), \
                    patch.object(cli, 'download_pixiv', side_effect=download), patch.object(cli, 'inspect_files', side_effect=uninspected), \
                    contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                def consume(*args):
                    raise subprocess.TimeoutExpired(['whisper-cli'], 1)
                self.assertEqual(cli.download(args, {}, records, media_consumer=consume), 1)
            self.assertEqual(records[0]['failure'], 'postprocessing')


if __name__ == '__main__':
    unittest.main()
