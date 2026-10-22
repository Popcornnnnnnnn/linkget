"""Normal-page fixtures and controller lifecycle; no actual browser access."""
from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from linkget import rendered_page as reader, toutiao

VIDEO = 'https://www.toutiao.com/video/123/'
ARTICLE = 'https://www.toutiao.com/article/123/'
PI = 'https://www.raspberrypi.com/products/raspberry-pi-5/'
ZH = 'https://zhuanlan.zhihu.com/p/347981609'
PAIR = {'video': 'https://v9-web.toutiaovod.com/video/tos/fixture/media-video-avc1/?l=batch&dy_q=123',
        'audio': 'https://v9-web.toutiaovod.com/video/tos/fixture/media-audio-und-mp4a/?l=batch&dy_q=123'}


class RenderedPageTests(unittest.TestCase):
    def controller_proof(self):
        return {'kind':'browseros_neo_mcp','reader_revision':1,
                'temporary_tab_cleanup':'closed','credentials_exported':False,'server_version':'0.0.69'}

    def test_neo_backend_provenance_survives_second_validation(self):
        raw=self.fixture_public();raw['browser_controller']=self.controller_proof()
        first=reader.validate_public_page(raw,ZH)
        second=reader.validate_public_page(first,ZH)
        self.assertEqual(first,second)
        self.assertEqual(second['reader_backend'],'browseros_neo')
        self.assertEqual(second['transport_proof']['kind'],'browseros_neo_dom')
        self.assertFalse(second['transport_proof']['http_transfer_verified'])
        video=dict(url=VIDEO,title='Fixture',kind='video',duration=42,captions=[],
                   media_binding='paired_page_vod_resources',media_streams=PAIR,
                   browser_controller=self.controller_proof(),
                   media_selection={'strategy':'latest_observed_video_variant','video_variant_count':2,
                       'audio_resource_count':1,'selected_video_resource_index':3,'same_request_batch':True})
        once=toutiao.validate_page(VIDEO,video);twice=toutiao.validate_page(VIDEO,once)
        self.assertEqual(twice,once)
        self.assertEqual(twice['acquisition'],'browseros_neo_rendered_page')
        self.assertEqual(twice['media_selection'],video['media_selection'])
        bad={**video,'media_selection':{**video['media_selection'],'same_request_batch':False}}
        with self.assertRaises(ValueError):toutiao.validate_page(VIDEO,bad)
        bad={**raw,'browser_controller':{**raw['browser_controller'],'temporary_tab_cleanup':'unknown'}}
        with self.assertRaises(ValueError):reader.validate_public_page(bad,ZH)

    @unittest.skipUnless(shutil.which('node'), 'Node is needed for the real reader expression')
    def test_chrome_initial_blank_waits_but_other_pages_remain_rejected(self):
        raw=self.fixture_public()
        expression=[]
        def control(action,*args,**kwargs):
            if action=='open':return '1:2:3'
            if action=='close':return 'closed'
            expression.append(args[2])
            return json.dumps(raw)
        with patch.object(reader,'controller',side_effect=control):
            reader.read_public(ZH,backend='chrome')
        for url,expected in [('about:blank',None),('',None),('https://example.org/',{'diagnostic':{'reason':'public_article_mismatch'}})]:
            script='global.location={href:'+json.dumps(url)+'};process.stdout.write('+expression[0]+');'
            result=subprocess.run([shutil.which('node'),'-e',script],capture_output=True,text=True,check=True)
            self.assertEqual(json.loads(result.stdout),expected)

    def test_backend_selection_rejects_unknown_or_implicit_fallback(self):
        self.assertEqual(reader.selected_backend('neo'),'neo')
        with self.assertRaises(ValueError):reader.selected_backend('unknown')

    def test_stable_article_proof_survives_validation_and_rejects_incomplete_range(self):
        text = ('A complete paragraph. ' * 12).strip()
        proof = dict(basis='stable_rendered_article_range', document_state='interactive',
                     stable_ms=1500, paragraph_count=10, paragraph_chars=len(text), pending_images=0,
                     visible_loaders=0, visible_expand_controls=0, clipped_article=False, end_excerpt=text)
        raw = dict(url=ARTICLE, title='Article', kind='article', text=text, content_complete=True,
                   article_dom_proof=proof, browser_controller=self.controller_proof())
        first = toutiao.validate_page(ARTICLE, raw)
        self.assertEqual(toutiao.validate_page(ARTICLE, first), first)
        for changed in ({'stable_ms':0}, {'paragraph_count':1}, {'pending_images':1},
                        {'visible_loaders':1}, {'visible_expand_controls':1},
                        {'clipped_article':True}, {'end_excerpt':'not the captured tail'}):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                toutiao.validate_page(ARTICLE, {**raw, 'article_dom_proof':{**proof, **changed}})

    def test_product_tech_specs_label_keeps_exact_scope_and_tail_binding(self):
        text='Tech specs\nBroadcom BCM2712\n'+'Actual product details. '*20+'\nFAQs\n'+'Answer. '*30
        raw={'url':PI,'final_url':PI,'kind':'article','title':'Raspberry Pi 5','text':text,'content_complete':True,
             'dom_proof':{'selector':'main','reader_revision':2,'basis':'bounded_article_dom','http_transfer_verified':False,
                'end_excerpt':text[-512:],'sections':{name:{'offset':text.index(marker),'excerpt':text[text.index(marker):text.index(marker)+1000]}
                    for name,marker in [('specifications','Tech specs'),('faq','FAQs')]}}}
        self.assertEqual(reader.validate_public_page(raw,PI)['text'],text)
        with self.assertRaises(ValueError):reader.validate_public_page({**raw,'text':text.replace('BCM2712','missing')},PI)

    def test_allowed_paths_do_not_expand_to_other_pages_or_credentials(self):
        self.assertEqual(reader.public_article_url(PI.rstrip('/'))[0], PI)
        self.assertEqual(reader.public_article_url(ZH + '/')[0], ZH)
        for bad in ('chrome://extensions/', 'chrome-extension://example/options.html', 'http://zhuanlan.zhihu.com/p/123',
                    'https://user@zhuanlan.zhihu.com/p/123', 'https://www.raspberrypi.com/', PI + '?private=1',
                    ZH + '#comments', 'https://zhuanlan.zhihu.com/p/1/2', 'https://zhuanlan.zhihu.com:444/p/1',
                    'https://zhuanlan.zhihu.com.evil.test/p/1'):
            with self.subTest(bad=bad), self.assertRaises(ValueError): reader.public_article_url(bad)
        with patch.object(reader, 'controller') as control:
            with self.assertRaises(ValueError): reader.read_page('https://example.org/article')
            control.assert_not_called()

    def test_split_and_pending_evidence_survives_read_browser_second_validation(self):
        for source in ({'media_streams': PAIR, 'media_binding': 'paired_page_vod_resources'},
                       {'media_url': PAIR['video'], 'media_binding': 'player_current_src'}):
            raw = dict(url=VIDEO, final_url=VIDEO, title='Fixture', kind='video', duration=None,
                       duration_pending=True, captions=[], **source)
            once = toutiao.validate_page(VIDEO, raw)
            twice = toutiao.validate_page(VIDEO, once)
            self.assertEqual(twice, once)
            self.assertTrue(twice['duration_pending'])
            self.assertFalse(twice['content_complete'])
        with self.assertRaises(ValueError):
            toutiao.validate_page(VIDEO, {**raw, 'final_url': 'https://www.toutiao.com/video/124/'})

    def fixture_public(self, url=ZH):
        text = 'Fixture title\n\n' + 'An actual scoped body with distinct content. ' * 8 + '\n\n发布于 2026-10-12 12:00'
        return dict(url=url, final_url=url, kind='article', title='Fixture title', text=text, content_complete=True,
                    dom_proof=dict(selector='article .RichText', publication_text='发布于 2026-10-12 12:00',
                                   reader_revision=2, basis='bounded_article_dom', http_transfer_verified=False,
                                   end_excerpt=text[-512:]))

    def test_public_validation_requires_final_url_tail_publication_and_no_http_claim(self):
        raw = self.fixture_public()
        verified = reader.validate_public_page({**raw, 'cookies': 'synthetic', 'other': 'drop'}, ZH)
        self.assertNotIn('cookies', verified)
        self.assertEqual(verified['text_sha256'], hashlib.sha256(raw['text'].encode()).hexdigest())
        self.assertFalse(verified['transport_proof']['http_transfer_verified'])
        cases = [{**raw, 'final_url': ZH.replace('347981609', '1')}, {**raw, 'title': 'wrong'},
                 {**raw, 'dom_proof': {**raw['dom_proof'], 'end_excerpt': 'unrelated'}},
                 {**raw, 'dom_proof': {**raw['dom_proof'], 'http_transfer_verified': True}},
                 {**raw, 'dom_proof': {**raw['dom_proof'], 'publication_text': 'today'}},
                 {**raw, 'text': 'title only'}, {**raw, 'content_complete': False},
                 {**raw, 'text': '字' * 50000}]
        for value in cases:
            with self.subTest(value=value.keys()), self.assertRaises(ValueError): reader.validate_public_page(value, ZH)

    def test_controller_cleanup_on_success_and_content_failure(self):
        for raw, succeeds in [(self.fixture_public(), True), ({'diagnostic': {'reason': 'public_article_mismatch'}}, False)]:
            calls = []
            def control(action, *args, **kw):
                calls.append((action, args))
                return '11:22:33' if action == 'open' else 'closed' if action == 'close' else json.dumps(raw)
            with patch.object(reader, 'reader_lock', return_value=nullcontext()), patch.object(reader, 'controller', side_effect=control):
                if succeeds:
                    result = reader.read_public(ZH, backend='chrome')
                    self.assertEqual(result['browser_controller']['temporary_tab_cleanup'], 'closed')
                else:
                    with self.assertRaises(ValueError): reader.read_public(ZH, backend='chrome')
            self.assertEqual([call[0] for call in calls], ['open', 'read', 'close'])
            self.assertEqual(calls[-1][1], ('11', '22', '33', ZH))

    def test_controller_permission_timeout_and_cleanup_failures_are_dependency_failures(self):
        for exception in (reader.BrowserUnavailable('not authorized'), subprocess.TimeoutExpired('osascript', 8)):
            calls = []
            def control(action, *args, **kw):
                calls.append(action)
                if action == 'read': raise exception
                return '11:22:33' if action == 'open' else 'closed'
            with patch.object(reader, 'reader_lock', return_value=nullcontext()), patch.object(reader, 'controller', side_effect=control):
                with self.assertRaises(reader.BrowserUnavailable): reader.read_public(ZH, backend='chrome')
            self.assertEqual(calls, ['open', 'read', 'close'])
        with patch.object(reader, 'reader_lock', return_value=nullcontext()), patch.object(reader, 'controller',
                side_effect=['11:22:33', json.dumps(self.fixture_public()), 'unverified']):
            with self.assertRaises(reader.BrowserUnavailable): reader.read_public(ZH, backend='chrome')

    def test_lock_serializes_and_is_bounded_without_opening_tabs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(reader, 'lock_path', return_value=Path(tmp)/'browser/reader.lock'):
            with reader.reader_lock(reader.time.monotonic() + 1):
                with self.assertRaises(reader.ReaderBusy), reader.reader_lock(reader.time.monotonic()): pass
        self.assertTrue(issubclass(reader.ReaderBusy, reader.BrowserUnavailable))

    def test_temp_controller_never_reuses_or_closes_unrelated_tabs(self):
        code = Path(reader.__file__).with_name('rendered_page.applescript').read_text()
        self.assertIn('make new tab', code)
        self.assertIn('(id of candidateTab as text) is tabID', code)
        self.assertIn('preserved_navigated_tab', code)
        self.assertNotIn('activate', code.replace('not activated', ''))
        self.assertNotIn('Allow JavaScript', code)


