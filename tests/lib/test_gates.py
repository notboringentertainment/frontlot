import json
import os
import stat
from datetime import datetime, timedelta, timezone

import pytest

from lib import gates
from lib.gates import GateBinding, GateTokenExpired, GateTokenInvalid


@pytest.fixture(autouse=True)
def gates_dir(tmp_path, monkeypatch):
    d = tmp_path / "gates"
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(d))
    return d


@pytest.fixture(autouse=True)
def _gate_handler():
    # Tests stand in for the gate handler explicitly (round 2 #4); the
    # refusal tests below leave the context by clearing the nonce.
    with gates.handler_context():
        yield


BIND = dict(project_id="proj-alpha", stage="visual_bible", scope="hero:ch-001", record_sha256="ab" * 32)


def test_mint_stores_only_hmac_and_key_is_private(gates_dir):
    token = gates.mint_gate_token(**BIND, user_response="approve")
    key_path = gates_dir / "key"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    pending = list((gates_dir / "pending").iterdir())
    assert len(pending) == 1
    stored = pending[0].read_text()
    assert token not in stored
    assert pending[0].stem == json.loads(stored)["token_hmac"]
    assert json.loads(stored)["user_response"] == "approve"


def test_consume_succeeds_once_then_rejects_reuse(gates_dir):
    token = gates.mint_gate_token(**BIND, user_response="yes")
    binding = gates.consume_gate_token(token, **BIND)
    assert isinstance(binding, GateBinding)
    assert binding.user_response == "yes"
    assert list((gates_dir / "pending").iterdir()) == []
    assert len(list((gates_dir / "consumed").iterdir())) == 1
    with pytest.raises(GateTokenInvalid):
        gates.consume_gate_token(token, **BIND)


@pytest.mark.parametrize("field", ["project_id", "stage", "scope", "record_sha256"])
def test_consume_rejects_binding_mismatch_without_consuming(gates_dir, field):
    token = gates.mint_gate_token(**BIND)
    wrong = dict(BIND, **{field: "other"})
    with pytest.raises(GateTokenInvalid):
        gates.consume_gate_token(token, **wrong)
    assert len(list((gates_dir / "pending").iterdir())) == 1
    gates.consume_gate_token(token, **BIND)


def test_unknown_token_rejected():
    gates.mint_gate_token(**BIND)
    with pytest.raises(GateTokenInvalid):
        gates.consume_gate_token("not-a-real-token", **BIND)


def test_expired_token_rejected(monkeypatch):
    token = gates.mint_gate_token(**BIND, ttl_seconds=60)
    later = datetime.now(timezone.utc) + timedelta(seconds=61)
    monkeypatch.setattr(gates, "_now", lambda: later)
    with pytest.raises(GateTokenExpired):
        gates.consume_gate_token(token, **BIND)


def _receipt():
    return {"receipt_id": "r-1", "kind": "hero", "record_sha256": "cd" * 32, "approved_at": "t"}


def test_sign_and_verify_receipt_signature():
    r = _receipt()
    r["signature"] = gates.sign_receipt(r)
    assert gates.verify_receipt_signature(r)
    tampered = dict(r, record_sha256="ef" * 32)
    assert not gates.verify_receipt_signature(tampered)
    assert not gates.verify_receipt_signature(dict(r, signature="00" * 32))
    assert not gates.verify_receipt_signature(_receipt())


def test_ledger_append_and_lookup():
    token = gates.mint_gate_token(**BIND)
    binding = gates.consume_gate_token(token, **BIND)
    gates.ledger_append(binding, "r-1")
    assert gates.ledger_has("r-1", BIND["record_sha256"])
    assert not gates.ledger_has("r-1", "ff" * 32)
    assert not gates.ledger_has("r-2", BIND["record_sha256"])


def test_verify_receipt_rejects_forgeries():
    token = gates.mint_gate_token(**BIND)
    token_hmac = gates.token_digest(token)
    # Forged: correct hash, no signature
    unsigned = dict(_receipt(), record_sha256=BIND["record_sha256"],
                    project_id=BIND["project_id"], token_hmac=token_hmac)
    assert not gates.verify_receipt(unsigned)
    # Forged: correct hash, wrong signature
    assert not gates.verify_receipt(dict(unsigned, signature="ab" * 32))
    # Valid signature but no ledger entry
    signed = dict(unsigned)
    signed["signature"] = gates.sign_receipt(signed)
    assert gates.verify_receipt_signature(signed)
    assert not gates.verify_receipt(signed)
    # Signed + ledgered (full tuple)
    binding = gates.consume_gate_token(token, **BIND)
    gates.ledger_append(binding, signed["receipt_id"])
    assert gates.verify_receipt(signed)


def test_verify_receipt_requires_full_ledger_tuple():
    """A ledger row must match token_hmac, receipt_id, record_sha256 AND project_id (#9)."""
    token = gates.mint_gate_token(**BIND)
    binding = gates.consume_gate_token(token, **BIND)
    gates.ledger_append(binding, "r-1")
    base = dict(_receipt(), record_sha256=BIND["record_sha256"],
                project_id=BIND["project_id"], token_hmac=binding.token_hmac)
    ok = dict(base); ok["signature"] = gates.sign_receipt(ok)
    assert gates.verify_receipt(ok)
    for field, value in (("project_id", "other-project"), ("token_hmac", "0" * 64),
                         ("receipt_id", "r-2")):
        bad = dict(base, **{field: value}); bad["signature"] = gates.sign_receipt(bad)
        assert gates.verify_receipt_signature(bad)
        assert not gates.verify_receipt(bad), field
    # A receipt missing the binding fields entirely never verifies
    legacy = _receipt(); legacy["signature"] = gates.sign_receipt(legacy)
    assert not gates.verify_receipt(legacy)


def test_generation_ledger_and_verification():
    receipt = {"receipt_id": "g-1", "output_sha256": "ab" * 32, "project_id": "proj-x",
               "execution_id": "e-1", "tool": "seedream_image"}
    receipt["signature"] = gates.sign_receipt(receipt)
    assert not gates.verify_generation_receipt(receipt)  # signed, not ledgered
    gates.generation_ledger_append(receipt)
    assert gates.verify_generation_receipt(receipt)
    assert not gates.verify_generation_receipt(dict(receipt, project_id="proj-y"))
    forged = dict(receipt, output_sha256="cd" * 32)
    assert not gates.verify_generation_receipt(forged)
    forged["signature"] = gates.sign_receipt(forged)
    assert not gates.verify_generation_receipt(forged)  # signed but no ledger row


def test_peek_binding_and_wal_roundtrip():
    token = gates.mint_gate_token(**BIND)
    binding = gates.peek_binding(token)
    assert binding.token_hmac == gates.token_digest(token)
    assert gates.is_pending(binding.token_hmac)
    path = gates.wal_write(binding.token_hmac, {"token_hmac": binding.token_hmac, "x": 1})
    assert path.exists() and gates.wal_entries() == [{"token_hmac": binding.token_hmac, "x": 1}]
    gates.wal_delete(binding.token_hmac)
    assert gates.wal_entries() == []
    gates.consume_gate_token(token, **BIND)
    assert not gates.is_pending(binding.token_hmac)
    assert gates.consumed_binding(binding.token_hmac) == binding
    with pytest.raises(gates.GateTokenInvalid):
        gates.peek_binding(token)


def test_key_persists_across_calls(gates_dir):
    r = _receipt()
    sig1 = gates.sign_receipt(r)
    sig2 = gates.sign_receipt(r)
    assert sig1 == sig2
    assert (gates_dir / "key").stat().st_size == 32
