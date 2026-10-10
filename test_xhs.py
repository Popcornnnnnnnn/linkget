import json
import sys
from urllib.parse import urlsplit
from http.cookiejar import MozillaCookieJar
from urllib.request import HTTPCookieProcessor, Request, build_opener
from src.linkget.xiaohongshu import initial_state

url = sys.argv[1]
jar = MozillaCookieJar("/Users/forge/Library/Application Support/linkget/sessions/xiaohongshu.com.txt")
jar.load(ignore_discard=True)
opener = build_opener(HTTPCookieProcessor(jar))
req = Request(url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36", "Referer": "https://www.xiaohongshu.com/"})
try:
    resp = opener.open(req, timeout=20).read().decode('utf-8', 'replace')
    state = initial_state(resp)
    with open("/tmp/xhs_state.json", "w") as f:
        json.dump(state, f, indent=2)
    print("State saved to /tmp/xhs_state.json")
except Exception as e:
    print(e)
