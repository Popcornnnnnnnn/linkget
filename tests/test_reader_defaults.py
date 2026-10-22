"""Reader selection across foreground, batch, retained and submitted tasks."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from linkget import content, read_store, read_tasks, rendered_page, neo_browser, toutiao, accounts, cli
from test_rendered_page import ARTICLE, VIDEO, PI, ZH
import test_rendered_page as fixtures

PROOF={'kind':'browseros_neo_mcp','reader_revision':1,'temporary_tab_cleanup':'closed',
       'credentials_exported':False,'server_version':'0.0.69'}


class DefaultReaderTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        mocked=patch.object(read_store,'root',return_value=self.root/'data');mocked.start();self.addCleanup(mocked.stop)
        env=patch.dict(os.environ, {}, clear=False);env.start();self.addCleanup(env.stop)
        os.environ.pop('LINKGET_RENDERED_BACKEND',None)
        directory=patch.object(accounts,'directory',return_value=self.root/'settings'/'sessions');directory.start();self.addCleanup(directory.stop)
        accounts.set_preferences(reader_browser='neo')
        self.article={'url':ARTICLE,'kind':'article','title':'Fixture article','text':'Complete source paragraph. '*20,'content_complete':True}

    def call(self, argv):
        with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            return content.main(argv)

    def test_default_and_explicit_selection_precedence(self):
        self.assertEqual(rendered_page.selected_backend(),'neo')
        with patch.dict(os.environ,{'LINKGET_RENDERED_BACKEND':'chrome'}):
            self.assertEqual(rendered_page.selected_backend(),'chrome')
            self.assertEqual(rendered_page.selected_backend('neo',browser='chrome'),'neo')
        with patch.dict(os.environ,{'LINKGET_RENDERED_BACKEND':'neo'}):
            self.assertEqual(rendered_page.selected_backend(browser='chrome'),'chrome')
        with patch.dict(os.environ,{'LINKGET_RENDERED_BACKEND':'invalid'}):
            with self.assertRaises(ValueError):rendered_page.selected_backend()

    def test_clean_user_follows_saved_or_system_browser_without_substitution(self):
        (accounts.directory().parent/"preferences.json").unlink()
        for default in ('chrome', 'safari', 'firefox', 'edge', None):
            with self.subTest(default=default),patch.object(accounts,'default_browser',return_value=default):
                if default == 'chrome':
                    self.assertEqual(rendered_page.selected_backend(),'chrome')
                else:
                    with self.assertRaisesRegex(rendered_page.BrowserUnavailable,'no supported browser controller'):
                        rendered_page.selected_backend()
        accounts.set_preferences(browser='chrome')
        with patch.object(accounts,'default_browser',return_value='safari'):
            self.assertEqual(rendered_page.selected_backend(),'chrome')
        accounts.set_preferences(reader_browser='neo')
        self.assertEqual(rendered_page.selected_backend(),'neo')

    def test_configuration_changes_only_reader_preference(self):
        accounts.set_preferences(destination={'mode':'cwd'},browser='safari',blocked=['douyin.com'])
        before=accounts.preferences()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['config','--reader-browser','chrome']),0)
        self.assertEqual(accounts.preferences(),dict(before,reader_browser='chrome'))

    def test_download_auto_uses_shared_preferences_and_freezes_task_choice(self):
        from linkget import douyin, jobs
        folder=self.root/'download'
        with patch.object(cli,'download_douyin',side_effect=douyin.MediaUnavailable('fixture')),patch.object(accounts,'copy_saved',return_value=False),patch.object(cli,'read_douyin_browser',side_effect=RuntimeError('fixture')) as reader,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(['https://www.douyin.com/video/123','--folder',str(folder),'--rendered-backend','chrome']),1)
            reader.assert_called_once_with('https://www.douyin.com/video/123',backend='chrome')
        journal=jobs.select(None)
        self.assertEqual(journal.data['items'][0]['rendered_backend'],'chrome')
        accounts.set_preferences(reader_browser='chrome')
        for flags,expected in [([], 'chrome'),(['--rendered-backend','neo'],'neo'),([], 'neo')]:
            with patch.object(cli,'download_douyin',side_effect=douyin.MediaUnavailable('fixture')),patch.object(accounts,'copy_saved',return_value=False),patch.object(cli,'read_douyin_browser',side_effect=RuntimeError('fixture')) as reader,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(cli.main(['retry',journal.identifier,*flags]),1)
                reader.assert_called_once_with('https://www.douyin.com/video/123',backend=expected)

    def test_toutiao_entrypoint_records_the_resolved_choice(self):
        cases=[([],None,'neo'),(['--browser','chrome'],None,'chrome'),([], 'chrome','chrome'),
               (['--rendered-backend','neo','--browser','chrome'],'chrome','neo')]
        for index,(flags,env,expected) in enumerate(cases):
            folder=self.root/str(index)
            with patch.dict(os.environ,{'LINKGET_RENDERED_BACKEND':env} if env else {}),patch.object(toutiao,'read_browser',return_value=self.article) as read:
                self.assertEqual(self.call([ARTICLE,'--folder',str(folder),'--refresh',*flags]),0)
            read.assert_called_once_with(ARTICLE,backend=expected)
            record=json.loads((folder/'.read-job.json').read_text())
            self.assertEqual(record['options']['rendered_backend'],expected)

    def test_public_article_and_batch_use_neo(self):
        raw=fixtures.RenderedPageTests().fixture_public();raw['browser_controller']=PROOF
        with patch.object(rendered_page,'read_public',return_value=raw) as public,patch.object(toutiao,'read_browser',return_value=self.article) as read:
            self.assertEqual(self.call(['batch',ARTICLE,ZH,'--folder',str(self.root/'batch'),'--refresh']),0)
        public.assert_called_once_with(ZH,backend='neo');read.assert_called_once_with(ARTICLE,backend='neo')

    def test_video_reaches_neo_before_existing_speech_prerequisite_check(self):
        video={'url':VIDEO,'kind':'video','title':'Fixture','duration':4,'media_url':'https://v9-web.toutiaovod.com/fixture.mp4'}
        with patch.object(read_store,'model_default',return_value=None),patch.object(toutiao,'read_browser',return_value=video) as read:
            self.assertEqual(self.call([VIDEO,'--folder',str(self.root/'video'),'--refresh']),1)
        read.assert_called_once_with(VIDEO,backend='neo')

    def test_browser_off_and_other_browser_never_launch_reader(self):
        for index,(url,flags) in enumerate([(ARTICLE,['--browser','none']),(ZH,['--browser','none']),
                                          (ARTICLE,['--browser','firefox']),(ZH,['--browser','safari']),
                                          (ARTICLE,['--browser','none','--rendered-backend','neo'])]):
            with patch.object(toutiao,'read_browser') as read,patch.object(rendered_page,'read_public') as public:
                self.assertEqual(self.call([url,'--folder',str(self.root/f'off{index}'),'--refresh',*flags]),1)
            read.assert_not_called();public.assert_not_called()

    def test_page_data_does_not_require_browser_or_valid_backend_environment(self):
        page=self.root/'page.json';page.write_text(json.dumps(self.article))
        with patch.dict(os.environ,{'LINKGET_RENDERED_BACKEND':'invalid'}),patch.object(toutiao,'read_browser') as read:
            self.assertEqual(self.call([ARTICLE,'--page-data',str(page),'--browser','none','--folder',str(self.root/'offline')]),0)
        read.assert_not_called()

    def test_neo_failure_and_cancellation_never_call_chrome(self):
        for error in [neo_browser.NeoBrowserError('Neo unavailable'),KeyboardInterrupt()]:
            with patch.object(rendered_page,'reader_lock',return_value=contextlib.nullcontext()),patch.object(neo_browser,'read_dom',side_effect=error) as neo,patch.object(rendered_page,'controller') as chrome:
                with self.assertRaises((rendered_page.BrowserUnavailable,KeyboardInterrupt)):
                    rendered_page.read_page(ARTICLE)
            neo.assert_called_once();chrome.assert_not_called()

    def test_retry_preserves_recorded_choice_and_explicit_override(self):
        folder=self.root/'retry'
        with patch.object(toutiao,'read_browser',side_effect=RuntimeError('fixture interrupted')):
            self.assertEqual(self.call([ARTICLE,'--folder',str(folder),'--rendered-backend','chrome']),1)
        for override,expected in [([], 'chrome'),(['--rendered-backend','neo'],'neo')]:
            with patch.object(toutiao,'read_browser',side_effect=RuntimeError('fixture interrupted')) as read:
                self.assertEqual(self.call(['retry',str(folder),*override]),1)
            read.assert_called_once_with(ARTICLE,backend=expected)
        self.assertEqual(json.loads((folder/'.read-job.json').read_text())['options']['rendered_backend'],'neo')

    def test_legacy_toutiao_cli_preserves_explicit_chrome(self):
        with patch.object(toutiao,'read_browser',return_value=self.article) as read,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(toutiao.main([ARTICLE,'--folder',str(self.root/'legacy'),'--rendered-backend','chrome']),0)
        read.assert_called_once_with(ARTICLE,backend='chrome')

    def test_background_submission_retains_environment_and_explicit_choices(self):
        with patch.dict(os.environ,{'LINKGET_RENDERED_BACKEND':'chrome'}),patch.object(read_tasks.subprocess,'Popen') as popen,contextlib.redirect_stdout(io.StringIO()):
            popen.return_value.pid=900001
            self.assertEqual(read_tasks.main([ARTICLE,'--rendered-backend','neo']),0)
        self.assertEqual(popen.call_args.kwargs['env']['LINKGET_RENDERED_BACKEND'],'chrome')
        record=next(read_tasks.directory().glob('*.json'))
        self.assertEqual(json.loads(record.read_text())['argv'],[ARTICLE,'--rendered-backend','neo'])

    def test_background_retry_passes_override_to_worker_without_new_access(self):
        folder=self.root/'retained';folder.mkdir();(folder/'.read-job.json').write_text('{}')
        with patch.object(read_tasks,'submit',return_value=0) as submit:
            self.assertEqual(read_tasks.main(['retry',str(folder),'--rendered-backend','neo']),0)
        submit.assert_called_once_with(['retry',str(folder.resolve()),'--rendered-backend','neo'],'retry')
