"""Exercise the actual browser script against single and incomplete galleries."""

import json
from pathlib import Path
import shutil
import subprocess
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from linkget.douyin_browser import load_item, read_item
from linkget import neo_browser, douyin_browser
from linkget.failures import BrowserReadError, classify


class BrowserRoutingTests(unittest.TestCase):
    def test_default_neo_uses_existing_lifecycle_and_content_only_result(self):
        item = {'aweme_id':'123', 'images':[{'live_photo_type':0, 'url_list':['https://cdn.example/image.jpg']}],
                'browser_controller':{'kind':'browseros_neo_mcp'}}
        with patch.object(neo_browser, 'read_dom', return_value=item) as neo, patch.object(douyin_browser.subprocess, 'run') as chrome:
            result = read_item('https://www.douyin.com/note/123')
        self.assertNotIn('browser_controller', result)
        neo.assert_called_once()
        self.assertEqual(neo.call_args.args[0], 'https://www.douyin.com/note/123')
        self.assertIn('live_photo_type', neo.call_args.args[1])
        chrome.assert_not_called()

    def test_explicit_chrome_keeps_legacy_path_without_neo(self):
        item = {'aweme_id':'123', 'images':[{'live_photo_type':0, 'url_list':['https://cdn.example/image.jpg']}]}
        with patch.object(neo_browser, 'read_dom') as neo, patch.object(douyin_browser.subprocess, 'run', return_value=CompletedProcess([], 0, json.dumps(item), '')) as chrome:
            self.assertEqual(read_item('https://www.douyin.com/note/123', backend='chrome'), item)
        chrome.assert_called_once()
        neo.assert_not_called()

    def test_neo_failure_does_not_fall_back_to_applescript_or_claim_unsupported(self):
        with patch.object(neo_browser, 'read_dom', side_effect=neo_browser.NeoBrowserError('complete media unavailable: unsupported layout')), patch.object(douyin_browser.subprocess, 'run') as chrome:
            with self.assertRaises(BrowserReadError) as failure:
                read_item('https://www.douyin.com/note/123')
        chrome.assert_not_called()
        reason = classify(failure.exception)
        self.assertEqual(reason.category, 'browser_state')
        self.assertIn('Neo', reason.next_step)
        self.assertNotIn('linkget sites', reason.next_step)
        self.assertFalse(reason.retryable)

    def test_neo_scope_and_wrong_post_are_rejected(self):
        for url in ['https://www.douyin.com/note/123', 'https://www.douyin.com/video/123']:
            self.assertIn(json.dumps('[data-e2e="player-container"]'), neo_browser._read_script(url, 'function snapshot() {}', 'fixture', 1000, 17))
            self.assertEqual(neo_browser._content_url(url), url)
        for url in ['https://www.douyin.com/user/123', 'https://www.douyin.com/note/123?token=private', 'https://www.douyin.com/note/123#other']:
            with self.assertRaises(ValueError): neo_browser._content_url(url)
        for item in [{'aweme_id':'456','images':[{}]}, {'aweme_id':'123','images':['invalid']}, {'aweme_id':'123','video':'invalid'}]:
            with patch.object(neo_browser, 'read_dom', return_value=item):
                with self.assertRaises(BrowserReadError): read_item('https://www.douyin.com/note/123')


class ConnectorDataTests(unittest.TestCase):
    def test_only_exact_post_content_is_accepted(self):
        valid = {'aweme_id': '123', 'images': [{'live_photo_type': 0, 'url_list': ['https://cdn.example/still.jpg']}]}
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory)/'page.json'
            page.write_text(json.dumps(valid))
            self.assertEqual(load_item(page, 'https://www.douyin.com/note/123'), valid)
            for item in ({**valid, 'aweme_id': '456'}, {**valid, 'cookies': []},
                         {**valid, 'images': ['not an image']}, {'aweme_id': '123', 'video': 'not a video'}):
                page.write_text(json.dumps(item))
                with self.subTest(item=item), self.assertRaises(ValueError):
                    load_item(page, 'https://www.douyin.com/note/123')


