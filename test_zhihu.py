import urllib.request
import re

url = "https://zhuanlan.zhihu.com/p/354494191"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    html = urllib.request.urlopen(req).read().decode('utf-8')
    images = re.findall(r'data-actualsrc="([^"]+)"', html)
    print(len(images))
    print(images[:3])
except Exception as e:
    print(e)
