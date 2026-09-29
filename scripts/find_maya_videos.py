import urllib.request
import re
import json
import sys

sys.stdout.reconfigure(encoding='utf-8')

headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
req = urllib.request.Request('https://www.youtube.com/@VV1n-oe9gs/shorts', headers=headers)

try:
    html = urllib.request.urlopen(req).read().decode('utf-8')
except Exception as e:
    print(f"Error fetching page: {e}")
    sys.exit(1)

# Find ytInitialData
match = re.search(r'var ytInitialData\s*=\s*({.*?});</script>', html)
if not match:
    # Try alternate regex
    match = re.search(r'ytInitialData\s*=\s*({.*?});', html)

if match:
    json_str = match.group(1)
    # Extract richItemRenderer or videoId with title
    # Let's search for "videoId":"..." and surrounding title
    vids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', json_str)
    seen = set()
    unique_vids = [x for x in vids if not (x in seen or seen.add(x))]
    print(f"Found {len(unique_vids)} video IDs:")
    for v in unique_vids:
        # Find title near videoId
        # Look for "title":{"runs":[{"text":"..."
        idx = json_str.find(v)
        snippet = json_str[idx:idx+800]
        title_match = re.search(r'"title":\{"runs":\[\{"text":"(.*?)"\}', snippet)
        title = title_match.group(1) if title_match else "Unknown"
        print(f"  - [{v}] {title}")
else:
    print("Could not find ytInitialData")
