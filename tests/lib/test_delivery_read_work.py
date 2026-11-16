"""Measure the real verified Delivery read on invented, signed material."""
import cProfile
import json
import os
import pstats
import time
from pathlib import Path

from tests.backlot.test_delivery_browser import delivery_cut
from tests.lib.test_cutting_signed import signed_cut
from tests.tools.test_signed_footage import film, provider, looks, workspace, sign


def test_profile_verified_delivery_read(delivery_cut, tmp_path, monkeypatch):
    from lib import trailer_delivery, gates
    root, calls, posts = delivery_cut
    manifest=os.environ.get('DELIVERY_PROFILE_MANIFEST')
    if manifest:
        parent=Path(os.environ['TMPDIR']).resolve().parent
        path=Path(manifest).resolve();assert path.is_relative_to(parent)
        material=json.loads(path.read_text())
        root=Path(material['root']).resolve();signing=Path(material['signing']).resolve()
        assert root.is_relative_to(parent) and signing.is_relative_to(parent)
        monkeypatch.setenv('OPENMONTAGE_GATES_DIR',str(signing))
        monkeypatch.setattr('lib.paths.PROJECTS_DIR',root.parent)
    before = {p: p.read_bytes() for p in root.glob('checkpoint_*.json')}
    approvals = (root / 'approvals.jsonl').read_bytes()
    checks = []
    original = gates.verify_local_projection
    def checked(*args):
        checks.append(args[1])
        return original(*args)
    monkeypatch.setattr(gates, 'verify_local_projection', checked)
    profile = cProfile.Profile()
    started = time.monotonic()
    value = profile.runcall(trailer_delivery.snapshot, root)
    elapsed = time.monotonic() - started
    assert value['rough_status'] == ('Signed by you' if manifest else 'Needs your signature')
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert (root / 'approvals.jsonl').read_bytes() == approvals
    assert len(calls['posts']) == 3 and len(posts) == 1
    destination = Path(os.environ.get('DELIVERY_PROFILE_OUTPUT', str(tmp_path)))
    destination.mkdir(parents=True, exist_ok=True)
    profile.dump_stats(str(destination / 'delivery-read.pstats'))
    with (destination / 'delivery-read.txt').open('x') as output:
        pstats.Stats(profile, stream=output).sort_stats('cumulative').print_stats(35)
    with (destination / 'delivery-read.json').open('x') as output:
        json.dump({'seconds': elapsed, 'root': str(root), 'chain_checks': checks}, output)
    assert len(checks) <= 2, 'One read should prove each unchanged receipt stream once.'
