"""scripts/chain_bootstrap.py — one-time adoption of pre-chain receipts into the
signed per-project receipt chain."""

from __future__ import annotations

import io
import json
import sys

import pytest

from lib import gates, receipts
from lib.canonical_json import record_sha256
from lib.state_io import append_jsonl, read_jsonl
from scripts import chain_bootstrap, gate_approve

PROJECT_ID = "proj-zephyr"
RECORD = {"asset_id": "aa" * 32, "role": "hero", "approved_prompt_block": "a lighthouse at dawn"}


@pytest.fixture(autouse=True)
def gates_dir(tmp_path, monkeypatch):
    d = tmp_path / "gates"
    monkeypatch.setenv("OPENMONTAGE_GATES_DIR", str(d))
    return d


@pytest.fixture
def projects_dir(tmp_path):
    d = tmp_path / "projects"
    d.mkdir()
    return d


@pytest.fixture
def project(projects_dir):
    p = projects_dir / PROJECT_ID
    p.mkdir()
    return p


class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.fixture
def human_tty(monkeypatch):
    def _set(answers: str = "y\n"):
        monkeypatch.setattr(sys, "stdin", _Tty(answers))

    _set()
    return _set


def _approve(project, entity="ch-001"):
    with gates.handler_context():
        token = gates.mint_gate_token(PROJECT_ID, "visual_bible", f"hero:{entity}", record_sha256(RECORD),
                                      user_response="approve")
    return receipts.record_human_approval(project, PROJECT_ID, "visual_bible", f"hero:{entity}", RECORD, token,
                                          "hero", entity_id=entity)


def _generate(project, sha="22" * 32):
    return receipts.record_generation(
        project, execution_id="exec-1", tool="image_gen", model_endpoint="fal-ai/example/edit",
        normalized_inputs_hash="11" * 32, output_sha256=sha, cost_usd=0.0, started_at="t0", finished_at="t1",
    )


def _drop_chains(project_id=PROJECT_ID):
    """Simulate receipts minted before chains existed: signed + ledgered, but unchained."""
    for stream in ("approval", "generation"):
        gates.chain_path(project_id, stream).unlink(missing_ok=True)
        gates.chain_tip_path(project_id, stream).unlink(missing_ok=True)


def _pre_chain_project(project, extra_approvals=()):
    a = _approve(project)
    g = _generate(project)
    for entity in extra_approvals:
        _approve(project, entity)
    _drop_chains()
    with pytest.raises(receipts.ReceiptChainError, match="chain_bootstrap"):
        receipts.chained_rows(project, "approval")
    return a, g


def _run(projects_dir, *extra):
    return chain_bootstrap.main(["--project", PROJECT_ID, "--projects-dir", str(projects_dir), *extra])


def test_prechain_project_bootstraps_and_readers_work(project, projects_dir, human_tty, capsys):
    a, g = _pre_chain_project(project)
    approvals_before = (project / "approvals.jsonl").read_bytes()
    gens_before = (project / "generation-receipts.jsonl").read_bytes()

    assert _run(projects_dir) == 0
    out = capsys.readouterr().out
    assert a["receipt_id"] in out and g["receipt_id"] in out and "hero:ch-001" in out

    # local files untouched, readers accept them
    assert (project / "approvals.jsonl").read_bytes() == approvals_before
    assert (project / "generation-receipts.jsonl").read_bytes() == gens_before
    assert receipts.chained_rows(project, "approval") == [a]
    assert receipts.chained_rows(project, "generation") == [g]
    assert receipts.find_approval(project, "hero", "ch-001") == a
    assert receipts.find_generation(project, "22" * 32) == g

    # marker: signed row in the bootstrap chain, mirrored in the gates ledger
    marker_rows = gates.chain_rows(PROJECT_ID, "bootstrap")
    assert len(marker_rows) == 1 and marker_rows[0]["kind"] == "chain_bootstrap"
    ledger = [r for r in read_jsonl(gates.ledger_path()) if r.get("kind") == "chain_bootstrap"]
    assert len(ledger) == 1
    marker = ledger[0]
    assert marker["project_id"] == PROJECT_ID
    assert marker["adopted_receipt_ids"] == [a["receipt_id"], g["receipt_id"]]
    assert marker["adopted_at"]
    assert gates.verify_receipt_signature(marker)
    assert gates.receipt_digest(marker) == marker_rows[0]["record_sha256"]
    assert gates.ledger_has(marker["receipt_id"], marker["record_sha256"], project_id=PROJECT_ID)

    # new receipts keep chaining after bootstrap
    b = _approve(project, "ch-002")
    assert receipts.chained_rows(project, "approval") == [a, b]


def test_declining_at_the_prompt_writes_nothing(project, projects_dir, human_tty):
    _pre_chain_project(project)
    human_tty("n\n")
    assert _run(projects_dir) == 1
    assert gates.chain_rows(PROJECT_ID, "approval") == []
    assert gates.chain_rows(PROJECT_ID, "bootstrap") == []


