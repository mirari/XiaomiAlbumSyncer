import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import worker
from engine import Mirror
from xiaomi import Xiaomi, CloudError, segment, recording_name


def item(path, body=b'original'):
    return {'path':path,'sha1':hashlib.sha1(body).hexdigest(),'size':len(body)}


class Provider:
    def __init__(self):
        self.entries = {'1':item('相机/a.jpg')}
        self.body = b'original'
        self.fail = False
        self.calls = 0
        self.unstable = False

    def snapshot(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError('authentication failed')
        if self.unstable and self.calls % 2 == 0:
            return {}
        return copy.deepcopy(self.entries)

    def download(self, key, path):
        path.write_bytes(self.body)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.root, self.state = base/'photos',base/'state'
        self.cloud = Provider()
        self.mirror = Mirror(self.root,self.state,self.cloud,min_delete_age=10)

    def baseline(self):
        self.mirror.run(True,now=1)

    def test_report_never_downloads_or_commits(self):
        result = self.mirror.run(now=1)
        self.assertEqual(result['added'],['相机/a.jpg'])
        self.assertFalse(self.root.exists())
        self.assertFalse((self.state/'manifest.json').exists())

    def test_adopt_existing_and_idempotent(self):
        self.root.joinpath('相机').mkdir(parents=True)
        self.root.joinpath('相机/a.jpg').write_bytes(b'original')
        self.cloud.download = lambda *args: self.fail('must not redownload')
        self.baseline()
        self.assertEqual(self.mirror.run(True,now=2)['added'],[])

    def test_auth_failure_cannot_remove_or_advance_baseline(self):
        self.baseline()
        old = (self.state/'manifest.json').read_bytes()
        self.cloud.fail = True
        with self.assertRaises(RuntimeError): self.mirror.run(True,now=20)
        self.assertEqual(old,(self.state/'manifest.json').read_bytes())
        self.assertTrue((self.root/'相机/a.jpg').is_file())

    def test_delete_needs_two_spaced_complete_runs_and_quarantines(self):
        self.baseline()
        self.cloud.entries = {}
        self.mirror.run(True,now=2)
        self.mirror.run(True,now=3)
        self.assertTrue((self.root/'相机/a.jpg').exists())
        report = self.mirror.run(True,now=20)
        self.assertEqual(report['quarantined'],['相机/a.jpg'])
        self.assertFalse((self.root/'相机/a.jpg').exists())
        self.assertEqual(list((self.state/'quarantine').rglob('a.jpg'))[0].read_bytes(),b'original')

    def test_bad_replacement_keeps_old(self):
        self.baseline()
        self.cloud.entries['1'] = item('相机/a.jpg',b'edited')
        self.cloud.body = b'corrupt'
        with self.assertRaises(ValueError): self.mirror.run(True,now=2)
        self.assertEqual((self.root/'相机/a.jpg').read_bytes(),b'original')

    def test_edit_retains_old_version(self):
        self.baseline()
        self.cloud.entries['1'] = item('相机/a.jpg',b'edited')
        self.cloud.body = b'edited'
        self.mirror.run(True,now=2)
        self.assertEqual((self.root/'相机/a.jpg').read_bytes(),b'edited')
        self.assertEqual(list((self.state/'quarantine').rglob('a.jpg'))[0].read_bytes(),b'original')

    def test_move_and_new_album(self):
        self.baseline()
        self.cloud.entries = {'new-id':item('旅行/a.jpg')}
        report = self.mirror.run(True,now=2)
        self.assertTrue(report['moved'])
        self.assertTrue((self.root/'旅行/a.jpg').exists())
        self.assertTrue((self.root/'相机/a.jpg').exists())
        self.mirror.run(True,now=20)
        self.assertFalse((self.root/'相机/a.jpg').exists())

    def test_same_id_move_retains_tombstone(self):
        self.baseline()
        self.cloud.entries = {'1':item('旅行/a.jpg')}
        self.mirror.run(True,now=2)
        self.mirror.run(True,now=20)
        self.assertFalse((self.root/'相机/a.jpg').exists())
        self.assertTrue((self.root/'旅行/a.jpg').exists())

    def test_do_not_deduplicate_cloud_membership(self):
        self.cloud.entries['2'] = item('旅行/a.jpg')
        self.baseline()
        self.assertTrue((self.root/'相机/a.jpg').exists())
        self.assertTrue((self.root/'旅行/a.jpg').exists())

    def test_unknown_local_file_is_never_deleted(self):
        self.baseline()
        (self.root/'unknown.jpg').write_bytes(b'keep')
        self.cloud.entries = {}
        self.mirror.run(True,now=2)
        self.mirror.run(True,now=20)
        self.assertTrue((self.root/'unknown.jpg').exists())

    def test_local_conflict_blocks_overwrite(self):
        self.baseline()
        (self.root/'相机/a.jpg').write_bytes(b'local-edit')
        with self.assertRaises(ValueError): self.mirror.run(True,now=2)
        self.assertEqual((self.root/'相机/a.jpg').read_bytes(),b'local-edit')

    def test_cloud_changes_during_download_no_cleanup(self):
        self.baseline()
        self.cloud.entries = {}
        self.mirror.run(True,now=2)
        self.cloud.entries = {'new':item('旅行/b.jpg')}
        self.cloud.unstable = True
        self.cloud.calls = 0
        with self.assertRaises(ValueError): self.mirror.run(True,now=20)
        self.assertTrue((self.root/'相机/a.jpg').exists())

    def test_local_edit_during_download_is_not_overwritten(self):
        self.baseline()
        self.cloud.entries['1'] = item('相机/a.jpg', b'new')
        def download(key, path):
            path.write_bytes(b'new')
            (self.root/'相机/a.jpg').write_bytes(b'local-edit')
        self.cloud.download = download
        with self.assertRaises(ValueError): self.mirror.run(True, now=2)
        self.assertEqual((self.root/'相机/a.jpg').read_bytes(), b'local-edit')

    def test_case_only_move_cannot_remove_active_windows_path(self):
        self.baseline()
        self.cloud.entries['1']['path'] = '相机/A.jpg'
        with self.assertRaises(ValueError): self.mirror.run(True,now=20)
        self.assertTrue((self.root/'相机/a.jpg').exists())

    def test_account_change_requires_new_state(self):
        self.cloud.identity = 'first'
        self.baseline()
        self.cloud.identity = 'second'
        with self.assertRaises(ValueError): self.mirror.run(True,now=20)

    def test_path_collision_traversal_symlink(self):
        for path in ('../escape','/escape','a\\b','a:stream','a//b','a/./b'):
            self.cloud.entries = {'1':item(path)}
            with self.assertRaises(ValueError): self.mirror.run(True,now=1)
        self.cloud.entries = {'1':item('A.jpg'),'2':item('a.jpg')}
        with self.assertRaises(ValueError): self.mirror.run(True,now=1)
        self.root.mkdir(exist_ok=True)
        (self.root/'link').symlink_to(self.state,target_is_directory=True)
        self.cloud.entries = {'1':item('link/a.jpg')}
        with self.assertRaises(ValueError): self.mirror.run(True,now=1)


class AdapterTests(unittest.TestCase):
    def test_recording_names_match_upstream(self):
        for source, expected in (
            ('phone.mp3_78_1_1658720546000_1666531492635', 'phone.mp3'),
            ('meeting.m4a_686_0_1641032438000_1666737108163', 'meeting.m4a'),
            ('app_recording.aac_4054_3_17785072729_1778632353827', 'app_recording.aac'),
            ('sample-audio.m4a_device_location_type_suffix', 'sample-audio.m4a'),
            ('plain.mp3', 'plain.mp3'),
        ):
            self.assertEqual(recording_name(source), expected)

    def test_existing_upstream_recording_is_adopted_without_download(self):
        cloud = Xiaomi('/unused', 1, include_audio=True)
        cloud._albums = lambda: {'1': {'name':'相机','count':0}}
        body = b'existing recording'
        cloud._json = lambda endpoint, params: ({'list': [dict(id=123, name='meeting.mp3_45_0_100_200', sha1=hashlib.sha1(body).hexdigest(), size=len(body))]} if endpoint.startswith('sfs/') else {'isLastPage':True})
        cloud.download = Mock(side_effect=AssertionError('Existing recording must not be downloaded'))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'photos'
            (root/'录音').mkdir(parents=True)
            (root/'录音/123_meeting.mp3').write_bytes(body)
            result = Mirror(root, Path(temp)/'state', cloud).run(True)
            self.assertEqual(result['status'], 'completed')
            cloud.download.assert_not_called()
            self.assertEqual(len(list(root.rglob('*.mp3'))), 1)

    def test_selected_album_scope_and_type_filters(self):
        cloud = Xiaomi('/unused', 1, album_ids=['2'], include_images=False)
        cloud._albums = lambda: {'1': {'name':'相机', 'count':5}, '2': {'name':'旅行', 'count':2}}
        cloud._json = lambda *a, **k: {'galleries': [dict(id=1,fileName='a.jpg',sha1='a'*40,type='image'), dict(id=2,fileName='b.mp4',sha1='b'*40,type='video')], 'isLastPage':True}
        self.assertEqual(list(cloud.snapshot()), ['photo:2:2'])
        cloud.album_ids = {'missing'}
        with self.assertRaises(CloudError): cloud.snapshot()

    def test_download_hosts_do_not_receive_account_cookies(self):
        cloud = Xiaomi('/unused',1)
        cloud.session.request = Mock(return_value=Mock(status_code=200))
        cloud._request('https://c3.xmssmc.mws.xiaomi.net/object')
        self.assertEqual(cloud.session.request.call_args.kwargs['headers']['Cookie'], '')
        for url in ('https://xiaomi.net.attacker.example/object', 'http://xiaomi.net/object'):
            with self.assertRaises(CloudError): cloud._request(url)
        with self.assertRaises(CloudError):
            cloud._request('https://c3.xmssmc.mws.xiaomi.net/object', cookie='private')

    def test_empty_album_omits_gallery_list(self):
        cloud = Xiaomi('/unused',1)
        cloud._albums = lambda: {'1':{'name':'相机','count':0,'updated':0}}
        cloud._json = lambda *a,**k: {'isLastPage':True, 'indexHash':0}
        self.assertEqual(cloud.snapshot(), {})
        cloud._albums = lambda: {'1':{'name':'相机','count':1,'updated':0}}
        with self.assertRaises(CloudError): cloud.snapshot()

    def test_missing_page_does_not_become_empty_album(self):
        cloud = Xiaomi('/unused',1)
        cloud._json = lambda *a,**k: {'albums':[]}
        with self.assertRaises(CloudError): cloud.snapshot()

    def test_count_mismatch_blocks_snapshot(self):
        cloud = Xiaomi('/unused',1)
        cloud._albums = lambda: {'1':{'name':'相机','count':1,'updated':0}}
        cloud._json = lambda *a,**k: {'galleries':[],'isLastPage':True}
        with self.assertRaises(CloudError): cloud.snapshot()

    def test_unsafe_names(self):
        for name in ('../a','CON','a:stream','a.','LPT1.jpg'):
            with self.assertRaises(CloudError): segment(name)


class WorkerTests(unittest.TestCase):
    def test_report_apply_and_scope_change(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            cfg = {'database':'unused', 'account_id':1, 'root':str(base/'photos'), 'state':str(base/'state'), 'run_dir':str(base/'run'), 'apply':False, 'album_ids':None}
            cloud = Provider()
            with patch.object(worker, 'Xiaomi', return_value=cloud):
                worker.execute(cfg)
                self.assertTrue((base/'run/report.json').is_file())
                self.assertFalse((base/'photos').exists())
                cfg['apply'] = True
                worker.execute(cfg)
                self.assertEqual((base/'photos/相机/a.jpg').read_bytes(), b'original')
                cfg['album_ids'] = ['changed']
                with self.assertRaises(CloudError): worker.execute(cfg)
                self.assertEqual((base/'photos/相机/a.jpg').read_bytes(), b'original')


if __name__ == '__main__':
    unittest.main()
