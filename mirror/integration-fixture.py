"""Cloud fixture for container integration tests; mount explicitly as MIRROR_WORKER."""
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch
sys.path.insert(0, '/app/mirror')
import worker

cfg = json.load(sys.stdin)
class Cloud:
    identity = 'integration-fixture'
    def snapshot(self):
        scenario = json.loads(Path('/fixture/scenario.json').read_text())
        if scenario.get('block'):
            time.sleep(120)
        self.body = scenario.get('body', 'test-photo').encode()
        return {'photo:1:1': {'path':'相机/test.jpg', 'sha1':hashlib.sha1(self.body).hexdigest(), 'size':len(self.body)}}
    def download(self, key, path):
        path.write_bytes(self.body)

with patch.object(worker, 'Xiaomi', return_value=Cloud()):
    worker.execute(cfg)
