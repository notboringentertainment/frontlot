"""_hero_budget against real signed receipts (no monkeypatched counts): the 'started but not judged or voided'
set and the resulting estimate. Own file so the receipt fixtures' autouse gates dir stays out of the pure adapter tests."""
import pytest

from backlot import claude_ops as ops
from lib import qc_receipts as qr
from lib.sheet_qc import policy
from tests.lib.test_authored_film_contract import PIPELINE, gates_dir, project, write_project_config  # noqa: F401
from tests.lib.look_lock_helpers import PROJECT

ENTITY = "hero-a"
LOOK = "b" * 64


def _series(builder):
    return {"entity_kind": "character", "entity_id": ENTITY, "role": "hero", "look_hash": LOOK,
            "headshot_receipt_id": None, "policy_bundle_sha256": policy.hero_bundle_sha256(),
            "builder_policy_sha256": builder * 64, "generation_endpoint": "m", "generation_model": "m",
            "judge_provider": "openai", "judge_model": "j"}


def _start(film, builder):
    key = _series(builder)
    return qr.start_attempt(film, key, max_attempts=9, budget_key=qr.hero_budget_key(PROJECT, ENTITY, LOOK), budget_cap=9)


def test_hero_budget_counts_unjudged_started_attempts_from_real_receipts(project, monkeypatch):
    _, film = project

    class Cfg:
        def require_hero_qc(self):
            class Q: max_hero_attempts = 5
            return Q()
    monkeypatch.setattr(ops, "_config", lambda f: Cfg())
    assert ops._hero_budget(film, ENTITY, LOOK) == (5, 0)
    voided, open_a, judged = _start(film, "1"), None, None
    qr.void_attempt(film, voided["attempt_id"], reason="reservation failed")
    judged = _start(film, "2")
    qr.attach_verdict(film, judged["attempt_id"], qc_receipt_id="qc-1")
    open_a = _start(film, "3")
    left, unjudged = ops._hero_budget(film, ENTITY, LOOK)
    assert (left, unjudged) == (2, 1)   # 3 started of 5; voided and judged are closed, the third is not
    monkeypatch.setattr(ops, "_active_look_hash", lambda f, e: LOOK)
    monkeypatch.setattr(ops, "_config_digest", lambda f: "c" * 64)
    (film / "checkpoint_headshots.json").write_text("{}")
    (film / "frontlot-work").mkdir()
    p = ops.prepare("headshot_candidates", {"entity": ENTITY, "candidates": 2}, repo=film, film_slug="film",
                    film_root=film, snapshot_dir=film / "snap")
    assert p.estimate_usd == ops._up(2 * (ops.HEADSHOT_PRICE_USD + ops.DEFAULT_RESERVE_USD) + ops.DEFAULT_RESERVE_USD)
    f = ops.prepare("headshot_finish", {"entity": ENTITY}, repo=film, film_slug="film", film_root=film, snapshot_dir=film / "snap")
    assert f.estimate_usd == ops._up(2 * (ops.HEADSHOT_PRICE_USD + ops.DEFAULT_RESERVE_USD) + 2 * ops.DEFAULT_RESERVE_USD)


def test_unreadable_receipts_become_a_plain_op_error(monkeypatch, tmp_path):
    class Cfg:
        def require_hero_qc(self):
            class Q: max_hero_attempts = 3
            return Q()
    monkeypatch.setattr(ops, "_config", lambda f: Cfg())
    def boom(*a, **k):
        raise qr.QCReceiptError("broken chain")
    monkeypatch.setattr(qr, "hero_attempts_started", boom)
    with pytest.raises(ops.OpError, match="can't be read"):
        ops._hero_budget(tmp_path, ENTITY, LOOK)
