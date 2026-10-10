import subprocess
import urllib.request

url = "https://www.reddit.com/r/pics/comments/1fpi61c/the_oldest_surviving_photograph_taken_in/"
try:
    urllib.request.urlopen(url)
    print("Accessible")
except Exception as e:
    print(e)
