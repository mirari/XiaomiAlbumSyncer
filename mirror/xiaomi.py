"""Read-only Xiaomi API adapter; authentication follows upstream TokenManager."""
import json
import hashlib
import re
import sqlite3
import time
import uuid
from urllib.parse import urlparse
import requests


class CloudError(RuntimeError):
    pass


def segment(name):
    # Reject ambiguous Windows names, rather than silently map two names to one path.
    if (not name or name in ('.', '..') or name[-1:] in (' ', '.')
            or re.search(r'[\\/:*?"<>|\x00-\x1f]', name)
            or name.split('.')[0].upper() in {'CON','PRN','AUX','NUL', *('COM'+str(i) for i in range(1,10)), *('LPT'+str(i) for i in range(1,10))}):
        raise CloudError('Unsupported local filename; rename in cloud before mirroring')
    return name


class Xiaomi:
    def __init__(self, database, account_id, include_audio=False, album_ids=None, include_images=True, include_videos=True):
        self.database = database
        self.account_id = account_id
        self.include_audio = include_audio
        self.session = requests.Session()
        self.session.headers['User-Agent'] = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36 Edg/139.0.0.0'
        self.cookie = None
        self.expires = 0
        self.assets = {}
        self.album_ids = None if album_ids is None else set(map(str, album_ids))
        self.include_images = include_images
        self.include_videos = include_videos

    def _request(self, url, cookie=None, **kwargs):
        host = urlparse(url).hostname or ''
        if urlparse(url).scheme != 'https' or not any(host == d or host.endswith('.'+d) for d in ('mi.com','xiaomi.com','xiaomi.net','ksyuncs.com','aliyuncs.com','volces.com')):
            raise CloudError('Unexpected cloud endpoint; request refused')
        if cookie and host not in ('i.mi.com','account.xiaomi.com'):
            raise CloudError('Authentication host mismatch')
        method = kwargs.pop('method', 'GET')
        try:
            response = self.session.request(method, url, headers={'Cookie': cookie or ''}, timeout=(20,120), allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise CloudError('Cloud request failed; credentials and URLs omitted') from None
        if not 200 <= response.status_code < 400:
            response.close()
            raise CloudError(f'Cloud HTTP {response.status_code}')
        return response

    def _authenticate(self):
        with sqlite3.connect('file:'+self.database+'?mode=ro', uri=True) as db:
            row = db.execute('select user_id,pass_token from xiaomi_account where id=?',(self.account_id,)).fetchone()
        if row is None:
            raise CloudError('Configured account not found')
        user, token = row
        self.identity = 'xiaomi:' + hashlib.sha256(str(user).encode()).hexdigest()
        cookie = f'userId={user}; deviceId=wb_{uuid.uuid4()}; passToken={token};'
        with self._request('https://i.mi.com/api/user/login', cookie, params={'followUp':'https://i.mi.com','_locale':'zh_CN','ts':int(time.time()*1000)}) as response:
            login = response.json()['data']['loginUrl']
        with self._request(login, cookie) as response:
            location = response.headers.get('Location')
        if not location:
            raise CloudError('Authentication failed; update Xiaomi credentials')
        with self._request(location, cookie) as response:
            service = response.cookies.get('serviceToken')
        if not service:
            raise CloudError('Authentication failed; update Xiaomi credentials')
        self.cookie = f'userId={user}; serviceToken={service};'
        self.expires = time.monotonic()+540

    def _json(self, endpoint, params=None):
        if time.monotonic() >= self.expires:
            self._authenticate()
        with self._request('https://i.mi.com/'+endpoint, self.cookie, params=params) as response:
            result = response.json()
        if result.get('code') != 0 or not isinstance(result.get('data'), dict):
            raise CloudError('Cloud response unsuccessful or incomplete')
        return result['data']

    def _albums(self):
        items = {}
        for page in range(10000):
            data = self._json('gallery/user/album/list', {'pageNum':page,'pageSize':10,'isShared':'false','numOfThumbnails':1})
            if not isinstance(data.get('albums'), list) or not isinstance(data.get('isLastPage'), bool):
                raise CloudError('Invalid album pagination')
            for album in data['albums']:
                key = str(album['albumId'])
                if key in items:
                    raise CloudError('Duplicate album page')
                items[key] = {'name':{ '1':'相机','2':'屏幕截图','1000':'隐私相册'}.get(key,album.get('name')), 'count':int(album['mediaCount']), 'updated':album.get('lastUpdateTime')}
            if data['isLastPage']:
                if '1' not in items:
                    raise CloudError('Camera album missing; incomplete snapshot refused')
                return items
        raise CloudError('Album pagination limit exceeded')

    def snapshot(self):
        albums = self._albums()
        if self.album_ids is not None and not self.album_ids.issubset(albums):
            raise CloudError('Selected album missing; review task scope before continuing')
        entries = {}
        for album_id, album in albums.items():
            if self.album_ids is not None and album_id not in self.album_ids:
                continue
            found = 0
            for page in range(100000):
                data = self._json('gallery/user/galleries', {'albumId':album_id,'pageNum':page,'pageSize':200})
                # Xiaomi omits galleries for a genuinely empty album. Accept only
                # the first, final page when the independently listed count is zero.
                if 'galleries' not in data and album['count'] == 0 and page == 0 and data.get('isLastPage') is True:
                    data['galleries'] = []
                if not isinstance(data.get('galleries'),list) or not isinstance(data.get('isLastPage'),bool):
                    raise CloudError('Incomplete photo page')
                for asset in data['galleries']:
                    found += 1
                    kind = str(asset.get('type', 'image')).lower()
                    if (kind == 'image' and not self.include_images) or (kind == 'video' and not self.include_videos):
                        continue
                    key = 'photo:'+album_id+':'+str(asset['id'])
                    if key in entries:
                        raise CloudError('Duplicate asset page')
                    entries[key] = {'path':segment(album['name'])+'/'+segment(asset['fileName']), 'sha1':asset['sha1'].lower(), 'size':int(asset.get('size') or 0)}
                    self.assets[key] = ('photo',str(asset['id']))
                if data['isLastPage']:
                    break
            else:
                raise CloudError('Photo pagination limit exceeded')
            if found != album['count']:
                raise CloudError('Album count changed or scan incomplete; cleanup refused')
        if self.include_audio:
            for page in range(100000):
                data = self._json('sfs/ns/recorder/dir/0/list', {'limit':500,'offset':page*500})
                if not isinstance(data.get('list'),list):
                    raise CloudError('Incomplete recording page')
                for asset in data['list']:
                    key = 'audio:'+str(asset['id'])
                    if key in entries:
                        raise CloudError('Duplicate recording page')
                    entries[key] = {'path':'录音/'+str(asset['id'])+'_'+segment(asset['name']), 'sha1':asset['sha1'].lower(), 'size':int(asset.get('size') or 0)}
                    self.assets[key] = ('audio',str(asset['id']))
                if len(data['list']) < 500:
                    break
            else:
                raise CloudError('Recording pagination limit exceeded')
        if self._albums() != albums:
            raise CloudError('Albums changed during scan; retry later')
        return entries

    def download(self, key, path):
        kind, asset_id = self.assets[key]
        endpoint = 'gallery/storage' if kind == 'photo' else f'sfs/ns/recorder/file/{asset_id}/cb/dl_sfs_cb_{int(time.time()*1000)}_0/storage'
        data = self._json(endpoint, {'id':asset_id,'ts':int(time.time()*1000)})
        with self._request(data['url']) as response:
            text = response.text
        start, end = text.find('('), text.rfind(')')
        if start < 0 or end <= start:
            raise CloudError('Invalid storage callback')
        signed = json.loads(text[start+1:end])
        with self._request(signed['url'], method='POST', data={'meta':signed['meta']}, stream=True) as response:
            if response.status_code != 200:
                raise CloudError('Download did not return full content')
            with open(path,'wb') as stream:
                for chunk in response.iter_content(1024*1024):
                    stream.write(chunk)
