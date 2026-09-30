"""Exercise the real SQL upgrade chain without touching a service database."""
import json
from pathlib import Path
import sqlite3
import unittest


class UpgradeTest(unittest.TestCase):
    def test_upstream_migrates_mirror_and_backup_paths_uniformly(self):
        migrations = Path(__file__).resolve().parents[1] / 'server/src/main/resources/db/migration'
        version = lambda p: tuple(int(n) for n in p.name.split('__')[0][1:].split('.'))
        with sqlite3.connect(':memory:') as db:
            files = sorted(migrations.glob('V*.sql'), key=version)
            for sql in files:
                if version(sql) < (0, 19, 0):
                    db.executescript(sql.read_text(encoding='utf-8'))
            configs = [
                dict(targetPath='/photos/person', syncMode='MIRROR', mirrorReportOnly=False),
                dict(targetPath='/photos/other', syncMode='MIRROR', mirrorReportOnly=True),
                dict(targetPath='/backup', syncMode='ADD_ONLY'),
                dict(targetPath='/backup', expressionTargetPath='/custom/${fileName}'),
            ]
            for i, config in enumerate(configs, 1):
                db.execute('INSERT INTO crontab (id,name,description,enabled,account_id,config) VALUES (?,?,?,0,1,?)',
                           (i, str(i), '', json.dumps(config)))
            for sql in files:
                if version(sql) >= (0, 19, 0):
                    script = sql.read_text(encoding='utf-8')
                    self.assertNotIn('${', script, 'Flyway would interpret this as a placeholder')
                    db.executescript(script)
            actual = [json.loads(row[0]) for row in db.execute('SELECT config FROM crontab ORDER BY id')]
            for i in range(2):
                self.assertEqual(actual[i], {**configs[i], 'targetPath': configs[i]['targetPath'] + '/${album}/${downloadFileName}'})
            self.assertEqual(actual[2]['targetPath'], '/backup/${album}/${downloadFileName}')
            self.assertEqual(actual[3]['targetPath'], '/custom/${fileName}')


if __name__ == '__main__':
    unittest.main()
