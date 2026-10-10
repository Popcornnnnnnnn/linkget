import urllib.request
import re

req = urllib.request.Request("https://old.reddit.com/login", headers={'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36'})
html = urllib.request.urlopen(req).read().decode('utf-8')
print("'login-form' in old.reddit.com:", "login-form" in html or "login" in html)
