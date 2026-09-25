"""Manifest-based, fail-closed one-way mirror. No cloud mutation APIs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import time
import uuid
import errno
import shutil
import stat


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha1').hexdigest()


def save_json(path, data, durable=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        if durable:
            os.fsync(stream.fileno())
    os.replace(tmp, path)


def read_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def fingerprint(path):
    try:
        info = path.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('Expected a regular file')
    return {'size': info.st_size, 'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}


class ProgressWriter:
    """Progress is replaceable UI state, not a durable per-file transaction."""
    def __init__(self, path, interval=2, clock=time.monotonic):
        self.path, self.interval, self.clock = path, interval, clock
        self.last_time, self.last_phase = float('-inf'), None

    def __call__(self, **data):
        now = self.clock()
        if (data['phase'] != self.last_phase or now - self.last_time >= self.interval
                or data.get('completed') == data.get('total')):
            save_json(self.path, data, durable=False)
            self.last_time, self.last_phase = now, data['phase']


def safe_path(root, relative):
    p = PurePosixPath(relative)
    if str(p) != relative or p.is_absolute() or not p.parts or any(x in ('', '.', '..') for x in p.parts) or '\\' in relative:
        raise ValueError('Unsafe relative path')
    current = root
    for part in p.parts:
        if ':' in part:
            raise ValueError('Unsafe path component')
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlink in managed path')
    # A canonical relative path with no '..' or symlink components stays inside
    # root. Avoid repeated resolve/stat walks across Windows-mounted filesystems.
    return current


def validate(entries, root):
    paths = set()
    for key, item in entries.items():
        if not key or len(item['sha1']) != 40 or any(c not in '0123456789abcdef' for c in item['sha1']):
            raise ValueError('Missing or invalid content hash')
        safe_path(root, item['path'])
        normalized = item['path'].casefold()
        if normalized in paths:
            raise ValueError('Multiple cloud files map to one local path')
        paths.add(normalized)


class Mirror:
    def __init__(self, root, state, provider, progress=None, bootstrap_existing=False):
        self.root = Path(root).resolve()
        self.state = Path(state).resolve()
        if self.state.is_relative_to(self.root) or self.root.is_relative_to(self.state):
            raise ValueError('State and photo roots must be separate')
        self.provider = provider
        self.progress = progress or (lambda **kwargs: None)
        self.bootstrap_existing = bootstrap_existing

    def run(self, apply=False, now=None):
        self.state.mkdir(parents=True, exist_ok=True)
        # POSIX lock is released by the kernel even after a crash. Container/WSL only.
        import fcntl
        with open(self.state / 'lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self._run(apply, time.time() if now is None else now)

    def _run(self, apply, now):
        previous = read_json(self.state / 'manifest.json', {'items': {}, 'missing': {}})
        old = previous['items']
        bootstrap = self.bootstrap_existing and not (self.state / 'manifest.json').exists()
        if previous.get('root', str(self.root)) != str(self.root):
            raise ValueError('Photo root changed; a new state directory is required')
        # Provider must return only after ALL pages and stability checks succeed.
        self.progress(phase='scanning', completed=0, total=0)
        current = self.provider.snapshot()
        source = getattr(self.provider, 'identity', 'test-provider')
        if previous.get('source', source) != source:
            raise ValueError('Cloud account changed; a new state directory is required')
        validate(current, self.root)
        validate(old, self.root)
        old_spelling = {v['path'].casefold(): v['path'] for v in old.values()}
        for item in current.values():
            if old_spelling.get(item['path'].casefold(), item['path']) != item['path']:
                raise ValueError('Case-only rename requires manual review on Windows storage')
        report = {'time': now, 'apply': apply, 'cloud_count': len(current),
                  'added': [], 'modified': [], 'moved': [], 'deleted': [], 'quarantined': []}
        old_by_hash = {}
        for key, item in old.items():
            old_by_hash.setdefault(item['sha1'], []).append((key, item))
        for key, item in current.items():
            prior = old.get(key)
            if prior and prior['sha1'] != item['sha1']:
                report['modified'].append({'from': prior['path'], 'to': item['path']})
            elif prior and prior['path'] != item['path']:
                report['moved'].append({'from': prior['path'], 'to': item['path']})
            elif not prior:
                matches = [(k, v) for k, v in old_by_hash.get(item['sha1'], []) if k not in current]
                if len(matches) == 1:
                    report['moved'].append({'from': matches[0][1]['path'], 'to': item['path']})
                else:
                    report['added'].append(item['path'])
        report['deleted'] = [v['path'] for k, v in old.items() if k not in current]
        run_id = f'{int(now)}-{uuid.uuid4().hex[:8]}'
        report_path = self.state / 'reports' / (run_id + '.json')
        report['status'] = 'planned'
        save_json(report_path, report)
        if not apply:
            return report
        self.root.mkdir(parents=True, exist_ok=True)
        staged = []
        observed = {}
        local = previous.get('local', {})
        counters = {'adopted': 0, 'unchanged': 0, 'downloaded': 0, 'resumed': 0, 'reused': 0, 'downloaded_bytes': 0}
        report['transfer'] = counters
        old_paths = {v['path']: v for v in old.values()}
        try:
            # Verified receipts survive cancellation. Legacy random .part names can
            # be recovered by hashing once, or by an operator-verified import index.
            cache = {}
            receipts = read_json(self.state / 'resume-index.json', {})
            wanted_sizes = {i.get('size') for i in current.values()}
            candidates = list((self.state / 'staging').rglob('*.part'))
            for index, candidate in enumerate(candidates):
                self.progress(phase='recovering_downloads', completed=index, total=len(candidates))
                relative = candidate.relative_to(self.state).as_posix()
                safe_path(self.state, relative)
                stamp = fingerprint(candidate)
                if stamp['size'] not in wanted_sizes:
                    continue
                receipt = read_json(candidate.with_suffix('.json'), receipts.get(relative, {}))
                before = stamp
                sha = receipt.get('sha1') if receipt.get('fingerprint') == stamp else digest(candidate)
                if fingerprint(candidate) != before:
                    raise ValueError('Staged file changed during verification')
                cache.setdefault(sha, []).append((candidate, stamp))
                if receipt.get('fingerprint') != stamp:
                    save_json(candidate.with_suffix('.json'), {'sha1': sha, 'fingerprint': stamp})
            # Download and verify ALL replacements before changing any managed file.
            for index, (key, item) in enumerate(current.items()):
                self.progress(phase='checking_files', completed=index, total=len(current), **counters)
                target = safe_path(self.root, item['path'])
                stamp = fingerprint(target)
                if stamp:
                    cached = local.get(item['path'], {})
                    known = cached.get('sha1') if cached.get('fingerprint') == stamp else None
                    if bootstrap and item.get('size', 0) > 0 and stamp['size'] == item['size']:
                        observed[item['path']] = {'sha1': item['sha1'], 'fingerprint': stamp}
                        counters['adopted'] += 1
                        continue
                    if known is None:
                        known = digest(target)
                        if fingerprint(target) != stamp:
                            raise ValueError('Local file changed during verification')
                    if known == item['sha1']:
                        observed[item['path']] = {'sha1': item['sha1'], 'fingerprint': stamp}
                        counters['unchanged'] += 1
                        continue
                    prior = old_paths.get(item['path'])
                    if not prior or known != prior['sha1']:
                        raise ValueError('Unmanaged or locally modified destination; manual review required')
                expected = stamp
                cached_stages = cache.get(item['sha1'], [])
                stage = None
                while cached_stages:
                    candidate, verified_stamp = cached_stages.pop()
                    if fingerprint(candidate) == verified_stamp and (not item.get('size') or verified_stamp['size'] == item['size']):
                        stage = candidate
                        counters['resumed'] += 1
                        break
                if stage is not None:
                    staged.append((stage, target, item, expected, fingerprint(stage)))
                    continue
                stage = self.state / 'staging' / run_id / (uuid.uuid4().hex + '.part')
                stage.parent.mkdir(parents=True, exist_ok=True)
                reuse = None
                for _, prior in old_by_hash.get(item['sha1'], []):
                    candidate = safe_path(self.root, prior['path'])
                    if candidate.is_file() and digest(candidate) == item['sha1']:
                        reuse = candidate
                        break
                if reuse:
                    shutil.copyfile(reuse, stage)
                    counters['reused'] += 1
                else:
                    self.progress(phase='downloading', completed=index, total=len(current), **counters)
                    self.provider.download(key, stage)
                    counters['downloaded'] += 1
                    counters['downloaded_bytes'] += stage.stat().st_size
                if not stage.is_file() or digest(stage) != item['sha1']:
                    raise ValueError('Downloaded file hash mismatch')
                if item.get('size', 0) > 0 and stage.stat().st_size != item['size']:
                    raise ValueError('Downloaded file size mismatch')
                stamp = fingerprint(stage)
                save_json(stage.with_suffix('.json'), {'sha1': item['sha1'], 'fingerprint': stamp})
                staged.append((stage, target, item, expected, stamp))
            for index, (stage, target, item, expected, stage_stamp) in enumerate(staged):
                self.progress(phase='publishing', completed=index, total=len(staged), **counters)
                safe_path(self.root, item['path'])
                if fingerprint(target) != expected or fingerprint(stage) != stage_stamp:
                    raise ValueError('Destination changed during download; replacement refused')
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    self._quarantine(target, item['path'], run_id, report, remove=False)
                # Stage final replacement on same filesystem for atomic publish.
                temp = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.tmp')
                try:
                    try:
                        os.replace(stage, target)
                    except OSError as error:
                        if error.errno != errno.EXDEV:
                            raise
                        shutil.copyfile(stage, temp)
                        if digest(temp) != item['sha1']:
                            raise ValueError('Copy verification failed')
                        os.replace(temp, target)
                        stage.unlink()
                finally:
                    temp.unlink(missing_ok=True)
                stage.with_suffix('.json').unlink(missing_ok=True)
                observed[item['path']] = {'sha1': item['sha1'], 'fingerprint': fingerprint(target)}
            # Reconcile this complete snapshot; later cloud changes belong to the next run.
            for index, item in enumerate(current.values()):
                self.progress(phase='checking_metadata', completed=index, total=len(current), **counters)
                if fingerprint(safe_path(self.root, item['path'])) != observed[item['path']]['fingerprint']:
                    raise ValueError('Local file changed during sync; cleanup deferred')
            self.progress(phase='reconciling', completed=len(current), total=len(current))
            active_paths = {v['path'] for v in current.values()}
            for key, item in old.items():
                if key in current and item['path'] == current[key]['path']:
                    continue
                if item['path'] in active_paths:
                    continue
                target = safe_path(self.root, item['path'])
                if target.exists():
                    if digest(target) != item['sha1']:
                        raise ValueError('Locally modified obsolete file; cleanup refused')
                    self._quarantine(target, item['path'], run_id, report)
            save_json(self.state / 'manifest.json', {'root': str(self.root), 'source': source, 'items': current, 'missing': {}, 'local': observed})
            report['status'] = 'completed'
        except Exception:
            report['status'] = 'failed; cleanup/baseline not committed'
            save_json(report_path, report)
            raise
        save_json(report_path, report)
        return report

    def _quarantine(self, source, relative, run_id, report, remove=True):
        # Retention is deliberately manual: never permanently delete a photo.
        import shutil
        destination = safe_path(self.state / 'quarantine' / run_id, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ValueError('Quarantine collision')
        shutil.copy2(source, destination)
        if digest(source) != digest(destination):
            raise ValueError('Quarantine verification failed')
        if remove:
            source.unlink()
        report['quarantined'].append(relative)
        save_json(self.state / 'reports' / (run_id + '.json'), report)
