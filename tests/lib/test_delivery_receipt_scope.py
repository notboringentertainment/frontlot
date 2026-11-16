"""A Delivery read may reuse proof only while every exact proof input matches."""
import json
import os

import pytest

from lib import gates, receipts
from tests.lib.test_receipts import project, gates_dir, RECORD, PROJECT_ID, STAGE, SCOPE, _mint, _gen


def approved(project):
    return receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE,
                                         RECORD, _mint(), 'hero', entity_id='ch-001')


def test_identical_receipt_proof_runs_once_and_returned_words_are_not_shared(project, monkeypatch):
    row = approved(project)
    checks = []
    original = gates.verify_local_projection
    def checked(*args):
        checks.append(1)
        return original(*args)
    monkeypatch.setattr(gates, 'verify_local_projection', checked)
    with receipts.verified_read_scope():
        for _ in range(10):
            value = receipts.verified_approvals(project, 'hero')
            assert value == [row]
            value[0]['record']['palette'].append('Not signed.')
    assert len(checks) == 1
    with receipts.verified_read_scope():
        assert receipts.verified_approvals(project, 'hero') == [row]
    assert len(checks) == 2, 'A later request starts with no earlier proof.'


@pytest.mark.parametrize('target', ['local', 'chain', 'tip', 'key', 'ledger'])
def test_same_size_same_time_tampering_never_reuses_delivery_proof(project, target):
    row = approved(project)
    paths = {'local': project / 'approvals.jsonl',
             'chain': gates.chain_path(PROJECT_ID, 'approval'),
             'tip': gates.chain_tip_path(PROJECT_ID, 'approval'),
             'key': gates.gates_dir() / 'key', 'ledger': gates.ledger_path()}
    path = paths[target]
    with receipts.verified_read_scope():
        assert receipts.verified_approvals(project, 'hero') == [row]
        raw = path.read_bytes()
        stat = path.stat()
        changed = bytes([raw[0] ^ 1]) + raw[1:]
        path.write_bytes(changed)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        try:
            assert receipts.verified_approvals(project, 'hero') == []
        except (receipts.ReceiptError, gates.ReceiptChainError, ValueError):
            pass
        else:
            assert target == 'ledger'


def test_a_new_signature_is_seen_inside_the_same_delivery_operation(project):
    approved(project)
    with receipts.verified_read_scope():
        assert len(receipts.verified_approvals(project, 'hero')) == 1
        record = {**RECORD, 'palette': ['#223344']}
        newer = receipts.record_human_approval(project, PROJECT_ID, STAGE, SCOPE,
                    record, _mint(record), 'hero', entity_id='ch-001')
        assert receipts.verified_approvals(project, 'hero')[-1] == newer
        assert len(receipts.verified_approvals(project, 'hero')) == 2


def test_foreign_production_cannot_borrow_a_warm_receipt_proof(project, tmp_path):
    row = approved(project)
    foreign = tmp_path / 'proj-beta'
    foreign.mkdir()
    (foreign / 'approvals.jsonl').write_text(json.dumps(row) + '\n')
    with receipts.verified_read_scope():
        assert receipts.verified_approvals(project, 'hero') == [row]
        with pytest.raises(receipts.ReceiptChainError):
            receipts.verified_approvals(foreign, 'hero')


@pytest.mark.parametrize('target', ['local', 'chain', 'tip', 'key', 'ledger'])
def test_generation_proof_rechecks_all_exact_inputs(project, target):
    row = _gen(project, '22' * 32)
    paths = {'local': receipts.generation_receipts_path(project),
             'chain': gates.chain_path(PROJECT_ID, 'generation'),
             'tip': gates.chain_tip_path(PROJECT_ID, 'generation'),
             'key': gates.gates_dir() / 'key', 'ledger': gates.generation_ledger_path()}
    with receipts.verified_read_scope():
        assert receipts.verified_generation_receipts(project) == [row]
        path = paths[target]
        raw, stat = path.read_bytes(), path.stat()
        path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        try:
            assert receipts.verified_generation_receipts(project) == []
        except (receipts.ReceiptError, gates.ReceiptChainError, ValueError):
            pass


def test_simultaneous_requests_do_not_share_a_receipt_scope(project, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock
    row = approved(project)
    barrier, lock, checks = Barrier(2), Lock(), []
    original = gates.verify_local_projection
    def counted(*args):
        with lock:
            checks.append(1)
        return original(*args)
    monkeypatch.setattr(gates, 'verify_local_projection', counted)
    def request():
        with receipts.verified_read_scope():
            assert receipts.verified_approvals(project, 'hero') == [row]
            barrier.wait(timeout=10)
            assert receipts.verified_approvals(project, 'hero') == [row]
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(request) for _ in range(2)]
        for job in jobs:
            job.result(timeout=15)
    assert len(checks) == 2
