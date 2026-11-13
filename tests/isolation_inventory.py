"""Read-only owner-folder inventory around explicitly isolated test runs."""
import argparse,hashlib,json,os,pwd
from pathlib import Path

def inventory():
    root=Path(pwd.getpwuid(os.getuid()).pw_dir)/'.openmontage'
    rows={}
    for path in [root,*root.rglob('*')]:
        s=path.lstat()
        rows[str(path.relative_to(root))]={'mtime_ns':s.st_mtime_ns,'mode':s.st_mode,'size':s.st_size,
          'value':os.readlink(path) if path.is_symlink() else hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None}
    return rows

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('output');parser.add_argument('--compare')
    args=parser.parse_args();rows=inventory()
    with Path(args.output).open('x') as f:json.dump(rows,f,sort_keys=True,indent=2)
    if args.compare:
        before=json.loads(Path(args.compare).read_text())
        changed=[k for k in rows.keys()|before.keys() if rows.get(k)!=before.get(k)]
        if changed:raise SystemExit('STOP: the real app folder changed during this run. '+str(len(changed))+' entries differ.')
    print(str(len(rows))+' real-folder entries inventoried; '+('unchanged.' if args.compare else 'read only.'))
