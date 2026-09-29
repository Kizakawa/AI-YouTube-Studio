import urllib.request
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

vids = [
    "Ek4mr52jcRI",
    "hl7ziBiwlsw",
    "KmGec-ZZiXs",
    "Rq61eyoCEWw",
    "epjJFN2GmBk",
    "CCywMy8q-Xo",
    "WJ5VBFqS06Y"
]

print("Fetching metadata for 7 videos on Psychology of the world:")
for v in vids:
    url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={v}&format=json"
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        res = urllib.request.urlopen(req).read().decode('utf-8')
        data = json.loads(res)
        title = data.get("title", "Unknown")
        author = data.get("author_name", "Unknown")
        print(f"[{v}] Title: {title} | Author: {author}")
    except Exception as e:
        print(f"[{v}] Error: {e}")
