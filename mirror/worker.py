"""One XAS-managed execution. Configuration arrives on stdin, never on argv."""
import json
import os
from pathlib import Path
import sys
from engine import Mirror, save_json, read_json, ProgressWriter
from xiaomi import Xiaomi, CloudError


def execute(cfg):
    state = Path(cfg['state'])
    run = Path(cfg['run_dir'])
    scope = {k: cfg.get(k) for k in ('root', 'account_id', 'album_ids', 'include_images', 'include_videos', 'include_audio')}
    if scope != read_json(state / 'scope.json', scope):
        raise CloudError('Task scope changed after baseline; create a new task with separate state')
    cloud = Xiaomi(cfg['database'], cfg['account_id'], cfg.get('include_audio', False),
                   cfg.get('album_ids'), cfg.get('include_images', True), cfg.get('include_videos', True))
    bootstrap = read_json(state / 'bootstrap.json', {})
    trust_existing = (bootstrap.get('mode') == 'path_and_size'
                      and bootstrap.get('root') == cfg['root']
                      and bootstrap.get('account_id') == cfg['account_id'])
    mirror = Mirror(cfg['root'], state, cloud, progress=ProgressWriter(run / 'progress.json'),
                    bootstrap_existing=trust_existing, verify_local=cfg.get('verify_local', False))
    # Bind scope before any apply writes, including interrupted first executions.
    if cfg.get('apply'):
        save_json(state / 'scope.json', scope)
    result = mirror.run(cfg.get('apply', False))
    save_json(run / 'report.json', result)


if __name__ == '__main__':
    os.umask(0o077)
    cfg = json.load(sys.stdin)
    prior_reports = set((Path(cfg['state']) / 'reports').glob('*.json'))
    try:
        execute(cfg)
    except Exception as error:
        # CloudError texts are authored locally, never include server bodies/URLs.
        message = str(error) if isinstance(error, CloudError) else 'Execution stopped. Check authentication, task scope, path conflicts, and file verification.'
        created = set((Path(cfg['state']) / 'reports').glob('*.json')) - prior_reports
        report = read_json(max(created, key=lambda p: p.stat().st_mtime), {}) if created else {}
        report.update(status='failed', error=message, error_type=type(error).__name__)
        save_json(Path(cfg['run_dir']) / 'report.json', report)
        sys.exit(1)
