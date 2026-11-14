"""Rehearsals own their short paths and do not depend on a runner's extras."""
import importlib,os
from pathlib import Path


def test_readiness_import_without_tmpdir_never_chooses_filesystem_root(monkeypatch):
    from tests.backlot import test_early_paid_readiness as readiness
    monkeypatch.delenv('TMPDIR',raising=False);monkeypatch.delenv('FRONTLOT_FOLLOWUPS_PARENT',raising=False)
    importlib.reload(readiness)
    assert readiness.PARENT!=Path('/') and readiness.PARENT.is_dir()


def test_long_tmpdir_keeps_signing_socket_in_short_isolated_app_parent(tmp_path,monkeypatch):
    from tests.backlot.isolation_helpers import signing_directory
    long=tmp_path/('long-disposable-temporary-directory-'*4);long.mkdir()
    monkeypatch.setenv('TMPDIR',str(long));monkeypatch.delenv('FRONTLOT_FOLLOWUPS_PARENT',raising=False)
    chosen=signing_directory('t')
    assert not chosen.is_relative_to(long)
    assert len(os.fsencode(chosen/'sessions'/'trailer-1234567890123456.sock'))<104
