"""Read-only CLI explanations and inspection boundaries."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from linkget import accounts, cli, rendered_page

class InspectionTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.root=Path(tmp.name)
        for mocked in (patch.object(accounts,'directory',return_value=self.root/'sessions'),
                       patch.object(accounts,'default_browser',return_value='safari'),
                       patch.dict(os.environ,{},clear=False)):
            mocked.start();self.addCleanup(mocked.stop)
        os.environ.pop('LINKGET_RENDERED_BACKEND',None)

    def test_effective_browser_provenance_and_disable_priority(self):
        self.assertEqual(rendered_page.selection()['source'],'system default')
        self.assertFalse(rendered_page.selection()['supported'])
        accounts.set_preferences(browser='chrome')
        self.assertEqual(rendered_page.selection()['source'],'saved browser choice')
        accounts.set_preferences(reader_browser='neo')
        self.assertEqual(rendered_page.selection()['source'],'personal reader preference')
        with patch.dict(os.environ,{'LINKGET_RENDERED_BACKEND':'chrome'}):
            self.assertEqual(rendered_page.selection()['source'],'LINKGET_RENDERED_BACKEND')
            self.assertEqual(rendered_page.selection(browser='chrome')['source'],'command browser option')
            self.assertEqual(rendered_page.selection('neo',browser='chrome')['browser'],'neo')
            self.assertEqual(rendered_page.selection('neo',backend_source='retained checkpoint')['source'],'retained checkpoint')
            self.assertFalse(rendered_page.selection('neo',browser='none')['enabled'])

    def test_config_query_explains_separate_sources_without_credentials_or_writes(self):
        accounts.set_preferences(reader_browser='auto',browser='chrome',sources={'x.com':'chrome/x.com:/private/profile-secret'})
        path=self.root/'preferences.json';before=path.read_bytes()
        out=io.StringIO()
        with patch.object(accounts,'read_session',side_effect=AssertionError('credential read')),patch.object(accounts,'status',side_effect=AssertionError('network')),contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(['config']),0)
        self.assertIn('Effective reader',out.getvalue());self.assertIn('saved browser choice',out.getvalue())
        self.assertIn('Login sources',out.getvalue());self.assertNotIn('profile-secret',out.getvalue())
        self.assertEqual(path.read_bytes(),before)
