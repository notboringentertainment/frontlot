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


def main():
    parent = Path(os.environ['FRONTLOT_FOLLOWUPS_PARENT']).resolve()
    for name in ('HOME', 'OPENMONTAGE_HOME', 'OPENMONTAGE_GATES_DIR',
                 'OPENMONTAGE_METADATA_PATH', 'TMPDIR'):
        path = Path(os.environ[name]).resolve()
        if not path.is_relative_to(parent):
            raise SystemExit('Stop: every home, signing, metadata and temporary location must be inside the test parent.')
    owner = Path(pwd.getpwuid(os.getuid()).pw_dir) / '.openmontage'
    output = Path(sys.argv[-1]).resolve()
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
    if len(sys.argv) == 3:
        before = json.loads(Path(sys.argv[1]).read_text())
        if before != inventory:
            raise SystemExit('STOP: the real app-folder fingerprint changed. Do not run further tests or helpers.')
    if len(inventory)-1 != 113:
        raise SystemExit('STOP: the real app folder does not have the expected 113 entries.')
    print('Real app-folder fingerprint: 113 entries and root permissions; unchanged.' if len(sys.argv) == 3
          else 'Real app-folder baseline: 113 entries and root permissions recorded read-only.')


if __name__ == '__main__':
    main()
