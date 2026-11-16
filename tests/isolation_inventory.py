"""Read-only owner-folder inventory around explicitly isolated test runs."""
import argparse,hashlib,json,os,pwd,stat
from pathlib import Path

def inventory():
    root=Path(pwd.getpwuid(os.getuid()).pw_dir)/'.openmontage'
    rows={}
    for path in [root,*root.rglob('*')]:
        s=path.lstat()
        rows[str(path.relative_to(root))]={'mtime_ns':s.st_mtime_ns,'mode':s.st_mode,'size':s.st_size,
          'value':os.readlink(path) if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
    return rows


def expected_live_log_change(before,after):
    """The orchestrator's temporary exception permits no other difference."""
    log='backlot/server.log'
    if before.keys()!=after.keys():return False
    changed={k for k in before if before[k]!=after[k]}
    if changed!={log}:return False
    old,new=before[log],after[log]
    if old.keys()!=new.keys() or not stat.S_ISREG(old.get('mode',0)):return False
    permitted={'mtime_ns','size','value'}
    return all(old[k]==new[k] for k in old if k not in permitted)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output');parser.add_argument('--compare')
    parser.add_argument('--allow-live-server-log',action='store_true')
    args=parser.parse_args();rows=inventory()
    with Path(args.output).open('x') as f:json.dump(rows,f,sort_keys=True,indent=2)
    if args.compare:
        before=json.loads(Path(args.compare).read_text())
        changed=[k for k in rows.keys()|before.keys() if rows.get(k)!=before.get(k)]
        if changed:
            if args.allow_live_server_log and expected_live_log_change(before,rows):
                print(str(len(rows))+' real-folder entries inventoried; only the existing server log changed, as expected. Both inventories are retained.')
                raise SystemExit(0)
            raise SystemExit('STOP: the real app folder changed during this run. '+str(len(changed))+' entries differ.')
    print(str(len(rows))+' real-folder entries inventoried; '+('unchanged.' if args.compare else 'read only.'))
