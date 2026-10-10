import urllib.request
import urllib.parse
import re

url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote("site:xiaohongshu.com/explore 实况")
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
try:
    html = urllib.request.urlopen(req).read().decode('utf-8')
    links = re.findall(r'href="(//www\.xiaohongshu\.com/explore/[a-zA-Z0-9]+)"', html)
    for l in links[:5]:
        print("https:" + l)
except Exception as e:
    print(e)
