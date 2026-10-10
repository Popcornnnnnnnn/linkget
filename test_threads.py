import urllib.request
import re
url = "https://www.threads.net/@zuck/post/CuW6-y1x7B2"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)'})
try:
    html = urllib.request.urlopen(req).read().decode('utf-8')
    print("Threads HTML length:", len(html))
    match = re.search(r'data-content-len="(\d+)"', html)
    print("Has data-content-len?", bool(match))
except Exception as e:
    print("Threads error:", e)
