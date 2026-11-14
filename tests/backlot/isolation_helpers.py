"""Short disposable signing paths, independent of a runner's optional flags."""
import os,tempfile
from pathlib import Path


def signing_directory(prefix='g'):
    # Leave room for sessions, an invented production name and '.sock' on
    # macOS. Prefer the explicitly isolated app parent, not a long TMPDIR.
    for name in ('FRONTLOT_FOLLOWUPS_PARENT','OPENMONTAGE_HOME','FRONTLOT_TEST_RUN_ROOT','TMPDIR'):
        raw=os.environ.get(name)
        if raw:
            parent=Path(raw).resolve()
            if parent.is_dir() and len(os.fsencode(parent))<=54:
                return Path(tempfile.mkdtemp(prefix=prefix,dir=parent))
    metadata=os.environ.get('OPENMONTAGE_METADATA_PATH')
    if metadata:
        parent=Path(metadata).resolve().parent
        if parent.is_dir() and len(os.fsencode(parent))<=54:
            return Path(tempfile.mkdtemp(prefix=prefix,dir=parent))
    # Ordinary pytest without any custom environment still owns a fresh,
    # private temporary folder. No application home is consulted.
    return Path(tempfile.mkdtemp(prefix=prefix,dir='/private/tmp' if Path('/private/tmp').is_dir() else '/tmp'))