@unittest.skipUnless(shutil.which('node'), 'Node.js is needed for offline DOM fixtures')
class BrowserDomTests(unittest.TestCase):
    def snapshot(self, script, fixture):
        source = Path(reader.__file__).with_name(script).read_text()
        harness = r'''
const f=JSON.parse(require('fs').readFileSync(0, 'utf8'));
global.location={href:f.url};
global.performance={getEntriesByType:()=>f.resources.map(name=>({name}))};
const leaf=(text='')=>({textContent:text,querySelector:()=>null,querySelectorAll:()=>[],cloneNode(){return this;}});
const pi=leaf(f.text),heading=leaf(f.alt?'':f.title);
heading.querySelector=()=>({getAttribute:()=>f.title});
pi.querySelector=s=>s==='h1'?heading:null;
const article=leaf(f.article||f.text);
article.innerText=f.article||f.text;
article.querySelectorAll=s=>s==='p'?(f.paragraphs||[]).map(innerText=>({innerText})):[];
article.__linkgetArticleScopeReady=f.scope_ready;
article.querySelector=s=>s==='.ContentItem-time, .Post-Time'?leaf(f.publication):f.gated?{}:null;
const player={currentSrc:f.src,duration:f.duration===null?NaN:f.duration,textTracks:[],dataset:{},readyState:1,
  paused:false,load:()=>{throw new Error('must not reload blob');}};
let loop=false,loopWrites=0;
Object.defineProperty(player,'loop',{get:()=>loop,set:value=>{
  if (++loopWrites > (f.max_loop_writes ?? Infinity)) throw new Error('repeated reflected loop mutation');
  loop=value;
}});
global.document={readyState:f.ready||'complete',documentElement:{dataset:{linkgetNeoReadOwner:f.owner}},querySelector:s=>s==='h1'?{innerText:f.title}:
  s==='h1.Post-Title'?leaf(f.title):s==='article'?article:s==='main'?pi:s==='video'?player:
  s==='article .RichText'?leaf(f.text):null};
const extract=(''' + source + r''');
let value;
for(let n=0;n<(f.repeat||1);n++) value=extract(f.expected||f.url);
process.stdout.write(JSON.stringify(value));
'''
        raw = subprocess.run([shutil.which('node'), '-e', harness], input=json.dumps(fixture), capture_output=True, text=True, check=True).stdout
        return json.loads(raw)

    def toutiao(self, **overrides):
        fixture = dict(url=VIDEO, expected=VIDEO, title='展开\nFixture title',
                       src='blob:https://www.toutiao.com/fixture', duration=42, resources=list(PAIR.values()))
        return self.snapshot('toutiao_browser.js', {**fixture, **overrides})

    def test_pending_blob_rechecks_do_not_repeat_reflected_player_mutations(self):
        self.assertIsNone(self.toutiao(resources=[], repeat=3, max_loop_writes=1))

    def test_interactive_article_requires_this_reads_unchanged_quiet_paragraph_range(self):
        paragraphs = [('First actual paragraph. '*4).strip(), ('Second actual paragraph. '*4).strip()]
        text = '\n\n'.join(paragraphs)
        fixture = dict(url=ARTICLE, title='Article', ready='interactive', article=text,
                       paragraphs=paragraphs, resources=[], owner='this-read')
        self.assertIsNone(self.snapshot('toutiao_browser.js', fixture))
        scope = dict(owner='this-read', paragraph_text=text, stable_ms=1500, paragraph_count=2)
        value = self.snapshot('toutiao_browser.js', {**fixture, 'scope_ready':scope})
        self.assertTrue(value['content_complete'])
        self.assertEqual(value['article_dom_proof']['paragraph_count'], 2)
        for changed in ({'owner':'previous-read'}, {'paragraph_text':text+'changed'},
                        {'paragraph_count':1}, {'stable_ms':0}):
            with self.subTest(changed=changed):
                self.assertIsNone(self.snapshot('toutiao_browser.js', {**fixture, 'scope_ready':{**scope, **changed}}))
        loaded = self.snapshot('toutiao_browser.js', {**fixture, 'ready':'complete'})
        self.assertTrue(loaded['content_complete'])
        self.assertNotIn('article_dom_proof', loaded)

    def test_split_tracks_and_unknown_duration_are_bound_to_one_page_batch(self):
        value = self.toutiao()
        self.assertEqual(value['media_streams'], PAIR)
        self.assertEqual(value['media_binding'], 'paired_page_vod_resources')
        self.assertEqual(value['media_selection'], {
            'strategy': 'latest_observed_video_variant', 'video_variant_count': 1,
            'audio_resource_count': 1, 'selected_video_resource_index': 0, 'same_request_batch': True})
        self.assertFalse(value['duration_pending'])
        pending = self.toutiao(duration=None)
        self.assertTrue(pending['duration_pending'])
        self.assertEqual(toutiao.validate_page(VIDEO, pending)['duration_basis'], 'pending_download_probe')

    def test_split_ambiguity_mixed_batch_extra_paths_and_wrong_post_are_rejected(self):
        invalid = [list(PAIR.values()) + ['https://v9-web.toutiaovod.com/another.mp4'],
                   [PAIR['video'], PAIR['audio'].replace('batch', 'other')],
                   [PAIR['video'], PAIR['audio'] + '&l=batch'],
                   [PAIR['video'], PAIR['audio'].replace('v9-web', 'v8-web')]]
        for resources in invalid:
            with self.subTest(resources=resources): self.assertIsNone(self.toutiao(resources=resources))
        wrong = self.toutiao(url='https://www.toutiao.com/video/124/')
        self.assertEqual(wrong['diagnostic']['reason'], 'post_mismatch')

    def test_adaptive_video_variants_choose_the_last_observed_path(self):
        newer = PAIR['video'].replace('/fixture/', '/variant-2/')
        resources = [PAIR['video'], PAIR['audio'], newer]
        value = self.toutiao(resources=resources, duration=106.97)
        self.assertEqual(value['media_streams'], {'video': newer, 'audio': PAIR['audio']})
        self.assertEqual(value['media_selection']['video_variant_count'], 2)
        self.assertEqual(value['media_selection']['selected_video_resource_index'], 2)
        # A later byte-range request for the first path is newer than the last
        # path inserted into the map; keep that request URL and observation.
        latest = PAIR['video'] + '&range=100-200'
        repeated = self.toutiao(resources=resources + [latest])
        self.assertEqual(repeated['media_streams']['video'], latest)
        self.assertEqual(repeated['media_selection']['selected_video_resource_index'], 3)
        self.assertEqual(repeated['media_selection']['video_variant_count'], 2)
        for count in (3, 4):
            variants = [PAIR['video'].replace('/fixture/', f'/variant-{index}/') for index in range(count)]
            with self.subTest(count=count):
                bounded = self.toutiao(resources=[PAIR['audio']] + variants, duration=None)
                self.assertEqual(bounded['media_streams']['video'], variants[-1])
                self.assertEqual(bounded['media_selection']['video_variant_count'], count)
                self.assertTrue(bounded['duration_pending'])

    def test_adaptive_variants_reject_mixed_batches_audio_and_unknown_paths(self):
        second = PAIR['video'].replace('/fixture/', '/variant-2/')
        invalid = {
            'other_video_batch': [PAIR['video'], PAIR['audio'], second.replace('batch', 'other')],
            'older_same_path_batch': [PAIR['video'].replace('batch', 'other'), PAIR['audio'], PAIR['video']],
            'duplicate_batch_parameter': [PAIR['video'], PAIR['audio'], second + '&l=batch'],
            'other_origin': [PAIR['video'], PAIR['audio'], second.replace('v9-web', 'v8-web')],
            'multiple_audio': [PAIR['video'], PAIR['audio'], PAIR['audio'].replace('/fixture/', '/other-audio/')],
            'unknown_extra_path': [PAIR['video'], PAIR['audio'], second, 'https://v9-web.toutiaovod.com/unknown.mp4'],
            'unknown_role': [PAIR['video'], PAIR['audio'], second.replace('media-video-avc1', 'media-video-unknown')],
            'too_many_variants': [PAIR['audio']] + [PAIR['video'].replace('/fixture/', f'/variant-{index}/') for index in range(5)],
        }
        for name, resources in invalid.items():
            with self.subTest(name=name):
                self.assertIsNone(self.toutiao(resources=resources))

    def test_single_blob_resource_is_only_valid_with_known_duration(self):
        one = ['https://v9-web.toutiaovod.com/fixture/whole.mp4?l=batch']
        value = self.toutiao(resources=one + one)
        self.assertEqual(value['media_binding'], 'single_page_vod_resource')
        self.assertIsNone(self.toutiao(resources=one, duration=None))
        pending = self.toutiao(src=one[0], duration=None, resources=[])
        self.assertEqual(pending['media_binding'], 'player_current_src')
        self.assertTrue(pending['duration_pending'])

    def test_public_product_alt_title_sections_unicode_offsets_and_tail(self):
        text = '🍓 Raspberry Pi product\nSpecifications\nBCM2712 ' + 'Product specifications. ' * 10 + '\nFAQ\nNormal product ending.'
        raw = self.snapshot('public_browser.js', dict(url=PI, title='Raspberry Pi 5', alt=True, text=text, resources=[]))
        value = reader.validate_public_page(raw, PI)
        self.assertEqual(value['title'], 'Raspberry Pi 5')
        self.assertTrue(text.endswith(value['dom_proof']['end_excerpt']))
        missing = self.snapshot('public_browser.js', dict(url=PI, title='Raspberry Pi 5', text=text.replace('FAQ','no questions'), resources=[]))
        self.assertIsNone(missing)

    def test_zhihu_body_publication_access_gate_and_scope_limits(self):
        base = dict(url=ZH, title='Fixture title', text='Scoped article body. ' * 20,
                    publication='编辑于 2026-10-12 12:00', resources=[])
        value = self.snapshot('public_browser.js', base)
        self.assertEqual(reader.validate_public_page(value, ZH)['dom_proof']['publication_text'], base['publication'])
        self.assertIsNone(self.snapshot('public_browser.js', {**base, 'publication': 'today'}))
        gated = self.snapshot('public_browser.js', {**base, 'gated': True})
        self.assertEqual(gated['diagnostic']['reason'], 'article_access_gate')
        oversized = self.snapshot('public_browser.js', {**base, 'text': '字' * 50000})
        self.assertEqual(oversized['diagnostic']['reason'], 'article_scope_size_out_of_bounds')


if __name__ == '__main__': unittest.main()
