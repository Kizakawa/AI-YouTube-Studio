import urllib.request
from html.parser import HTMLParser

html = urllib.request.urlopen('http://localhost:8000/').read().decode('utf-8')

assert 'dashboard.js?v=4.3' in html, 'v=4.3 not found in served html'
assert '<video id="repurposed-video-element" controls playsinline' in html, 'repurposed-video-element without autoplay not found'

class ModalChecker(HTMLParser):
    def __init__(self):
        super().__init__()
        self.stack = []
        self.repurposed_parents = None

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        if attrs_dict.get('id') == 'modal-repurposed-player':
            self.repurposed_parents = [p[1] for p in self.stack]
        if tag not in ['img', 'br', 'hr', 'input', 'meta', 'link']:
            self.stack.append((tag, attrs_dict.get('id', '')))

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                self.stack = self.stack[:i]
                break

checker = ModalChecker()
checker.feed(html)
print('Parents of modal-repurposed-player:', checker.repurposed_parents)
assert 'modal-publish-yt' not in checker.repurposed_parents, 'modal-repurposed-player is still inside modal-publish-yt!'
print('ALL CHECKS PASSED: modal-repurposed-player is now a top-level modal!')
