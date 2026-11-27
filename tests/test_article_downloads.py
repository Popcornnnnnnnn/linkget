"""Offline tests for X Articles, rich web article extraction, offline archives, CLI, and MCP."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
from subprocess import CompletedProcess
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from linkget import accounts, article_archive, cli, inspect_url, mcp_server, read_store, rendered_page, web_article, x_article


def _png_bytes(width=120, height=80):
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + struct.pack('>I', 13) + b'IHDR' + ihdr + b'\x00\x00\x00\x00IEND\xaeB`\x82'


def _jpeg_bytes(width=2000, height=800):
    sof0 = b'\xff\xc0' + struct.pack('>HBHH', 11, 8, height, width) + b'\x01\x01\x11\x00'
    return b'\xff\xd8' + sof0 + b'\xff\xd9'


def _mp4_bytes():
    return b'\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isommp41mdat\x01\x02\x03\x04'


class FakeResponse:
    def __init__(self, body, url, *, content_type='application/octet-stream', status=200):
        self._stream = io.BytesIO(body)
        self._url = url
        self.status = status
        self.code = status
        self.headers = {'Content-Type': content_type}

    def read(self, size=-1):
        return self._stream.read(size)

    def geturl(self):
        return self._url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._stream.close()
        return False


class FakeOpener:
    def __init__(self, handler):
        self.handler = handler
        self.requests = []

    def open(self, req, timeout=30):
        self.requests.append(req.full_url)
        return self.handler(req)


def _sample_x_graphql_item():
    text1 = '你好🤣🤣世界 bold_part link_here!'
    # UTF-16 indices:
    # 你(0) 好(1) 🤣(2,3) 🤣(4,5) 世(6) 界(7) ' '(8) b(9)..t(17) ' '(18) l(19)..!(28)
    bold_offset = len('你好🤣🤣世界 '.encode('utf-16-le')) // 2
    link_offset = len('你好🤣🤣世界 bold_part '.encode('utf-16-le')) // 2
    return {
        'tweet_id': '2124506790020948428',
        'date': '2026-08-01 10:00:00',
        'lang': 'zh',
        'view_count': 123456,
        'favorite_count': 4200,
        'retweet_count': 512,
        'reply_count': 88,
        'bookmark_count': 999,
        'quote_count': 34,
        'birdwatch': 'Readers added helpful context.',
        'author': {
            'id': 987654321,
            'name': '404jsh',
            'nick': 'Author <Tag>',
            'description': 'Bio line',
            'followers_count': 50000,
            'profile_image': 'https://pbs.twimg.com/profile_images/1/avatar_normal.jpg',
        },
        'article': {
            'article_results': {
                'result': {
                    'rest_id': '2124502533179834368',
                    'title': 'X Article: Colon "Quotes" & Surrogate Test',
                    'preview_text': 'Short preview summary.',
                    'summary_text': 'Longer summary.',
                    'metadata': {'first_published_at_secs': 1754042400},
                    'lifecycle_state': {'modified_at_secs': 1754046000},
                    'cover_media': {
                        'media_id': 'cover1',
                        'media_info': {
                            'original_img_url': 'https://pbs.twimg.com/media/cover1?format=jpg&name=large',
                            'original_img_width': 2000,
                            'original_img_height': 800,
                        },
                    },
                    'media_entities': [
                        {
                            'media_id': 'img1',
                            'media_info': {
                                'original_img_url': 'https://pbs.twimg.com/media/inline1.png',
                                'original_img_width': 120,
                                'original_img_height': 80,
                            },
                        },
                        {
                            'media_id': 'vid1',
                            'media_info': {
                                'duration_millis': 5000,
                                'preview_image': {
                                    'original_img_url': 'https://pbs.twimg.com/media/poster1?format=jpg&name=small',
                                    'original_img_width': 640,
                                    'original_img_height': 360,
                                },
                                'variants': [
                                    {'bit_rate': 256000, 'content_type': 'video/mp4', 'url': 'https://video.twimg.com/low.mp4'},
                                    {'bit_rate': 2176000, 'content_type': 'video/mp4', 'url': 'https://video.twimg.com/high.mp4'},
                                    {'content_type': 'application/x-mpegURL', 'url': 'https://video.twimg.com/playlist.m3u8'},
                                ],
                            },
                        },
                    ],
                    'content_state': {
                        'blocks': [
                            {'key': 'h1', 'type': 'header-two', 'text': 'Section Heading', 'inlineStyleRanges': [], 'entityRanges': []},
                            {
                                'key': 'p1',
                                'type': 'unstyled',
                                'text': text1,
                                'inlineStyleRanges': [
                                    {'offset': bold_offset, 'length': 9, 'style': 'Bold'},
                                    {'offset': bold_offset + 5, 'length': 4, 'style': 'Italic'},
                                ],
                                'entityRanges': [
                                    {'offset': link_offset, 'length': 9, 'key': 0},
                                ],
                                'data': {},
                            },
                            {'key': 'pws', 'type': 'unstyled', 'text': 'lead  spaced bold  tail', 'inlineStyleRanges': [{'offset': 5, 'length': 13, 'style': 'Bold'}], 'entityRanges': []},
                            {'key': 'ul0', 'type': 'unordered-list-item', 'depth': 0, 'text': 'Top bullet', 'inlineStyleRanges': [], 'entityRanges': []},
                            {'key': 'ol1', 'type': 'ordered-list-item', 'depth': 1, 'text': 'Nested step', 'inlineStyleRanges': [{'offset': 0, 'length': 6, 'style': 'Code'}], 'entityRanges': []},
                            {'key': 'bq', 'type': 'blockquote', 'text': 'Quoted insight <script>alert(1)</script>', 'inlineStyleRanges': [], 'entityRanges': []},
                            {'key': 'cb1', 'type': 'code-block', 'text': 'print("hi")', 'data': {'language': 'python'}, 'inlineStyleRanges': [], 'entityRanges': []},
                            {'key': 'a1', 'type': 'atomic', 'text': ' ', 'entityRanges': [{'offset': 0, 'length': 1, 'key': 1}]},
                            {'key': 'a2', 'type': 'atomic', 'text': ' ', 'entityRanges': [{'offset': 0, 'length': 1, 'key': 2}]},
                            {'key': 'a3', 'type': 'atomic', 'text': ' ', 'entityRanges': [{'offset': 0, 'length': 1, 'key': 3}]},
                            {'key': 'a4', 'type': 'atomic', 'text': ' ', 'entityRanges': [{'offset': 0, 'length': 1, 'key': 4}]},
                            {'key': 'a5', 'type': 'atomic', 'text': ' ', 'entityRanges': [{'offset': 0, 'length': 1, 'key': 5}]},
                        ],
                        'entityMap': [
                            {'key': 0, 'value': {'type': 'LINK', 'data': {'url': 'https://cp.lv'}}},
                            {'key': 1, 'value': {'type': 'MEDIA', 'data': {'mediaItems': [{'mediaId': 'img1'}], 'caption': 'Diagram 1'}}},
                            {'key': 2, 'value': {'type': 'MEDIA', 'data': {'mediaItems': [{'mediaId': 'vid1'}], 'caption': 'Demo clip'}}},
                            {'key': 3, 'value': {'type': 'MARKDOWN', 'data': {'markdown': '| k | v |\n| --- | --- |\n| x | 1 |'}}},
                            {'key': 4, 'value': {'type': 'DIVIDER', 'data': {}}},
                            {'key': 5, 'value': {'type': 'LATEX', 'data': {'tex': 'E = mc^2'}}},
                        ],
                    },
                }
            }
        },
    }


class XArticleExtractionTests(unittest.TestCase):
    def test_url_helpers_and_media_upgrade(self):
        self.assertTrue(x_article.is_x_article_url('https://x.com/404jsh/article/2124506790020948428'))
        self.assertTrue(x_article.is_x_article_url('https://x.com/i/article/2124502533179834368'))
        self.assertFalse(x_article.is_x_article_url('https://x.com/404jsh/status/2124506790020948428'))
        self.assertTrue(x_article.is_x_status_url('https://mobile.twitter.com/user/status/123/photo/1'))
        self.assertEqual(
            x_article.status_url_for_gallery_dl('https://x.com/404jsh/article/2124506790020948428'),
            'https://x.com/404jsh/status/2124506790020948428',
        )
        self.assertIsNone(x_article.status_url_for_gallery_dl('https://x.com/i/article/2124502533179834368'))
        self.assertEqual(
            x_article.upgrade_x_media_url('https://pbs.twimg.com/media/abc?format=jpg&name=small'),
            'https://pbs.twimg.com/media/abc?format=jpg&name=orig',
        )
        self.assertEqual(
            x_article.upgrade_x_media_url('https://pbs.twimg.com/media/abc.jpg'),
            'https://pbs.twimg.com/media/abc?format=jpg&name=orig',
        )

    def test_utf16_surrogates_overlapping_styles_whitespace_and_blocks(self):
        tdata = _sample_x_graphql_item()
        result_obj = tdata['article']['article_results']['result']
        parsed = x_article.parse_raw_article(result_obj, tdata, canonical_url='https://x.com/404jsh/status/2124506790020948428')

        self.assertEqual(parsed['title'], 'X Article: Colon "Quotes" & Surrogate Test')
        self.assertEqual(parsed['cover_image'], 'https://pbs.twimg.com/media/cover1?format=jpg&name=orig')
        self.assertEqual(parsed['metrics']['views'], 123456)
        self.assertEqual(parsed['community_note'], 'Readers added helpful context.')
        self.assertIn('## Section Heading', parsed['markdown'])
        self.assertIn('你好🤣🤣世界 **bold_*****part*** [link_here](https://cp.lv)!', parsed['markdown'])
        self.assertIn('<strong>bold_</strong><strong><em>part</em></strong>', parsed['body_html'])
        self.assertIn('lead  **spaced bold**  tail', parsed['markdown'])
        self.assertIn('- Top bullet\n  1. `Nested` step', parsed['markdown'])
        self.assertIn('```python\nprint("hi")\n```', parsed['markdown'])
        self.assertIn('![Diagram 1](https://pbs.twimg.com/media/inline1?format=png&name=orig)', parsed['markdown'])
        self.assertIn('https://video.twimg.com/high.mp4', parsed['markdown'])
        self.assertIn('---\n\n$$\nE = mc^2\n$$', parsed['markdown'])
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', parsed['body_html'])
        self.assertNotIn('<script>alert(1)</script>', parsed['body_html'])

        # Dict-keyed entityMap + entity data.urls/mentions/hashtags fallback + TWEET atomic block
        dict_entity_article = {
            'content_state': {
                'blocks': [
                    {
                        'type': 'unstyled',
                        'text': 'See example.com and @handle #tag',
                        'inlineStyleRanges': [{'offset': 0, 'length': 3, 'style': 'Strikethrough'}],
                        'entityRanges': [],
                        'data': {
                            'urls': [{'fromIndex': 4, 'toIndex': 15, 'url': 'https://example.com'}],
                            'mentions': [{'fromIndex': 20, 'toIndex': 27, 'screenName': 'handle'}],
                            'hashtags': [{'fromIndex': 28, 'toIndex': 32, 'text': 'tag'}],
                        },
                    },
                    {'type': 'atomic', 'text': ' ', 'entityRanges': [{'offset': 0, 'length': 1, 'key': 't1'}]},
                ],
                'entityMap': {
                    't1': {'type': 'TWEET', 'data': {'tweetId': '999888777'}},
                },
            }
        }
        rendered = x_article.render_content_state(dict_entity_article)
        self.assertIn('~~See~~', rendered['markdown'])
        self.assertIn('[example.com](https://example.com)', rendered['markdown'])
        self.assertIn('[@handle](https://x.com/handle)', rendered['markdown'])
        self.assertIn('[#tag](https://x.com/hashtag/tag)', rendered['markdown'])
        self.assertIn('https://x.com/i/web/status/999888777', rendered['markdown'])

    def test_fetch_via_gallery_dl_states(self):
        sample = _sample_x_graphql_item()
        guest_preview = {
            'tweet_id': '2124506790020948428',
            'article': {'rest_id': '2124502533179834368', 'title': 'Preview Only', 'preview_text': 'Short'},
        }
        normal_tweet = {'tweet_id': '111', 'content': 'Regular post without article'}

        with tempfile.TemporaryDirectory() as tmp:
            cookie_file = Path(tmp) / 'cookies.txt'
            cookie_file.write_text('# Netscape HTTP Cookie File\n')

            with patch.object(
                x_article,
                'run_process',
                return_value=CompletedProcess([], 0, json.dumps([[2, sample]]) + '\n', ''),
            ) as mock_run:
                res = x_article.fetch_via_gallery_dl(
                    'https://x.com/404jsh/article/2124506790020948428',
                    'gallery-dl',
                    cookie_path=cookie_file,
                )
            self.assertEqual(res['title'], 'X Article: Colon "Quotes" & Surrogate Test')
            cmd = mock_run.call_args.args[0]
            self.assertIn('extractor.twitter.articles=cover,media', cmd)
            self.assertEqual(cmd[-1], 'https://x.com/404jsh/status/2124506790020948428')

            with patch.object(
                x_article,
                'run_process',
                return_value=CompletedProcess([], 0, json.dumps([[2, guest_preview]]) + '\n', ''),
            ):
                with self.assertRaises(x_article.XArticleLoginRequired):
                    x_article.fetch_via_gallery_dl('https://x.com/404jsh/status/2124506790020948428', 'gallery-dl')

            with patch.object(
                x_article,
                'run_process',
                return_value=CompletedProcess([], 0, json.dumps([[2, normal_tweet]]) + '\n', ''),
            ):
                self.assertIsNone(
                    x_article.fetch_via_gallery_dl('https://x.com/user/status/111', 'gallery-dl')
                )

            with patch.object(
                x_article,
                'run_process',
                return_value=CompletedProcess([], 1, '', 'HTTP 401 Unauthorized: cookies expired'),
            ):
                with self.assertRaises(x_article.XArticleLoginRequired):
                    x_article.fetch_via_gallery_dl('https://x.com/user/status/111', 'gallery-dl')

            with patch.object(
                x_article,
                'run_process',
                return_value=CompletedProcess([], 0, '[]', '[twitter][warning] No such tweet (404)'),
            ):
                with self.assertRaises(x_article.XArticleError):
                    x_article.fetch_via_gallery_dl('https://x.com/user/status/404', 'gallery-dl')


class XArticleBrowserTests(unittest.TestCase):
    def test_x_article_browser_js_and_validation(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node not installed')
        script_path = Path(rendered_page.__file__).with_name('x_article_browser.js')
        js_code = script_path.read_text()

        # Case 1: complete DOM extraction with Twemoji alt substitution & media upgrade
        runner = (
            'const expectedUrl = "https://x.com/404jsh/status/2124506790020948428";\n'
            'function makeNode(tag, opts = {}) {\n'
            '  const children = opts.children || [];\n'
            '  const attrs = Object.assign({}, opts.attrs || {});\n'
            '  return {\n'
            '    nodeType: 1, tagName: tag.toUpperCase(),\n'
            '    innerText: opts.innerText || opts.textContent || "",\n'
            '    textContent: opts.textContent || opts.innerText || "",\n'
            '    src: attrs.src || "", alt: attrs.alt || "", poster: attrs.poster || "", href: attrs.href || "",\n'
            '    dateTime: attrs.datetime || "", parentElement: null,\n'
            '    attributes: Object.keys(attrs).map(k => ({name: k, value: attrs[k]})),\n'
            '    getAttribute(k) { return k in attrs ? attrs[k] : null; },\n'
            '    setAttribute(k, v) { attrs[k] = String(v); if (k === "src") this.src = String(v); },\n'
            '    removeAttribute(k) { delete attrs[k]; },\n'
            '    hasAttribute(k) { return k in attrs; },\n'
            '    contains(other) { return (this._imgs || []).includes(other) || other === inlineImg; },\n'
            '    before() {}, after() {}, remove() {},\n'
            '    childNodes: children,\n'
            '    querySelector(sel) {\n'
            '      if (sel.includes("twitterArticleReadView")) return this._view || null;\n'
            '      if (sel.includes("twitter-article-title") || sel === "h1") return this._title || null;\n'
            '      if (sel.includes("twitterArticleRichTextView")) return this._rich || null;\n'
            '      if (sel.includes("article-cover-image")) return this._cover || null;\n'
            '      if (sel === "time[datetime]") return this._time || null;\n'
            '      if (sel.includes("User-Name")) return this._user || null;\n'
            '      if (sel === "source[src]") return this._source || null;\n'
            '      return null;\n'
            '    },\n'
            '    querySelectorAll(sel) {\n'
            '      if (sel === "img") return this._allImgs || this._imgs || [];\n'
            '      if (sel === "video") return this._vids || [];\n'
            '      if (sel === "*") return this._all || [];\n'
            '      return this._blocks || [];\n'
            '    },\n'
            '    cloneNode() {\n'
            '      const c = Object.assign({}, this);\n'
            '      c.querySelectorAll = (sel) => {\n'
            '        if (sel === "img") return this._emojis || [];\n'
            '        if (sel === "*") return this._all || [];\n'
            '        return [];\n'
            '      };\n'
            '      c.cloneNode = () => c;\n'
            '      return c;\n'
            '    },\n'
            '    replaceWith(node) { this._replaced = node; },\n'
            '    closest() { return null; },\n'
            '    innerHTML: opts.innerHTML || "<p>Paragraph text for verification 🤣 with enough length.</p>",\n'
            '  };\n'
            '}\n'
            'const emoji = makeNode("img", {attrs: {src: "https://abs-0.twimg.com/emoji/v2/svg/1f923.svg", alt: "🤣"}});\n'
            'const inlineImg = makeNode("img", {attrs: {src: "https://pbs.twimg.com/media/in1?format=jpg&name=small", alt: "pic"}});\n'
            'const coverImg = makeNode("img", {attrs: {src: "https://pbs.twimg.com/media/cov1?format=jpg&name=small"}});\n'
            'const rich = makeNode("div", {\n'
            '  innerText: "Paragraph text for verification 🤣 with enough length for validation.",\n'
            '  innerHTML: "<p>Paragraph text for verification 🤣 with enough length for validation.</p><img src=\\"https://pbs.twimg.com/media/in1?format=jpg&name=small\\" alt=\\"pic\\">",\n'
            '});\n'
            'rich._emojis = [emoji, inlineImg];\n'
            'rich._all = [inlineImg];\n'
            'rich._imgs = [inlineImg];\n'
            'const coverWrap = makeNode("div"); coverWrap._imgs = [coverImg];\n'
            'coverImg.getBoundingClientRect = () => ({width: 800, height: 400});\n'
            'inlineImg.getBoundingClientRect = () => ({width: 600, height: 300});\n'
            'const view = makeNode("div");\n'
            'view._title = makeNode("h1", {innerText: "Rendered X Article"});\n'
            'view._cover = coverWrap;\n'
            'view._rich = rich;\n'
            'view._allImgs = [coverImg, emoji, inlineImg];\n'
            'view._vids = [];\n'
            'const timeEl = makeNode("time", {attrs: {datetime: "2026-08-01T10:00:00Z"}});\n'
            'const userEl = makeNode("div", {innerText: "Author\\n@404jsh"});\n'
            'global.location = {href: expectedUrl, pathname: "/404jsh/status/2124506790020948428"};\n'
            'global.document = {\n'
            '  title: "X Article",\n'
            '  body: {innerText: ""},\n'
            '  createTextNode: (t) => ({nodeType: 3, textContent: t}),\n'
            '  querySelector(sel) {\n'
            '    if (sel.includes("twitterArticleReadView")) return view;\n'
            '    if (sel === "time[datetime]") return timeEl;\n'
            '    if (sel.includes("User-Name")) return userEl;\n'
            '    if (sel.includes("login")) return null;\n'
            '    return null;\n'
            '  },\n'
            '};\n'
            f'const fn = ({js_code});\n'
            'console.log(JSON.stringify(fn(expectedUrl)));\n'
        )
        proc = subprocess.run([node, '-e', runner], capture_output=True, text=True, check=True)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload['title'], 'Rendered X Article')
        self.assertEqual(payload['cover_image'], 'https://pbs.twimg.com/media/cov1?format=jpg&name=orig')
        self.assertEqual(payload['images'], ['https://pbs.twimg.com/media/in1?format=jpg&name=orig'])

        validated = rendered_page.validate_x_article_page(
            {
                **payload,
                'browser_controller': {
                    'kind': 'browseros_neo_mcp',
                    'reader_revision': 1,
                    'temporary_tab_cleanup': 'closed',
                    'credentials_exported': False,
                    'server_version': '1.0.0',
                },
            },
            'https://x.com/404jsh/status/2124506790020948428',
        )
        self.assertEqual(validated['author_handle'], '404jsh')
        self.assertEqual(validated['cover_image'], 'https://pbs.twimg.com/media/cov1?format=jpg&name=orig')

        # Case 2: login redirect diagnostic
        login_runner = (
            'global.location = {href: "https://x.com/i/flow/login", pathname: "/i/flow/login"};\n'
            'global.document = {title: "Log in", body: {innerText: ""}, querySelector: () => null};\n'
            f'const fn = ({js_code});\n'
            'console.log(JSON.stringify(fn("https://x.com/404jsh/status/2124506790020948428")));\n'
        )
        proc_login = subprocess.run([node, '-e', login_runner], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(proc_login.stdout), {'diagnostic': {'reason': 'x_article_login_required'}})


class WebArticleUpgradesTests(unittest.TestCase):
    def test_rich_html_elements_and_asset_rerender(self):
        raw_html = '''<!doctype html>
        <html lang="zh-CN">
        <head>
          <meta charset="utf-8">
          <meta property="og:image" content="https://cdn.example.com/cover.jpg">
          <meta property="og:site_name" content="Example Blog">
          <script type="application/ld+json">
            {"@type": "Article", "headline": "Rich Web Guide", "author": {"name": "Alice", "url": "https://blog.example.com/alice"},
             "datePublished": "2026-08-01", "dateModified": "2026-08-02", "keywords": ["python", "archiving"]}
          </script>
        </head>
        <body>
          <article>
            <h1>Rich Web Guide</h1>
            <p>Has <del>old</del> text<sup>1</sup> and H<sub>2</sub>O.</p>
            <figure>
              <img data-src="/assets/small.png" srcset="/assets/small.png 400w, /assets/large.png 1200w" alt="Arch">
              <figcaption>Architecture figure</figcaption>
            </figure>
            <video poster="/posters/v1.jpg"><source src="/videos/demo.mp4" type="video/mp4"></video>
            <table><thead><tr><th>Name</th><th>Value</th></tr></thead><tbody><tr><td>A</td><td>1</td></tr></tbody></table>
            <pre><code class="language-python">def ok():
    return True</code></pre>
            <dl><dt>Term</dt><dd>Definition here</dd></dl>
          </article>
        </body>
        </html>'''

        parsed = web_article.extract_html_article(raw_html, 'https://blog.example.com/posts/1')
        self.assertEqual(parsed['language'], 'zh-CN')
        self.assertEqual(parsed['site_name'], 'Example Blog')
        self.assertEqual(parsed['modified_at'], '2026-08-02')
        self.assertEqual(parsed['keywords'], ['python', 'archiving'])
        self.assertEqual(parsed['cover_image'], 'https://cdn.example.com/cover.jpg')
        self.assertIn('~~old~~', parsed['markdown'])
        self.assertIn('![Arch](https://blog.example.com/assets/large.png)', parsed['markdown'])
        self.assertIn('*Architecture figure*', parsed['markdown'])
        self.assertIn('https://blog.example.com/videos/demo.mp4', parsed['markdown'])
        self.assertIn('| Name | Value |\n| --- | --- |\n| A | 1 |', parsed['markdown'])
        self.assertIn('```python\ndef ok():\n    return True\n```', parsed['markdown'])
        self.assertIn('**Term**', parsed['markdown'])

        rerendered = web_article.rerender_html_article(
            parsed['raw_html'],
            'https://blog.example.com/posts/1',
            asset_map={
                'https://blog.example.com/assets/large.png': 'assets/img-001.png',
                'https://blog.example.com/videos/demo.mp4': 'assets/video-001.mp4',
                'https://blog.example.com/posters/v1.jpg': 'assets/video-001-poster.jpg',
            },
        )
        self.assertIn('![Arch](assets/img-001.png)', rerendered['markdown'])
        self.assertIn('src="assets/video-001.mp4"', rerendered['body_html'])
        self.assertIn('poster="assets/video-001-poster.jpg"', rerendered['body_html'])


class ArticleArchiveTests(unittest.TestCase):
    def test_classify_article_target_boundaries(self):
        self.assertIsNone(article_archive.classify_article_target('https://x.com/user/status/123'))
        self.assertEqual(
            article_archive.classify_article_target('https://x.com/user/status/123', force_article=True)['kind'],
            'x_article',
        )
        self.assertEqual(
            article_archive.classify_article_target('https://x.com/user/article/123')['kind'],
            'x_article',
        )
        self.assertEqual(
            article_archive.classify_article_target('https://mp.weixin.qq.com/s/AbCdEf123456')['kind'],
            'wechat_article',
        )
        self.assertEqual(
            article_archive.classify_article_target('https://www.toutiao.com/article/7000001/')['kind'],
            'toutiao_article',
        )
        self.assertIsNone(article_archive.classify_article_target('https://www.toutiao.com/video/7000001/'))
        self.assertIsNone(article_archive.classify_article_target('https://www.pixiv.net/artworks/123', force_article=True))
        self.assertEqual(
            article_archive.classify_article_target('https://blog.example.com/post', force_article=False)['kind'],
            'web_article',
        )
        self.assertEqual(
            article_archive.classify_article_target('https://blog.example.com/post', force_article=True)['kind'],
            'web_article',
        )
        with self.assertRaises(ValueError):
            article_archive.classify_article_target('https://127.0.0.1/secret')
        # Literal 198.18.0.0/15 stays blocked; hostname DNS Fake-IP is allowed unless mixed with private IPs.
        with self.assertRaises(ValueError):
            web_article.validate_public_web_url('https://198.18.0.230/pic.jpg', resolve_dns=False)
        with patch.object(web_article.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('198.18.0.230', 443))]):
            with patch.object(web_article, '_fake_ip_proxy_active', return_value=False):
                with self.assertRaises(ValueError):
                    web_article.validate_public_web_url('https://pbs.twimg.com/media/x.jpg', resolve_dns=True)
            with patch.object(web_article, '_fake_ip_proxy_active', return_value=True):
                self.assertEqual(
                    web_article.validate_public_web_url('https://pbs.twimg.com/media/x.jpg', resolve_dns=True),
                    'https://pbs.twimg.com/media/x.jpg',
                )
        with patch.object(web_article, '_fake_ip_proxy_active', return_value=True), \
             patch.object(
                 web_article.socket,
                 'getaddrinfo',
                 return_value=[(2, 1, 6, '', ('198.18.0.230', 443)), (2, 1, 6, '', ('127.0.0.1', 443))],
             ):
            with self.assertRaises(ValueError):
                web_article.validate_public_web_url('https://pbs.twimg.com/media/x.jpg', resolve_dns=True)

    def test_ssrf_html_masquerade_fallback_xss_and_collision_safety(self):
        png = _png_bytes(120, 80)
        jpg = _jpeg_bytes(2000, 800)
        mp4 = _mp4_bytes()

        def handle_req(req):
            url = req.full_url
            if url.endswith('cover1?format=jpg&name=orig'):
                return FakeResponse(jpg, url, content_type='image/jpeg')
            if url.endswith('inline1?format=png&name=orig'):
                return FakeResponse(png, url, content_type='application/octet-stream')
            if url.endswith('high.mp4'):
                return FakeResponse(mp4, url, content_type='video/mp4')
            if url.endswith('poster1?format=jpg&name=orig'):
                return FakeResponse(b'<!doctype html><html><body>blocked</body></html>', url, content_type='text/html')
            if url.endswith('avatar_normal.jpg'):
                return FakeResponse(jpg, url, content_type='image/jpeg')
            raise AssertionError(f'Unexpected URL: {url}')

        opener = FakeOpener(handle_req)
        sample = _sample_x_graphql_item()
        article = x_article.parse_raw_article(
            sample['article']['article_results']['result'],
            sample,
            canonical_url='https://x.com/404jsh/status/2124506790020948428',
        )

        # Verify SSRF blocks private IP directly on download_article_assets
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(web_article.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 0))]):
                _, manifest, lims = article_archive.download_article_assets(
                    root / 'ssrf-check',
                    [{'role': 'cover', 'media_type': 'image', 'original_url': 'https://internal.example.com/img.png'}],
                    resolve_dns=True,
                )
            self.assertEqual(manifest[0]['status'], 'failed')
            self.assertTrue(lims)

            b1 = article_archive.allocate_bundle_dir(root, article)
            b2 = article_archive.allocate_bundle_dir(root, article)
            self.assertNotEqual(b1, b2)
            self.assertTrue(b2.name.endswith('-1'))

            clean, written = article_archive.write_article_bundle(
                b1,
                article,
                mode='download',
                download_assets=True,
                opener=opener,
                resolve_dns=False,
            )
            self.assertTrue((b1 / 'article.md').is_file())
            self.assertTrue((b1 / 'article.html').is_file())
            self.assertTrue((b1 / 'content.json').is_file())
            self.assertTrue((b1 / 'content.txt').is_file())
            self.assertTrue((b1 / 'source/x-article.json').is_file())
            self.assertTrue((b1 / 'assets/cover.jpg').is_file())
            self.assertTrue((b1 / 'assets/img-001.png').is_file())
            self.assertTrue((b1 / 'assets/video-001.mp4').is_file())
            self.assertEqual(clean['assets_downloaded'], 4)
            self.assertEqual(clean['assets_failed'], 1)  # HTML masquerade poster rejected safely

            # Verify transient fields removed from content.json
            saved_json = json.loads((b1 / 'content.json').read_text())
            for transient in ('raw_html', 'raw_source', 'body_html', 'media_items', 'content_state'):
                self.assertNotIn(transient, saved_json)
            cover_rec = next(item for item in saved_json['assets'] if item['role'] == 'cover')
            self.assertEqual((cover_rec['width'], cover_rec['height']), (2000, 800))
            img_rec = next(item for item in saved_json['assets'] if item['role'] == 'inline')
            self.assertEqual((img_rec['width'], img_rec['height']), (120, 80))

            md_text = (b1 / 'article.md').read_text()
            self.assertIn('![X Article: Colon "Quotes" & Surrogate Test](assets/cover.jpg)', md_text)
            self.assertIn('![Diagram 1](assets/img-001.png)', md_text)
            self.assertIn('title: "X Article: Colon \\"Quotes\\" & Surrogate Test"', md_text)

            html_text = (b1 / 'article.html').read_text()
            self.assertIn('Author &lt;Tag&gt;', html_text)
            self.assertNotIn('<script>alert(1)</script>', html_text)
            self.assertIn('assets/cover.jpg', html_text)


class CLIAndMCPArticleIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for p in (
            patch.object(accounts, 'directory', return_value=self.root / 'data/sessions'),
            patch.object(read_store, 'root', return_value=self.root / 'data'),
        ):
            p.start()
            self.addCleanup(p.stop)

    def run_cli(self, *argv):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = cli.main(list(argv))
        return code, stdout.getvalue(), stderr.getvalue()

    def test_config_article_folder_and_inspect(self):
        art_dir = self.root / 'articles'
        code, out, _ = self.run_cli('config', '--article-folder', str(art_dir), '--json')
        self.assertEqual(code, 0)
        cfg = json.loads(out)
        self.assertEqual(cfg['article_destination'], {'mode': 'folder', 'path': str(art_dir.resolve())})

        code, out, _ = self.run_cli('inspect', 'https://x.com/404jsh/article/2124506790020948428', '--no-network', '--json')
        self.assertEqual(code, 0)
        insp = json.loads(out)
        self.assertEqual(insp['site'], 'X Article')
        self.assertEqual(insp['engine'], 'x_article')
        self.assertTrue(insp['supports_article_download'])
        self.assertEqual(insp['destination_preview'], str(art_dir.resolve()))

        code, out, _ = self.run_cli('inspect', 'https://x.com/404jsh/status/2124506790020948428', '--no-network', '--json')
        self.assertEqual(code, 0)
        insp_status = json.loads(out)
        self.assertEqual(insp_status['engine'], 'gallery-dl')
        self.assertTrue(insp_status['supports_article_download'])

        code, out, _ = self.run_cli('inspect', 'https://x.com/404jsh/status/2124506790020948428', '--article', '--no-network', '--json')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['engine'], 'x_article')

        code, out, _ = self.run_cli('https://x.com/i/article/2124502533179834368', '--folder', str(art_dir), '--browser', 'none', '--json')
        self.assertEqual(code, 1)
        self.assertIn('/i/article/ID', json.loads(out)['error']['message'])

        code, out, _ = self.run_cli('config', '--clear-article-folder', '--json')
        self.assertEqual(code, 0)
        self.assertIsNone(json.loads(out)['article_destination'])

        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['--photos', '--article', 'https://x.com/404jsh/status/1'])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.main(['inspect', 'https://example.com/a', '--no-assets'])

    def test_x_status_auto_promotion_download_and_read_and_normal_tweet_preserved(self):
        sample = _sample_x_graphql_item()
        out_root = self.root / 'out'
        jpg = _jpeg_bytes(2000, 800)
        png = _png_bytes(120, 80)
        mp4 = _mp4_bytes()

        def handle_asset(req, timeout=30):
            url = req.full_url
            if url.endswith('high.mp4'):
                return FakeResponse(mp4, url, content_type='video/mp4')
            if url.endswith('.png?name=orig') or 'inline1' in url:
                return FakeResponse(png, url, content_type='image/png')
            return FakeResponse(jpg, url, content_type='image/jpeg')

        fake_opener = FakeOpener(handle_asset)

        def fake_dl_run(command, stdout=None, stderr=None, stdin=None, **kwargs):
            cmd_str = ' '.join(map(str, command))
            if '-j' in command:
                return CompletedProcess(command, 0, json.dumps([[2, sample]]) + '\n', '')
            if '2124506790020948428' in cmd_str:
                stdout.write('[twitter][warning] 2124506790020948428: Error while processing article data (KeyError: \'legacy\')\n')
                return CompletedProcess(command, 0)
            if '/status/111222' in cmd_str:
                idx = command.index('--directory') + 1
                media_dir = Path(command[idx])
                (media_dir / 'photo.jpg').write_bytes(jpg)
                return CompletedProcess(command, 0)
            raise AssertionError(f'Unexpected command: {command}')

        created_stages = []
        orig_mkdtemp = cli.tempfile.mkdtemp

        def tracked_mkdtemp(**kwargs):
            p = orig_mkdtemp(**kwargs)
            if kwargs.get('prefix') == 'linkget-':
                created_stages.append(Path(p))
            return p

        with patch.object(cli, 'find_tool', side_effect=lambda n: n if n == 'gallery-dl' else None), \
             patch.object(cli, 'run_process', side_effect=fake_dl_run), \
             patch.object(x_article, 'run_process', side_effect=fake_dl_run), \
             patch.object(article_archive, 'build_opener', return_value=fake_opener), \
             patch.object(web_article.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 0))]), \
             patch.object(cli.tempfile, 'mkdtemp', side_effect=tracked_mkdtemp):

            # 1. Dynamic X status -> X Article promotion in download mode
            code, out, err = self.run_cli(
                'https://x.com/404jsh/status/2124506790020948428',
                '--folder', str(out_root),
                '--browser', 'none',
                '--json',
            )
            self.assertEqual(code, 0, err + out)
            payload = json.loads(out)
            self.assertTrue(payload['ok'])
            self.assertEqual(payload['site'], 'X')
            self.assertIn('article_folder', payload)
            bundle = Path(payload['article_folder'])
            self.assertTrue((bundle / 'article.md').is_file())
            self.assertTrue((bundle / 'article.html').is_file())
            self.assertTrue((bundle / 'source/x-article.json').is_file())
            self.assertTrue((bundle / 'assets/cover.jpg').is_file())
            self.assertEqual(payload['assets_downloaded'], 5)
            self.assertEqual(payload['assets_failed'], 0)
            # Promoted temporary stage must be cleaned cleanly
            self.assertFalse(created_stages[-1].exists())

            # 2. Explicit --photos is refused cleanly on promoted X Article
            with patch.object(cli.sys, 'platform', 'darwin'), \
                 patch.object(cli, 'preflight') as mock_preflight:
                code, out, err = self.run_cli(
                    'https://x.com/404jsh/status/2124506790020948428',
                    '--photos',
                    '--browser', 'none',
                    '--json',
                )
            self.assertEqual(code, 1)
            self.assertIn('Photos', json.loads(out)['error']['message'])

            # Raycast can supply a per-run article folder while keeping Photos as the media default.
            with patch.object(cli.sys, 'platform', 'darwin'), \
                 patch.object(cli, 'preflight'):
                code, out, err = self.run_cli(
                    'https://x.com/404jsh/status/2124506790020948428',
                    '--article-folder', str(self.root / 'only-this-run'),
                    '--browser', 'none',
                    '--json',
                )
            self.assertEqual(code, 0, err + out)
            self.assertEqual(Path(json.loads(out)['article_folder']).parent, (self.root / 'only-this-run').resolve())
            self.assertIsNone(cli.configured_article_destination())

            # 3. Dynamic X status -> X Article promotion in `linkget read`
            read_out = self.root / 'read-x-article'
            code, out, err = self.run_cli(
                'read',
                'https://x.com/404jsh/status/2124506790020948428',
                '--folder', str(read_out),
                '--browser', 'none',
                '--foreground',
                '--json',
            )
            self.assertEqual(code, 0, err + out)
            read_payload = json.loads(out)
            self.assertTrue(read_payload['ok'])
            self.assertTrue((read_out / 'content.md').is_file())
            self.assertTrue((read_out / 'article.html').is_file())
            self.assertTrue((read_out / 'content.txt').is_file())
            self.assertTrue((read_out / 'source/x-article.json').is_file())
            self.assertTrue((read_out / 'assets/cover.jpg').is_file())

            # 4. Normal X tweet still downloads media normally without article promotion
            norm_out = self.root / 'normal-x'
            with patch.object(cli, 'inspect_files', return_value=[{'path': 'photo.jpg', 'kind': 'image', 'source': 'original', 'probe_ok': True, 'bytes': len(jpg)}]):
                code, out, err = self.run_cli(
                    'https://x.com/user/status/111222',
                    '--folder', str(norm_out),
                    '--browser', 'none',
                    '--json',
                )
            self.assertEqual(code, 0, err + out)
            norm_payload = json.loads(out)
            self.assertNotIn('article_folder', norm_payload)
            self.assertTrue((norm_out / 'photo.jpg').is_file())

    def test_web_article_no_assets_batch_and_mcp(self):
        sample_html = '''<!doctype html><html><head><title>Offline Guide</title></head>
        <body><article><h1>Offline Guide</h1><p>Full guide body with images.</p>
        <img src="https://cdn.example.com/pic.png" alt="pic"></article></body></html>'''

        def handle_web(req, timeout=30):
            url = req.full_url
            if url.endswith('/guide'):
                return FakeResponse(sample_html.encode('utf-8'), url, content_type='text/html; charset=utf-8')
            return FakeResponse(_png_bytes(50, 50), url, content_type='image/png')

        web_opener = FakeOpener(handle_web)
        with patch.object(web_article, 'build_opener', return_value=web_opener), \
             patch.object(article_archive, 'build_opener', return_value=web_opener), \
             patch.object(web_article.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 0))]):

            # Batch with --article --no-assets
            batch_out = self.root / 'web-batch'
            code, out, err = self.run_cli(
                'batch',
                'https://example.com/guide',
                '--article',
                '--no-assets',
                '--folder', str(batch_out),
                '--browser', 'none',
                '--json',
            )
            self.assertEqual(code, 0, err + out)
            b_payload = json.loads(out)
            self.assertTrue(b_payload['ok'])
            item = b_payload['items'][0]
            self.assertEqual(item['assets_downloaded'], 0)
            self.assertFalse((Path(item['article_folder']) / 'assets').exists())
            self.assertTrue((Path(item['article_folder']) / 'source/original.html').is_file())

            # MCP inspect_url & download_media
            insp_res = mcp_server._call_tool('inspect_url', {'url_or_path': 'https://example.com/guide', 'article': True, 'no_network': True})
            self.assertTrue(insp_res['supports_article_download'])
            dl_res = mcp_server._call_tool('download_media', {
                'url': 'https://example.com/guide',
                'folder': str(self.root / 'mcp-dl'),
                'article': True,
                'browser': 'none',
            })
            self.assertTrue(dl_res['ok'])
            self.assertEqual(dl_res['assets_downloaded'], 1)
            self.assertTrue((Path(dl_res['article_folder']) / 'assets/img-001.png').is_file())

            # Mixed batch isolates SSRF failure per item while completing valid article items
            bad_batch = self.root / 'mixed-batch'
            code, out, err = self.run_cli(
                'batch',
                'https://127.0.0.1/secret',
                'https://example.com/guide',
                '--folder', str(bad_batch),
                '--browser', 'none',
                '--json',
            )
            self.assertEqual(code, 1)
            mixed = json.loads(out)
            self.assertEqual(mixed['completed_count'], 1)
            self.assertEqual(mixed['failed_count'], 1)
            self.assertEqual(mixed['items'][0]['status'], 'failed')
            self.assertEqual(mixed['items'][1]['status'], 'completed')

    def test_symlink_refusal_svg_html_guard_fragment_and_read_retry_idempotence(self):
        png = _png_bytes(40, 40)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real_dir = root / 'real'
            real_dir.mkdir()
            sym_dir = root / 'sym-dest'
            sym_dir.symlink_to(real_dir)

            sample = {'title': 'Retry Check', 'text': 'Body text', 'url': 'https://example.com/p'}
            with self.assertRaises(ValueError):
                article_archive.allocate_bundle_dir(sym_dir, sample)
            with self.assertRaises(ValueError):
                article_archive.prepare_read_archive(sym_dir, sample, download_assets=False)

            bad_bundle = root / 'bad-source'
            bad_bundle.mkdir()
            (bad_bundle / 'source').symlink_to(real_dir)
            with self.assertRaises(ValueError):
                article_archive.write_article_bundle(
                    bad_bundle,
                    {**sample, 'raw_html': '<html><body>hi</body></html>'},
                    download_assets=False,
                )

            # HTML page containing <svg> inside <div> must be rejected, not saved as .svg
            with self.assertRaises(ValueError):
                article_archive._sniff_media_ext(
                    b'<div><svg xmlns="http://www.w3.org/2000/svg"></svg></div>',
                    'text/html; charset=utf-8',
                    'https://cdn.example.com/icon',
                    'image',
                )
            with self.assertRaises(ValueError):
                article_archive._sniff_media_ext(
                    b'<div><svg></svg></div>',
                    'application/octet-stream',
                    'https://cdn.example.com/err',
                    'image',
                )
            self.assertEqual(
                article_archive._sniff_media_ext(
                    b'<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>',
                    'image/svg+xml',
                    'https://cdn.example.com/icon.svg',
                    'image',
                ),
                ('.svg', 'image/svg+xml'),
            )

            # Browser fragment with dom_proof containing literal "<html>" text routes to render_fragment
            frag_opener = FakeOpener(lambda req: FakeResponse(png, req.full_url, content_type='image/png'))
            retry_dir = root / 'read-retry'
            frag_article = {
                'title': 'Fragment Test',
                'url': 'https://example.com/frag',
                'final_url': 'https://example.com/frag',
                'text': 'Example body with <html> literal snippet.',
                'raw_html': '<p>Example body with &lt;html&gt; literal snippet <img src="https://cdn.example.com/p.png" alt="p"></p>',
                'media_items': [{'role': 'inline', 'media_type': 'image', 'original_url': 'https://cdn.example.com/p.png'}],
                'dom_proof': {'basis': 'bounded_x_article_dom'},
                'fetched_at': '2026-08-01T10:00:00Z',
            }
            article_archive.prepare_read_archive(
                retry_dir,
                frag_article,
                opener=frag_opener,
                resolve_dns=False,
                formats=['md'],
            )
            self.assertTrue((retry_dir / 'assets/img-001.png').is_file())

            # Retry on same incomplete folder (with updated fetched_at) reuses img-001.png without creating -1 or crashing
            frag_article_v2 = {**frag_article, 'fetched_at': '2026-08-01T10:05:00Z'}
            clean_retry = article_archive.prepare_read_archive(
                retry_dir,
                frag_article_v2,
                opener=frag_opener,
                resolve_dns=False,
                formats=['md'],
            )
            self.assertFalse((retry_dir / 'assets/img-001-1.png').exists())
            self.assertEqual(clean_retry['files']['assets'], ['assets/img-001.png'])
            self.assertIn('markdown', clean_retry['files'])
            self.assertNotIn('text', clean_retry['files'])


if __name__ == '__main__':
    unittest.main()
