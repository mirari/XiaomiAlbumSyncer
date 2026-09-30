"""Exercise the full XAS task API against an isolated container/cloud fixture."""
import json
from pathlib import Path
import sys
import time
import requests

base = sys.argv[1] if len(sys.argv)>1 else 'http://127.0.0.1:18232'
fixture = Path(sys.argv[2])
s = requests.Session()
def api(method, path, **kwargs):
    r = s.request(method,base+path,timeout=20,**kwargs)
    r.raise_for_status()
    return r.json() if r.content else None

assert s.get(base+'/api/crontab/1/mirror/runs').status_code == 401
api('POST','/api/system-config',json={'password':'integration-test-only'})
api('GET','/api/token',params={'password':'integration-test-only'})
account = api('POST','/api/account',json={'nickname':'Mirror integration test','userId':'fixture-only','passToken':'fixture-only'})
cfg = dict(expression='0 0 3 * * ?',timeZone='Asia/Shanghai',targetPath='/photos',downloadImages=True,downloadVideos=True,downloadAudios=False,rewriteExifTime=False,rewriteExifTimeZone='Asia/Shanghai',syncMode='MIRROR',mirrorAllAlbums=True,mirrorReportOnly=True,notify=False)
task = api('POST','/api/crontab',json=dict(name='镜像集成测试',description='Isolated fixture',enabled=False,syncMode='FULL',accountId=account['id'],albumIds=[],config=cfg))
tid = task['id']
def run():
    before = api('GET',f'/api/crontab/{tid}/mirror/runs')
    api('POST',f'/api/crontab/{tid}/executions')
    deadline = time.monotonic()+30
    while time.monotonic()<deadline:
        runs=api('GET',f'/api/crontab/{tid}/mirror/runs')
        if runs and (not before or runs[0]['id']!=before[0]['id']) and runs[0]['status'] not in ('running','interrupted'):
            return runs[0]
        time.sleep(.2)
    raise AssertionError('execution did not finish')

fixture.joinpath('scenario.json').write_text(json.dumps({'body':'test-photo'}))
assert run()['status']=='reported'
assert not fixture.joinpath('photos/相机/test.jpg').exists()
cfg['mirrorReportOnly']=False
cfg['targetPath']='/photos/${album}/${downloadFileName}'
api('PUT',f'/api/crontab/{tid}',json={'config':cfg})
done=run()
assert done['status']=='completed'
assert fixture.joinpath('photos/相机/test.jpg').read_bytes()==b'test-photo'
resource=f'/api/crontab/{tid}/mirror/runs/{done["id"]}/files/added/0'
assert requests.get(base+resource).status_code == 401
info=api('GET',resource)
assert info['exists'] and info['path']=='/photos/相机/test.jpg' and info['location']=='当前文件'
assert s.get(base+resource+'/preview').status_code==404  # fixture content is not an image
fixture.joinpath('scenario.json').write_text(json.dumps({'body':'edited-photo'}))
edited=run()
report=json.loads(api('GET',f'/api/crontab/{tid}/mirror/runs/{edited["id"]}')['report'])
assert len(report['modified'])==1 and len(report['quarantined'])==1
assert fixture.joinpath('photos/相机/test.jpg').read_bytes()==b'edited-photo'
assert any(p.read_bytes()==b'test-photo' for p in fixture.joinpath('state').rglob('quarantine/*/相机/test.jpg'))
manifest = fixture.joinpath(f'state/{tid}/data/manifest.json')
baseline = manifest.read_bytes()
fixture.joinpath('scenario.json').write_text(json.dumps({'error':True}))
assert run()['status']=='failed'
assert manifest.read_bytes()==baseline
assert fixture.joinpath('photos/相机/test.jpg').read_bytes()==b'edited-photo'
fixture.joinpath('scenario.json').write_text(json.dumps({'empty':True}))
deleted=run()
assert deleted['status']=='completed'
assert not fixture.joinpath('photos/相机/test.jpg').exists()
for category in ('deleted','quarantined'):
    info=api('GET',f'/api/crontab/{tid}/mirror/runs/{deleted["id"]}/files/{category}/0')
    assert info['exists'] and '/quarantine/' in info['path'] and info['location']=='隔离副本'
fixture.joinpath('scenario.json').write_text(json.dumps({'block':True}))
api('POST',f'/api/crontab/{tid}/executions')
for _ in range(40):
    runs=api('GET',f'/api/crontab/{tid}/mirror/runs')
    if runs and runs[0]['status']=='running' and fixture.joinpath('block-started').exists(): break
    time.sleep(.1)
else: raise AssertionError('task was not running')
api('POST',f'/api/crontab/{tid}/mirror/stop')
fixture.joinpath('block-release').touch()
for _ in range(900):
    runs=api('GET',f'/api/crontab/{tid}/mirror/runs')
    if runs[0]['status']=='cancelled': break
    assert runs[0]['status'] not in ('failed','completed'), runs[0]
    time.sleep(.1)
else: raise AssertionError('task did not stop')
print(json.dumps({'native_cloud_api':True,'report_only':True,'download':True,'edit_and_quarantine':True,'failed_page_preserves_baseline':True,'delete':True,'stop':True,'task_id':tid}))
