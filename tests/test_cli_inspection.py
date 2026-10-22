"""Read-only CLI explanations and inspection boundaries."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from linkget import accounts, cli, rendered_page, diagnostics, build_info, jobs, ownership, retention, staging, tasks

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
            self.assertEqual(rendered_page.selection(browser='chrome',browser_source='retained checkpoint')['source'],'retained checkpoint')
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

    def test_chrome_probe_never_sends_events_even_when_os_access_is_granted(self):
        for access,expected in [('unknown','not checked'),('granted','OS access checked')]:
            with patch.object(diagnostics,'chrome_automation_access',return_value=access),patch.object(cli.subprocess,'run',side_effect=AssertionError('no control sender')):
                state,detail=diagnostics.browser_probe('chrome')
            self.assertEqual(state,expected);self.assertIn('no ',detail)

    def test_mac_automation_descriptor_matches_sdk_packed_abi(self):
        import ctypes
        with patch.object(diagnostics.sys,'platform','darwin'),patch.object(diagnostics.ctypes,'CDLL') as load:
            api=load.return_value
            def create(code,target,size,descriptor):
                self.assertEqual(ctypes.sizeof(descriptor._obj),12)
                self.assertEqual(type(descriptor._obj).dataHandle.offset,4)
                return 0
            api.AECreateDesc.side_effect=create
            api.AEDeterminePermissionToAutomateTarget.return_value=0
            self.assertEqual(diagnostics.chrome_automation_access(),'granted')
            self.assertIs(api.AECreateDesc.restype,ctypes.c_int16)
            self.assertIs(api.AEDisposeDesc.restype,ctypes.c_int16)
            self.assertFalse(api.AEDeterminePermissionToAutomateTarget.call_args.args[-1])

    def test_build_identity_is_local_validated_and_whitelisted(self):
        package=self.root/'keg/libexec/linkget';package.mkdir(parents=True)
        info=package.parent.parent/'BUILD_IDENTITY.json'
        data={'version':cli.VERSION,'homebrew_revision':7,'source_commit':'a'*40,'reviewed_head':'b'*40,'secret':'do-not-output'}
        info.write_text(json.dumps(data))
        with patch.object(build_info,'__file__',str(package/'build_info.py')):
            actual=build_info.describe()
            self.assertEqual(actual['build_revision'],7);self.assertEqual(actual['source_commit'],'a'*40)
            self.assertNotIn('secret',actual)
            data['version']='wrong';info.write_text(json.dumps(data))
            self.assertIsNone(build_info.describe()['source_commit'])
            info.unlink();info.symlink_to(self.root/'private')
            self.assertIsNone(build_info.describe()['source_commit'])

    def test_source_and_wheel_identity_have_honest_unknown_commit_and_short_version_stays(self):
        for relative,expected in [('source/src/linkget','source tree'),('env/lib/python/site-packages/linkget','Python package')]:
            package=self.root/relative;package.mkdir(parents=True)
            if expected=='source tree':(package.parent.parent/'pyproject.toml').write_text('fixture')
            with patch.object(build_info,'__file__',str(package/'build_info.py')):
                data=build_info.describe()
            self.assertEqual(data['installation'],expected);self.assertIsNone(data['source_commit'])
        out=io.StringIO()
        with contextlib.redirect_stdout(out),self.assertRaises(SystemExit) as result:
            cli.main(['--version'])
        self.assertEqual(result.exception.code,0);self.assertEqual(out.getvalue().strip(),'linkget '+cli.VERSION)
        out=io.StringIO()
        with contextlib.redirect_stdout(out):self.assertEqual(cli.main(['version','--json']),0)
        self.assertEqual(json.loads(out.getvalue())['version'],cli.VERSION)

    def test_command_help_is_focused_and_never_inspects_user_data(self):
        for command,present,absent in [('config','--reader-browser','--jobs'),('doctor','--probe-browser','--jobs'),('retry','--redownload','--reader-browser'),('recover','--verify','--redownload'),('batch','--jobs','--check-session')]:
            out=io.StringIO()
            with patch.object(accounts,'preferences',side_effect=AssertionError('config read')),contextlib.redirect_stdout(out):
                self.assertEqual(cli.main([command,'--help']),0)
            self.assertIn('linkget '+command,out.getvalue());self.assertIn(present,out.getvalue());self.assertNotIn(absent,out.getvalue())
        out=io.StringIO()
        with contextlib.redirect_stdout(out):self.assertEqual(cli.main(['help','doctor']),0)
        self.assertIn('--check-session',out.getvalue())

    def retained_journal(self):
        from types import SimpleNamespace
        stage=tempfile.TemporaryDirectory(prefix='linkget-');self.addCleanup(stage.cleanup);root=Path(stage.name).resolve()
        records=[]
        for name in ['working.tmp','media.jpg']:
            with ownership.Writer(root/name,register=True) as writer:writer.write(b'original fixture bytes')
            records.append(writer.proof.record())
        destination=self.root/'saved';destination.mkdir()
        copy=destination/'copy.tmp';copy.write_bytes(b'original fixture bytes')
        journal=jobs.Journal.create(['https://example.invalid/private?token=secret'],SimpleNamespace(folder=str(destination)))
        journal.update(1,status='completed',stage=str(root),stage_identity=staging.identity(root),owned_files=records,
            destination_files=[ownership.FileProof.observe(copy).record()])
        with jobs.item(journal,1):staging.remember(root,staging.identity(root))
        return journal,root

    def test_preview_uses_existing_proofs_and_never_mutates_or_candidates_media(self):
        journal,root=self.retained_journal()
        before={p: (p.read_bytes(),p.stat().st_mtime_ns) for p in [*root.iterdir(),journal.path]}
        with patch.object(ownership.FileProof,'remove',side_effect=AssertionError('delete')),patch.object(ownership.FileProof,'observe',side_effect=AssertionError('new ownership')):
            entries=retention.preview(journal,1)
        by_name={Path(e['path']).name:e for e in entries}
        self.assertEqual(by_name['working.tmp']['classification'],'candidate')
        self.assertEqual(by_name['media.jpg']['classification'],'must keep')
        self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns) for p in before})
        out=io.StringIO()
        with contextlib.redirect_stdout(out):self.assertEqual(tasks.main([journal.identifier,'--cleanup-preview']),0)
        self.assertIn('Read-only cleanup preview',out.getvalue());self.assertIn('Retained because:',out.getvalue())
        self.assertNotIn('token=secret',out.getvalue())

    def test_preview_preserves_failed_photos_legacy_changed_and_shared_stages(self):
        journal,root=self.retained_journal()
        for changes,expected in [({'status':'failed'},'must keep'),({'receipt':{'staged':[]}},'must keep'),({'stage_snapshots':{}},'unknown'),({'protected_stages':[str(root)]},'must keep')]:
            original=dict(journal.data['items'][0]);journal.data['items'][0].update(changes)
            self.assertTrue(all(e['classification']==expected for e in retention.preview(journal,1)))
            journal.data['items'][0]=original
        (root/'working.tmp').write_bytes(b'changed')
        result=retention.preview(journal,1)
        self.assertTrue(all(e['classification']!='candidate' for e in result))
        self.assertTrue(any('Changed' in e['reason'] for e in result))

    def test_preview_requires_a_single_task_and_read_results_are_never_candidates(self):
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as error:
            tasks.main(['--cleanup-preview'])
        self.assertEqual(error.exception.code,2)
        out=io.StringIO()
        with contextlib.redirect_stdout(out):tasks.show_read({'id':'fixture','kind':'read','status':'completed','outputs':[str(self.root)]},preview=True)
        self.assertIn('must keep',out.getvalue())

    def test_preview_excludes_shared_stages_and_configured_profiles(self):
        from types import SimpleNamespace
        journal,root=self.retained_journal()
        other=jobs.Journal.create(['https://example.invalid/other'],SimpleNamespace(folder=str(self.root/'elsewhere')))
        other.update(1,stage=str(root))
        self.assertTrue(all(e['classification']=='must keep' for e in retention.preview(journal,1)))
        other.update(1,stage=None)
        accounts.set_preferences(profile=str(root/'browser-profile'))
        self.assertTrue(all(e['classification']=='must keep' for e in retention.preview(journal,1)))

    def test_preview_rejects_copy_proofs_outside_destination_without_reading_them(self):
        journal,root=self.retained_journal()
        journal.data['options']['folder']=str(self.root/'different-destination')
        with patch.object(ownership.FileProof,'verify',side_effect=AssertionError('out-of-scope copy read')):
            entries=retention.preview(journal,1)
        self.assertTrue(all(e['classification']=='unknown' for e in entries))

    def test_invalid_preview_metadata_never_inspects_or_reauthorizes_paths(self):
        journal,root=self.retained_journal()
        journal.data['items'][0]['previous_stages']='invalid'
        with patch.object(staging,'_inventory',side_effect=AssertionError('invalid path inspected')):
            result=retention.preview(journal,1)
        self.assertEqual(result[0]['classification'],'unknown');self.assertIsNone(result[0]['path'])
        out=io.StringIO()
        with contextlib.redirect_stdout(out):tasks.show_download(journal,preview=True)
        self.assertIn('Preview unknown',out.getvalue())
        journal.data['items'][0]['previous_stages']=[]
        journal.data['items'][0]['receipt']={'staged':None}
        out=io.StringIO()
        with contextlib.redirect_stdout(out):tasks.show_download(journal,preview=True)
        self.assertIn('Preview unknown',out.getvalue())
