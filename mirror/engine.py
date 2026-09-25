"""Manifest-based, fail-closed one-way mirror. No cloud mutation APIs."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import time
import uuid


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha1').hexdigest()


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(tmp, path)


def read_json(path, default):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


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
    def __init__(self, root, state, provider, confirmations=2, min_delete_age=21600, progress=None):
        self.root = Path(root).resolve()
        self.state = Path(state).resolve()
        if self.state.is_relative_to(self.root) or self.root.is_relative_to(self.state):
            raise ValueError('State and photo roots must be separate')
        self.provider = provider
        self.confirmations = max(2, confirmations)
        self.min_delete_age = max(0, min_delete_age)
        self.progress = progress or (lambda **kwargs: None)

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
        old_paths = {v['path']: v for v in old.values()}
        try:
            # Download and verify ALL replacements before changing any managed file.
            for index, (key, item) in enumerate(current.items()):
                self.progress(phase='verifying_and_downloading', completed=index, total=len(current))
                target = safe_path(self.root, item['path'])
                if target.exists() and digest(target) == item['sha1']:
                    continue
                if target.exists():
                    prior = old_paths.get(item['path'])
                    if not prior or digest(target) != prior['sha1']:
                        raise ValueError('Unmanaged or locally modified destination; manual review required')
                expected = digest(target) if target.exists() else None
                stage = self.state / 'staging' / run_id / (uuid.uuid4().hex + '.part')
                stage.parent.mkdir(parents=True, exist_ok=True)
                self.provider.download(key, stage)
                if not stage.is_file() or digest(stage) != item['sha1']:
                    raise ValueError('Downloaded file hash mismatch')
                if item.get('size', 0) > 0 and stage.stat().st_size != item['size']:
                    raise ValueError('Downloaded file size mismatch')
                staged.append((stage, target, item, expected))
            for stage, target, item, expected in staged:
                safe_path(self.root, item['path'])
                if (digest(target) if target.exists() else None) != expected:
                    raise ValueError('Destination changed during download; replacement refused')
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    self._quarantine(target, item['path'], run_id, report, remove=False)
                # Stage final replacement on same filesystem for atomic publish.
                import shutil
                temp = target.with_name('.' + target.name + '.' + uuid.uuid4().hex + '.tmp')
                try:
                    shutil.copyfile(stage, temp)
                    if digest(temp) != item['sha1']:
                        raise ValueError('Copy verification failed')
                    os.replace(temp, target)
                finally:
                    temp.unlink(missing_ok=True)
                    stage.unlink(missing_ok=True)
            # A second cloud read must match before cleaning ANY obsolete path.
            self.progress(phase='rechecking_cloud', completed=len(current), total=len(current))
            if self.provider.snapshot() != current:
                raise ValueError('Cloud changed during download; cleanup deferred')
            for item in current.values():
                if digest(safe_path(self.root, item['path'])) != item['sha1']:
                    raise ValueError('Final verification failed; cleanup deferred')
            self.progress(phase='reconciling', completed=len(current), total=len(current))
            active_paths = {v['path'] for v in current.values()}
            missing = {}
            retained = dict(current)
            for key, item in old.items():
                if key in current and item['path'] == current[key]['path']:
                    continue
                if item['path'] in active_paths:
                    continue
                prior = previous['missing'].get(key, {'count': 0, 'since': now})
                observation = {'count': prior['count'] + 1, 'since': prior['since']}
                if observation['count'] < self.confirmations or now - observation['since'] < self.min_delete_age:
                    missing[key] = observation
                    # Keep obsolete paths under a unique tombstone key even after same-ID moves.
                    tombstone = key if key not in current else 'obsolete:' + item['path']
                    retained[tombstone] = item
                    missing[tombstone] = observation
                    continue
                target = safe_path(self.root, item['path'])
                if target.exists():
                    if digest(target) != item['sha1']:
                        raise ValueError('Locally modified obsolete file; cleanup refused')
                    self._quarantine(target, item['path'], run_id, report)
            save_json(self.state / 'manifest.json', {'root': str(self.root), 'source': source, 'items': retained, 'missing': missing})
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
