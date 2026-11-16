"""Read-only owner-folder fingerprints around the follow-up test suites.

Run directly from the repository, with all app locations redirected first.
This does not operate a production or read any signing secret into output.
"""
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import sys
import argparse


def allowed_difference(before,after,*,allow_server_log=False):
    """Only the owner's explicitly authorised live server-log changes qualify."""
    if before.keys()!=after.keys():return False
    mutable={'sha256','size','mtime_ns'}
    for name,row in before.items():
        if row==after[name]:continue
        if not allow_server_log or name!='backlot/server.log':return False
        if {k:v for k,v in row.items() if k not in mutable}!={k:v for k,v in after[name].items() if k not in mutable}:return False
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-server-log',action='store_true')
    parser.add_argument('paths',nargs='+')
    args=parser.parse_args()
    if len(args.paths) not in (1,2):parser.error('Give an output inventory, or the before inventory followed by the new output.')
    parent = Path(os.environ['FRONTLOT_FOLLOWUPS_PARENT']).resolve()
    for name in ('HOME', 'OPENMONTAGE_HOME', 'OPENMONTAGE_GATES_DIR',
                 'OPENMONTAGE_METADATA_PATH', 'TMPDIR'):
        path = Path(os.environ[name]).resolve()
        if not path.is_relative_to(parent):
            raise SystemExit('Stop: every home, signing, metadata and temporary location must be inside the test parent.')
    owner = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.openmontage'
    output = Path(args.paths[-1]).resolve()
    if output.is_relative_to(owner.resolve()):
        raise SystemExit('Stop: fingerprint output must never be written inside the real app folder.')
    if not output.is_relative_to(parent):
        raise SystemExit('Stop: fingerprint output must stay inside the test parent.')
    inventory = {'.': {'mode':owner.lstat().st_mode}}
    for path in sorted(owner.rglob('*')):
        info = path.lstat()
        row = {'mode': info.st_mode, 'size': info.st_size, 'mtime_ns': info.st_mtime_ns}
        if stat.S_ISREG(info.st_mode):
            digest = hashlib.sha256()
            with path.open('rb') as source:
                for block in iter(lambda: source.read(65536), b''):
                    digest.update(block)
            row['sha256'] = digest.hexdigest()
        elif stat.S_ISLNK(info.st_mode):
            row['link'] = os.readlink(path)
        inventory[str(path.relative_to(owner))] = row
    with output.open('x') as handle:
        json.dump(inventory, handle, indent=2)
    if len(args.paths) == 2:
        before = json.loads(Path(args.paths[0]).read_text())
        if not allowed_difference(before,inventory,allow_server_log=args.allow_server_log):
            raise SystemExit('STOP: the real app-folder fingerprint changed. Do not run further tests or helpers.')
        if before!=inventory:
            print('Expected live server-log change only: contents, size or modification time. No entries added or removed; all other data and root permissions unchanged.')
            return
    print(f'Real app-folder fingerprint: {len(inventory)-1} entries and root permissions; unchanged.' if len(args.paths)==2
          else f'Real app-folder baseline: {len(inventory)-1} entries and root permissions recorded read-only.')


if __name__ == '__main__':
    main()