@unittest.skipUnless(shutil.which("node"), "Node.js is needed for browser-script tests")
class DouyinBrowserTests(unittest.TestCase):
    def snapshot(self, *, sources=("https://cdn.example/one.jpg",), counter="",
                 slides=1, complete=True, width=690, track_children=1,
                 panel_children=1, panel_class="focusPanel", video=False, motion=None,
                 metadata=None, metadata_post_id="123"):
        fixture = dict(sources=sources, counter=counter, slides=slides,
                       complete=complete, width=width, track_children=track_children,
                       panel_children=panel_children, panel_class=panel_class, video=video, motion=motion or [],
                       metadata=metadata, metadata_post_id=metadata_post_id)
        script = (Path(__file__).resolve().parents[1] / "src/linkget/douyin_browser.js").read_text()
        harness = r"""
const fixture = JSON.parse(process.argv[1]);
const pictures = fixture.sources.map(src => ({src, currentSrc: src,
  complete: fixture.complete, naturalWidth: fixture.width}));
const panel = {children: Array(fixture.panel_children),
  classList: {contains: name => name === fixture.panel_class}};
const track = {children: Array(fixture.track_children), parentElement: panel};
const slides = Array.from({length: fixture.slides}, () => ({parentElement: track,
  querySelectorAll: selector => selector === 'img' ? pictures : []}));
const motion = fixture.motion.map(item => ({currentSrc: item.src,
  videoWidth: item.width ?? 720, videoHeight: item.height ?? 1280,
  readyState: item.readyState ?? 4,
  closest: () => item.image === undefined ? null : {
    querySelectorAll: () => [pictures[item.image]]}}));
const player = {innerText: fixture.counter, querySelectorAll: selector => {
  if (selector === '.dySwiperSlide img') return pictures;
  if (selector === '.dySwiperSlide') return slides;
  if (selector === 'video' && fixture.video) return [{videoWidth: 1920,
    videoHeight: 1080, readyState: 4, currentSrc: 'https://cdn.example/video.mp4'}];
  if (selector === 'video') return motion;
  return [];
}};
global.document = {querySelector: () => player};
global.location = {pathname: '/note/123'};
const metadata = fixture.metadata === null
  ? [...new Set(fixture.sources)].map(src => ({urlList: [src], video: null}))
  : fixture.metadata;
if (metadata !== false) player.__reactFiber$fixture = {memoizedProps: {
  awemeInfo: {awemeId: fixture.metadata_post_id, images: metadata}}};
"""
        result = subprocess.run([shutil.which("node"), "-e",
                                 harness + "\nconsole.log(JSON.stringify((" + script + ")()));",
                                 json.dumps(fixture)], capture_output=True, text=True, check=True)
        return json.loads(result.stdout)

    def test_reported_single_slide_with_duplicate_rendered_image(self):
        result = self.snapshot(sources=["https://cdn.example/one.jpg"] * 2)
        self.assertEqual(result, {"aweme_id": "123", "images": [{"url_list": ["https://cdn.example/one.jpg"], "live_photo_type": 0}]})
        with tempfile.TemporaryDirectory() as directory:
            page = Path(directory)/'snapshot.json'
            page.write_text(json.dumps(result))
            self.assertEqual(load_item(page, 'https://www.douyin.com/note/123'), result)

    def test_single_picture_without_counter(self):
        self.assertEqual(len(self.snapshot()["images"]), 1)

    def test_counterless_gallery_must_have_single_complete_slide(self):
        cases = [dict(slides=2), dict(track_children=2), dict(panel_children=2),
                 dict(panel_class="unknown"), dict(complete=False), dict(width=0),
                 dict(sources=["https://cdn.example/one.jpg", ""]),
                 dict(sources=["data:image/png;base64,placeholder"]),
                 dict(sources=["https://cdn.example/one.jpg", "https://cdn.example/two.jpg"])]
        for case in cases:
            with self.subTest(case=case):
                self.assertIsNone(self.snapshot(**case))

    def test_counter_does_not_allow_a_partial_gallery(self):
        self.assertIsNone(self.snapshot(counter="1/2"))

    def test_complete_gallery_keeps_source_order(self):
        urls = ["https://cdn.example/two.jpg", "https://cdn.example/one.jpg"]
        self.assertEqual(self.snapshot(sources=urls, counter="1/2", slides=2,
                                       track_children=2, panel_children=3),
                         {"aweme_id": "123", "images": [{"url_list": [url], "live_photo_type": 0} for url in urls]})

    def test_single_image_preserves_motion_before_video_mounts(self):
        metadata = [{"urlList": ["https://cdn.example/one.jpg"], "livePhotoType": 1,
                     "video": {"width": 720, "height": 1280,
                               "playAddr": [{"src": "https://cdn.example/motion.mp4"}]}}]
        result = self.snapshot(metadata=metadata)
        self.assertEqual(result["images"][0], {
            "url_list": ["https://cdn.example/one.jpg"], "live_photo_type": 1,
            "video": {"play_addr": {"url_list": ["https://cdn.example/motion.mp4"]},
                      "width": 720, "height": 1280}})

    def test_lazy_gallery_motion_is_associated_by_source_metadata(self):
        metadata = [{"urlList": ["https://cdn.example/one.jpg"], "video": None},
                    {"urlList": ["https://cdn.example/two.jpg"], "livePhotoType": 1,
                     "video": {"width": 720, "height": 1280,
                               "playAddr": [{"src": "https://cdn.example/motion.mp4"}]}}]
        result = self.snapshot(sources=["https://cdn.example/one.jpg", "https://cdn.example/two.jpg"],
                               counter="1/2", slides=2, metadata=metadata)
        self.assertEqual([item["live_photo_type"] for item in result["images"]], [0, 1])

    def test_unknown_or_incomplete_metadata_keeps_polling(self):
        for metadata in (False, [], [{"urlList": ["https://cdn.example/one.jpg"]}],
                         [{"urlList": ["https://cdn.example/one.jpg"], "livePhotoType": 1}],
                         [{"urlList": ["https://cdn.example/one.jpg"], "video": {}}],
                         [{"urlList": ["https://cdn.example/other.jpg"], "video": None}]):
            with self.subTest(metadata=metadata):
                self.assertIsNone(self.snapshot(metadata=metadata))
        self.assertIsNone(self.snapshot(metadata_post_id="456"))

    def test_background_audio_url_shape_does_not_determine_motion(self):
        for source in ("https://cdn.example/music.mp3", "https://cdn.example/play?id=music", "blob:music"):
            with self.subTest(source=source):
                self.assertEqual(self.snapshot(motion=[dict(src=source, width=0, height=0)])["images"][0]["live_photo_type"], 0)

    def test_motion_uses_highest_exposed_image_rendition(self):
        metadata = [{"urlList": ["https://cdn.example/one.jpg"], "livePhotoType": 1,
                     "video": {"width": 720, "height": 1280, "playAddr": [{"src": "https://cdn.example/720.mp4"}],
                               "bitRateList": [{"width": 1080, "height": 1920,
                                                "playAddr": [{"src": "https://cdn.example/1080.mp4"}]}]}}]
        self.assertEqual(self.snapshot(metadata=metadata)["images"][0]["video"]["width"], 1080)

    def test_unknown_gallery_and_video_paths(self):
        self.assertIsNone(self.snapshot(sources=[]))
        self.assertEqual(self.snapshot(sources=[], video=True), {"aweme_id": "123", "video": {"play_addr": {
            "url_list": ["https://cdn.example/video.mp4"], "width": 1920, "height": 1080}}})


if __name__ == "__main__":
    unittest.main()
