"""Local pipe client. All cloud operations are owned by XAS's native API."""
import json
import sys


class CloudError(RuntimeError):
    pass


class NativeProvider:
    def __init__(self, identity):
        self.identity = identity

    def request(self, operation, **arguments):
        print(json.dumps(dict(operation=operation, **arguments)), flush=True)
        line = sys.stdin.readline()
        if not line:
            raise CloudError('Native cloud service disconnected')
        response = json.loads(line)
        if 'error' in response:
            raise CloudError(response['error'])
        return response['result']

    def snapshot(self):
        return self.request('snapshot')

    def download(self, key, path):
        self.request('download', key=key, path=str(path))
