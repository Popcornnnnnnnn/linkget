import urllib.request
import re

url = "https://www.kuaishou.com/short-video/3xu7kpznwgbvtds?streamSource=hotrank&trendingId=U23%E5%9B%BD%E8%B6%B31%E6%AF%942%E9%9F%A9%E5%9B%BD&area=homexxunknown"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36'})
try:
    html = urllib.request.urlopen(req).read().decode('utf-8')
    with open('/tmp/ks.html', 'w') as f:
        f.write(html)
    match = re.search(r'window\.window\.__INITIAL_STATE__=({.*?});', html)
    if not match:
        match = re.search(r'window\.__INITIAL_STATE__=({.*?});', html)
    if match:
        print("State found")
    else:
        print("No state found")
except Exception as e:
    print(e)
