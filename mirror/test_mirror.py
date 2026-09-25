import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from engine import Mirror
from xiaomi import Xiaomi, CloudError, segment


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
        for path in ('../escape','/escape','a\\b','a:stream'):
            self.cloud.entries = {'1':item(path)}
            with self.assertRaises(ValueError): self.mirror.run(True,now=1)
        self.cloud.entries = {'1':item('A.jpg'),'2':item('a.jpg')}
        with self.assertRaises(ValueError): self.mirror.run(True,now=1)
        self.root.mkdir(exist_ok=True)
        (self.root/'link').symlink_to(self.state,target_is_directory=True)
        self.cloud.entries = {'1':item('link/a.jpg')}
        with self.assertRaises(ValueError): self.mirror.run(True,now=1)


class AdapterTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
