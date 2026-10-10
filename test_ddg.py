import urllib.request
import urllib.parse
import re

url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote("site:kuaishou.com/f/photo")
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
html = urllib.request.urlopen(req).read().decode('utf-8')
links = re.findall(r'uddg=([^&]+)', html)
for l in links:
    print(urllib.parse.unquote(l))
