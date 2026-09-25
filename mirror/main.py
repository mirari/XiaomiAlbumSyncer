import argparse
import json
import os
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from engine import Mirror
from xiaomi import Xiaomi


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--apply', action='store_true', help='Enable writes and quarantining; default is report-only')
    parser.add_argument('--verify-local', action='store_true', help='Read and hash all local files instead of trusting unchanged cloud entries')
    parser.add_argument('--daily', action='store_true', help='Run daily at configured local time')
    args = parser.parse_args()
    os.umask(0o077)
    with open(args.config,encoding='utf-8') as stream:
        cfg = json.load(stream)
    def run():
        cloud = Xiaomi(cfg['database'], cfg['account_id'], cfg.get('include_audio',False))
        engine = Mirror(cfg['root'], cfg['state'], cloud, verify_local=args.verify_local)
        try:
            report = engine.run(args.apply)
            print(json.dumps({'status':report['status'],'cloud_count':report['cloud_count'], 'changes':{k:len(report[k]) for k in ('added','modified','moved','deleted','quarantined')}}),flush=True)
        except Exception as error:
            # Never emit requests traces, authentication headers or signed URLs.
            print(json.dumps({'status':'failed','error_type':type(error).__name__,'message':'Review local state; verify connectivity and Xiaomi authentication. No baseline committed.'}),flush=True)
            if not args.daily:
                raise SystemExit(1)
    if not args.daily:
        run()
        return
    zone = ZoneInfo(cfg.get('timezone','Asia/Shanghai'))
    hour, minute = map(int,cfg.get('time','03:00').split(':'))
    while True:
        now = datetime.now(zone)
        target = now.replace(hour=hour,minute=minute,second=0,microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        print(json.dumps({'next_run':target.isoformat(),'apply':args.apply}),flush=True)
        while datetime.now(zone) < target:
            time.sleep(min(60,max(0,(target-datetime.now(zone)).total_seconds())))
        run()


if __name__ == '__main__':
    main()