def test_tampered_row_refuses_whole_bootstrap(project, projects_dir, human_tty, capsys):
    a, _ = _pre_chain_project(project, extra_approvals=("ch-002",))
    path = project / "approvals.jsonl"
    rows = read_jsonl(path)
    rows[1]["record"] = {"asset_id": "bb" * 32}  # alter the second row; the first stays valid
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))

    assert _run(projects_dir) == 2
    err = capsys.readouterr().err
    assert rows[1]["receipt_id"] in err and "refus" in err
    # nothing adopted, not even the valid first row
    assert gates.chain_rows(PROJECT_ID, "approval") == []
    assert gates.chain_rows(PROJECT_ID, "generation") == []
    assert gates.chain_rows(PROJECT_ID, "bootstrap") == []


def test_unledgered_approval_refuses(project, projects_dir, human_tty, capsys, gates_dir):
    _pre_chain_project(project)
    (gates_dir / "ledger.jsonl").write_text("")  # signature intact, ledger entry gone
    assert _run(projects_dir) == 2
    assert "ledger" in capsys.readouterr().err
    assert gates.chain_rows(PROJECT_ID, "approval") == []


def test_non_tty_refused(project, projects_dir, monkeypatch, capsys):
    _pre_chain_project(project)
    monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
    assert _run(projects_dir) == 2
    assert "interactive terminal" in capsys.readouterr().err
    assert gates.chain_rows(PROJECT_ID, "approval") == []


def test_second_run_refused(project, projects_dir, human_tty, capsys):
    _pre_chain_project(project)
    assert _run(projects_dir) == 0
    human_tty("y\n")
    assert _run(projects_dir) == 2
    assert "already" in capsys.readouterr().err
    assert len(gates.chain_rows(PROJECT_ID, "bootstrap")) == 1


def test_post_chain_project_with_marker_absent_but_fully_chained_is_refused(project, projects_dir, human_tty, capsys):
    """A project born after chains exist needs no bootstrap."""
    _approve(project)
    assert _run(projects_dir) == 2
    assert "nothing to adopt" in capsys.readouterr().err


def test_crash_resume_adopts_remaining_rows(project, projects_dir, human_tty, monkeypatch, capsys):
    a, g = _pre_chain_project(project, extra_approvals=("ch-002",))
    b = read_jsonl(project / "approvals.jsonl")[1]

    real_append = gates.chain_append
    calls = {"n": 0}

    def crashing_append(project_id, stream, receipt):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("simulated crash mid-adoption")
        return real_append(project_id, stream, receipt)

    monkeypatch.setattr(chain_bootstrap.gates, "chain_append", crashing_append)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _run(projects_dir)
    monkeypatch.setattr(chain_bootstrap.gates, "chain_append", real_append)

    # partial chain: first approval only, no marker
    assert [r["receipt_id"] for r in gates.chain_rows(PROJECT_ID, "approval")] == [a["receipt_id"]]
    assert gates.chain_rows(PROJECT_ID, "bootstrap") == []

    human_tty("y\n")
    assert _run(projects_dir) == 0
    out = capsys.readouterr().out
    assert "resum" in out.lower()
    assert receipts.chained_rows(project, "approval") == [a, b]
    assert receipts.chained_rows(project, "generation") == [g]
    marker = [r for r in read_jsonl(gates.ledger_path()) if r.get("kind") == "chain_bootstrap"]
    assert len(marker) == 1
    assert marker[0]["adopted_receipt_ids"] == [a["receipt_id"], b["receipt_id"], g["receipt_id"]]


def test_resume_refuses_when_partial_chain_diverges_from_local(project, projects_dir, human_tty, capsys):
    a, _ = _pre_chain_project(project, extra_approvals=("ch-002",))
    # partial chain holds a receipt the local file does not lead with
    stray = dict(a, receipt_id="not-the-local-head")
    stray["signature"] = gates.sign_receipt(stray)
    gates.chain_append(PROJECT_ID, "approval", stray)

    assert _run(projects_dir) == 2
    assert "diverge" in capsys.readouterr().err
    assert gates.chain_rows(PROJECT_ID, "bootstrap") == []


def test_fail_closed_error_names_bootstrap_hint_only_when_chain_is_empty(project):
    a = _approve(project)
    _drop_chains()
    with pytest.raises(receipts.ReceiptChainError, match=r"pre-chain receipts.*chain_bootstrap\.py --project proj-zephyr"):
        receipts.chained_rows(project, "approval")
    # a non-empty chain with an extra local row is a forgery, not a pre-chain project
    gates.chain_append(PROJECT_ID, "approval", a)
    forged = dict(a, receipt_id="forged-row")
    forged["signature"] = gates.sign_receipt(forged)
    append_jsonl(project / "approvals.jsonl", forged)
    with pytest.raises(receipts.ReceiptChainError) as exc:
        receipts.chained_rows(project, "approval")
    assert "chain_bootstrap" not in str(exc.value)


def test_require_tty_is_gate_approve_rule():
    assert chain_bootstrap.require_tty is gate_approve.require_tty
