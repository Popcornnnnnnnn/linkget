"""Read-only CLI explanations and inspection boundaries."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from linkget import accounts, cli, rendered_page, diagnostics

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
        self.assertIn('Login browser sources',out.getvalue());self.assertNotIn('profile-secret',out.getvalue())
        self.assertEqual(path.read_bytes(),before)

    def test_default_diagnostics_neither_read_credentials_nor_probe_connections(self):
        self.root.joinpath('sessions').mkdir()
        secret=accounts.path_for('x.com');secret.write_text('not-a-real-secret')
        before=secret.read_bytes()
        with patch.object(accounts,'status',side_effect=AssertionError('network')),patch.object(accounts,'read_session',side_effect=AssertionError('credential read')),patch.object(diagnostics,'browser_probe',side_effect=AssertionError('browser probe')):
            rows,code=diagnostics.inspect()
        output=str(rows)
        self.assertEqual(code,0);self.assertIn('validity unknown',output)
        self.assertNotIn('not-a-real-secret',output);self.assertEqual(secret.read_bytes(),before)

    def test_live_diagnostics_are_single_target_and_respect_disable_and_logout(self):
        from types import SimpleNamespace
        args=SimpleNamespace(browser='auto',check_session='bilibili',probe_browser=None)
        with patch.object(accounts,'status',return_value=accounts.Session('invalid','Expired')) as check:
            rows,code=diagnostics.inspect(args)
        check.assert_called_once_with('bilibili.com');self.assertEqual(code,1)
        self.assertIn('Expired',str(rows));self.assertIn('reconnection',str(rows))
        for browser,blocked in [('none',[]),('auto',['bilibili.com'])]:
            accounts.set_preferences(blocked=blocked)
            args.browser=browser;args.probe_browser='neo'
            with patch.object(accounts,'status',side_effect=AssertionError('disabled session')):
                with patch.object(diagnostics,'browser_probe',return_value=('connected','MCP only')) as probe:
                    rows,code=diagnostics.inspect(args)
            if browser=='none':probe.assert_not_called()
            else:probe.assert_called_once_with('neo')

    def test_neo_probe_checks_transport_only(self):
        from linkget import neo_browser
        with patch.object(neo_browser,'_RPCClient') as factory:
            client=factory.return_value;client.server_version='0.0.69'
            state,detail=diagnostics.browser_probe('neo')
        client.initialize.assert_called_once();client.call.assert_not_called()
        self.assertEqual(state,'connected');self.assertIn('not tested',detail)

    def test_chrome_probe_requires_nonprompt_permission_confirmation(self):
        with patch.object(diagnostics,'chrome_automation_access',return_value='unknown'),patch.object(diagnostics.subprocess,'run') as control:
            state,detail=diagnostics.browser_probe('chrome')
        self.assertEqual(state,'not checked');control.assert_not_called()
        from subprocess import CompletedProcess
        with patch.object(diagnostics,'chrome_automation_access',return_value='granted'),patch.object(diagnostics.subprocess,'run',return_value=CompletedProcess([],0,'1','')) as control:
            state,detail=diagnostics.browser_probe('chrome')
        self.assertEqual(state,'control responded');self.assertIn('not tested',detail)
        self.assertIn('javascript "1"',control.call_args.args[0][-1])
