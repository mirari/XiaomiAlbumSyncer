"""HTTP cloud fixture: exercises the real TokenManager, XiaoMiApi and mirror pipe."""
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import time
from urllib.parse import urlparse

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_POST(self): self.do_GET()
    def do_GET(self):
        scenario = json.loads(Path('/fixture/scenario.json').read_text())
        body = scenario.get('body', 'test-photo').encode()
        base = 'http://' + self.headers['Host']
        path = urlparse(self.path).path
        headers = {}
        status = 200
        if path == '/api/user/login': data = {'data': {'loginUrl': base+'/login'}}
        elif path == '/login': status, data, headers = 302, {}, {'Location':base+'/token'}
        elif path == '/token': data, headers = {}, {'Set-Cookie':'serviceToken=fixture-only; Path=/'}
        elif path == '/gallery/user/album/list':
            data = {'code':0,'data':{'albums':[{'albumId':1,'mediaCount':0 if scenario.get('empty') else 1,'lastUpdateTime':0}],'isLastPage':True}}
        elif path == '/gallery/user/galleries':
            if scenario.get('block'):
                Path('/fixture/block-started').touch()
                deadline = time.monotonic() + 30
                while not Path('/fixture/block-release').exists() and time.monotonic() < deadline:
                    time.sleep(.05)
            if scenario.get('error'):
                data = {'code':10001,'retriable':False,'data':None}
            else:
                assets = [] if scenario.get('empty') else [dict(id=1,fileName='test.jpg',sha1=hashlib.sha1(body).hexdigest(),size=len(body),type='image',dateTaken=0,mimeType='image/jpeg')]
                data = {'code':0,'data':{'galleries':assets,'isLastPage':True}}
        elif path == '/gallery/storage': data = {'code':0,'data':{'url':base+'/signed'}}
        elif path == '/signed': data = ('callback('+json.dumps({'url':base+'/file','meta':'fixture'})+')').encode()
        elif path == '/file': data = body
        else: status, data = 404, {}
        content = data if isinstance(data, bytes) else json.dumps(data).encode()
        self.send_response(status)
        for key, value in headers.items(): self.send_header(key, value)
        self.send_header('Content-Length',str(len(content)))
        self.end_headers()
        try: self.wfile.write(content)
        except BrokenPipeError: pass

ThreadingHTTPServer(('0.0.0.0',18080),Handler).serve_forever()
